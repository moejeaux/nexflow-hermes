// ---------------------------------------------------------------------------
// FIFO metrics – pure functions that compute per-wallet entry/exit from
// normalized token transfers.  No I/O; easy to test independently.
// ---------------------------------------------------------------------------

import type { NormalizedTransfer } from "./etherscanClient";

// ---- public types --------------------------------------------------------

export interface WalletMetrics {
  wallet: string;
  entry_block: number;
  entry_timestamp: number;
  exit_block: number | null;
  exit_timestamp: number | null;
  entry_rank_pct: number;
  exit_rank_pct: number | null;
  final_balance_raw: string;
}

export interface FifoMetricsResult {
  status: "success" | "error";
  error?: string;
  contract_address: string;
  chain_id: number;
  wallets: WalletMetrics[];
}

// Fraction of peak balance that triggers an "exit" event.
const EXIT_DROP_RATIO = 0.5;

// ---- helpers -------------------------------------------------------------

/**
 * Build per-wallet running balances from an ordered list of transfers,
 * tracking entry / exit blocks along the way.
 */
function buildWalletStates(transfers: NormalizedTransfer[]) {
  interface State {
    balance: bigint;
    peakBalance: bigint;
    entryBlock: number | null;
    entryTimestamp: number | null;
    exitBlock: number | null;
    exitTimestamp: number | null;
  }

  const wallets = new Map<string, State>();

  const getOrInit = (addr: string): State => {
    let s = wallets.get(addr);
    if (!s) {
      s = {
        balance: 0n,
        peakBalance: 0n,
        entryBlock: null,
        entryTimestamp: null,
        exitBlock: null,
        exitTimestamp: null,
      };
      wallets.set(addr, s);
    }
    return s;
  };

  for (const tx of transfers) {
    if (tx.is_error) continue;

    const amount = BigInt(tx.value_raw);
    if (amount === 0n) continue;

    const from = tx.from.toLowerCase();
    const to = tx.to.toLowerCase();

    // --- debit sender ---
    const senderState = getOrInit(from);
    senderState.balance -= amount;
    // Check exit condition for sender (balance dropped ≥ EXIT_DROP_RATIO from peak)
    if (
      senderState.exitBlock === null &&
      senderState.peakBalance > 0n &&
      senderState.balance * 100n <=
        senderState.peakBalance * BigInt(Math.round((1 - EXIT_DROP_RATIO) * 100))
    ) {
      senderState.exitBlock = tx.block_number;
      senderState.exitTimestamp = tx.timestamp;
    }

    // --- credit receiver ---
    const receiverState = getOrInit(to);
    receiverState.balance += amount;

    // Track entry: first block balance goes positive
    if (receiverState.entryBlock === null && receiverState.balance > 0n) {
      receiverState.entryBlock = tx.block_number;
      receiverState.entryTimestamp = tx.timestamp;
    }

    // Track peak
    if (receiverState.balance > receiverState.peakBalance) {
      receiverState.peakBalance = receiverState.balance;
    }
  }

  return wallets;
}

// ---- main export ---------------------------------------------------------

