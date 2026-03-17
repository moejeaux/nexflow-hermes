# Hermes Orchestration Layer - Architecture Specification v2

## 1. Executive Summary

**Goal**: Build Hermes as a standalone orchestration repo that coordinates LangGraph workflows with LangChain components, integrating NXFX01 trading, ML scoring, and Wallet Intelligence.

### Key Requirements (Fixed)
- **Hermes is its own repo**, separate from NXFX01 trading repo
- **LangGraph-style graph runtime** for workflows: planner → tools → reviewer with loops/conditionals
- **LangChain-style components**: LLM calls, Tools (HTTP/MCP/custom), Prompt + LLM + retriever chains
- **HTTP API Integrations**:
  - NXFX01: `POST /hermes-gateway/executeStrategy`
  - ML sidecar: `GET /ml/score-token?token={address}`
  - WalletIntel: `GET /wallet/{address}/score`

---

## 2. Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                         Hermes Orchestrator                          │
│                    (LangGraph + LangChain)                           │
└─────────────────────────────────────────────────────────────────────┘

  ┌──────────────┐     ┌──────────────┐     ┌──────────────┐
  │   Planner    │────►│    Tools     │────►│   Reviewer   │
  │   (LLM)      │     │  (HTTP/MCP)  │     │   (LLM)      │
  └──────────────┘     └──────────────┘     └──────────────┘
         │                    │                    │
         │              ┌─────┴─────┐              │
         │              ▼           ▼              │
         │        ┌─────────┐ ┌─────────┐        │
         │        │ NXFX01  │ │   ML    │        │
         │        │  API    │ │  API    │        │
         │        └─────────┘ └─────────┘        │
         │              │           │              │
         │              └─────┬─────┘              │
         │                    ▼                     │
         │            ┌─────────────┐              │
         │            │ WalletIntel │              │
         │            │    API      │              │
         │            └─────────────┘              │
         │                    │                     │
         └────────────────────┼─────────────────────┘
                              ▼
                    ┌─────────────────┐
                    │   Execution     │
                    │   (Trade/Alert) │
                    └─────────────────┘
```

---

## 3. Module Structure

```
hermes/
├── src/
│   ├── index.ts                 # Entry point
│   │
│   ├── graph/                    # LangGraph workflow definitions
│   │   ├── index.ts             # Graph builder
│   │   ├── nodes/
│   │   │   ├── planner.ts       # Decide what tools to call
│   │   │   ├── executor.ts      # Execute tool calls
│   │   │   ├── reviewer.ts      # Review results, decide if done
│   │   │   └── router.ts        # Conditional edge routing
│   │   └── edges/
│   │       └── conditional.ts    # Conditional edge logic
│   │
│   ├── components/               # LangChain-style components
│   │   ├── llms/
│   │   │   └── openrouter.ts    # OpenRouter LLM wrapper
│   │   │
│   │   ├── tools/               # Tool definitions
│   │   │   ├── base.ts          # Base tool interface
│   │   │   ├── nxfx01.client.ts # NXFX01 HTTP tool
│   │   │   ├── ml.client.ts     # ML scoring tool
│   │   │   ├── wallet.client.ts # Wallet intelligence tool
│   │   │   └── market.data.ts   # Market data tool
│   │   │
│   │   └── chains/
│   │       ├── planning.chain.ts    # Planner chain
│   │       └── review.chain.ts       # Reviewer chain
│   │
│   ├── services/                 # External API clients
│   │   ├── nxfx01.service.ts    # NXFX01 gateway
│   │   ├── ml.service.ts         # ML scoring sidecar
│   │   └── wallet.service.ts     # Wallet intelligence
│   │
│   ├── types/                    # TypeScript interfaces
│   │   ├── hermes.types.ts       # Core Hermes types
│   │   ├── nxfx01.types.ts       # NXFX01 contract types
│   │   ├── ml.types.ts           # ML scoring types
│   │   └── wallet.types.ts       # Wallet intel types
│   │
│   ├── config/                   # Configuration
│   │   └── index.ts              # Config loader
│   │
│   └── utils/                    # Utilities
│       ├── logger.ts             # Logging setup
│       └── validators.ts         # Input validation
│
├── package.json
├── tsconfig.json
├── .env.example
└── README.md
```

---

## 4. Key Interfaces

### 4.1 Hermes Core Types

```typescript
// src/types/hermes.types.ts

export interface HermesMessage {
  id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  timestamp: string;
}

export interface HermesContext {
  messages: HermesMessage[];
  state: WorkflowState;
}

