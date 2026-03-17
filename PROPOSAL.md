# Hermes Orchestration Layer - Architecture Proposal

## Step 1: Architecture and File Layout

### Confirmation
✅ **Greenfield hermes repo**: Yes, this is a new TypeScript/Node project

---

### Proposed File/Folder Layout

```
hermes/
├── package.json
├── tsconfig.json
├── .env.example
├── README.md
└── src/
    ├── index.ts                    # CLI entry point (main export)

    ├── core/
    │   ├── state.ts                # Workflow state definition + type guards
    │   ├── tools.ts                # Base Tool interface + registry
    │   ├── policies.ts             # Guardrails, rate limits, allowed actions

    ├── graphs/
    │   ├── base.ts                 # Base graph builder + common edges
    │   ├── plannerGraph.ts         # Main planner → tools → reviewer workflow
    │   ├── agentNodes.ts           # Planner, Reviewer, Router node implementations
    │   └── toolNode.ts             # Generic tool executor node

    ├── jobs/
    │   └── runner.ts               # Cron job runner for scheduled tasks

    └── services/
        ├── nxfx01-client.ts        # NXFX01 HTTP client (executeStrategy)
        ├── walletintel-client.ts   # Wallet intelligence API client
        └── ml-client.ts            # ML scoring sidecar client
```

---

### Module Responsibilities

| File | Responsibility |
|------|----------------|
| `index.ts` | CLI entry point; parses args, invokes graph, outputs results |
| `core/state.ts` | Defines WorkflowState interface; tracks messages, tool calls, results, iteration count |
| `core/tools.ts` | Base Tool abstract class; tool registry for dynamic lookup by name |
| `core/policies.ts` | Guardrails (max iterations, rate limits); allowed action whitelist; risk overrides |
| `graphs/base.ts` | Graph builder utilities; common conditional edge functions; max iteration guard |
| `graphs/plannerGraph.ts` | Composes the main workflow: planner → tools → reviewer with loops |
| `graphs/agentNodes.ts` | Implements Planner (decides tools), Reviewer (validates results), Router (routes edges) |
| `graphs/toolNode.ts` | Generic node that resolves tool name from state, executes it, merges result back |
| `jobs/runner.ts` | Runs scheduled workflows (e.g., hourly market scan); manages job queue |
| `services/nxfx01-client.ts` | HTTP client for NXFX01 `/hermes-gateway/executeStrategy` endpoint |
| `services/walletintel-client.ts` | HTTP client for WalletIntel `/wallet/{address}/score` endpoint |
| `services/ml-client.ts` | HTTP client for ML sidecar `/ml/score-token` endpoint |

---

### Initial Dependencies (package.json)

```json
{
  "dependencies": {
    // LangGraph + LangChain (core runtime)
    "@langchain/langgraph": "^0.2.0",
    "@langchain/langchain": "^0.3.0",
    "@langchain/core": "^0.3.0",

    // LLM Providers
    "langchain-openai": "^0.2.0",
    "openrouter": "^0.1.0",

    // HTTP Client
    "axios": "^1.7.0",

    // Logging
    "winston": "^3.15.0",

    // Config
    "dotenv": "^16.4.0",
    "zod": "^3.23.0",

    // CLI
    "commander": "^12.1.0",

    // Scheduling (optional, for jobs/)
    "node-cron": "^3.0.3"
  },
  "devDependencies": {
    "typescript": "^5.5.0",
    "@types/node": "^20.14.0",
    "@types/axios": "^1.7.0",
    "@types/winston": "^2.4.0",
    "@types/node-cron": "^3.0.0",
    "tsx": "^4.16.0"
  }
}
```

---

### Data Flow Summary

```
User Request
    │
    ▼
┌─────────────────┐
│  Planner Node   │ ◄─── LLM decides which tools to call
└────────┬────────┘
         │ returns ToolCall[]
         ▼
┌─────────────────┐
│  Tool Node      │ ◄─── Executes each tool (NXFX01/ML/WalletIntel)
└────────┬────────┘
         │ returns ToolResult[]
         ▼
┌─────────────────┐
│  Reviewer Node  │ ◄─── LLM validates results, decides if done
└────────┬────────┘
         │ returns Approved/Feedback
         ▼
    Conditional Edge
    (approved or max iterations → END; else → Planner)
```

---

### Next Steps (After Confirmation)

1. Generate `package.json` with dependencies
2. Generate `tsconfig.json`
3. Generate `src/core/state.ts`
4. Generate `src/core/tools.ts`
5. Generate `src/core/policies.ts`
6. Continue with services, graphs, jobs, index

---

**Please confirm this architecture before I generate any code.**
