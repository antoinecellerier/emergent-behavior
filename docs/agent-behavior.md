# Agent Behavior Design

## Design Philosophy

The experiment optimizes for emergent collaboration, not just a working game. The orchestrator provides minimal structure — turn order, a shared workspace, and a message board — then lets agents self-organize through prompt design rather than code enforcement.

Key principle: **prompt over machinery**. Most behavioral changes come from editing system prompts, not adding orchestrator features.

## The Four Core Agents

| Agent | Model | Blocked Tools | Role |
|-------|-------|--------------|------|
| Architect | sonnet/medium | Bash | Writes ARCHITECTURE.md only. No skeleton code — trusts the team to implement. |
| Engine | sonnet/medium | (none extra) | Raycasting, rendering, terminal I/O. Has full Bash access to test code. |
| Gameplay | sonnet/medium | (none extra) | Controls, enemies, levels, game loop. Builds on Engine's interfaces. |
| Reviewer | sonnet/medium | (none extra) | Reads all code, runs the game, fixes bugs, identifies missing perspectives. |

All agents share `ALWAYS_BLOCKED` tools: NotebookEdit, WebFetch, WebSearch.

## Behavioral Prompts That Matter

### Rule 2 — Permission to Disagree
> "If you believe a technical approach is suboptimal, make your case on the message board with a concrete alternative. The team's first idea isn't always the best one."

LLMs default to agreement. Without explicit permission to disagree, agents silently implement whatever the Architect proposes, even if they have better ideas. This rule produced real disagreements in testing — e.g., Gameplay flagging file naming conflicts between Architect and Engine specs.

### Rule 8 — Reflection on Missing Perspectives
> "Before ending your turn, briefly reflect: what perspective or expertise is the team missing?"

Forces agents to look outward rather than just executing their task. Combined with the Reviewer's explicit gap-finding mandate, this surfaces concerns like accessibility, terminal compatibility, and error handling that no single agent would notice on their own.

### Agent Recruitment
Rule 8 also tells agents they can request new team members: "say 'We need a [Role] agent' and describe what they would do." The Facilitator picks up explicit requests and writes `NEW_AGENT.json`. This keeps recruitment agent-driven, not Facilitator-driven.

## The Architect Problem

Early experiments showed the Architect creating 12+ skeleton files in Round 1, leaving no room for other agents to make design decisions. Fix: the Architect prompt now says "Do NOT create implementation files or skeleton code. Your deliverable is ARCHITECTURE.md, not .py files. Trust your team."

This was one of the most impactful prompt changes — it shifted the dynamic from "Architect dictates, others implement" to genuine collaboration where Engine and Gameplay make their own structural decisions within the architectural framework.

## The Facilitator Problem

The Facilitator went through several iterations:

1. **v1 (haiku, TEAM_DIRECTIVES.md):** Wrote specific code, assigned tasks with effort estimates, set priorities with urgency labels. Essentially a project manager, which killed emergent behavior.

2. **v2 (haiku, tightened prompt):** Still overstepped. Haiku couldn't follow complex negative constraints ("do NOT write code"). Invented conflicts that agents hadn't raised. Wrote 24 tool calls spelunking through git internals.

3. **v3 (sonnet, summary only):** Removed TEAM_DIRECTIVES.md entirely. Facilitator only writes MESSAGE_BOARD_SUMMARY.md (factual recap) and handles agent roster changes on explicit request. No directive authority.

Key insight: every "flag communication gaps" responsibility eventually escalated into task assignment. The summary format — "decisions made, open questions, who's working on what" — surfaces gaps implicitly without giving the Facilitator a vehicle to overstep.

## Planning Rounds

3 planning rounds before implementation, with Write/Edit/Bash blocked:

- **Round 1:** Agents propose approaches, identify dependencies
- **Round 2:** React to proposals, flag disagreements
- **Round 3:** Converge — each agent states exactly what they'll build in Round 1

The Facilitator runs between planning rounds 1-2 and 2-3 to summarize (not after the last one, since implementation starts immediately).

Planning rounds produced measurably better coordination than jumping straight into coding. Agents asked direct questions ("What map format should I expect?"), flagged conflicts early, and arrived at Round 1 with a shared understanding.

## Communication Fidelity

Messages from the current round stay on the board at full text. Only messages from previous rounds get archived and summarized. This prevents the Reviewer's detailed bug report from being reduced to a one-line summary before other agents have read it.

Agents are told to prioritize recent messages over the summary: "If the summary contradicts a recent message, trust the recent message."

## Turn Order Awareness

The Facilitator needed to understand that agents go sequentially (Architect -> Engine -> Gameplay -> Reviewer). Without this context, it flagged "unanswered questions" that were simply from agents earlier in the same round who hadn't had their turn yet.

## What Emergence Looks Like

Observed emergent behaviors from successful runs:
- Agents developing a shared vocabulary through the message board
- The Reviewer taking on a "team glue" role — reading everything, fixing integration issues
- Gameplay referencing Engine's specific function signatures after reading the code (not just the architecture doc)
- Agents naturally requesting fewer changes in later rounds as the codebase stabilizes
- Cross-agent bug reports: Reviewer finds a bug, explains it on the board, Gameplay fixes it next round
