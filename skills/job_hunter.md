---
name: Job Hunter
description: Find, evaluate, and pursue freelance/contract opportunities for autonomous revenue generation.
version: 1.0.0
author: NXFX01
tags: [revenue, jobs, freelance, hunting, proposals]
---

# Job Hunter Skill

## Purpose
Autonomously find, score, and pursue freelance and contract opportunities that match NexFlow's capabilities. The goal is consistent revenue generation through job hunting across multiple platforms.

## Platforms to Scan

### Primary (check every cycle)
- **Upwork** — Filter for: Node.js, TypeScript, API development, AI/LLM integration, blockchain, smart contracts
- **Claw Earn** — Crypto-native bounties and tasks, especially Base/EVM-related work
- **x402 Job Boards** — Agent-to-agent commerce; jobs payable via x402 micropayments

### Secondary (check daily)
- **ACP Marketplaces** — Agent Commerce Protocol listings for automated service delivery
- **GitHub Sponsors / Bounties** — Open-source bounties matching our tech stack

## Scoring Criteria

Score each opportunity 0-100 based on weighted factors:

| Factor | Weight | Scoring |
|--------|--------|---------|
| Pay rate | 30% | >$100/hr = 100, $50-100 = 70, $25-50 = 40, <$25 = 10 |
| Skill match | 25% | Perfect match = 100, Adjacent = 60, Stretch = 30 |
| Estimated effort | 20% | <4hrs = 100, 4-16hrs = 70, 16-40hrs = 40, >40hrs = 20 |
| Platform reliability | 15% | Established + escrow = 100, New but legit = 60, Unknown = 20 |
| Repeat potential | 10% | Ongoing/retainer = 100, Multi-phase = 70, One-off = 30 |

**Threshold: Only pursue opportunities scoring ≥55.**

## Proposal Template

```
Subject: [Role] — [Key Differentiator]

Hi [Client Name],

I noticed your need for [specific requirement]. I've built [relevant experience] 
that directly applies here — specifically [concrete example].

My approach:
1. [Step 1 — shows understanding of the problem]
2. [Step 2 — shows technical depth]
3. [Step 3 — shows delivery mindset]

Timeline: [realistic estimate]
Rate: [competitive but profitable]

Happy to discuss further or share relevant work samples.
```

## Workflow

1. **Scan** — Query each platform for new listings matching our keywords
2. **Filter** — Remove duplicates, already-applied, and below-threshold
3. **Score** — Calculate composite score using the criteria table
4. **Rank** — Sort by score descending
5. **Report** — Send top 5 opportunities to Telegram with scores and links
6. **Apply** — For pre-approved job types (score ≥75), draft and submit proposals
7. **Track** — Log each application: platform, job_id, score, proposal_sent, outcome

## Revenue Tracking Format

```json
{
  "job_id": "upwork_12345",
  "platform": "upwork",
  "title": "Build REST API for DeFi dashboard",
  "score": 82,
  "status": "applied|accepted|completed|paid",
  "applied_at": "2026-03-14T10:00:00Z",
  "amount_usd": 500,
  "hours_spent": 4,
  "effective_rate": 125,
  "notes": "Client wants ongoing work — schedule follow-up"
}
```

## Escalation Rules

- **Score ≥75**: Auto-apply with template proposal (notify on Telegram)
- **Score 55-74**: Send to Telegram for human review before applying
- **Score <55**: Log and skip
- **Any job requiring >$50 upfront cost**: Require human approval
- **Any job involving private key access**: REJECT immediately

## Learning

After each completed job, record:
- Actual time vs estimated time
- Client satisfaction / review score
- Effective hourly rate achieved
- Platform-specific lessons (e.g., "Upwork prefers video intros")

Update scoring weights quarterly based on which factors best predicted success.
