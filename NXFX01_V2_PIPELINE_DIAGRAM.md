# NXFX01 v2.1 — Complete Scoring Pipeline Diagram

> Generated: March 15, 2026  
> Policy: `scoring_policy.yaml` v2.1  
> Changelog: Added Mempool Features layer, Major Interest gate, enhanced SM/Whale cohort metrics, mempool-based de-risk triggers  

---

```mermaid
flowchart TD
    subgraph DETECT["🔍 Detection (T+0s)"]
        A[New Token Launch on Base] --> B[launch_scanner]
        B --> C{Known Launchpad / Factory?}
        C -->|Yes| D[Set launch_type + confidence]
        C -->|No| E[launch_type = stealth/unknown]
        D --> F[Insert into launches table]
        E --> F
    end

    subgraph STAGE1["⚡ Stage 1 — Initial Scoring (T+0–10s)"]
        F --> G[contract_scanner]
        F --> H[deployer_profiler]
        F --> I[cex_labeler]
        G --> G1[GoPlus + Bytecode Analysis]
        G1 --> G2[contract_safety 0–100]
        H --> H1[Deployment History + Funding Trace]
        H1 --> H2[deployer_reputation 0–100]
        H1 --> H3[funding_risk 0–100]
        I --> I1[Trace Inbound ETH ≤2 hops]
        I1 --> I2{CEX share ≥ 30%?}
        I2 -->|Yes| I3[is_cex_funded = true]
        I2 -->|No| I4[is_cex_funded = false]

        G2 --> J[initial_scorer]
        H2 --> J
        H3 --> J
        J --> K[overall_safety_initial]

        K --> L{Critical Red Flags?}
        L -->|"honeypot + change_balance\nproxy + change_balance\nhidden_owner + take_back"| M["🚫 action_initial = BLOCK"]
        L -->|No critical flags| N{Score ≥ 70?}
        N -->|Yes| O["action_initial = FAST"]
        N -->|No| P{Score ≥ 40?}
        P -->|Yes| Q["action_initial = WAIT"]
        P -->|No| M
    end

    subgraph STAGE2["🔬 Stage 2 — Behavioral Analysis (T+1–30min)"]
        O --> R[behavior_updater]
        Q --> R
        M -.->|stays BLOCK| BLOCKED

        R --> S1[SmartMoney Scorer]
        R --> S2[Whale Behavior Scorer]
        R --> S3[Graph Risk Scorer]
        R --> S4[Rug Risk Scorer]
        R --> S5[Liquidity Quality Scorer]
        R --> S6[Social Quality Scorer]
        R --> S7[Data Confidence Scorer]

        S1 --> S1a["SM Alignment 0–100\n(cohort, accumulation, diversity,\nhold time, exits, pending conviction)"]
        S2 --> S2a["Whale Behavior 0–100\n(net flow, z-score, trend,\ndip buys, rip sells, pending bias)"]
        S3 --> S3a["Graph Risk 0–100 ⚠️\n(centralization, loops, LP concentration)"]
        S4 --> S4a["Rug Risk 0–100 ⚠️\n(code + graph + behavioral + funding)"]
        S5 --> S5a["Liquidity Quality 0–100\n(LP depth, volume, spread)"]
        S6 --> S6a["Social Quality 0–100\n(mentions, sentiment, reports, creator)"]
        S7 --> S7a["Data Confidence 0–100\n(9 feature groups incl. mempool)"]
    end

    subgraph MEMPOOL["📡 Mempool Features Layer (T+0 continuous)"]
        F --> MW[mempool_watcher]
        MW --> MW1["Decode pending swaps\n(Uniswap V2/V3, Aerodrome)"]
        MW1 --> MW2["Label actors\n(SM, Whale, Retail, Flagged)"]
        MW2 --> MW3["15s rolling aggregation"]
        MW3 --> MW4["MempoolSnapshot\n(SM/Whale buy/sell USD,\ntiny_swap_density, anomaly_density,\nfee_urgency, sm_conviction,\nwhale_bias)"]
    end

    MW4 -->|mempool_snapshot| S1
    MW4 -->|mempool_snapshot| S2
    MW4 -->|mempool_flags| DR

    subgraph FINAL["📊 Final Scoring"]
        S1a --> WS[Weighted Sum]
        S2a --> WS
        S3a -->|"100 - risk"| WS
        S4a -->|"100 - risk"| WS
        S5a --> WS
        S6a --> WS
        WS --> DC["× Confidence Factor\n(0.5 + DataConf/200)"]
        S7a --> DC
        DC --> RAW[modulated_score]
    end

    subgraph GATES["🚦 FAST Hard Gates"]
        RAW --> HG{All Hard Gates Pass?}
        HG -->|"LP ≥ $5K ✓\nVol1h ≥ $1K ✓\nSpread ≤ 500bp ✓\nRug ≤ 45 ✓\nDataConf ≥ 60 ✓\nGraph ≤ 60 ✓\nCritical data present ✓\nLiquidity gates ✓"| GATE_OK["All gates passed ✅"]
        HG -->|"Rug risk > 45"| GATE_BLOCK["⛔ → BLOCK"]
        HG -->|"Any other gate fails"| GATE_WAIT["⏸️ → WAIT"]

        GATE_OK --> TH{Score Threshold}
        TH -->|"≥ 70"| FAST["✅ action_final = FAST"]
        TH -->|"40–69"| WAIT2["⏸️ action_final = WAIT"]
        TH -->|"< 40"| BLOCK2["⛔ action_final = BLOCK"]
    end

    subgraph MI["🎯 Major Interest Gate (v2.1)"]
        FAST --> MIG{Evaluate Major Interest}
        MIG --> MIG1{"Risk gates ✓\n(rug≤35, graph≤45)"}
        MIG --> MIG2{"Signal gates ✓\n(SM≥60, whale≥55,\nliq≥60, data≥70)"}
        MIG --> MIG3{"Mempool quality ✓\n(tiny_density≤0.50)"}
        MIG --> MIG4{"No sell veto ✓\n(sm_conv>-0.5 OR\nwhale_bias>-0.5)"}
        MIG1 & MIG2 & MIG3 & MIG4 -->|All pass| MI_YES["major_interest = TRUE\n🟢 Aggressive sizing eligible"]
        MIG1 & MIG2 & MIG3 & MIG4 -->|Any fail| MI_NO["major_interest = FALSE\n🟡 Standard sizing only"]
    end

    subgraph DERISK["🛡️ De-Risk Trigger Engine (Continuous)"]
        MI_YES --> DR[Evaluate Triggers Each Tick]
        MI_NO --> DR
        WAIT2 -.-> DR

        DR --> T1{"SM cohort exit > 40%?"}
        DR --> T2{"SM cohort exit > 70%?"}
        DR --> T3{"Whale net flow < 0\n& trend < -0.3?"}
        DR --> T4{"Rug risk > 70?"}
        DR --> T5{"LP drain > 30%?"}
        DR --> T6{"Volume 1h < $500?"}
        DR --> T7{"Spread > 800bp?"}
        DR --> T8{"Graph risk > 70?"}
        DR --> T9{"🆕 Mempool SM sell\n(conviction < -0.5)?"}
        DR --> T10{"🆕 Mempool whale sell\n(bias < -0.5)?"}
        DR --> T11{"🆕 Mempool anomaly\ndensity > 0.50?"}

        T1 -->|Yes| SOFT["SOFT_DERISK"]
        T3 -->|Yes| SOFT
        T6 -->|Yes| SOFT
        T7 -->|Yes| SOFT
        T8 -->|Yes| SOFT
        T9 -->|Yes| SOFT
        T10 -->|Yes| SOFT
        T11 -->|Yes| SOFT

        T2 -->|Yes| HARD["HARD_EXIT"]
        T4 -->|Yes| HARD
        T5 -->|Yes| HARD

        SOFT --> ESC{"≥ 3 SOFT triggers?"}
        ESC -->|Yes| HARD
        ESC -->|No| PA_SOFT["position = SOFT_DERISK\n⚠️ Reduce exposure"]
        HARD --> PA_HARD["position = HARD_EXIT\n🚨 Exit immediately"]
    end

    subgraph OUTCOME["📈 Outcome Tracking (T+1h → 7d)"]
        MI_YES --> OT[outcome_tracker]
        MI_NO --> OT
        OT --> OT1[Track PnL @ 1h, 24h, 7d]
        OT1 --> OT2[Record max_drawdown, peak_mcap]
        OT2 --> OT2a["v2.1: Snapshot sub_scores\n+ major_interest_flag at entry"]
        OT2a --> OT3{Rugged?}
        OT3 -->|Yes| OT4["final_status = RUGGED"]
        OT3 -->|No| OT5{Volume died?}
        OT5 -->|Yes| OT6["final_status = DEAD"]
        OT5 -->|No| OT7["final_status = GRADUATED / ACTIVE"]
    end

    subgraph LEARN["🧠 Self-Learning Loop (Weekly)"]
        OT4 --> SL[Correlate scores vs outcomes]
        OT6 --> SL
        OT7 --> SL
        SL --> SL1["Identify patterns:\n• Score ranges → outcomes\n• Wallet patterns → rug rate\n• Gate effectiveness\n• v2.1: major_interest → PnL correlation\n• v2.1: mempool signals → outcome accuracy"]
        SL1 --> SL2["Propose policy_patch\n(weight/threshold adjustments)"]
        SL2 --> SL3["Human review → approve/reject"]
        SL3 -->|Approved| SL4["Update scoring_policy.yaml"]
    end

    BLOCKED["⛔ BLOCKED — No Trade"]
    GATE_BLOCK --> BLOCKED
    BLOCK2 --> BLOCKED

    style DETECT fill:#1a1a2e,stroke:#16213e,color:#e0e0e0
    style STAGE1 fill:#1a1a2e,stroke:#16213e,color:#e0e0e0
    style STAGE2 fill:#1a1a2e,stroke:#16213e,color:#e0e0e0
    style MEMPOOL fill:#1a1a2e,stroke:#7b2cbf,color:#e0e0e0
    style MI fill:#1a1a2e,stroke:#2d6a4f,color:#e0e0e0
    style FINAL fill:#1a1a2e,stroke:#16213e,color:#e0e0e0
    style GATES fill:#1a1a2e,stroke:#16213e,color:#e0e0e0
    style DERISK fill:#1a1a2e,stroke:#16213e,color:#e0e0e0
    style OUTCOME fill:#1a1a2e,stroke:#16213e,color:#e0e0e0
    style LEARN fill:#1a1a2e,stroke:#16213e,color:#e0e0e0
    style FAST fill:#2d6a4f,stroke:#40916c,color:#fff
    style BLOCKED fill:#6a040f,stroke:#9d0208,color:#fff
    style M fill:#6a040f,stroke:#9d0208,color:#fff
    style GATE_BLOCK fill:#6a040f,stroke:#9d0208,color:#fff
    style BLOCK2 fill:#6a040f,stroke:#9d0208,color:#fff
    style PA_HARD fill:#9d0208,stroke:#d00000,color:#fff
    style PA_SOFT fill:#e85d04,stroke:#f48c06,color:#fff
    style GATE_OK fill:#2d6a4f,stroke:#40916c,color:#fff
```

