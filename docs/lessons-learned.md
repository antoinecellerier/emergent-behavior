# Lessons Learned

Observations from building and iterating on the multi-agent orchestrator.

## Test Before You Run

The single most important workflow rule. Multi-agent experiments cost minutes and tokens per run. Every bug found in production (empty results, wrong CLI flags, budget cutoff) could have been caught with a cheap haiku test.

Pattern: write a pytest test reproducing the exact conditions (same flags, same prompt structure, same sandbox settings), verify it passes with haiku/low effort, then run the real experiment.

Anti-pattern: making a code change and immediately running a full multi-agent experiment to see if it works.

## Prompts Over Machinery

Most behavioral improvements came from prompt edits, not code changes:

- Architect over-creating files -> prompt: "Do NOT create .py files. Trust your team."
- Agents not disagreeing -> prompt: "Make your case with a concrete alternative."
- Missing perspectives -> prompt: "Reflect: what expertise is the team missing?"
- Facilitator overstepping -> removed the directive file entirely
- Agents passively accepting in planning -> devil's advocate prompt in round 2
- Recruited agents blindly following instructions -> "you own your domain, push back"

Code changes were needed for infrastructure (streaming, sandboxing, archival, module split, agent configs) but not for agent behavior.

## Split the Orchestrator Early

The original monolithic `orchestrator.py` became hard to iterate on. Splitting into four modules (`orchestrator.py`, `agents.py`, `board.py`, `prompts.py`) made it possible to edit prompts without touching agent machinery, and vice versa. Agent configs moving to JSON files in `agents/` made it easy to experiment with different team compositions without code changes.

## LLMs Default to Agreement

Without explicit permission to disagree, agents silently accepted every proposal. Adding "The team's first idea isn't always the best one. Disagree constructively." to the ground rules produced immediate results -- agents started flagging real conflicts.

The devil's advocate prompt in planning round 2 ("Challenge the current plan: what's the weakest technical decision so far?") pushes agents past their tendency to converge too early.

This is a fundamental property of LLM-based agents and must be designed around.

## The Facilitator Trap

Every responsibility given to the Facilitator escalated:
- "Flag communication gaps" -> assigned tasks with effort estimates
- "Surface disagreements" -> made design decisions
- "Write a summary" -> wrote a project management report with code

The solution was radical simplification: the Facilitator only writes a factual summary and handles explicit agent requests for roster/order changes. No directives, no guidance, no recommendations.

Haiku was particularly bad at following negative constraints. Switching to sonnet helped, but removing the temptation (the directives file) was the real fix.

## Don't Mention What Not To Do

Telling the Facilitator "Do NOT write TEAM_DIRECTIVES.md" introduced the concept. Better to never mention it -- the agent can't try to create something it doesn't know exists.

## Let Agents Own Their Roles

Recruited agents receive a role_prompt written by a teammate (via the Facilitator). Early versions had agents blindly following these instructions. Adding "you own your domain -- push back if their technical assumptions are wrong" gave recruited agents agency to challenge suboptimal instructions rather than implementing them verbatim.

## Budget Caps Cause Worse Problems

`--max-budget-usd 0.10` cut agents off before they could produce their text summary, causing empty results and no message board posts. The root cause: sonnet with tool use easily exceeds $0.10 in a single turn.

Removed the budget cap entirely. Agents self-regulate via the prompt ("aim for ~15 tool calls max").

## Rate Limits Need Graceful Handling

Rate limits are detected via the `is_error` flag on the stream-json `result` event. When hit, the experiment pauses and prints a resume command. This avoids wasting tokens retrying against a limit and preserves all progress.

## Message Board Grows Fast

1,885 lines after 3 rounds + planning. Without archival, agents spend increasing time reading stale discussions. The fix: archive old rounds, keep current round at full fidelity, provide a summary for context.

Critical: don't archive the current round's messages before all agents have read them. The Facilitator runs after the last agent, so archival only moves previous rounds to the archive.

Planning round archival uses `keep_plan=N` to keep only the current planning round's messages. When implementation starts, all planning messages are archived.

## Ctrl-C Handling Is Tricky

Piping output through `tee` creates SIGPIPE issues when tee dies on Ctrl-C. Three fixes were needed:
- `signal.signal(signal.SIGPIPE, signal.SIG_DFL)` at module level
- `BrokenPipeError` catch in the `log()` function
- `KeyboardInterrupt` catch during subprocess readline, with a 30-second grace period

The two-press pattern (first Ctrl-C = graceful stop after current agent, second = force quit) gives the operator control without losing in-progress work.

## Stream-JSON Parsing

`--output-format text` buffers everything until the agent finishes -- no live progress. `--output-format stream-json` (requires `--verbose`) gives real-time events but needs careful parsing:

- Use `readline()` in a loop, not `for line in proc.stdout` (pipe buffering)
- Tool use events are in `assistant` messages, content is an array of blocks
- The `result` event contains the final text
- If `result` is empty, fall back to collected text blocks from `assistant` events

## Tool Hints Improve Observability

Showing `[Read]` or `[Edit]` during a turn isn't enough context. Adding tool-specific hints (Bash description fields, Edit old_string snippets, Read line ranges, Grep patterns) makes it possible to follow what an agent is doing without reading the full event stream.

## Pipe Prompts via Stdin

Passing long prompts as `-p "..."` CLI arguments makes `ps aux` output unreadable and risks hitting arg length limits. Piping via stdin is cleaner:
```python
proc = subprocess.Popen(["claude", "-p", ...], stdin=PIPE)
proc.stdin.write(prompt)
proc.stdin.close()
```

## Sandbox Layers Don't Overlap

Bubblewrap sandbox only wraps Bash commands. Built-in Read/Write/Edit tools bypass it entirely. Need a PreToolUse hook for those. The two layers are complementary, not redundant.

Also: `--dangerously-skip-permissions` bypasses the sandbox entirely. Use `--permission-mode bypassPermissions` instead -- it skips interactive prompts while respecting sandbox boundaries.

## Permission Rule Precedence

In Claude Code's permission system, deny ALWAYS wins over allow, regardless of path specificity. You cannot do "deny ~, allow ~/project" -- the deny blocks the project too.

For the sandbox filesystem layer (bubblewrap), the opposite is true: allow overrides deny.

This asymmetry means you need different strategies for each layer.

## Effort Level Matters for Speed, Not Quality

Switching from `high` to `medium` effort cut turn times from 2-12 minutes to under 2 minutes with no observable quality loss. `medium` is sufficient for code writing tasks.

## Workspace Isolation Prevents Accidents

Timestamped run directories (`runs/<YYYYMMDD_HHMMSS>/`) prevent accidental deletion of previous experiment results. The old approach of a single `workspace/` directory at the project root led to destroying valuable data.

## Agent Roster Persistence Enables Resume

Persisting the agent roster (including dynamically recruited agents) to `AGENT_ROSTER.json` at the run root was essential for resume support. Without it, resuming a run that had recruited new agents would lose those agents and their turn order.
