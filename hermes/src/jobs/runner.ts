/**
 * Hermes Job Runner
 * 
 * Provides a simple interface to run Hermes orchestration jobs.
 */

import { compileHermesGraph, buildHermesGraph } from '../graphs/base';
import { HermesState, createInitialState, HermesMessage } from '../core/state';

/**
 * Result of a Hermes job execution
 */
export interface HermesJobResult {
  /** Final messages in the conversation */
  messages: HermesMessage[];
  /** All tool results from execution */
  toolResults: Record<string, any>;
  /** Final response content */
  finalResponse: string;
  /** Number of iterations executed */
  iterations: number;
  /** Whether execution was successful */
  success: boolean;
  /** Error message if failed */
  error?: string;
}

/**
 * Runs a Hermes orchestration job
 * 
 * @param input - User input/message to process
 * @param policyId - Policy ID to use (default: 'trading-default')
 * @returns HermesJobResult with final state
 */
export async function runHermesJob(
  input: string,
  policyId: string = 'trading-default'
): Promise<HermesJobResult> {
  try {
    console.log(`Starting Hermes job with policy: ${policyId}`);
    console.log(`Input: ${input}`);
    
    // Build and compile the graph
    const graph = buildHermesGraph(policyId);
    const compiledGraph = graph.compile();
    
    // Create initial state with user message
    const initialState = createInitialState(input, policyId);
    
    // Add the user message to messages
    initialState.messages.push({
      id: `msg_${Date.now()}`,
      role: 'user',
      content: input,
      timestamp: new Date().toISOString(),
    });
    
    console.log('Invoking graph...');
    
    // Invoke the graph
    const finalState = await compiledGraph.invoke(initialState);
    
    // Extract results
    const messages = finalState.messages || [];
    const toolResults = finalState.toolResults || {};
    
    // Get the final assistant message
    const finalAssistantMsg = [...messages].reverse().find(
      (m) => m.role === 'assistant' && !m.toolCallId
    );
    
    const finalResponse = finalAssistantMsg?.content || 'No response generated';
    
    // Count iterations
    const iterations = finalState.iteration || 0;
    
    console.log(`Job completed in ${iterations} iterations`);
    
    return {
      messages,
      toolResults,
      finalResponse,
      iterations,
      success: true,
    };
  } catch (error) {
    const errorMessage = error instanceof Error ? error.message : 'Unknown error';
    console.error(`Job failed: ${errorMessage}`);
    
    return {
      messages: [],
      toolResults: {},
      finalResponse: '',
      iterations: 0,
      success: false,
      error: errorMessage,
    };
  }
}

/**
 * Runs a Hermes job with streaming output
 * 
 * @param input - User input/message to process
 * @param policyId - Policy ID to use
 * @param onChunk - Callback for each chunk of streaming output
 */
export async function runHermesJobStream(
  input: string,
  policyId: string = 'trading-default',
  onChunk?: (chunk: any) => void
): Promise<HermesJobResult> {
  try {
    console.log(`Starting streaming Hermes job with policy: ${policyId}`);
    
    // Build and compile the graph
    const graph = buildHermesGraph(policyId);
    const compiledGraph = graph.compile();
    
    // Create initial state
    const initialState = createInitialState(input, policyId);
    initialState.messages.push({
      id: `msg_${Date.now()}`,
      role: 'user',
      content: input,
      timestamp: new Date().toISOString(),
    });
    
    // Stream the graph
    let finalState: HermesState | null = null;
    
    for await (const chunk of compiledGraph.stream(initialState)) {
      if (onChunk) {
        onChunk(chunk);
      }
      // Keep track of final state
      finalState = chunk;
    }
    
    if (!finalState) {
      throw new Error('No output from graph');
    }
    
    // Extract results
    const messages = finalState.messages || [];
    const toolResults = finalState.toolResults || {};
    
    const finalAssistantMsg = [...messages].reverse().find(
      (m) => m.role === 'assistant' && !m.toolCallId
    );
    
    const finalResponse = finalAssistantMsg?.content || 'No response generated';
    const iterations = finalState.iteration || 0;
    
    return {
      messages,
      toolResults,
      finalResponse,
      iterations,
      success: true,
    };
  } catch (error) {
    const errorMessage = error instanceof Error ? error.message : 'Unknown error';
    
    return {
      messages: [],
      toolResults: {},
      finalResponse: '',
      iterations: 0,
      success: false,
      error: errorMessage,
    };
  }
}

export default runHermesJob;
