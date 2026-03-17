/**
 * Core state definitions for Hermes orchestration.
 * Uses LangGraph-style annotations for type-safe state management.
 */

import { Annotation, RootAnnotation } from '@langchain/langgraph';

/**
 * A message in the conversation history.
 */
export interface HermesMessage {
  /** Unique message identifier */
  id: string;
  /** Message role: user, assistant, system, or tool */
  role: 'user' | 'assistant' | 'system' | 'tool';
  /** Message content */
  content: string;
  /** ISO 8601 timestamp */
  timestamp: string;
  /** Optional: name of tool if role is tool */
  toolName?: string;
  /** Optional: tool call ID if role is tool */
  toolCallId?: string;
}

/**
 * Result from a tool execution.
 */
export interface ToolResult {
  /** Whether the tool executed successfully */
  success: boolean;
  /** Tool name that was executed */
  toolName: string;
  /** Returned data from tool */
  data?: unknown;
  /** Error message if failed */
  error?: string;
  /** Execution duration in milliseconds */
  durationMs: number;
  /** ISO 8601 timestamp of execution */
  timestamp: string;
}

/**
 * A planned tool call from the planner.
 */
export interface PlannedToolCall {
  /** Unique identifier for this call */
  id: string;
  /** Name of the tool to execute */
  toolName: string;
  /** Arguments to pass to the tool */
  arguments_: Record<string, unknown>;
  /** Optional: reason for calling this tool */
  reason?: string;
}

/**
 * Review result from the reviewer node.
 */
export interface ReviewResult {
  /** Whether the workflow can proceed */
  approved: boolean;
  /** Feedback from reviewer */
  feedback: string;
  /** Optional: suggested actions */
  suggestedActions?: string[];
  /** Optional: specific errors to address */
  errorsToFix?: string[];
}

/**
 * The main Hermes workflow state.
 * Annotated for LangGraph state management.
 */
export interface HermesState {
  /** Input: the user's request */
  userRequest: string;

  /** Conversation history */
  messages: HermesMessage[];

  /** Current policy being enforced */
  policyId: string;

  /** Plans from the planner node */
  plannedTools: PlannedToolCall[];

  /** Results from tool executions */
  toolResults: Record<string, ToolResult>;

  /** Reviewer feedback */
  reviewResult: ReviewResult | null;

  /** Final response to return */
  finalResponse: string;

  /** Number of iterations executed */
  iteration: number;

  /** Total tokens used in LLM calls */
  tokensUsed: number;

  /** Errors encountered during execution */
  errors: string[];

  /** Metadata for debugging/observability */
  metadata: Record<string, unknown>;
}

/**
 * LangGraph-style annotations for HermesState.
 * This defines the reducer functions for each state field.
 */
export const HermesStateAnnotation: RootAnnotation<HermesState> = Annotation.Root({
  /** Input: user request (not reduced, set once at start) */
  userRequest: Annotation<string>,

  /** Conversation messages - append new messages */
  messages: Annotation<HermesMessage[]>((x, y) => x.concat(y)),

  /** Current policy ID (replace on update) */
  policyId: Annotation<string>,

  /** Planned tool calls - append new plans */
  plannedTools: Annotation<PlannedToolCall[]>((x, y) => x.concat(y)),

  /** Tool results - merge new results into existing */
  toolResults: Annotation<Record<string, ToolResult>>((x, y) => ({ ...x, ...y })),

  /** Review result - replace on update */
  reviewResult: Annotation<ReviewResult | null>,

  /** Final response - replace on update */
  finalResponse: Annotation<string>,

  /** Iteration counter - increment by 1 */
  iteration: Annotation<number>((x, y) => x + y),

  /** Tokens used - accumulate */
  tokensUsed: Annotation<number>((x, y) => x + y),

  /** Errors - append new errors */
  errors: Annotation<string[]>((x, y) => x.concat(y)),

  /** Metadata - merge */
  metadata: Annotation<Record<string, unknown>>((x, y) => ({ ...x, ...y })),
});

/**
 * Creates the initial state for a new workflow.
 */
export function createInitialState(userRequest: string, policyId: string = 'trading-default'): HermesState {
  return {
    userRequest,
    messages: [],
    policyId,
    plannedTools: [],
    toolResults: {},
    reviewResult: null,
    finalResponse: '',
    iteration: 0,
    tokensUsed: 0,
    errors: [],
    metadata: {
      startedAt: new Date().toISOString(),
    },
  };
}

/**
 * Type guard to check if a value is a valid HermesState.
 */
export function isHermesState(value: unknown): value is HermesState {
  if (typeof value !== 'object' || value === null) return false;
  const state = value as Record<string, unknown>;
  return (
    typeof state.userRequest === 'string' &&
    Array.isArray(state.messages) &&
    typeof state.policyId === 'string' &&
    typeof state.iteration === 'number'
  );
}

/**
 * Creates a new HermesMessage.
 */
export function createMessage(
  role: HermesMessage['role'],
  content: string,
  options?: Partial<HermesMessage>
): HermesMessage {
  return {
    id: `msg_${Date.now()}_${Math.random().toString(36).slice(2, 9)}`,
    role,
    content,
    timestamp: new Date().toISOString(),
    ...options,
  };
}

/**
 * Creates a new PlannedToolCall.
 */
export function createPlannedToolCall(
  toolName: string,
  arguments_: Record<string, unknown>,
  reason?: string
): PlannedToolCall {
  return {
    id: `call_${Date.now()}_${Math.random().toString(36).slice(2, 9)}`,
    toolName,
    arguments_,
    reason,
  };
}

/**
 * Creates a new ToolResult.
 */
export function createToolResult(
  toolName: string,
  success: boolean,
  durationMs: number,
  data?: unknown,
  error?: string
): ToolResult {
  return {
    success,
    toolName,
    data,
    error,
    durationMs,
    timestamp: new Date().toISOString(),
  };
}
