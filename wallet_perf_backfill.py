#!/usr/bin/env python3
"""
Wallet Performance Backfill Script

Calculates trading performance metrics from Aerodrome swaps and populates
base_wallet_performance_window table.

Usage:
    python wallet_perf_backfill.py --window-start 2026-03-01 --window-days 7
    
    # With custom parameters
    python wallet_perf_backfill.py --window-start 2026-02-01 --window-days 30 --min-trades 5

Environment:
    NXFX01_DATABASE_URL - Postgres DSN (Supabase)
"""

import os
import sys
import argparse
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Any, Tuple, Optional
from dataclasses import dataclass
from collections import defaultdict

import psycopg2
from psycopg2.extras import execute_values
from dotenv import load_dotenv

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


@dataclass
class Swap:
    """Represents a single swap from base_dex_swaps"""
    id: int
    wallet: str
    pool_address: str
    token_in: str
    token_out: str
    amount_in_raw: int
    amount_out_raw: int
    amount_in_usd: Optional[float]
    amount_out_usd: Optional[float]
    block_timestamp: datetime


@dataclass 
class WalletMetrics:
    """Aggregated performance metrics for a wallet"""
    wallet: str
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    realized_pnl_usd: float = 0.0
    unrealized_pnl_usd: float = 0.0
    
    # Additional metrics
    launch_trades: int = 0
    launch_realized_pnl: float = 0.0
    
    # For detailed calculations
    swap_history: List[Swap] = None
    
    def __post_init__(self):
        if self.swap_history is None:
            self.swap_history = []


