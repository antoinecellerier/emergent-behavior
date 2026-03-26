# Agent Behavior Design

## Design Philosophy

The experiment optimizes for emergent collaboration, not just a working game. The orchestrator provides minimal structure -- turn order, a shared workspace, and a message board -- then lets agents self-organize through prompt design rather than code enforcement.

Key principle: **prompt over machinery**. Most behavioral changes come from editing system prompts, not adding orchestrator features.

## Agent Rosters

Agents are configured via JSON files in `agents/`, selected with `--config`. The default team:

| Agent | Blocked Tools | Role |
|-------|--------------|------|
| Architect | Bash | Writes ARCHITECTURE.md only. No skeleton code. |
| Engine | (none extra) | Raycasting, rendering, terminal I/O. Full Bash access. |
| Gameplay | (none extra) | Controls, enemies, levels, game loop. Builds on Engine. |
| Reviewer | (none extra) | Reads all code, runs the game, fixes bugs, identifies gaps. |

All agents use sonnet/medium. All share `ALWAYS_BLOCKED` tools: NotebookEdit, WebFetch, WebSearch.

Alternative configs include `minimal` (Engine + Reviewer), `game-designer` (solo designer that must recruit), and `publisher` (solo publisher that must assemble a team).

## No Language Prescription

The shared context tells agents: "The scope, features, language, and technical approach are up to the team." Agents choose Python, Go, Rust, C, Node, or whatever they decide fits. The Engine prompt says "Choose the best language, tools, and libraries for the job."

## Behavioral Prompts

### Role Ownership (shared context)

> "Your role description was written by a teammate. It reflects their view of what you should do -- but you own your domain. If their technical assumptions are wrong or their approach is suboptimal, push back on the message board."

This applies to all agents, including dynamically recruited ones. A recruited agent's role_prompt comes from the requesting agent (via the Facilitator), but the new agent is explicitly told it can challenge those instructions.

### Rule 2 -- Permission to Disagree

> "If you believe a technical approach is suboptimal, make your case on the message board with a concrete alternative. The team's first idea isn't always the best one."

LLMs default to agreement. Without this, agents silently implement whatever the first agent proposes. This rule produced real disagreements -- e.g., Gameplay flagging file naming conflicts between Architect and Engine specs.

### Rule 8 -- Reflection and Recruitment

> "Before ending your turn, briefly reflect: what perspective or expertise is the team missing?"

Forces agents to look outward. Agents have three options when they identify a gap:
- **(a)** Solve it themselves this turn
- **(b)** Request a new specialist: "We need a [Role] agent" with a description of outcomes and team fit
- **(c)** Propose a concrete next step for an existing teammate

The rule explicitly prohibits flagging a gap without acting, and prohibits repeating an observation from a previous round without taking action.

### Rule 9 -- Turn Order Requests

> "If your turn would be more productive at a different point in the round (e.g., you need another agent's output first), say 'I should run after [Agent]' on the message board."

Agents can self-organize their execution order. The Facilitator picks up these requests and writes `REORDER_AGENTS.json`.

### TEAM_PRACTICES.md -- Institutional Memory

Agents may write to `TEAM_PRACTICES.md` to document working methods the team discovers (testing approaches, useful patterns, tools built). This file persists across rounds and helps the team accumulate knowledge.

## The Architect Problem

Early experiments showed the Architect creating 12+ skeleton files in Round 1, leaving no room for other agents to make design decisions. Fix: the Architect prompt says "Do NOT create implementation files or skeleton code. Your deliverable is ARCHITECTURE.md, not .py files. Trust your team."

This shifted the dynamic from "Architect dictates, others implement" to genuine collaboration where Engine and Gameplay make their own structural decisions within the architectural framework.

The Architect also has Bash blocked, reinforcing that it produces documentation, not code.

## The Facilitator Problem

The Facilitator went through several iterations:

1. **v1 (haiku, TEAM_DIRECTIVES.md):** Wrote specific code, assigned tasks with effort estimates, set priorities with urgency labels. Killed emergent behavior.

2. **v2 (haiku, tightened prompt):** Still overstepped. Haiku couldn't follow complex negative constraints. Invented conflicts agents hadn't raised.

3. **v3 (sonnet, summary only):** Removed TEAM_DIRECTIVES.md entirely. Facilitator only writes MESSAGE_BOARD_SUMMARY.md (factual recap) and handles roster/order changes on explicit agent request. No directive authority.

Key insight: every "flag communication gaps" responsibility escalated into task assignment. The summary format -- "decisions made, open questions, who's working on what" -- surfaces gaps implicitly without giving the Facilitator a vehicle to overstep.

The Facilitator is also told that agents take turns sequentially, so a question asked earlier in the same round is not "unanswered" -- the other agent simply hasn't had their turn yet.

## Planning Rounds

Configurable planning rounds (default 3) before implementation, with Write/Edit/Bash blocked:

- **Round 1:** Propose approaches, identify dependencies
- **Round 2 (devil's advocate):** "Challenge the current plan: what's the weakest technical decision so far? What would you do differently? Push back on anything you accepted too easily in round 1."
- **Round 3:** Converge -- each agent states exactly what they'll build in Round 1

Newly recruited agents joining mid-planning get a different prompt: "You just joined the team. Read the discussion so far with fresh eyes. React to the plan, flag anything that concerns you."

The Facilitator runs between each planning round (not just implementation rounds), summarizing and handling any roster changes that arise during planning.

## Planning Round Archival

After each Facilitator turn during planning, old planning messages are archived. The `archive_message_board` function accepts `keep_plan=N` to keep only messages from the current planning round, moving older planning messages to the archive. When implementation starts, all planning messages are archived.

## Communication Fidelity

Messages from the current round stay on the board at full text. Only messages from previous rounds get archived and summarized. This prevents a Reviewer's detailed bug report from being reduced to a one-line summary before other agents have read it.

Priority order: recent messages > summary > archive.

## Dynamic Agent Lifecycle

New agents are inserted at the front of the turn order so they go first in the next round and can establish context before other agents build on their work.

Dynamically recruited agents:
- Inherit sonnet/medium model/effort (locked, not configurable by the recruiting agent)
- Have their role_prompt capped at 2000 characters
- Inherit `ALWAYS_BLOCKED` tools
- Receive the same shared context as all agents, including the "you own your domain" language
- Get assigned colors from a rotating pool of 10 terminal colors

## What Emergence Looks Like

Observed emergent behaviors from successful runs:
- Agents developing a shared vocabulary through the message board
- The Reviewer taking on a "team glue" role -- reading everything, fixing integration issues
- Gameplay referencing Engine's specific function signatures after reading the code (not just the architecture doc)
- Agents naturally requesting fewer changes in later rounds as the codebase stabilizes
- Cross-agent bug reports: Reviewer finds a bug, explains it on the board, Gameplay fixes it next round
- Solo configs (game-designer, publisher) bootstrapping full teams through recruitment
