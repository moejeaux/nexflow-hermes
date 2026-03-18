/**
 * Core Tool definitions for Hermes orchestration.
 * Wraps service clients as LangChain-compatible tools.
 */

import { z } from 'zod';
import { tool } from '@langchain/core/tools';
import { nxfx01Client, NXFX01ExecuteParams } from '../services/nxfx01-client';
import { walletIntelClient } from '../services/walletintel-client';
import { mlClient, TokenFeatures } from '../services/ml-client';

/**
 * NXFX01 Execute Tool
 */
export const nxfx01Execute = tool(
  async (input: NXFX01ExecuteParams): Promise<string> => {
    try {
      const result = await nxfx01Client.executeStrategy(input);
      return JSON.stringify({
        success: result.accepted,
        executionId: result.executionId,
        message: result.message,
        scores: result.scores,
      });
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Unknown error';
      return JSON.stringify({ success: false, error: message });
    }
  },
  {
    name: 'nxfx01_execute',
    description: 'Execute a trading strategy via NXFX01 launch intelligence. Input: tokenAddress (required), action (FAST|WAIT|BLOCK), sizeUsd (optional), metadata (optional).',
  }
);

/**
 * WalletIntel Score Tool
 */
export const walletIntelScore = tool(
  async (input: { address: string; includeHistory?: boolean }): Promise<string> => {
    try {
      const score = await walletIntelClient.getWalletScore(input.address);
      let history = null;
      if (input.includeHistory) {
        history = await walletIntelClient.getWalletHistory(input.address, 30);
      }
      return JSON.stringify({
        wallet: score.wallet,
        tier: score.wallet_tier,
        valueScore: score.wallet_value_score,
        performanceScore: score.wallet_performance_score,
        clusterId: score.cluster_id,
        clusterTier: score.cluster_tier,
        alphaCohort: score.alpha_cohort_flag,
        metrics: score.metrics,
        history: history?.transactions || null,
      });
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Unknown error';
      return JSON.stringify({ success: false, error: message });
    }
  },
  {
    name: 'wallet_intel_score',
    description: 'Get wallet intelligence including tier classification. Input: address (required), includeHistory (optional boolean).',
  }
);

/**
 * ML Review Token Tool
 */
export const mlReviewToken = tool(
  async (input: TokenFeatures): Promise<string> => {
    try {
      const result = await mlClient.reviewToken(input);
      return JSON.stringify({
        tokenAddress: input.token_address,
        confidenceScore: result.confidence_score,
        riskLevel: result.risk_level,
        recommendation: result.recommendation,
        keyFactors: result.key_factors,
        modelVersion: result.model_version,
        timestamp: result.timestamp,
      });
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Unknown error';
      return JSON.stringify({ success: false, error: message });
    }
  },
  {
    name: 'ml_review_token',
    description: 'Review a token using ML model for risk assessment. Input: token_address (required), optional: token_symbol, price_usd, market_cap_usd, liquidity_usd, volume_24h, holder_count, top_10_holders_pct, buy_tax, sell_tax, is_honeypot, is_open_source, is_verified, transfer_paused.',
  }
);

/**
 * Core tools available to all agents
 */
export const coreTools = [
  nxfx01Execute,
  walletIntelScore,
  mlReviewToken,
];

/**
 * Get tool by name
 */
export function getToolByName(name: string) {
  return coreTools.find((t) => t.name === name);
}
