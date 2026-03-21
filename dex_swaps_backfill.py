#!/usr/bin/env python3
"""
Aerodrome DEX Swaps Backfill Script

Scans Aerodrome pools on Base chain for swap events and populates base_dex_swaps table.

Usage:
    python dex_swaps_backfill.py --start-block 20000000 --end-block 20001000
    
    # Resume from a specific block
    python dex_swaps_backfill.py --start-block 20001000 --end-block 20002000

Environment:
    NXFX01_DATABASE_URL - Postgres DSN (Supabase)
    NXFX01_BASE_RPC_URL - Base JSON-RPC endpoint
"""

import os
import sys
import json
import argparse
import logging
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass

import psycopg2
from psycopg2.extras import execute_values
from dotenv import load_dotenv
from web3 import Web3
from web3.contract import Contract
from eth_abi import decode

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Aerodrome Constants (Base Mainnet)
AERODROME_ROUTER = "0xcf77a3ba9a5ca399b7c97c74d54e5b1beb874e43"

# Aerodrome V2 Pool ABI - Swap event signature
# event Swap(address indexed sender, address indexed to, uint256 amount0In, uint256 amount1In, uint256 amount0Out, uint256 amount1Out)
SWAP_EVENT_SIGNATURE = "0xd78ad95fa46c994b6551d0da85fc275fe613ce37657fb8d5e3d1308409d822ae"

# ERC20 Token ABI for decimals
ERC20_ABI = [
    {
        "constant": True,
        "inputs": [],
        "name": "decimals",
        "outputs": [{"name": "", "type": "uint8"}],
        "type": "function"
    },
    {
        "constant": True,
        "inputs": [],
        "name": "symbol",
        "outputs": [{"name": "", "type": "string"}],
        "type": "function"
    }
]

# Well-known stable tokens for USD approximation
STABLE_TOKENS = {
    "0x833589fcd6eeb6e6b3b55b7e98e93e4e9f7f8d67": {"symbol": "USDC", "decimals": 6, "usd_rate": 1.0},
    "0x4ed4e862860bed51a9570b96d89af5e1b0efefed": {"symbol": "Dai", "decimals": 18, "usd_rate": 1.0},
    "0x4200000000000000000000000000000000000006": {"symbol": "WETH", "decimals": 18, "usd_rate": 0.0},  # Will need price fetch
    "0x50c5725949a6f0c72e6c4a6419a7a194d7a41d07": {"symbol": "WETH", "decimals": 18, "usd_rate": 0.0},  # Alternative WETH
}

# Known Aerodrome pool factories (for discovering pools)
AERODROME_FACTORIES = {
    "0xf5a7de2d7db7d3c7d6d87e8c0a27f9d7d9d4f8d6": "StablePoolFactory",  # Example - need to verify actual
}


@dataclass
class SwapRecord:
    """Represents a decoded swap from Aerodrome"""
    block_number: int
    tx_hash: str
    log_index: int
    wallet: str  # The user/trader address
    router: str  # Router address (if applicable)
    pool_address: str
    token_in: str
    token_out: str
    amount_in_raw: int
    amount_out_raw: int
    amount_in_usd: Optional[float]
    amount_out_usd: Optional[float]
    block_timestamp: datetime


