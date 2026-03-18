/**
 * WalletIntel HTTP Client
 * 
 * Provides methods to interact with the Wallet Intelligence API.
 * Endpoints are configured via WALLET_INTEL_URL environment variable.
 */

import axios, { AxiosInstance } from 'axios';

/**
 * Wallet score response
 */
export interface WalletScoreResponse {
  /** Wallet address */
  wallet: string;
  /** Tier classification */
  wallet_tier: 'TIER_1_WHALE' | 'TIER_2_SMART_MONEY' | 'TIER_3_RETAIL' | 'TIER_4_FLAGGED' | 'UNKNOWN';
  /** Value score (0-100) */
  wallet_value_score: number;
  /** Performance score (0-100) */
  wallet_performance_score: number;
  /** Cluster ID if applicable */
  cluster_id: string | null;
  /** Cluster tier */
  cluster_tier: 'TIER_1_WHALE_CLUSTER' | 'TIER_2_SMART_CLUSTER' | 'TIER_3_NEUTRAL' | 'TIER_4_FLAGGED' | 'UNKNOWN';
  /** Whether wallet is in alpha cohort */
  alpha_cohort_flag: boolean;
  /** Historical performance metrics */
  metrics?: {
    total_trades: number;
    profitable_trades: number;
    win_rate: number;
    avg_pnl: number;
    total_pnl: number;
  };
}

/**
 * Wallet transaction history response
 */
export interface WalletHistoryResponse {
  wallet: string;
  transactions: Array<{
    hash: string;
    timestamp: string;
    token_address: string;
    token_symbol: string;
    action: 'buy' | 'sell' | 'transfer';
    amount_usd: number;
    profit_loss_usd?: number;
  }>;
}

/**
 * WalletIntel API Client
 */
export class WalletIntelClient {
  private client: AxiosInstance;
  private baseUrl: string;

  constructor(baseUrl?: string) {
    this.baseUrl = baseUrl || process.env.WALLET_INTEL_URL || 'http://localhost:8001';
    this.client = axios.create({
      baseURL: this.baseUrl,
      timeout: 30000,
      headers: {
        'Content-Type': 'application/json',
      },
    });
  }

  /**
   * Get wallet score and classification
   * GET /wallet/{address}/score
   */
  async getWalletScore(address: string): Promise<WalletScoreResponse> {
    try {
      const response = await this.client.get<WalletScoreResponse>(
        `/wallet/${address}/score`
      );
      return response.data;
    } catch (error) {
      if (axios.isAxiosError(error)) {
        throw new Error(
          `WalletIntel API error: ${error.response?.status} - ${error.response?.data?.message || error.message}`
        );
      }
      throw error;
    }
  }

  /**
   * Get wallet transaction history
   * GET /wallet/{address}/history
   */
  async getWalletHistory(address: string, days: number = 30): Promise<WalletHistoryResponse> {
    try {
      const response = await this.client.get<WalletHistoryResponse>(
        `/wallet/${address}/history`,
        { params: { days } }
      );
      return response.data;
    } catch (error) {
      if (axios.isAxiosError(error)) {
        throw new Error(
          `WalletIntel API error: ${error.response?.status} - ${error.response?.data?.message || error.message}`
        );
      }
      throw error;
    }
  }

  /**
   * Get multiple wallet scores in batch
   * POST /wallets/batch-score
   */
  async getBatchWalletScores(addresses: string[]): Promise<WalletScoreResponse[]> {
    try {
      const response = await this.client.post<WalletScoreResponse[]>(
        '/wallets/batch-score',
        { addresses }
      );
      return response.data;
    } catch (error) {
      if (axios.isAxiosError(error)) {
        throw new Error(
          `WalletIntel API error: ${error.response?.status} - ${error.response?.data?.message || error.message}`
        );
      }
      throw error;
    }
  }

  /**
   * Get the status of WalletIntel service
   */
  async getStatus(): Promise<{ status: string; version: string }> {
    try {
      const response = await this.client.get('/health');
      return response.data;
    } catch (error) {
      return { status: 'unavailable', version: 'unknown' };
    }
  }
}

// Export a singleton instance
export const walletIntelClient = new WalletIntelClient();
