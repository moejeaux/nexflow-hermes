/**
 * Base Graph Builder for Hermes Orchestration
 */

import { StateGraph, START, END } from '@langchain/langgraph';
import { HermesState, HermesStateAnnotation, createInitialState } from '../core/state';
import { getPolicy, Policy } from '../core/policies';
import { coreTools } from '../core/tools';
import { plannerNode } from './agentNodes';
import { toolNode } from './toolNode';

/**
 * Creates a filtered tools array based on policy
 */
function getFilteredTools(policy: Policy): any[] {
  return coreTools.filter((t: any) => policy.allowedTools.includes(t.name));
}

/**
 * Route from planner to tools or end
 */
function shouldCallTools(state: HermesState): 'tools' | 'end' {
  const messages = state.messages || [];
  const lastMessage = messages[messages.length - 1];
  
  if (lastMessage && lastMessage.role === 'assistant' && lastMessage.toolCallId) {
    return 'tools';
  }
  return 'end';
}

/**
 * Builds the Hermes StateGraph
 */
export function buildHermesGraph(policyId: string = 'trading-default'): any {
  const policy = getPolicy(policyId);
  const allowedTools = policy ? getFilteredTools(policy) : coreTools;
  
  console.log(`Building Hermes graph with policy: ${policyId}`);
  console.log(`Allowed tools: ${allowedTools.map((t: any) => t.name).join(', ')}`);

  const workflow: any = new StateGraph(HermesStateAnnotation);

  workflow.addNode('planner', plannerNode);

  // Only add tools node and tool edges if there are any allowed tools
  if (allowedTools.length > 0) {
    workflow.addNode('tools', toolNode);

    workflow.addEdge(START, 'planner');

    workflow.addConditionalEdges(
      'planner',
      shouldCallTools,
      ['tools', END]
    );

    workflow.addEdge('tools', 'planner');
  } else {
    // No tools for this policy: simple planner -> END graph
    workflow.addEdge(START, 'planner');
    workflow.addEdge('planner', END);
  }

  return workflow;
}

export function compileHermesGraph(policyId: string = 'trading-default') {
  const graph = buildHermesGraph(policyId);
  return graph.compile();
}

export function createHermesRunnable(userRequest: string, policyId: string = 'trading-default') {
  const compiledGraph = compileHermesGraph(policyId);
  const initialState = createInitialState(userRequest, policyId);
  
  return {
    graph: compiledGraph,
    initialState,
    invoke: (config?: any) => compiledGraph.invoke(initialState, config),
    stream: (config?: any) => compiledGraph.stream(initialState, config),
  };
}

export default buildHermesGraph;
