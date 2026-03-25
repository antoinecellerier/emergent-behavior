# Lessons Learned

Observations from building and iterating on the multi-agent orchestrator.

## Test Before You Run

The single most important workflow rule. Multi-agent experiments cost minutes and tokens per run. Every bug we found in production (empty results, wrong CLI flags, budget cutoff) could have been caught with a $0.01 haiku test.

Pattern: write a pytest test reproducing the exact conditions (same flags, same prompt structure, same sandbox settings), verify it passes with haiku/low effort, then run the real experiment.

Anti-pattern: making a code change and immediately running a full 4-agent experiment to see if it works.

## Prompts Over Machinery

Most behavioral improvements came from prompt edits, not code changes:

- Architect over-creating files -> prompt: "Do NOT create .py files. Trust your team."
- Agents not disagreeing -> prompt: "Make your case with a concrete alternative."
- Missing perspectives -> prompt: "Reflect: what expertise is the team missing?"
- Facilitator overstepping -> removed the directive file entirely

Code changes were needed for infrastructure (streaming, sandboxing, archival) but not for agent behavior.

## LLMs Default to Agreement

Without explicit permission to disagree, agents silently accepted every proposal. Adding "The team's first idea isn't always the best one. Disagree constructively." to the ground rules produced immediate results — agents started flagging real conflicts (file naming mismatches, architectural concerns).

This is a fundamental property of LLM-based agents and must be designed around.

## The Facilitator Trap

Every responsibility given to the Facilitator escalated:
- "Flag communication gaps" -> assigned tasks with effort estimates
- "Surface disagreements" -> made design decisions
- "Write a summary" -> wrote a project management report with code

The solution was radical simplification: the Facilitator only writes a factual summary and handles explicit agent requests for roster changes. No directives, no guidance, no recommendations.

Haiku was particularly bad at following negative constraints. Switching to sonnet helped, but removing the temptation (the directives file) was the real fix.

## Don't Mention What Not To Do

Telling the Facilitator "Do NOT write TEAM_DIRECTIVES.md" introduced the concept. Better to never mention it — the agent can't try to create something it doesn't know exists.

## Budget Caps Cause Worse Problems

`--max-budget-usd 0.10` cut agents off before they could produce their text summary, causing empty results and no message board posts. The root cause: sonnet with tool use easily exceeds $0.10 in a single turn.

Removed the budget cap entirely. Agents self-regulate via the prompt ("aim for ~15 tool calls max").

## Stream-JSON Parsing

`--output-format text` buffers everything until the agent finishes — no live progress. `--output-format stream-json` (requires `--verbose`) gives real-time events but needs careful parsing:

- Use `readline()` in a loop, not `for line in proc.stdout` (pipe buffering)
- Tool use events are in `assistant` messages, content is an array of blocks
- The `result` event contains the final text
- Budget exceeded gives `subtype: "error_max_budget_usd"` with empty `result`

## Pipe Prompts via Stdin

Passing long prompts as `-p "..."` CLI arguments works but makes `ps aux` output unreadable and risks hitting arg length limits. Piping via stdin is cleaner:
```python
proc = subprocess.Popen(["claude", "-p", ...], stdin=PIPE)
proc.stdin.write(prompt)
proc.stdin.close()
```

## Sandbox Layers Don't Overlap

Bubblewrap sandbox only wraps Bash commands. Built-in Read/Write/Edit tools bypass it entirely. Need a PreToolUse hook for those. The two layers are complementary, not redundant.

Also: `--dangerously-skip-permissions` bypasses the sandbox entirely. Use `--permission-mode bypassPermissions` instead — it skips interactive prompts while respecting sandbox boundaries.

## Permission Rule Precedence

In Claude Code's permission system, deny ALWAYS wins over allow, regardless of path specificity. You cannot do "deny ~, allow ~/project" — the deny blocks the project too.

For the sandbox filesystem layer (bubblewrap), the opposite is true: allow overrides deny.

This asymmetry means you need different strategies for each layer.

## Message Board Grows Fast

1,885 lines after 3 rounds + planning. Without archival, agents spend increasing time reading stale discussions. The fix: archive old rounds, keep current round at full fidelity, provide a summary for context.

Critical: don't archive the current round's messages before all agents have read them. The Facilitator runs after the last agent in a round, so archival should only move PREVIOUS rounds to the archive.

## Effort Level Matters for Speed, Not Quality

Switching Architect and Engine from `high` to `medium` effort cut turn times from 2-12 minutes to under 2 minutes with no observable quality loss. `medium` is sufficient for code writing tasks.

## Workspace Isolation Prevents Accidents

Timestamped run directories (`runs/<YYYYMMDD_HHMMSS>/`) prevent accidental deletion of previous experiment results. The old approach of a single `workspace/` directory at the project root led to `rm -rf workspace/` before each run, which destroyed valuable data.
