// ---------------------------------------------------------------------------
// Express server – three JSON endpoints for Hermes token/wallet intel.
// Uses Blockscout API (free, no key) for Base chain data.
// ---------------------------------------------------------------------------

import express, { type Request, type Response } from "express";
import { config } from "./config";
import { getTokenTransfers } from "./etherscanClient";
import { computeBriefing, computeFifoMetrics } from "./fifoMetrics";

const app = express();

// ---- helpers -------------------------------------------------------------

const CHECKSUM_RE = /^0x[0-9a-fA-F]{40}$/;

function requireContractAddress(req: Request, res: Response): string | null {
  const addr = req.query.contract_address;
  if (typeof addr !== "string" || !CHECKSUM_RE.test(addr)) {
    res.status(400).json({
      status: "error",
      error: "contract_address is required and must be a valid 0x-prefixed address",
      result: [],
    });
    return null;
  }
  return addr;
}

function intParam(val: unknown, fallback: number): number {
  if (val === undefined || val === null) return fallback;
  const n = parseInt(String(val), 10);
  return Number.isNaN(n) ? fallback : n;
}

// --------------------------------------------------------------------------
// 1) GET /api/token-transfers
// --------------------------------------------------------------------------

app.get("/api/token-transfers", async (req: Request, res: Response) => {
  const contractAddress = requireContractAddress(req, res);
  if (!contractAddress) return;

  const maxPages = intParam(req.query.max_pages, config.maxPages);
  const sort = req.query.sort === "desc" ? "desc" as const : "asc" as const;

  const result = await getTokenTransfers({
    contract_address: contractAddress,
    max_pages: maxPages,
    sort,
  });

  res.json(result);
});

// --------------------------------------------------------------------------
// 2) GET /api/token-fifo-metrics
// --------------------------------------------------------------------------

app.get("/api/token-fifo-metrics", async (req: Request, res: Response) => {
  const contractAddress = requireContractAddress(req, res);
  if (!contractAddress) return;

  const chainId = intParam(req.query.chain_id, 8453);
  const maxPages = intParam(req.query.max_pages, config.maxPages);

  const transfers = await getTokenTransfers({
    contract_address: contractAddress,
    max_pages: maxPages,
  });

  if (transfers.status === "error") {
    res.json({
      status: "error",
      error: transfers.error,
      contract_address: contractAddress,
      chain_id: chainId,
      wallets: [],
    });
    return;
  }

  const metrics = computeFifoMetrics(transfers.result, contractAddress, chainId);
  res.json(metrics);
});

// --------------------------------------------------------------------------
// 3) GET /api/token-fifo-briefing
// --------------------------------------------------------------------------

app.get("/api/token-fifo-briefing", async (req: Request, res: Response) => {
  const contractAddress = requireContractAddress(req, res);
  if (!contractAddress) return;

  const chainId = intParam(req.query.chain_id, 8453);
  const maxPages = intParam(req.query.max_pages, config.maxPages);

  const transfers = await getTokenTransfers({
    contract_address: contractAddress,
    max_pages: maxPages,
  });

  if (transfers.status === "error") {
    res.json({
      status: "error",
      error: transfers.error,
      contract_address: contractAddress,
      chain_id: chainId,
      summary: {
        total_wallets: 0,
        leader_wallets_count: 0,
        herd_wallets_count: 0,
        early_exit_count: 0,
        bagholder_count: 0,
      },
      leaders: [],
    });
    return;
  }

  const metrics = computeFifoMetrics(transfers.result, contractAddress, chainId);
  const briefing = computeBriefing(metrics);
  res.json(briefing);
});

// ---- start ---------------------------------------------------------------

app.listen(config.port, () => {
  console.log(`mcp-basescan listening on :${config.port}`);
});