export interface WorkflowState {
  // Input
  userRequest: string;
  
  // Planning phase
  plannedTools: ToolCall[];
  
  // Execution phase
  toolResults: Record<string, ToolResult>;
  
  // Review phase
  reviewedResults: ReviewResult;
  
  // Final output
  finalResponse: string;
  
  // Metadata
  iterations: number;
  tokensUsed: number;
  errors: string[];
}

export interface ToolCall {
  toolName: string;
  arguments: Record<string, unknown>;
}

export interface ToolResult {
  success: boolean;
  data?: unknown;
  error?: string;
  durationMs: number;
}

export interface ReviewResult {
  approved: boolean;
  feedback: string;
  suggestedActions?: string[];
}
```

### 4.2 NXFX01 Integration Types

```typescript
// src/types/nxfx01.types.ts

export interface NXFX01ExecuteRequest {
  launch_id: string;
  token_address: string;
  strategy: 'FAST' | 'WAIT' | 'BLOCK';
  position_size_usd?: number;
  max_slippage_pct?: number;
}

export interface NXFX01ExecuteResponse {
  success: boolean;
  execution_id?: string;
  error?: string;
  estimated_entry?: number;
}

export interface NXFX01LaunchSignal {
  launch_id: string;
  token_address: string;
  chain: string;
  action: 'FAST' | 'WAIT' | 'BLOCK';
  confidence: number;
  scores: {
    overall_safety_initial: number;
    modulated_score: number;
  };
}
```

### 4.3 ML Scoring Types

```typescript
// src/types/ml.types.ts

export interface MLTokenScoreRequest {
  token_address: string;
  chain: string;
  features?: Record<string, number>;
}

export interface MLTokenScoreResponse {
  token_address: string;
  score: number;           // 0-100
  confidence: number;      // 0-1
  factors: {
    sentiment: number;
    momentum: number;
    volatility: number;
  };
  recommendation: 'BUY' | 'SELL' | 'HOLD';
}
```

### 4.4 Wallet Intelligence Types

```typescript
// src/types/wallet.types.ts

export interface WalletScoreRequest {
  wallet_address: string;
}

export interface WalletScoreResponse {
  wallet: string;
  wallet_tier: 'TIER_1_WHALE' | 'TIER_2_SMART_MONEY' | 'TIER_3_RETAIL' | 'TIER_4_FLAGGED' | 'UNKNOWN';
  wallet_value_score: number;
  wallet_performance_score: number;
  alpha_cohort_flag: boolean;
  cluster_id?: string;
  cluster_tier?: string;
}
```

---

## 5. LangGraph Workflow

### 5.1 Graph Definition

```typescript
// src/graph/index.ts
import { StateGraph, Annotation } from '@langchain/langgraph';
import { plannerNode } from './nodes/planner';
import { executorNode } from './nodes/executor';
import { reviewerNode } from './nodes/reviewer';
import { routerEdge } from './edges/conditional';

const workflowState = Annotation.Root({
  userRequest: Annotation<string>,
  plannedTools: Annotation<ToolCall[]>,
  toolResults: Annotation<Record<string, ToolResult>>,
  reviewedResults: Annotation<ReviewResult>,
  finalResponse: Annotation<string>,
  iterations: Annotation<number>,
  errors: Annotation<string[]>,
});

const graph = new StateGraph(workflowState)
  .addNode('planner', plannerNode)
  .addNode('executor', executorNode)
  .addNode('reviewer', reviewerNode)
  .addEdge('__start__', 'planner')
  .addConditionalEdges('planner', routerEdge, {
    execute: 'executor',
    respond: 'reviewer',
  })
  .addEdge('executor', 'reviewer')
  .addConditionalEdges('reviewer', (state) => {
    if (state.reviewedResults.approved || state.iterations >= 3) {
      return '__end__';
    }
    return 'planner';
  });

export const hermesGraph = graph.compile();
```

### 5.2 Node: Planner

```typescript
// src/graph/nodes/planner.ts
import { BaseMessage, HumanMessage } from '@langchain/core/messages';

