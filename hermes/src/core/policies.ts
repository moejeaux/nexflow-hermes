import { Tool } from '@langchain/core/tools';

/**
 * Policy definitions and registry for Hermes orchestration.
 * Policies define guardrails, allowed tools, and execution limits.
 */

/**
 * A policy defines the constraints for a workflow execution.
 */
export interface Policy {
  /** Unique policy identifier */
  id: string;
  /** Human-readable policy description */
  description: string;
  /** List of allowed tool names */
  allowedTools: string[];
  /** Maximum number of workflow steps */
  maxSteps: number;
  /** Maximum iterations (plan → tools → review loops) */
  maxIterations: number;
  /** Rate limit: max requests per minute */
  rateLimitPerMinute: number;
  /** Whether trading actions are allowed */
  allowTrading: boolean;
  /** Whether to auto-approve certain results */
  autoApproveThreshold?: number;
  /** Custom metadata */
  metadata?: Record<string, unknown>;
}

/**
 * Predefined policy registry.
 */
export const POLICY_REGISTRY: Record<string, Policy> = {
  /**
   * Read-only policy - only allows data retrieval tools.
   * No trading, no state modifications.
   */
  'read-only': {
    id: 'read-only',
    description: 'Read-only policy - data retrieval only, no trading',
    allowedTools: [],
    maxSteps: 10,
    maxIterations: 3,
    rateLimitPerMinute: 60,
    allowTrading: false,
    autoApproveThreshold: 0.9,
    metadata: {
      riskLevel: 'minimal',
      requiresHumanApproval: false,
    },
  },

  /**
   * Default trading policy - allows analysis and execution.
   */
  'trading-default': {
    id: 'trading-default',
    description: 'Default trading policy - analysis + execution allowed',
    allowedTools: [
      'nxfx01_execute',
      'wallet_intel_score',
      'ml_review_token',
    ],
    maxSteps: 20,
    maxIterations: 5,
    rateLimitPerMinute: 30,
    allowTrading: true,
    autoApproveThreshold: 0.95,
    metadata: {
      riskLevel: 'medium',
      requiresHumanApproval: false,
    },
  },

  /**
   * Conservative trading policy - requires more review.
   */
  'trading-conservative': {
    id: 'trading-conservative',
    description: 'Conservative trading - requires human approval for execution',
    allowedTools: [
      'nxfx01_execute',
      'wallet_intel_score',
      'ml_review_token',
    ],
    maxSteps: 25,
    maxIterations: 7,
    rateLimitPerMinute: 15,
    allowTrading: true,
    autoApproveThreshold: undefined,
    metadata: {
      riskLevel: 'low',
      requiresHumanApproval: true,
    },
  },

  /**
   * Research policy - deep analysis without execution.
   */
  research: {
    id: 'research',
    description: 'Research policy - deep analysis, no execution',
    allowedTools: [
      'ml_review_token',
      'wallet_intel_score',
    ],
    maxSteps: 30,
    maxIterations: 10,
    rateLimitPerMinute: 20,
    allowTrading: false,
    autoApproveThreshold: 0.85,
    metadata: {
      riskLevel: 'minimal',
      requiresHumanApproval: false,
    },
  },

  /**
   * Emergency policy - severely restricted operations.
   */
  emergency: {
    id: 'emergency',
    description: 'Emergency policy - minimal operations, halt trading',
    allowedTools: [],
    maxSteps: 5,
    maxIterations: 1,
    rateLimitPerMinute: 5,
    allowTrading: false,
    autoApproveThreshold: 0,
    metadata: {
      riskLevel: 'none',
      requiresHumanApproval: true,
      isEmergency: true,
    },
  },
};

/**
 * Result of policy validation.
 */
export interface PolicyValidationResult {
  /** Whether the action is allowed */
  allowed: boolean;
  /** Error message if not allowed */
  error?: string;
  /** Warning messages */
  warnings: string[];
}

/**
 * Gets a policy by ID from the registry.
 */
export function getPolicy(policyId: string): Policy | undefined {
  return POLICY_REGISTRY[policyId];
}

/**
 * Validates if a tool is allowed by a policy.
 */
export function isToolAllowed(policy: Policy, toolName: string): boolean {
  return policy.allowedTools.includes(toolName);
}

/**
 * Validates if the current iteration count is within policy limits.
 */
export function isIterationAllowed(policy: Policy, currentIteration: number): PolicyValidationResult {
  if (currentIteration >= policy.maxIterations) {
    return {
      allowed: false,
      error: `Maximum iterations (${policy.maxIterations}) exceeded`,
      warnings: [],
    };
  }
  return { allowed: true, warnings: [] };
}

/**
 * Validates if trading is allowed by the policy.
 */
export function isTradingAllowed(policy: Policy): PolicyValidationResult {
  if (!policy.allowTrading) {
    return {
      allowed: false,
      error: 'Trading is not allowed by current policy',
      warnings: ['Policy does not permit trading actions'],
    };
  }
  return { allowed: true, warnings: [] };
}

/**
 * Gets the allowed tools for a given policy, filtered from a list of available tools.
 */
export function getAllowedToolsForPolicy(
  policyId: string,
  allTools: Tool[]
): Tool[] {
  const policy = getPolicy(policyId);
  if (!policy) {
    // If policy not found, return empty array (no tools allowed)
    console.warn(`Policy '${policyId}' not found in registry. No tools allowed.`);
    return [];
  }

  const allowedToolNames = new Set(policy.allowedTools);
  return allTools.filter((tool) => allowedToolNames.has(tool.name as string));
}

/**
 * Validates a tool call against a policy.
 */
export function validateToolCall(
  policy: Policy,
  toolName: string,
  currentIteration: number
): PolicyValidationResult {
  // Check if tool is in allowed list
  if (!isToolAllowed(policy, toolName)) {
    return {
      allowed: false,
      error: `Tool '${toolName}' is not allowed by policy '${policy.id}'`,
      warnings: [],
    };
  }

  // Check iteration limit
  const iterationCheck = isIterationAllowed(policy, currentIteration);
  if (!iterationCheck.allowed) {
    return iterationCheck;
  }

  return { allowed: true, warnings: [] };
}

/**
 * Registers a new policy or updates an existing one.
 */
export function registerPolicy(policy: Policy): void {
  if (!policy.id || !policy.allowedTools || !Array.isArray(policy.allowedTools)) {
    throw new Error('Invalid policy: must have id and allowedTools array');
  }
  POLICY_REGISTRY[policy.id] = policy;
}

/**
 * Creates a custom policy with defaults.
 */
export function createCustomPolicy(
  id: string,
  description: string,
  allowedTools: string[],
  options?: Partial<Policy>
): Policy {
  return {
    id,
    description,
    allowedTools,
    maxSteps: options?.maxSteps ?? 20,
    maxIterations: options?.maxIterations ?? 5,
    rateLimitPerMinute: options?.rateLimitPerMinute ?? 30,
    allowTrading: options?.allowTrading ?? false,
    autoApproveThreshold: options?.autoApproveThreshold,
    metadata: options?.metadata,
  };
}

/**
 * Checks if a review result should be auto-approved based on policy.
 */
export function shouldAutoApprove(
  policy: Policy,
  confidenceScore: number
): boolean {
  if (policy.autoApproveThreshold === undefined) {
    return false;
  }
  return confidenceScore >= policy.autoApproveThreshold;
}