class WalletPerformanceCalculator:
    """
    Calculates wallet performance metrics from swap data.
    
    PnL Calculation Approach (First-Cut):
    ======================================
    
    This implementation uses a TOKEN-PAIR ROUND-TRIP approach:
    
    1. For each wallet, group swaps by token pair (token_in -> token_out)
    2. Look for "round trips" where:
       - Wallet sells token A (gets token B)
       - Later, wallet sells token B (gets token A)
    3. Calculate PnL based on the difference in token amounts
    
    Simplifications & Assumptions:
    -----------------------------
    1. We use a "simplified USD" approach:
       - If both tokens in a pair are stables (USDC, DAI, USDT), treat as 1:1
       - For non-stable pairs, PnL is marked as 0 (requires price feed)
    2. A "trade" is defined as one side of a swap
    3. "Winning trade" = realized PnL > 0, "losing trade" = realized PnL < 0
    4. Launch trades are identified by checking if the pool/tokens are in a "launch" list
    
    Alternative Approaches (for future):
    -----------------------------------
    - Use actual price feeds (Coingecko,DEX APIs) for true USD PnL
    - Implement time-weighted returns (Sharpe ratio)
    - Track unrealized PnL from current holdings
    """
    
    # Known stablecoin addresses (Base)
    STABLECOINS = {
        '0x833589fcd6eeb6e6b3b55b7e98e93e4e9f7f8d67': 1.0,  # USDC
        '0x4ed4e862860bed51a9570b96d89af5e1b0efefed': 1.0,  # DAI
    }
    
    # Known launch tokens (would be populated from launches table)
    LAUNCH_TOKENS = set()  # Populated from DB
    
    def __init__(self, db_url: str, min_trades: int = 1):
        self.db_url = db_url
        self.min_trades = min_trades
    
    def load_swaps(self, window_start: datetime, window_days: int) -> Dict[str, List[Swap]]:
        """
        Load all swaps for the given window, grouped by wallet.
        
        Returns: Dict[wallet -> List[Swap]] sorted by timestamp
        """
        conn = psycopg2.connect(self.db_url)
        conn.autocommit = True
        cur = conn.cursor()
        
        window_end = window_start + timedelta(days=window_days)
        
        query = """
            SELECT 
                id, wallet, pool_address, token_in, token_out,
                amount_in_raw, amount_out_raw, amount_in_usd, amount_out_usd,
                block_timestamp
            FROM base_dex_swaps
            WHERE block_timestamp >= %s AND block_timestamp < %s
            ORDER BY wallet, block_timestamp
        """
        
        cur.execute(query, (window_start, window_end))
        
        swaps_by_wallet = defaultdict(list)
        
        for row in cur.fetchall():
            swap = Swap(
                id=row[0],
                wallet=row[1],
                pool_address=row[2],
                token_in=row[3],
                token_out=row[4],
                amount_in_raw=row[5],
                amount_out_raw=row[6],
                amount_in_usd=row[7],
                amount_out_usd=row[8],
                block_timestamp=row[9]
            )
            swaps_by_wallet[swap.wallet].append(swap)
        
        cur.close()
        conn.close()
        
        total_swaps = sum(len(swaps) for swaps in swaps_by_wallet.values())
        logger.info(f"Loaded {total_swaps} swaps for {len(swaps_by_wallet)} wallets")
        
        return dict(swaps_by_wallet)
    
    def load_launch_tokens(self) -> set:
        """Load token addresses from recent launches"""
        conn = psycopg2.connect(self.db_url)
        cur = conn.cursor()
        
        try:
            # Get tokens from launches in the last 30 days
            query = """
                SELECT DISTINCT token_address 
                FROM launches 
                WHERE created_at > NOW() - INTERVAL '30 days'
            """
            cur.execute(query)
            tokens = {row[0].lower() for row in cur.fetchall()}
            self.LAUNCH_TOKENS = tokens
            logger.info(f"Loaded {len(tokens)} launch tokens")
        except Exception as e:
            logger.warning(f"Could not load launch tokens: {e}")
            self.LAUNCH_TOKENS = set()
        finally:
            cur.close()
            conn.close()
        
        return self.LAUNCH_TOKENS
    
    def _is_stable(self, token: str) -> bool:
        """Check if token is a known stablecoin"""
        return token.lower() in self.STABLECOINS
    
    def _is_launch_token(self, token: str) -> bool:
        """Check if token is a launch token"""
        return token.lower() in self.LAUNCH_TOKENS
    
    def _calculate_pair_pnl(self, swaps: List[Swap]) -> Tuple[float, int, int]:
        """
        Calculate PnL for a sequence of swaps in a token pair.
        
        Strategy: Track the "position" a wallet has in each token.
        When they sell what they bought, calculate the difference.
        
        Simplified version: 
        - For stable<>stable pairs: calculate profit/loss directly
        - For other pairs: mark as break-even (0 PnL)
        
        Returns: (realized_pnl, winning_trades, losing_trades)
        """
        if len(swaps) < 2:
            return 0.0, 0, 0
        
        # Track positions: token_address -> cumulative amount
        positions = defaultdict(int)
        realized_pnl = 0.0
        wins = 0
        losses = 0
        
        for swap in swaps:
            # swap.token_in = what they GAVE (sold)
            # swap.token_out = what they GOT (bought)
            
            # They're selling token_in, buying token_out
            sold_amount = swap.amount_in_raw
            bought_amount = swap.amount_out_raw
            
            # Check if they have a previous position in token_out
            # that they're now reducing (taking profit/loss)
            if positions[swap.token_out] > 0:
                # They're closing part of a position
                # Calculate PnL based on cost basis
                # Simplified: use USD values if available
                
                if swap.amount_out_usd and swap.amount_in_usd:
                    # We have USD values - calculate PnL
                    # This is a simplification - true PnL needs cost basis tracking
                    pnl = swap.amount_out_usd - swap.amount_in_usd
                    
                    if pnl > 0:
                        wins += 1
                    elif pnl < 0:
                        losses += 1
                    
                    realized_pnl += pnl
            
            # Update position: they now hold more of token_out
            positions[swap.token_out] += bought_amount
            
            # And less of token_in (if they had it)
            positions[swap.token_in] -= sold_amount
        
        return realized_pnl, wins, losses
    
    def _calculate_stable_pair_pnl(self, swaps: List[Swap]) -> Tuple[float, int, int]:
        """
        Calculate PnL for stable<>stable pairs.
        
        Since both tokens are ~$1, we can calculate actual PnL:
        - Compare amount_out_usd to amount_in_usd directly
        """
        realized_pnl = 0.0
        wins = 0
        losses = 0
        
        for swap in swaps:
            if swap.amount_in_usd and swap.amount_out_usd:
                pnl = swap.amount_out_usd - swap.amount_in_usd
                
                if pnl > 0:
                    wins += 1
                elif pnl < 0:
                    losses += 1
                    
                realized_pnl += pnl
        
        return realized_pnl, wins, losses
    
    def calculate_wallet_metrics(self, wallet: str, swaps: List[Swap]) -> WalletMetrics:
        """
        Calculate all performance metrics for a wallet's swap history.
        """
        metrics = WalletMetrics(wallet=wallet)
        metrics.swap_history = swaps
        
        if len(swaps) < self.min_trades:
            return metrics
        
        # Total trades = number of swaps
        metrics.total_trades = len(swaps)
        
        # Separate swaps by token pair
        pairs = defaultdict(list)
        for swap in swaps:
            # Create pair key (sorted to handle A->B and B->A as same pair)
            pair = tuple(sorted([swap.token_in, swap.token_out]))
            pairs[pair].append(swap)
        
        # Calculate PnL for each pair
        total_realized_pnl = 0.0
        total_wins = 0
        total_losses = 0
        
        for pair, pair_swaps in pairs.items():
            # Check if both tokens are stables
            token0, token1 = pair
            both_stable = self._is_stable(token0) and self._is_stable(token1)
            
            if both_stable:
                pnl, wins, losses = self._calculate_stable_pair_pnl(pair_swaps)
            else:
                # For non-stable pairs, we can't reliably calculate PnL
                # without price data - just count as trades
                pnl, wins, losses = 0.0, 0, 0
            
            total_realized_pnl += pnl
            total_wins += wins
            total_losses += losses
        
        metrics.realized_pnl_usd = total_realized_pnl
        metrics.winning_trades = total_wins
        metrics.losing_trades = total_losses
        
        # Identify launch trades
        launch_swaps = [s for s in swaps if self._is_launch_token(s.token_in) or self._is_launch_token(s.token_out)]
        metrics.launch_trades = len(launch_swaps)
        
        # Calculate win rate
        if metrics.total_trades > 0:
            win_rate = (metrics.winning_trades / metrics.total_trades) * 100
        else:
            win_rate = 0.0
        
        # Calculate additional metrics
        # Avg win/loss
        if metrics.winning_trades > 0:
            metrics.avg_win_usd = total_realized_pnl / metrics.winning_trades if total_realized_pnl > 0 else 0
        
        # Profit factor (gross wins / gross losses)
        # Simplified - using win count ratio for now
        if metrics.losing_trades > 0:
            metrics.profit_factor = metrics.winning_trades / metrics.losing_trades
        else:
            metrics.profit_factor = metrics.winning_trades if metrics.winning_trades > 0 else 0
        
        # Performance tier (based on win rate and PnL)
        metrics.perf_tier = self._assign_perf_tier(metrics)
        
        return metrics
    
    def _assign_perf_tier(self, metrics: WalletMetrics) -> str:
        """
        Assign performance tier based on metrics.
        
        Tiers:
        - TIER_1: >60% win rate AND positive PnL
        - TIER_2: >40% win rate OR positive PnL
        - TIER_3: Everything else
        - TIER_4: Negative PnL and <40% win rate
        """
        win_rate = metrics.winning_trades / metrics.total_trades if metrics.total_trades > 0 else 0
        
        if metrics.realized_pnl_usd > 0 and win_rate >= 0.6:
            return 'TIER_1_WHALE'
        elif metrics.realized_pnl_usd > 0 or win_rate >= 0.4:
            return 'TIER_2_SMART_MONEY'
        elif metrics.realized_pnl_usd < 0 and win_rate < 0.4:
            return 'TIER_4_FLAGGED'
        else:
            return 'TIER_3_RETAIL'
    
    def save_metrics(self, metrics_list: List[WalletMetrics], window_start: datetime, window_days: int) -> int:
        """Save wallet metrics to database"""
        if not metrics_list:
            return 0
        
        conn = psycopg2.connect(self.db_url)
        cur = conn.cursor()
        
        # Calculate derived fields
        values = []
        for m in metrics_list:
            win_rate = (m.winning_trades / m.total_trades * 100) if m.total_trades > 0 else 0
            launch_win_rate = 0  # Simplified
            
            # Avg win/loss
            avg_win = m.realized_pnl_usd / m.winning_trades if m.winning_trades > 0 and m.realized_pnl_usd > 0 else 0
            avg_loss = m.realized_pnl_usd / m.losing_trades if m.losing_trades > 0 and m.realized_pnl_usd < 0 else 0
            
            values.append((
                m.wallet,
                window_start.date(),
                window_days,
                m.total_trades,
                m.winning_trades,
                m.losing_trades,
                m.realized_pnl_usd,
                m.unrealized_pnl_usd,
                win_rate,
                avg_win,
                avg_loss,
                m.profit_factor,
                m.perf_tier,
                m.launch_trades,
                m.launch_realized_pnl,
                launch_win_rate
            ))
        
        query = """
            INSERT INTO base_wallet_performance_window (
                wallet, window_start, window_days,
                total_trades, winning_trades, losing_trades,
                realized_pnl_usd, unrealized_pnl_usd,
                win_rate, avg_win_usd, avg_loss_usd, profit_factor,
                perf_tier, launch_trades, launch_realized_pnl, launch_win_rate
            ) VALUES %s
            ON CONFLICT (wallet, window_start, window_days) DO UPDATE SET
                total_trades = EXCLUDED.total_trades,
                winning_trades = EXCLUDED.winning_trades,
                losing_trades = EXCLUDED.losing_trades,
                realized_pnl_usd = EXCLUDED.realized_pnl_usd,
                unrealized_pnl_usd = EXCLUDED.unrealized_pnl_usd,
                win_rate = EXCLUDED.win_rate,
                avg_win_usd = EXCLUDED.avg_win_usd,
                avg_loss_usd = EXCLUDED.avg_loss_usd,
                profit_factor = EXCLUDED.profit_factor,
                perf_tier = EXCLUDED.perf_tier,
                launch_trades = EXCLUDED.launch_trades,
                launch_realized_pnl = EXCLUDED.launch_realized_pnl,
                launch_win_rate = EXCLUDED.launch_win_rate
        """
        
        try:
            result = execute_values(cur, query, values, fetch=True)
            conn.commit()
            inserted = len(result)
            logger.info(f"Inserted/updated {inserted} wallet performance records")
            return inserted
        except Exception as e:
            logger.error(f"Database error: {e}")
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()


