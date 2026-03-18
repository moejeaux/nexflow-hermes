/**
 * ML Sidecar HTTP Client
 * 
 * Provides methods to interact with the ML scoring service.
 * Endpoints are configured via ML_URL environment variable.
 */

import axios, { AxiosInstance } from 'axios';

/**
 * Token features for ML review
 */
export interface TokenFeatures {
  /** Token contract address */
  token_address: string;
  /** Token symbol */
  token_symbol?: string;
  /** Current price in USD */
  price_usd?: number;
  /** Market cap in USD */
  market_cap_usd?: number;
  /** Liquidity in USD */
  liquidity_usd?: number;
  /** 24h volume */
  volume_24h?: number;
  /** Holder count */
  holder_count?: number;
  /** Top 10 holder percentage */
  top_10_holders_pct?: number;
  /** Buy/sell tax percentage */
  buy_tax?: number;
  /** Sell tax percentage */
  sell_tax?: number;
  /** Is honeypot flag */
  is_honeypot?: boolean;
  /** Is open source */
  is_open_source?: boolean;
  /** Is verified on explorer */
  is_verified?: boolean;
  /** Transferability issues */
  transfer_paused?: boolean;
  /** Mint authority */
  mint_authority?: string;
  /** Additional features */
  [key: string]: unknown;
}

/**
 * ML review response
 */
export interface MLReviewResponse {
  /** Token address reviewed */
  token_address: string;
  /** Overall confidence score (0-100) */
  confidence_score: number;
  /** Risk level: low, medium, high, critical */
  risk_level: 'low' | 'medium' | 'high' | 'critical';
  /** Recommendation: buy, sell, hold, avoid */
  recommendation: 'buy' | 'sell' | 'hold' | 'avoid';
  /** Key factors contributing to the score */
  key_factors: Array<{
    factor: string;
    impact: 'positive' | 'negative' | 'neutral';
    description: string;
  }>;
  /** Model version used */
  model_version: string;
  /** Timestamp of the review */
  timestamp: string;
}

/**
 * Batch review response
 */
export interface MLBatchReviewResponse {
  results: MLReviewResponse[];
  summary: {
    total_reviewed: number;
    avg_confidence: number;
    recommendations: {
      buy: number;
      sell: number;
      hold: number;
      avoid: number;
    };
  };
}

/**
 * ML API Client
 */
export class MLClient {
  private client: AxiosInstance;
  private baseUrl: string;

  constructor(baseUrl?: string) {
    this.baseUrl = baseUrl || process.env.ML_URL || 'http://localhost:8002';
    this.client = axios.create({
      baseURL: this.baseUrl,
      timeout: 60000, // ML inference can take longer
      headers: {
        'Content-Type': 'application/json',
      },
    });
  }

  /**
   * Review a token using ML model
   * POST /ml/score-token
   */
  async reviewToken(features: TokenFeatures): Promise<MLReviewResponse> {
    try {
      const response = await this.client.post<MLReviewResponse>(
        '/ml/score-token',
        features
      );
      return response.data;
    } catch (error) {
      if (axios.isAxiosError(error)) {
        throw new Error(
          `ML API error: ${error.response?.status} - ${error.response?.data?.message || error.message}`
        );
      }
      throw error;
    }
  }

  /**
   * Batch review multiple tokens
   * POST /ml/batch-score
   */
  async batchReviewTokens(featuresList: TokenFeatures[]): Promise<MLBatchReviewResponse> {
    try {
      const response = await this.client.post<MLBatchReviewResponse>(
        '/ml/batch-score',
        { tokens: featuresList }
      );
      return response.data;
    } catch (error) {
      if (axios.isAxiosError(error)) {
        throw new Error(
          `ML API error: ${error.response?.status} - ${error.response?.data?.message || error.message}`
        );
      }
      throw error;
    }
  }

  /**
   * Get model information
   * GET /ml/model-info
   */
  async getModelInfo(): Promise<{
    model_version: string;
    model_type: string;
    trained_at: string;
    features_used: string[];
  }> {
    try {
      const response = await this.client.get('/ml/model-info');
      return response.data;
    } catch (error) {
      throw new Error(`ML API error: Failed to get model info`);
    }
  }

  /**
   * Get the status of ML service
   */
  async getStatus(): Promise<{ status: string; model_version: string; ready: boolean }> {
    try {
      const response = await this.client.get('/health');
      return response.data;
    } catch (error) {
      return { status: 'unavailable', model_version: 'unknown', ready: false };
    }
  }
}

// Export a singleton instance
export const mlClient = new MLClient();