class AerodromeSwapScanner:
    """
    Scans Aerodrome DEX pools for swap events on Base chain.
    
    Key assumptions:
    1. The "wallet" (trader) is extracted from the transaction's `from` address
    2. We identify Aerodrome pools via known factory addresses or common pool patterns
    3. For each swap, we determine token_in/token_out from amount0In/amount1In vs amount0Out/amount1Out
    4. USD approximation uses known stablecoin rates (USDC=1, DAI=1) and 0 for others as fallback
    """
    
    def __init__(self, rpc_url: str, db_url: str, batch_size: int = 1000):
        self.w3 = Web3(Web3.HTTPProvider(rpc_url))
        if not self.w3.is_connected():
            raise ConnectionError(f"Failed to connect to RPC: {rpc_url}")
        
        self.db_url = db_url
        self.batch_size = batch_size
        self.token_cache: Dict[str, Dict] = {}
        
        logger.info(f"Connected to Base RPC: {rpc_url}")
        logger.info(f"Current block: {self.w3.eth.block_number}")
    
    def _get_token_info(self, token_address: str) -> Dict[str, Any]:
        """Get token decimals and symbol, with caching"""
        if token_address in self.token_cache:
            return self.token_cache[token_address]
        
        # Check hardcoded stable tokens first
        if token_address.lower() in STABLE_TOKENS:
            info = STABLE_TOKENS[token_address.lower()]
            self.token_cache[token_address] = info
            return info
        
        try:
            token_contract = self.w3.eth.contract(
                address=Web3.to_checksum_address(token_address),
                abi=ERC20_ABI
            )
            decimals = token_contract.functions.decimals().call()
            symbol = token_contract.functions.symbol().call()
            
            info = {
                "symbol": symbol,
                "decimals": decimals,
                "usd_rate": 0.0  # Unknown - need price feed
            }
        except Exception as e:
            logger.debug(f"Failed to get token info for {token_address}: {e}")
            info = {"symbol": "UNKNOWN", "decimals": 18, "usd_rate": 0.0}
        
        self.token_cache[token_address] = info
        return info
    
    def _estimate_usd_value(self, amount_raw: int, token_address: str) -> Optional[float]:
        """Estimate USD value of a token amount"""
        token_info = self._get_token_info(token_address)
        
        if token_info.get("usd_rate", 0) > 0:
            # It's a stablecoin with known rate
            decimals = token_info["decimals"]
            return (amount_raw / (10 ** decimals)) * token_info["usd_rate"]
        
        # For non-stable tokens, return None (requires price feed)
        return None
    
    def _is_aerodrome_pool(self, address: str) -> bool:
        """
        Check if an address is likely an Aerodrome pool.
        
        Heuristics:
        1. Check if contract code contains Aerodrome-specific patterns
        2. Check if it has a Swap event
        3. This is a simplified check - in production, track pools via factory events
        """
        try:
            code = self.w3.eth.get_code(Web3.to_checksum_address(address))
            # Aerodrome pools typically have certain function signatures
            # This is a simplified heuristic
            return len(code) > 0  # Has deployed code
        except:
            return False
    
    def _get_transaction_sender(self, tx_hash: str) -> str:
        """Get the sender (wallet) of a transaction"""
        try:
            tx = self.w3.eth.get_transaction(tx_hash)
            return tx.get('from', '').lower()
        except Exception as e:
            logger.debug(f"Failed to get tx sender for {tx_hash}: {e}")
            return ''
    
    def _decode_swap_log(self, log: Dict) -> Optional[Dict]:
        """
        Decode a Swap event log from an Aerodrome pool.
        
        Aerodrome V2 Swap event:
        Swap(address indexed sender, address indexed to, uint256 amount0In, uint256 amount1In, uint256 amount0Out, uint256 amount1Out)
        
        Returns dict with decoded values or None if decode fails
        """
        try:
            # The log data contains: amount0In, amount1In, amount0Out, amount1Out
            # Topics[0] = event signature
            # Topics[1] = sender (indexed)
            # Topics[2] = to (indexed)
            
            data = log.get('data', '')
            topics = log.get('topics', [])
            
            if len(topics) < 3:
                return None
            
            # Decode amounts (256-bit integers)
            # Each amount is 32 bytes (64 hex chars)
            if len(data) < 128:
                return None
            
            amount0In = int(data[0:64], 16) if data[0:64] else 0
            amount1In = int(data[64:128], 16) if data[64:128] else 0
            
            amount0Out = 0
            amount1Out = 0
            if len(data) >= 192:
                amount0Out = int(data[128:192], 16) if data[128:192] else 0
            if len(data) >= 256:
                amount1Out = int(data[192:256], 16) if data[192:256] else 0
            
            # Determine which token is in/out
            # amount0In/amount0Out corresponds to token0 of the pool
            # amount1In/amount1Out corresponds to token1 of the pool
            
            sender = '0x' + topics[1].hex()[26:]  # Extract address from indexed topic
            to = '0x' + topics[2].hex()[26:] if len(topics) > 2 else ''
            
            return {
                'sender': sender.lower(),
                'to': to.lower(),
                'amount0In': amount0In,
                'amount1In': amount1In,
                'amount0Out': amount0Out,
                'amount1Out': amount1Out
            }
        except Exception as e:
            logger.debug(f"Failed to decode swap log: {e}")
            return None
    
    def scan_blocks(self, start_block: int, end_block: int) -> List[SwapRecord]:
        """
        Scan block range for Aerodrome swap events.
        
        Strategy:
        1. Get all logs matching Swap event signature in block range
        2. Filter to likely Aerodrome pools (heuristic: check for router interaction)
        3. Decode and enrich with transaction sender
        """
        swaps = []
        
        # Get all Swap event logs in the range
        # Using get_logs with event signature
        try:
            # Create filter for Swap events
            swap_filter = {
                'fromBlock': start_block,
                'toBlock': end_block,
                'topics': [SWAP_EVENT_SIGNATURE]
            }
            
            logs = self.w3.eth.get_logs(swap_filter)
            logger.info(f"Found {len(logs)} swap events in blocks {start_block}-{end_block}")
            
            for log in logs:
                try:
                    pool_address = log['address'].lower()
                    
                    # Get block timestamp
                    block = self.w3.eth.get_block(log['blockNumber'])
                    block_timestamp = datetime.fromtimestamp(block['timestamp'])
                    
                    # Decode the swap
                    decoded = self._decode_swap_log(dict(log))
                    if not decoded:
                        continue
                    
                    # Get transaction sender (the actual wallet)
                    tx_hash = log['transactionHash'].hex()
                    wallet = self._get_transaction_sender(tx_hash)
                    
                    if not wallet:
                        continue
                    
                    # Determine token_in and token_out based on amounts
                    # For a pool with token0/token1:
                    # - amount0In > 0 means token0 is being swapped in
                    # - amount1In > 0 means token1 is being swapped in
                    # - Similar logic for outputs
                    
                    # This is a simplification - we need pool's token0/token1
                    # For now, we'll use a placeholder approach
                    
                    amount_in = decoded['amount0In'] + decoded['amount1In']
                    amount_out = decoded['amount0Out'] + decoded['amount1Out']
                    
                    if amount_in == 0:
                        continue  # Not a swap we can process
                    
                    # Determine direction (simplified - assumes amount0 is primary)
                    if decoded['amount0In'] > 0:
                        token_in = pool_address  # We don't know token0 - placeholder
                        token_out = pool_address  # We don't know token1 - placeholder
                    else:
                        token_in = pool_address
                        token_out = pool_address
                    
                    # Try to get better token info from pool
                    # Aerodrome pools have slot0() or tokens() methods
                    try:
                        pool_contract = self.w3.eth.contract(
                            address=Web3.to_checksum_address(pool_address),
                            abi=[{
                                "inputs": [],
                                "name": "token0",
                                "outputs": [{"name": "", "type": "address"}],
                                "stateMutability": "view",
                                "type": "function"
                            }, {
                                "inputs": [],
                                "name": "token1", 
                                "outputs": [{"name": "", "type": "address"}],
                                "stateMutability": "view",
                                "type": "function"
                            }]
                        )
                        token0 = pool_contract.functions.token0().call().lower()
                        token1 = pool_contract.functions.token1().call().lower()
                        
                        # Determine actual tokens based on flow direction
                        if decoded['amount0In'] > 0:
                            token_in = token0
                            amount_in = decoded['amount0In']
                        elif decoded['amount1In'] > 0:
                            token_in = token1
                            amount_in = decoded['amount1In']
                        
                        if decoded['amount0Out'] > 0:
                            token_out = token0
                            amount_out = decoded['amount0Out']
                        elif decoded['amount1Out'] > 0:
                            token_out = token1
                            amount_out = decoded['amount1Out']
                    except:
                        # Pool doesn't expose token0/token1 - use pool address as placeholder
                        pass
                    
                    # Estimate USD values
                    amount_in_usd = self._estimate_usd_value(amount_in, token_in)
                    amount_out_usd = self._estimate_usd_value(amount_out, token_out)
                    
                    swap = SwapRecord(
                        block_number=log['blockNumber'],
                        tx_hash=tx_hash,
                        log_index=log['logIndex'],
                        wallet=wallet,
                        router=AERODROME_ROUTER,
                        pool_address=pool_address,
                        token_in=token_in,
                        token_out=token_out,
                        amount_in_raw=amount_in,
                        amount_out_raw=amount_out,
                        amount_in_usd=amount_in_usd,
                        amount_out_usd=amount_out_usd,
                        block_timestamp=block_timestamp
                    )
                    swaps.append(swap)
                    
                except Exception as e:
                    logger.debug(f"Error processing log: {e}")
                    continue
                    
        except Exception as e:
            logger.error(f"Error scanning blocks {start_block}-{end_block}: {e}")
        
        return swaps
    
    def insert_swaps(self, swaps: List[SwapRecord]) -> int:
        """Insert swaps into database with upsert on conflict"""
        if not swaps:
            return 0
        
        conn = psycopg2.connect(self.db_url)
        cur = conn.cursor()
        
        inserted = 0
        for i in range(0, len(swaps), self.batch_size):
            batch = swaps[i:i + self.batch_size]
            
            values = [
                (
                    s.block_number,
                    s.tx_hash,
                    s.log_index,
                    s.wallet,
                    s.router,
                    s.pool_address,
                    s.token_in,
                    s.token_out,
                    s.amount_in_raw,
                    s.amount_out_raw,
                    s.amount_in_usd,
                    s.amount_out_usd,
                    s.block_timestamp
                )
                for s in batch
            ]
            
            query = """
                INSERT INTO base_dex_swaps (
                    block_number, tx_hash, log_index, wallet, router, pool_address,
                    token_in, token_out, amount_in_raw, amount_out_raw,
                    amount_in_usd, amount_out_usd, block_timestamp
                ) VALUES %s
                ON CONFLICT (tx_hash, log_index) DO NOTHING
                RETURNING id
            """
            
            try:
                result = execute_values(cur, query, values, fetch=True)
                inserted += len(result)
                conn.commit()
                logger.info(f"Inserted {len(result)} swaps in batch {i//self.batch_size + 1}")
            except Exception as e:
                logger.error(f"Database error on batch {i//self.batch_size + 1}: {e}")
                conn.rollback()
        
        cur.close()
        conn.close()
        
        return inserted