export function computeFifoMetrics(
  transfers: NormalizedTransfer[],
  contractAddress: string,
  chainId: number,
): FifoMetricsResult {
  const states = buildWalletStates(transfers);

  // Collect wallets that had an entry (balance went positive at some point).
  const entries: {
    wallet: string;
    entryBlock: number;
    entryTimestamp: number;
    exitBlock: number | null;
    exitTimestamp: number | null;
    finalBalance: bigint;
  }[] = [];

  for (const [addr, s] of states) {
    if (s.entryBlock === null) continue;
    entries.push({
      wallet: addr,
      entryBlock: s.entryBlock,
      entryTimestamp: s.entryTimestamp!,
      exitBlock: s.exitBlock,
      exitTimestamp: s.exitTimestamp,
      finalBalance: s.balance < 0n ? 0n : s.balance,
    });
  }

  // ---- rank by entry_block ----
  const byEntry = [...entries].sort((a, b) => a.entryBlock - b.entryBlock);
  const entryRankMap = new Map<string, number>();
  for (let i = 0; i < byEntry.length; i++) {
    entryRankMap.set(
      byEntry[i].wallet,
      byEntry.length > 1 ? i / (byEntry.length - 1) : 0,
    );
  }

  // ---- rank by exit_block (only wallets that exited) ----
  const exiters = entries.filter((e) => e.exitBlock !== null);
  const byExit = [...exiters].sort((a, b) => a.exitBlock! - b.exitBlock!);
  const exitRankMap = new Map<string, number>();
  for (let i = 0; i < byExit.length; i++) {
    exitRankMap.set(
      byExit[i].wallet,
      byExit.length > 1 ? i / (byExit.length - 1) : 0,
    );
  }

  // ---- assemble output ----
  const wallets: WalletMetrics[] = entries.map((e) => ({
    wallet: e.wallet,
    entry_block: e.entryBlock,
    entry_timestamp: e.entryTimestamp,
    exit_block: e.exitBlock,
    exit_timestamp: e.exitTimestamp,
    entry_rank_pct: round4(entryRankMap.get(e.wallet) ?? 0),
    exit_rank_pct: e.exitBlock !== null ? round4(exitRankMap.get(e.wallet) ?? 0) : null,
    final_balance_raw: e.finalBalance.toString(),
  }));

  // Sort output by entry_block ascending for consistency.
  wallets.sort((a, b) => a.entry_block - b.entry_block);

  return {
    status: "success",
    contract_address: contractAddress,
    chain_id: chainId,
    wallets,
  };
}

// ---- briefing summary ----------------------------------------------------

export interface BriefingSummary {
  status: "success" | "error";
  error?: string;
  contract_address: string;
  chain_id: number;
  summary: {
    total_wallets: number;
    leader_wallets_count: number;
    herd_wallets_count: number;
    early_exit_count: number;
    bagholder_count: number;
  };
  leaders: { wallet: string; entry_rank_pct: number; exit_rank_pct: number | null }[];
}

export function computeBriefing(metrics: FifoMetricsResult): BriefingSummary {
  const { wallets, contract_address, chain_id } = metrics;

  const totalWallets = wallets.length;
  const leaderWalletsCount = wallets.filter((w) => w.entry_rank_pct <= 0.1).length;
  const herdWalletsCount = wallets.filter((w) => w.entry_rank_pct >= 0.7).length;
  const earlyExitCount = wallets.filter(
    (w) => w.exit_rank_pct !== null && w.exit_rank_pct <= 0.3,
  ).length;

  // Bagholder: exited late (exit_rank_pct >= 0.7) OR never exited but still holding
  const bagholderCount = wallets.filter((w) => {
    if (w.exit_rank_pct !== null && w.exit_rank_pct >= 0.7) return true;
    if (w.exit_block === null && BigInt(w.final_balance_raw) > 0n) return true;
    return false;
  }).length;

  // Top 10 leaders by lowest entry_rank_pct
  const leaders = [...wallets]
    .sort((a, b) => a.entry_rank_pct - b.entry_rank_pct)
    .slice(0, 10)
    .map((w) => ({
      wallet: w.wallet,
      entry_rank_pct: w.entry_rank_pct,
      exit_rank_pct: w.exit_rank_pct,
    }));

  return {
    status: "success",
    contract_address,
    chain_id,
    summary: {
      total_wallets: totalWallets,
      leader_wallets_count: leaderWalletsCount,
      herd_wallets_count: herdWalletsCount,
      early_exit_count: earlyExitCount,
      bagholder_count: bagholderCount,
    },
    leaders,
  };
}

// ---- util ----------------------------------------------------------------

function round4(n: number): number {
  return Math.round(n * 10000) / 10000;
}