export async function plannerNode(state: WorkflowState): Promise<Partial<WorkflowState>> {
  const systemPrompt = `You are the Hermes Planner. 
Given the user's request, decide which tools to call.
Available tools:
- nxfx01: Execute trading strategy on NXFX01
- ml_score: Get ML-based token score
- wallet_score: Get wallet intelligence
- market_data: Get market data

Respond with a JSON array of tool calls.`;

  const messages: BaseMessage[] = [
    new HumanMessage(state.userRequest),
  ];
  
  // Call LLM to get tool plan
  const response = await llm.invoke([systemPrompt, ...messages]);
  const plannedTools = parseToolCalls(response.content);
  
  return {
    plannedTools,
    iterations: state.iterations + 1,
  };
}
```

---

## 6. Implementation Plan (Stepwise)

### Phase 1: Foundation

1. **package.json + tsconfig.json** - Project setup
2. **config/index.ts** - Configuration loader
3. **utils/logger.ts** - Logging setup
4. **types/*.ts** - All TypeScript interfaces

### Phase 2: Services (HTTP Clients)

5. **services/nxfx01.service.ts** - NXFX01 gateway client
6. **services/ml.service.ts** - ML scoring client
7. **services/wallet.service.ts** - Wallet intel client

### Phase 3: Tools (LangChain Tools)

8. **tools/base.ts** - Base tool interface
9. **tools/nxfx01.client.ts** - NXFX01 tool
10. **tools/ml.client.ts** - ML scoring tool
11. **tools/wallet.client.ts** - Wallet intel tool
12. **tools/market.data.ts** - Market data tool

### Phase 4: Chains (LangChain Chains)

13. **chains/planning.chain.ts** - Planner chain
14. **chains/review.chain.ts** - Reviewer chain

### Phase 5: Graph (LangGraph)

15. **graph/nodes/planner.ts** - Planner node
16. **graph/nodes/executor.ts** - Executor node
17. **graph/nodes/reviewer.ts** - Reviewer node
18. **graph/edges/conditional.ts** - Conditional routing
19. **graph/index.ts** - Graph builder

### Phase 6: Entry Point

20. **index.ts** - Main entry point with CLI

---

## 7. Configuration

### 7.1 Environment Variables

```bash
# Hermes
HERMES_ENV=production
HERMES_LOG_LEVEL=info

# LLM Provider (OpenRouter)
OPENROUTER_API_KEY=your-key
OPENROUTER_MODEL=anthropic/claude-3-sonnet

# External APIs
NXFX01_API_URL=http://localhost:8100
NXFX01_API_KEY=your-key
ML_SIDECAR_URL=http://localhost:8200
WALLET_INTEL_URL=http://localhost:8300
```

### 7.2 Config Structure

```typescript
// src/config/index.ts
export interface HermesConfig {
  env: 'development' | 'production';
  logLevel: 'debug' | 'info' | 'warn' | 'error';
  
  llm: {
    provider: 'openrouter';
    apiKey: string;
    model: string;
    temperature: number;
    maxTokens: number;
  };
  
  services: {
    nxfx01: {
      baseUrl: string;
      apiKey: string;
      timeout: number;
    };
    ml: {
      baseUrl: string;
      timeout: number;
    };
    walletIntel: {
      baseUrl: string;
      timeout: number;
    };
  };
  
  graph: {
    maxIterations: number;
    toolTimeoutMs: number;
  };
}
```

---

## 8. Acceptance Criteria

1. ✅ LangGraph workflow compiles and runs
2. ✅ LangChain tools execute HTTP calls to NXFX01, ML, WalletIntel
3. ✅ Conditional edges route based on LLM decisions
4. ✅ Max iteration guard prevents infinite loops
5. ✅ TypeScript strict mode passes with no errors
6. ✅ Configuration loaded from env vars
7. ✅ Logging captures all workflow steps
8. ✅ Error handling with graceful fallbacks

---

## 9. File Responsibilities

| File | Responsibility |
|------|----------------|
| `package.json` | Dependencies: langgraph, langchain, openrouter |
| `tsconfig.json` | Strict TypeScript config |
| `config/index.ts` | Env var loading with validation |
| `utils/logger.ts` | Winston/Pino logger setup |
| `types/hermes.types.ts` | Core workflow state types |
| `types/nxfx01.types.ts` | NXFX01 API types |
| `types/ml.types.ts` | ML scoring types |
| `types/wallet.types.ts` | Wallet intel types |
| `services/nxfx01.service.ts` | HTTP client for NXFX01 |
| `services/ml.service.ts` | HTTP client for ML |
| `services/wallet.service.ts` | HTTP client for WalletIntel |
| `tools/base.ts` | Base tool class |
| `tools/*.ts` | LangChain Tool implementations |
| `chains/planning.chain.ts` | LLM chain for planning |
| `chains/review.chain.ts` | LLM chain for review |
| `graph/nodes/*.ts` | LangGraph nodes |
| `graph/edges/*.ts` | Conditional edges |
| `graph/index.ts` | Graph compilation |
| `index.ts` | CLI entry point |
