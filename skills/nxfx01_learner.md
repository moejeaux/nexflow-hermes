---
name: NXFX01 Learner
description: Self-learning loop for NXFX01 launch intelligence — outcome analysis, policy refinement, and accuracy tracking.
version: 1.0.0
author: system
tags: [learning, policy, scoring, launches, self-improvement]
---

# NXFX01 Self-Learning Skill

## Purpose
Continuously improve NXFX01's launch scoring accuracy by analyzing past outcomes, identifying systematic errors, and proposing targeted policy adjustments.

## Guardrails

### Auto-adjustable (no human review needed)
- Ranking and sorting of launches in lists
- Commentary and explanation text
- Tags and labels on launches
- Analysis time windows (e.g. look-back period)

### Human-only (requires manual approval via policy_suggestions table)
- Score thresholds (fast_min, block_max)
- Dimension weights (initial_weights, final_weights)
- Critical red flag list
- Shadow mode toggle
- Velocity block threshold
- Any change to the BLOCK decision logic

## Daily Outcome Analysis

### When
- Runs daily at 01:00 UTC (cron job `nxfx01_daily_learner`)
- Can also be triggered manually

### Process
1. Call `get_past_launch_outcomes(since_days=7)` to get recent outcomes
2. For each outcome, compare:
   - `action_initial` vs `action_final` vs actual result
   - `overall_safety_initial` / `overall_safety_final` vs `rugged` / `final_status`
3. Classify accuracy:
   - **True Positive**: FAST → not rugged, positive PnL
   - **True Negative**: BLOCK → rugged or dead
   - **False Positive**: FAST → rugged (worst case — capital at risk)
   - **False Negative**: BLOCK → healthy, missed opportunity
4. Compute weekly accuracy rates per mode
5. Log accuracy to agent stats via `report_agent_stats(agent_id="NXFX01")`

### Key Metrics
- `rug_miss_rate`: % of FAST launches that turned out rugged (target: <5%)
- `opportunity_miss_rate`: % of BLOCK launches that were actually healthy (acceptable: <20%)
- `score_calibration_error`: mean absolute error between safety score and outcome quality
- `avg_initial_latency_s`: how fast initial scoring happens (target: <30s)
- `avg_behavior_latency_s`: how fast full scoring completes (target: <60s)

## Weekly Policy Review

### When
- Runs weekly on Sunday at 02:00 UTC (cron job `nxfx01_weekly_learner`)

### Process
1. Call `get_past_launch_outcomes(since_days=30)` for a larger sample
2. Analyze score distributions:
   - What range of safety scores do rugged tokens fall in?
   - What range do successful tokens fall in?
   - Are there "dead zones" where the score doesn't discriminate well?
3. Analyze dimension contributions:
   - Which dimensions (contract_safety, deployer_reputation, etc.) correlate most with outcomes?
   - Any dimension consistently over- or under-weighted?
4. Analyze wallet/cluster patterns:
   - Do alpha cohort flags actually predict success?
   - Are flagged wallets reliably associated with rugs?
5. Draft policy patch if warranted:
   ```json
   {
     "patch": {
       "final_weights": {"smart_money_participation": 0.12},
       "thresholds": {"fast_min": 72}
     },
     "rationale": "Smart money participation correlated r=0.45 with 7d PnL over 120 launches, up from 0.10 weight to 0.12. fast_min raised from 70 to 72 because 8% of FAST launches in 65-72 range were rugged.",
     "evidence_snapshot": {
       "sample_size": 120,
       "period_days": 30,
       "rug_rate_in_65_72": 0.08,
       "smart_money_correlation": 0.45
     }
   }
   ```
6. Submit via `update_launch_policy_suggestion(policy_patch)` for human review

### Constraints on proposals
- **Minimum sample size**: 50 launches before any weight changes, 100 for threshold changes
- **Maximum adjustment per cycle**: ±0.03 for weights, ±5 for thresholds
- **No overfitting**: must show consistency across at least 2 non-overlapping weeks
- **Risk-first**: proposals that increase BLOCK sensitivity are preferred over those that increase FAST rate

## Regime Detection Updates

### When
- Runs every 6 hours (cron job `nxfx01_regime_check`)

### Process
1. Count launches and outcomes in the last 24 hours:
   - `launch_count_24h`: total new launches
   - `rug_rate_24h`: fraction of recent outcomes that are rugged
   - `fast_success_rate_24h`: fraction of FAST launches with positive outcomes
2. Determine regime:
   - **HOT**: launch_count > 2× weekly average AND rug_rate < 15%
   - **COLD**: launch_count < 0.5× weekly average OR rug_rate > 30%
   - **NORMAL**: everything else
3. Update config: `set_config("base_market_regime", regime)`
4. Log regime change to `base_market_regime_log` table

## Shadow Mode Graduation

### Criteria to propose exiting shadow mode
All of the following must hold over the **most recent 14 days** of shadow data:
- At least 50 launches fully scored (through outcome tracking)
- `rug_miss_rate` < 5% (no more than 1 in 20 FAST calls are rugs)
- `avg_initial_latency_s` < 30 seconds consistently
- `score_calibration_error` < 15 points
- Zero critical false positives (FAST launches that were confirmed honeypots)

### Proposal format
```json
{
  "patch": {"mode": "live"},
  "rationale": "14-day shadow period complete. 62 launches scored. Rug miss rate: 3.2%. Calibration error: 11.4. Zero honeypot false positives.",
  "evidence_snapshot": {
    "period_days": 14,
    "total_scored": 62,
    "rug_miss_rate": 0.032,
    "calibration_error": 11.4,
    "honeypot_false_positives": 0
  }
}
```
This is submitted as a policy suggestion — human flips the switch.