---

## Pipeline Phases Summary

| Phase | Timing | Key Decision | Outputs |
|---|---|---|---|
| **Detection** | T+0s | Launchpad classification | `launch_type`, `launch_type_confidence` |
| **Mempool Features** *(v2.1)* | T+0 continuous | Pending tx decode + actor labeling | `MempoolSnapshot` (sm/whale conviction, anomaly density) |
| **Stage 1 — Initial** | T+0–10s | Critical red flag check → FAST/WAIT/BLOCK | `overall_safety_initial`, `action_initial` |
| **CEX Labeling** | T+0–10s | Funding trace ≤2 hops | `is_cex_funded`, `cex_funding_share` |
| **Stage 2 — Behavioral** | T+1–30min | 7 sub-scores + mempool integration | SM, Whale, Graph, Rug, Liquidity, Social, DataConf |
| **Final Scoring** | T+1–30min | Weighted sum × confidence factor | `modulated_score` |
| **FAST Hard Gates** | T+1–30min | 8 circuit breakers | `action_final` (FAST/WAIT/BLOCK) |
| **Major Interest** *(v2.1)* | T+1–30min | Composite institutional interest gate | `major_interest_flag`, `major_interest_score` |
| **De-Risk Engine** | Continuous | 11 trigger types (8 legacy + 3 mempool), escalation | `position_action` (HOLD/SOFT_DERISK/HARD_EXIT) |
| **Outcome Tracking** | T+1h → 7d | PnL snapshots, rug detection, sub-scores snapshot | `final_status`, `major_interest_flag_at_entry` |
| **Self-Learning** | Weekly | Score↔outcome + major_interest↔PnL correlation | `policy_patch` proposals |

