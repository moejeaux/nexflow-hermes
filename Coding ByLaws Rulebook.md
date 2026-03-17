AI Coding Bylaws (NexFlow Edition)

“These rules govern how Cursor is used on NexFlow; breaking them is considered a bug in the process.”

0. Purpose

Cursor exists to move the NexFlow codebase forward, not to brainstorm endlessly or drain my card. Every session must produce concrete repo changes.

1. Budget & Billing
1.1. I will set a monthly Cursor budget:

Pro subscription + max $0 in overages.

1.2. If projected or actual spend for the month exceeds this budget, I must:

Pause premium usage for the rest of the month or

Explicitly decide to raise the budget and write down the new number.

1.3. I will review Cursor usage once per week:

Identify top 10% most expensive days or sessions.

For each, note at least one change to avoid similar waste (e.g., stay on Auto, narrow context, fewer retries).​

2. Models
2.1. Default model is Auto.

I use Auto for: normal feature work, small fixes, tests, docs, and basic refactors.​

2.2. I may switch to a premium model (GPT‑4‑class / Claude‑class) only when:

Doing architecture or system design that affects multiple services.

Performing large or risky refactors.

Working on security‑sensitive, financial, or critical infra code.

2.3. When I switch to a premium model:

I state the reason in the prompt: Reason for premium: <short reason>.

I do not keep premium on for routine follow‑ups; I drop back to Auto when the critical part is done.

3. Scope: What Cursor Is Allowed To Do
3.1. Cursor is for repo‑bound work only:

Implementing features in NexFlow, EasePay, CommerceCast, SMF, etc.

Refactoring, debugging, writing tests.

Editing infra: Docker, Terraform, CI, deployment configs.​

3.2. Cursor is not for:

Long ideation, strategy, business plans, or “talk therapy”.

Market research, writing email copy, docs not tightly tied to the repo.

Random experiments that don’t live in the NexFlow ecosystem.

Those happen here first; Cursor gets distilled instructions, not raw thinking.

4. Context & Safety
4.1. Before using Agent / big actions, I will:

Specify exact files or folders (via @ and explicit file lists) whenever possible.

Avoid giving it full‑repo carte blanche unless the task truly requires global change.​

4.2. For large edits:

I require Cursor to show a plan first (Step 1/2/3…).

I skim the plan; if anything feels off, I correct the plan before it edits.

4.3. After large edits, I:

Run tests or basic checks (build, lint, unit tests).

If the change is non‑trivial, I ask Cursor for a short “diff summary” so I know what actually changed.

5. Session Rules
5.1. Every Cursor session starts with a goal sentence at the top of the prompt:

Goal: crete outcome with acceptance criteria>.

5.2. If I’m more than 30 minutes into a session with no commit or clear progress:

I stop, summarize what happened, and ask:

“Did I give Cursor a clear enough goal?”

“Is this actually a design/strategy question I should handle outside Cursor first?”

5.3. I commit early and often:

No session should produce a huge uncommitted diff that I don’t understand.

When in doubt, I break work into smaller goals and separate commits.

6. Personal Conduct
6.1. I do not chase “perfect prompts” inside Cursor.

I give it clear, direct instructions, then iterate on code, not on prompt poetry.

6.2. I do not let Cursor run endless retries to “make it perfect.”

If something fails twice in a row, I stop and rethink the approach or ask for help here.

6.3. Cursor is a power tool, not a slot machine.

If my behavior starts to feel like gambling with credits, I step away and reset.