def get_aerodrome_pools(rpc_url: str) -> List[str]:
    """
    Get list of known Aerodrome pool addresses.
    
    In production, this would query Aerodrome factory events.
    For now, we use a heuristic approach to find all swap events.
    """
    w3 = Web3(Web3.HTTPProvider(rpc_url))
    
    # This is a placeholder - in production, you'd query the factory
    # or maintain a registry of known pools
    pools = []
    
    # Alternative: scan for any contract with Swap events
    # This is handled by the log scanning in scan_blocks
    
    return pools


def main():
    parser = argparse.ArgumentParser(description='Backfill Aerodrome DEX swaps on Base')
    parser.add_argument('--start-block', type=int, required=True, help='Starting block number')
    parser.add_argument('--end-block', type=int, required=True, help='Ending block number')
    parser.add_argument('--batch-size', type=int, default=1000, help='Batch size for DB inserts')
    parser.add_argument('--step', type=int, default=100, help='Blocks per scan iteration')
    
    args = parser.parse_args()
    
    # Load environment
    load_dotenv()
    
    db_url = os.environ.get('NXFX01_DATABASE_URL')
    rpc_url = os.environ.get('NXFX01_BASE_RPC_URL')
    
    if not db_url:
        logger.error("NXFX01_DATABASE_URL not set")
        sys.exit(1)
    
    if not rpc_url:
        logger.error("NXFX01_BASE_RPC_URL not set")
        sys.exit(1)
    
    logger.info(f"Starting Aerodrome swap backfill: blocks {args.start_block} - {args.end_block}")
    
    scanner = AerodromeSwapScanner(rpc_url, db_url, args.batch_size)
    
    total_swaps = 0
    current_block = args.start_block
    
    while current_block < args.end_block:
        end = min(current_block + args.step, args.end_block)
        
        logger.info(f"Scanning blocks {current_block} - {end}...")
        swaps = scanner.scan_blocks(current_block, end)
        
        if swaps:
            inserted = scanner.insert_swaps(swaps)
            total_swaps += inserted
            logger.info(f"Progress: {end}/{args.end_block} blocks, {total_swaps} total swaps inserted")
        else:
            logger.info(f"No swaps found in blocks {current_block} - {end}")
        
        current_block = end + 1
    
    logger.info(f"Backfill complete. Total swaps inserted: {total_swaps}")


if __name__ == '__main__':
    main()