## Final Weights (v2)

| Dimension | Weight | Direction |
|---|---|---|
| contract_safety | 0.12 | Higher = safer |
| deployer_reputation | 0.10 | Higher = safer |
| funding_risk | 0.08 | Higher = safer (inverted) |
| **smart_money_alignment** | **0.15** | Higher = better |
| whale_behavior | 0.08 | Higher = better |
| **graph_risk** | **0.10** | Stored as risk → `100 - risk` in formula |
| **rug_risk** | **0.15** | Stored as risk → `100 - risk` in formula |
| liquidity_quality | 0.12 | Higher = better |
| social_quality | 0.05 | Higher = better |
| holder_distribution | 0.05 | Higher = better |

## FAST Hard Gates

| Gate | Threshold | On Fail |
|---|---|---|
| LP depth | `lp_usd ≥ $5,000` | → WAIT |
| Volume | `volume_1h ≥ $1,000` | → WAIT |
| Spread | `spread ≤ 500bp` | → WAIT |
| Rug risk | `rug_risk ≤ 45` | → **BLOCK** |
| Data confidence | `data_confidence ≥ 60` | → WAIT |
| Graph risk | `graph_risk ≤ 60` | → WAIT |
| Critical data | Contract + Deployer + Liquidity present | → WAIT |
| Liquidity gates | `passes_hard_gates = true` | → WAIT |

## De-Risk Triggers

| Trigger | Severity | Condition |
|---|---|---|
| SM cohort exit | SOFT | `exit_pct > 40%` |
| Founding cohort exit | **HARD** | `exit_pct > 70%` |
| Whale distribution flip | SOFT | `net_flow < 0` & `trend < -0.3` |
| Rug risk spike | **HARD** | `rug_risk > 70` |
| LP drain | **HARD** | `lp_change_rate < -0.3` |
| Volume collapse | SOFT | `volume_1h < $500` |
| Spread explosion | SOFT | `spread > 800bp` |
| Graph risk spike | SOFT | `graph_risk > 70` |
| 🆕 Mempool SM sell | SOFT→**HARD** | `sm_conviction < -0.5` (→HARD if exits > 30%) |
| 🆕 Mempool whale sell | SOFT→**HARD** | `whale_bias < -0.5` (→HARD if z < -0.5) |
| 🆕 Mempool anomaly | SOFT→**HARD** | `anomaly_density > 0.50` (→HARD if > 0.75) |

**Escalation**: 1+ HARD → HARD_EXIT | 3+ SOFT → HARD_EXIT | 1-2 SOFT → SOFT_DERISK
