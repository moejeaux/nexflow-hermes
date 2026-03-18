/**
 * Agent Node Implementations for Hermes Graph
 * 
 * Contains the Planner, Reviewer, and Router nodes.
 */

import { BaseMessage, HumanMessage, AIMessage, SystemMessage } from '@langchain/core/messages';
import { ChatOpenAI } from '@langchain/openai';
import { Runnable, RunnableConfig } from '@langchain/core/runnables';
import { HermesState, HermesMessage, createMessage, HermesStateAnnotation } from '../core/state';
import { coreTools } from '../core/tools';
import { getPolicy } from '../core/policies';

/**
 * Hermes system prompt defining the orchestrator's role and behavior
 */
const HERMES_SYSTEM_PROMPT = `You are Hermes, the lead orchestrator for NexFlow's autonomous trading system.

Your role is to:
1. Analyze user requests and determine what information is needed
2. Use available tools to gather data from NXFX01 (launch intelligence), WalletIntel (wallet scoring), and ML models
3. Synthesize the results into actionable intelligence
4. Recommend trading actions (FAST/WAIT/BLOCK) based on risk assessment

Available tools:
- nxfx01_execute: Get launch analysis and execute trades for Base tokens
- wallet_intel_score: Score wallet classification (whale/smart money/retail)
- ml_review_token: Get ML-based risk assessment and recommendations

Guidelines:
- Always prioritize capital safety: avoid rugs, honeypots, and structurally bad launches
- Use wallet intelligence to identify smart money participation
- Cross-reference multiple data sources before making recommendations
- Provide confidence scores and reasoning for all recommendations
- When uncertain, recommend WAIT rather than FAST

Response format:
- Be concise but thorough
- Include specific scores and metrics when available
- Flag any concerns or red flags clearly`;

/**
 * Creates a configured ChatOpenAI model instance
 * Uses TOOLS_MODEL for policies that need tools, HERMES_MODEL for read-only
 */
function createChatModel(policyId?: string): ChatOpenAI {
  const useToolsModel = policyId && policyId !== 'read-only';

  return new ChatOpenAI({
    model: useToolsModel
      ? (process.env.TOOLS_MODEL ?? "openai/gpt-5-mini")
      : (process.env.HERMES_MODEL ?? "nousresearch/hermes-4-70b"),
    temperature: parseFloat(process.env.LLM_TEMPERATURE || "0.2"),
    maxTokens: parseInt(process.env.LLM_MAX_TOKENS || "4096"),
    verbose: false,
    configuration: {
      baseURL: "https://openrouter.ai/api/v1",
      apiKey: process.env.OPENROUTER_API_KEY,
    },
  });
}

/**
 * Converts HermesState messages to LangChain BaseMessage format
 */
function toLangChainMessages(messages: HermesMessage[]): BaseMessage[] {
  return messages.map((msg) => {
    switch (msg.role) {
      case 'user':
        return new HumanMessage({ content: msg.content, name: msg.toolName });
      case 'assistant':
        return new AIMessage({ 
          content: msg.content, 
          tool_calls: msg.toolCallId ? [
            {
              name: msg.toolName || '',
              args: {},
              id: msg.toolCallId,
            }
          ] : undefined
        });
      case 'system':
        return new SystemMessage({ content: msg.content });
      case 'tool':
        return new HumanMessage({ 
          content: msg.content, 
          name: msg.toolName 
        });
      default:
        return new HumanMessage({ content: msg.content });
    }
  });
}

/**
 * Planner Node
 * 
 * Analyzes user request and decides which tools to call using LLM with bound tools.
 * Takes state.messages as input and returns updated state with AI response.
 */
