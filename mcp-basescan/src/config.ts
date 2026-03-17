// ---------------------------------------------------------------------------
// Config – reads env vars and exposes typed constants with sensible defaults.
// ---------------------------------------------------------------------------

function envStr(key: string, fallback: string): string {
  return process.env[key] ?? fallback;
}

function envInt(key: string, fallback: number): number {
  const v = process.env[key];
  if (v === undefined) return fallback;
  const n = parseInt(v, 10);
  return Number.isNaN(n) ? fallback : n;
}

function envFloat(key: string, fallback: number): number {
  const v = process.env[key];
  if (v === undefined) return fallback;
  const n = parseFloat(v);
  return Number.isNaN(n) ? fallback : n;
}

export const config = {
  /** Base URL for Blockscout API (Base chain) – free, no key required */
  blockscoutBaseUrl: envStr("BLOCKSCOUT_BASE_URL", "https://base.blockscout.com"),

  /** Express listen port */
  port: envInt("PORT", 3100),

  /** Max pages to fetch when paginating Blockscout results */
  maxPages: envInt("MAX_PAGES", 10),

  // ---- filter defaults used by other services / cron jobs ----
  tokenMinAgeDays: envInt("TOKEN_MIN_AGE_DAYS", 3),
  tokenMaxAgeDays: envInt("TOKEN_MAX_AGE_DAYS", 90),
  minLiqUsd: envFloat("MIN_LIQ_USD", 7500),
  noFullLpDrainWindowHours: envInt("NO_FULL_LP_DRAIN_WINDOW_HOURS", 24),
  minBuys72h: envInt("MIN_BUYS_72H", 50),
  minSells72h: envInt("MIN_SELLS_72H", 30),
  minUniqueHolders72h: envInt("MIN_UNIQUE_HOLDERS_72H", 80),
  minUniqueSellers24h: envInt("MIN_UNIQUE_SELLERS_24H", 10),
  maxTopHolderSupplyRatio24h: envFloat("MAX_TOP_HOLDER_SUPPLY_RATIO_24H", 0.8),
} as const;
