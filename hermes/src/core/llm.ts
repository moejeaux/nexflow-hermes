/**
 * LLM Abstraction Layer for Hermes Orchestrator
 * 
 * Provides a unified interface for calling language models,
 * hiding provider details (OpenRouter vs local) behind a single function.
 */

import { ChatOpenAI } from '@langchain/openai';

/**
 * Supported LLM providers
 */
export type LLMProvider = 'openrouter' | 'local';

/**
 * Model identifiers
 */
export type ModelId = 
  | 'hermes'       // Hermes 4 for planner (read-only)
  | 'tools';       // Gemini 2.5 Flash for tool-using policies

/**
 * Default models per context
 */
export const DEFAULT_MODELS: Record<ModelId, string> = {
  hermes: process.env.HERMES_MODEL ?? 'nousresearch/hermes-4-70b',
  tools: process.env.TOOLS_MODEL ?? 'google/gemini-2.5-flash',
};

/**
 * Parameters for call_model()
 */
export interface CallModelParams {
  /** Which LLM to use - defaults based on model field */
  provider?: LLMProvider;
  /** Model identifier - 'hermes' or 'tools', or explicit model name */
  model: ModelId | string;
  /** Override temperature (default: 0.2) */
  temperature?: number;
  /** Override max tokens (default: 4096) */
  maxTokens?: number;
}

/**
 * Creates a ChatOpenAI instance configured for OpenRouter
 */
function createOpenRouterModel(modelName: string, temperature?: number, maxTokens?: number): ChatOpenAI {
  return new ChatOpenAI({
    model: modelName,
    temperature: temperature ?? parseFloat(process.env.LLM_TEMPERATURE ?? '0.2'),
    maxTokens: maxTokens ?? parseInt(process.env.LLM_MAX_TOKENS ?? '4096'),
    verbose: false,
    configuration: {
      baseURL: 'https://openrouter.ai/api/v1',
      apiKey: process.env.OPENROUTER_API_KEY,
    },
  });
}

/**
 * Unified LLM calling function.
 * 
 * @param params - Configuration for the model call
 * @returns Configured ChatOpenAI instance
 * 
 * Usage:
 *   // Planner (read-only) - uses Hermes 4
 *   const plannerModel = call_model({ model: 'hermes' });
 *   
 *   // Tool-using policy - uses Gemini 2.5 Flash
 *   const toolModel = call_model({ model: 'tools' });
 *   
 *   // Explicit model override
 *   const customModel = call_model({ model: 'anthropic/claude-3-opus' });
 */
export function call_model(params: CallModelParams): ChatOpenAI {
  const provider = params.provider ?? 'openrouter';
  
  // Handle different providers
  if (provider === 'local') {
    // TODO: Implement local LLM support
    // This will call a local LLM server on the Mac mini (e.g., Qwen 3 7-8B)
    // Expected to run on localhost with a compatible OpenAI-compatible API
    throw new Error(
      'Local LLM provider not yet implemented. ' +
      'To enable: implement local server (e.g., Qwen 3 7-8B on localhost:11434) ' +
      'and set provider="local" with appropriate model configuration.'
    );
  }
  
  // Default: OpenRouter
  if (provider === 'openrouter') {
    // Determine model name based on ModelId or use explicit string
    let modelName: string;
    
    if (params.model === 'hermes') {
      modelName = DEFAULT_MODELS.hermes;
    } else if (params.model === 'tools') {
      modelName = DEFAULT_MODELS.tools;
    } else {
      // Explicit model name provided
      modelName = params.model;
    }
    
    return createOpenRouterModel(
      modelName,
      params.temperature,
      params.maxTokens
    );
  }
  
  // Unknown provider
  throw new Error(`Unknown LLM provider: ${provider}`);
}

/**
 * Get model for planner (read-only policies)
 * Uses Hermes 4 via OpenRouter
 */
export function getPlannerModel(): ChatOpenAI {
  return call_model({ model: 'hermes' });
}

/**
 * Get model for tool-using policies
 * Uses Gemini 2.5 Flash via OpenRouter
 */
export function getToolsModel(): ChatOpenAI {
  return call_model({ model: 'tools' });
}

/**
 * Get model based on policy context
 * 
 * @param policyId - The policy being executed
 * @returns Appropriate ChatOpenAI instance
 */
export function getModelForPolicy(policyId: string): ChatOpenAI {
  // Read-only policy uses Hermes model (no tools)
  if (policyId === 'read-only') {
    return getPlannerModel();
  }
  
  // All other policies (trading-default, etc.) use tools model
  return getToolsModel();
}