export async function plannerNode(
  state: typeof HermesStateAnnotation.State,
  config?: RunnableConfig
): Promise<Partial<typeof HermesStateAnnotation.State>> {
  // Get policy to filter allowed tools
  const policyId = state.policyId || 'trading-default';
  const policy = getPolicy(policyId);
  
  // Get available tools (filtered by policy if exists)
  const availableTools = policy 
    ? coreTools.filter((t) => policy.allowedTools.includes(t.name))
    : coreTools;
  
  // Create model - uses TOOLS_MODEL for policies with tools, HERMES_MODEL for read-only
  const model = createChatModel(policyId).bindTools(availableTools, {
    tool_choice: 'auto',
  });
  
  // Prepare messages for LLM
  const langChainMessages = toLangChainMessages(state.messages);
  
  // Prepend system message
  const systemMessage = new HumanMessage({
    content: HERMES_SYSTEM_PROMPT,
    name: 'system',
  });
  
  // Invoke model
  const response = await model.invoke([systemMessage, ...langChainMessages], config);
  
  // Convert response to HermesMessage - handle content type
  const content = typeof response.content === 'string' ? response.content : JSON.stringify(response.content);
  const aiMessage: HermesMessage = createMessage('assistant', content);
  
  // Handle tool calls if present
  if (response.tool_calls && response.tool_calls.length > 0) {
    aiMessage.toolCallId = response.tool_calls[0].id;
    aiMessage.toolName = response.tool_calls[0].name;
  }
  
  return {
    messages: [aiMessage],
    iteration: 1,
  };
}

/**
 * Reviewer Node
 * 
 * Reviews tool results and determines if the workflow is complete or needs more iteration.
 * Provides feedback on whether to continue or end.
 */
export async function reviewerNode(
  state: typeof HermesStateAnnotation.State,
  config?: RunnableConfig
): Promise<Partial<typeof HermesStateAnnotation.State>> {
  // Get tool results
  const toolResults = state.toolResults;
  const toolResultCount = Object.keys(toolResults).length;
  
  // Get the last assistant message
  const lastAssistantMsg = [...state.messages].reverse().find(
    (m) => m.role === 'assistant'
  );
  
  // Check if there are pending tool calls
  const hasPendingToolCalls = lastAssistantMsg?.toolCallId !== undefined;
  
  // Create review result
  let approved = false;
  let feedback = '';
  
  if (!hasPendingToolCalls && toolResultCount > 0) {
    // All tool calls completed, approve the response
    approved = true;
    feedback = 'All tool calls completed successfully. Ready to respond to user.';
  } else if (toolResultCount === 0) {
    // No tools called yet, need more iteration
    approved = false;
    feedback = 'Waiting for tool execution results.';
  } else {
    // Still have pending tool calls
    approved = false;
    feedback = 'Tool calls still pending execution.';
  }
  
  // Check iteration limit
  const policyId = state.policyId || 'trading-default';
  const policy = getPolicy(policyId);
  const maxIterations = policy?.maxIterations || 5;
  
  if (state.iteration >= maxIterations) {
    approved = true;
    feedback = `Maximum iterations (${maxIterations}) reached. Ending workflow.`;
  }
  
  return {
    reviewResult: {
      approved,
      feedback,
      suggestedActions: approved ? [] : ['Continue to tool execution'],
    },
  };
}

/**
 * Router Node
 * 
 * Routes to either "tools" (if there are tool calls) or END (if approved).
 * This is used as a conditional edge function.
 */
export function routerNode(
  state: typeof HermesStateAnnotation.State
): 'tools' | '__end__' {
  const policyId = state.policyId || 'trading-default';

  // For read-only policy, always end immediately (no tools)
  if (policyId === 'read-only') {
    return '__end__';
  }

  const review = state.reviewResult;

  if (review?.approved) {
    return '__end__';
  }

  const lastMessage = [...state.messages].reverse()[0];
  if (lastMessage?.role === 'assistant' && lastMessage?.toolCallId) {
    return 'tools';
  }

  return '__end__';
}

/**
 * Creates the agent nodes ready for graph composition
 */
export const agentNodes = {
  planner: plannerNode,
  reviewer: reviewerNode,
  router: routerNode,
};
