/**
 * Tool Node for Hermes Graph
 * 
 * Generic node that executes tools and merges results back into state.
 */

import { HermesState, HermesMessage, ToolResult, createToolResult, createMessage } from '../core/state';
import { coreTools, getToolByName } from '../core/tools';

/**
 * ToolNode - Executes tools and updates state with results
 * 
 * This node:
 * 1. Extracts tool calls from the last assistant message
 * 2. Executes each tool using the coreTools registry
 * 3. Merges results back into state.toolResults
 * 4. Adds tool result messages to state.messages
 */
export async function toolNode(
  state: typeof HermesStateAnnotation.State
): Promise<Partial<typeof HermesStateAnnotation.State>> {
  // Get the last assistant message with tool calls
  const messages = [...state.messages];
  const lastAssistantMsg = messages.reverse().find(
    (m) => m.role === 'assistant' && m.toolCallId
  );
  
  if (!lastAssistantMsg || !lastAssistantMsg.toolCallId || !lastAssistantMsg.toolName) {
    return {
      toolResults: state.toolResults,
    };
  }
  
  const toolName = lastAssistantMsg.toolName;
  const toolCallId = lastAssistantMsg.toolCallId;
  
  // Get the tool from registry
  const tool = getToolByName(toolName);
  
  if (!tool) {
    const errorResult: ToolResult = createToolResult(
      toolName,
      false,
      0,
      undefined,
      `Tool '${toolName}' not found in registry`
    );
    
    const errorMessage: HermesMessage = createMessage('tool', errorResult.error || 'Tool not found', {
      toolName,
      toolCallId,
    });
    
    return {
      toolResults: {
        ...state.toolResults,
        [toolCallId]: errorResult,
      },
      messages: [errorMessage],
    };
  }
  
  // Get tool call arguments from the message
  const toolCalls = (lastAssistantMsg as any).tool_calls;
  const toolCall = toolCalls?.[0];
  const toolArgs = toolCall?.arguments || {};
  
  // Execute the tool with timing
  const startTime = Date.now();
  let toolResult: ToolResult;
  let resultMessage: HermesMessage;
  
  try {
    // Invoke the tool - result is ToolMessage or string
    const toolMsg = await tool.invoke(toolArgs);
    let result: string;
    if (typeof toolMsg === 'string') {
      result = toolMsg;
    } else if (toolMsg && typeof toolMsg.content === 'string') {
      result = toolMsg.content;
    } else {
      result = JSON.stringify(toolMsg);
    }
    const durationMs = Date.now() - startTime;
    
    toolResult = createToolResult(toolName, true, durationMs, result);
    resultMessage = createMessage('tool', result, {
      toolName,
      toolCallId,
    });
  } catch (error) {
    const durationMs = Date.now() - startTime;
    const errorMessage = error instanceof Error ? error.message : 'Unknown error';
    
    toolResult = createToolResult(toolName, false, durationMs, undefined, errorMessage);
    resultMessage = createMessage('tool', errorMessage, {
      toolName,
      toolCallId,
    });
  }
  
  return {
    toolResults: {
      ...state.toolResults,
      [toolCallId]: toolResult,
    },
    messages: [resultMessage],
  };
}

// Import the annotation for type annotations
import { HermesStateAnnotation } from '../core/state';

/**
 * Type annotation for the tool node state
 */
export type ToolNodeState = typeof HermesStateAnnotation.State;
