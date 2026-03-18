/**
 * NXFX01 HTTP Client
 * 
 * Provides methods to interact with the NXFX01 launch intelligence API.
 * Endpoints are configured via NXFX01_URL environment variable.
 */

import axios, { AxiosInstance } from 'axios';

/**
 * Parameters for executing a trading strategy via NXFX01
 */
export interface NXFX01ExecuteParams {
  /** Token address to analyze/trade */
  tokenAddress: string;
  /** Action mode: FAST, WAIT, or BLOCK */
  action: 'FAST' | 'WAIT' | 'BLOCK';
  /** Position size in USD (optional) */
  sizeUsd?: number;
  /** Additional metadata */
  metadata?: Record<string, unknown>;
}

/**
 * Response from NXFX01 executeStrategy endpoint
 */
export interface NXFX01ExecuteResponse {
  /** Whether the strategy was accepted */
  accepted: boolean;
  /** Execution ID if accepted */
  executionId?: string;
  /** Message from the service */
  message: string;
  /** Scores returned by NXFX01 */
  scores?: {
    overall_safety_initial: number;
    contract_safety: number;
    deployer_reputation: number;
    funding_risk: number;
  };
}

/**
 * NXFX01 API Client
 */
export class NXFX01Client {
  private client: AxiosInstance;
  private baseUrl: string;

  constructor(baseUrl?: string) {
    this.baseUrl = baseUrl || process.env.NXFX01_URL || 'http://localhost:8000';
    this.client = axios.create({
      baseURL: this.baseUrl,
      timeout: 30000,
      headers: {
        'Content-Type': 'application/json',
      },
    });
  }

  /**
   * Execute a trading strategy via NXFX01
   * POST /hermes-gateway/executeStrategy
   */
  async executeStrategy(params: NXFX01ExecuteParams): Promise<NXFX01ExecuteResponse> {
    try {
      const response = await this.client.post<NXFX01ExecuteResponse>(
        '/hermes-gateway/executeStrategy',
        params
      );
      return response.data;
    } catch (error) {
      if (axios.isAxiosError(error)) {
        throw new Error(
          `NXFX01 API error: ${error.response?.status} - ${error.response?.data?.message || error.message}`
        );
      }
      throw error;
    }
  }

  /**
   * Get the status of NXFX01 service
   */
  async getStatus(): Promise<{ status: string; version: string }> {
    try {
      const response = await this.client.get('/health');
      return response.data;
    } catch (error) {
      return { status: 'unavailable', version: 'unknown' };
    }
  }

  /**
   * Get recent actionable launches
   */
  async getActionableLaunches(mode: string, minSafety: number = 50, limit: number = 10): Promise<any[]> {
    try {
      const response = await this.client.get('/api/v1/launches/actionable', {
        params: { mode, min_safety: minSafety, limit },
      });
      return response.data;
    } catch (error) {
      console.error('Failed to get actionable launches:', error);
      return [];
    }
  }

  /**
   * Get details for a specific launch
   */
  async getLaunchDetails(launchId: string): Promise<any> {
    try {
      const response = await this.client.get(`/api/v1/launches/${launchId}`);
      return response.data;
    } catch (error) {
      console.error(`Failed to get launch details for ${launchId}:`, error);
      return null;
    }
  }
}

// Export a singleton instance
export const nxfx01Client = new NXFX01Client();