def main():
    parser = argparse.ArgumentParser(description='Calculate wallet performance metrics')
    parser.add_argument('--window-start', type=str, required=True, 
                        help='Window start date (YYYY-MM-DD)')
    parser.add_argument('--window-days', type=int, default=30,
                        help='Number of days in the window (default: 30)')
    parser.add_argument('--min-trades', type=int, default=1,
                        help='Minimum number of trades to calculate metrics (default: 1)')
    
    args = parser.parse_args()
    
    # Parse window start
    try:
        window_start = datetime.strptime(args.window_start, '%Y-%m-%d')
    except ValueError:
        logger.error(f"Invalid date format: {args.window_start}. Use YYYY-MM-DD")
        sys.exit(1)
    
    # Load environment
    load_dotenv()
    
    db_url = os.environ.get('NXFX01_DATABASE_URL')
    if not db_url:
        logger.error("NXFX01_DATABASE_URL not set")
        sys.exit(1)
    
    logger.info(f"Calculating wallet performance for window: {window_start.date()} to {(window_start + timedelta(days=args.window_days)).date()}")
    
    calculator = WalletPerformanceCalculator(db_url, args.min_trades)
    
    # Load launch tokens for identification
    calculator.load_launch_tokens()
    
    # Load swaps
    swaps_by_wallet = calculator.load_swaps(window_start, args.window_days)
    
    # Calculate metrics for each wallet
    all_metrics = []
    for wallet, swaps in swaps_by_wallet.items():
        if len(swaps) >= args.min_trades:
            metrics = calculator.calculate_wallet_metrics(wallet, swaps)
            all_metrics.append(metrics)
    
    logger.info(f"Calculated metrics for {len(all_metrics)} wallets")
    
    # Save to database
    if all_metrics:
        inserted = calculator.save_metrics(all_metrics, window_start, args.window_days)
        logger.info(f"Performance backfill complete. Updated {inserted} wallets")
    else:
        logger.info("No wallets with sufficient trades to process")


if __name__ == '__main__':
    main()
