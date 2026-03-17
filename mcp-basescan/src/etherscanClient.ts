// ---------------------------------------------------------------------------
// Blockscout client – calls Base Blockscout API v2 and maps to normalized
// transfer types.  Free, no API key required.
// ---------------------------------------------------------------------------

import { config } from "./config";

// ---- public types --------------------------------------------------------

export interface NormalizedTransfer {
  tx_hash: string;
  block_number: number;
  timestamp: number;       // seconds since epoch
  from: string;
  to: string;
  value_raw: string;
  token_decimals: number;
  token_symbol: string;
  is_error: boolean;
}

export interface TransferResult {
  status: "success" | "error";
  error?: string;
  result: NormalizedTransfer[];
}

// ---- Blockscout raw shapes -----------------------------------------------

interface BlockscoutToken {
  address: string;
  decimals: string | null;
  symbol: string | null;
}

interface BlockscoutTransfer {
  tx_hash: string;
  block_number: number;
  timestamp: string;       // ISO 8601
  from: { hash: string };
  to: { hash: string };
  total: { value: string; decimals: string | null };
  token: BlockscoutToken;
}

interface BlockscoutPage {
  items: BlockscoutTransfer[];
  next_page_params: Record<string, string | number> | null;
}

// ---- params --------------------------------------------------------------

export interface GetTokenTransfersParams {
  contract_address: string;
  max_pages?: number;
  sort?: "asc" | "desc";
}

// ---- implementation ------------------------------------------------------

export async function getTokenTransfers(
  params: GetTokenTransfersParams,
): Promise<TransferResult> {
  const {
    contract_address,
    max_pages = config.maxPages,
    sort = "asc",
  } = params;

  const allTransfers: NormalizedTransfer[] = [];
  let nextPageParams: Record<string, string | number> | null = null;

  for (let page = 0; page < max_pages; page++) {
    const url = new URL(
      `/api/v2/tokens/${contract_address}/transfers`,
      config.blockscoutBaseUrl,
    );

    if (nextPageParams) {
      for (const [k, v] of Object.entries(nextPageParams)) {
        url.searchParams.set(k, String(v));
      }
    }

    let raw: BlockscoutPage;
    try {
      const res = await fetch(url.toString(), {
        headers: { Accept: "application/json" },
      });
      if (!res.ok) {
        return {
          status: "error",
          error: `Blockscout HTTP ${res.status}: ${res.statusText}`,
          result: allTransfers,
        };
      }
      raw = (await res.json()) as BlockscoutPage;
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      return { status: "error", error: `Fetch failed: ${msg}`, result: allTransfers };
    }

    if (!raw.items || raw.items.length === 0) break;

    for (const tx of raw.items) {
      const decimals = tx.total?.decimals ?? tx.token?.decimals ?? "18";
      allTransfers.push({
        tx_hash: tx.tx_hash,
        block_number: tx.block_number,
        timestamp: Math.floor(new Date(tx.timestamp).getTime() / 1000),
        from: tx.from.hash.toLowerCase(),
        to: tx.to.hash.toLowerCase(),
        value_raw: tx.total?.value ?? "0",
        token_decimals: parseInt(decimals, 10),
        token_symbol: tx.token?.symbol ?? "UNKNOWN",
        is_error: false,
      });
    }

    if (!raw.next_page_params) break;
    nextPageParams = raw.next_page_params;
  }

  // Blockscout returns newest-first; sort ascending by block if requested.
  if (sort === "asc") {
    allTransfers.sort((a, b) => a.block_number - b.block_number || a.timestamp - b.timestamp);
  }

  return { status: "success", result: allTransfers };
}
