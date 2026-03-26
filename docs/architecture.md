# Architecture

## Overview

The system is split across four Python modules:

| Module | Responsibility |
|--------|---------------|
| `orchestrator.py` | CLI, run directory setup, main loop (planning + implementation rounds), graceful shutdown |
| `agents.py` | Claude subprocess invocation, stream-json parsing, git helpers, agent/facilitator turns, roster persistence, dynamic agent management |
| `board.py` | Message board init, append, archival (with round-aware filtering) |
| `prompts.py` | Agent config loading from JSON, shared context builder, facilitator system prompt, always-blocked tool list |

Agent rosters live in JSON files under `agents/` and are selected via `--config <name>`.

```
orchestrator.py
  |
  |-- Planning rounds (Write/Edit/Bash blocked, discussion only)
  |     Agent1 -> Agent2 -> ... -> AgentN
  |     [Facilitator summarizes + archives between rounds]
  |
  |-- Implementation rounds
  |     Agent1 -> Agent2 -> ... -> AgentN
  |     [Facilitator summarizes + archives every N rounds]
  |
  v
runs/<timestamp>/
  workspace/            # shared git repo where agents build the game
  logs/                 # per-turn .md summaries + .jsonl event streams
  AGENT_ROSTER.json     # persisted roster for resume (configs + active list + order)
  logs/sandbox-settings.json  # generated per-run sandbox settings with hooks
```

## Agent Configurations

Agent rosters are defined in `agents/*.json`. Each config has a description and a map of agent names to settings:

```json
{
  "description": "Full team: Architect, Engine, Gameplay, Reviewer",
  "agents": {
    "AgentName": {
      "model": "sonnet",
      "effort": "medium",
      "disallowed_tools": ["Bash"],
      "role_prompt": "You are the **AgentName**.\n\n..."
    }
  }
}
```

Available configs:
- `default` -- Architect, Engine, Gameplay, Reviewer (the standard 4-agent team)
- `minimal` -- Engine + Reviewer only, no architect
- `game-designer` -- solo GameDesigner that must recruit its own team
- `publisher` -- solo Publisher that must assemble a dev team

Select with `--config <name>` or list with `--list-configs`.

## Agent Lifecycle

Each agent turn is a single `claude -p` invocation:

1. Orchestrator builds a prompt with workspace state (file tree, git log, diff since agent's last turn)
2. Prompt is piped via stdin to `claude -p` (avoids CLI arg length limits)
3. Agent reads files, writes code, and produces a text summary
4. Orchestrator captures the summary from the stream-json `result` event
5. Summary is appended to `MESSAGE_BOARD.md`
6. All workspace changes are committed to git: `[Agent] RN: first line of summary`
7. Next agent's turn begins

## Communication Model

Agents communicate through three files, in priority order:

1. **MESSAGE_BOARD.md** -- current round messages at full fidelity (primary)
2. **MESSAGE_BOARD_SUMMARY.md** -- factual summary of older rounds (written by Facilitator)
3. **MESSAGE_BOARD_ARCHIVE.md** -- complete history if exact wording is needed

Agents are told: "If the summary contradicts a recent message, trust the recent message."

Agents also communicate implicitly through code, docs, and git history. They may write to **TEAM_PRACTICES.md** for institutional memory (working methods, patterns, tools discovered).

The Facilitator is a meta-agent (sonnet) that runs between rounds. It reads the board and writes a factual summary. It does NOT direct agents -- it only summarizes and handles agent roster changes when explicitly requested by agents.

## Dynamic Agent Management

Three JSON files trigger roster changes, processed after each Facilitator turn. The Facilitator writes them to the workspace; the orchestrator moves them to the run root before processing (so agents never see them):

| File | Format | Effect |
|------|--------|--------|
| `NEW_AGENT.json` | `{"name": "...", "role_prompt": "...", "requested_by": "..."}` or array | New agent added to front of turn order (runs first next round). Model/effort locked to sonnet/medium. Prompt capped at 2000 chars. Inherits `ALWAYS_BLOCKED` tools. Recruitment context appended to role_prompt. |
| `RETIRE_AGENT.json` | `{"name": "...", "reason": "..."}` or array | Agent removed from active list |
| `REORDER_AGENTS.json` | `["Agent1", "Agent2", ...]` | Turn order changed (must list all active agents) |

Files are deleted after processing. Order: reorder (against current roster) → new agents (appended) → retirements. Changes are committed and persisted to `AGENT_ROSTER.json` at the run root for resume support.

Dynamically recruited agents get colors from a rotating pool of 10 terminal colors.

## Sandbox Model

Two-layer isolation:

**Layer 1 -- Bubblewrap (Bash commands):**
- Filesystem: `allowRead: ["."]`, `denyRead: ["/home"]`, `allowWrite: ["."]`, `denyWrite: ["/home"]`
- Network: `allowedDomains: []`, `allowManagedDomainsOnly: true` (blocks all network)
- `allowUnsandboxedCommands: false`

**Layer 2 -- PreToolUse hook (Read/Write/Edit/Glob/Grep):**
- Default-deny: blocks all file access outside allowed prefixes
- Allowed: workspace directory (via `SANDBOX_ALLOWED_DIR` env var), `/usr`, `/tmp`, `/proc`, `/etc/timezone`, `/etc/localtime`
- Path traversal protection via `realpath -m`, fail-closed if `realpath` unavailable
- Per-tool field extraction (`file_path` for Read/Edit/Write, `path` for Glob/Grep)

**Layer 3 -- Context isolation:**
- `claudeMdExcludes` in `workspace/.claude/settings.local.json` blocks the orchestrator's CLAUDE.md and `.claude/` from leaking into agent context
- `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1` env var prevents agents from loading the user's auto-memory files

**Permission mode:** `bypassPermissions` (no interactive prompts, respects sandbox boundaries)

## Run Isolation

Each experiment creates `runs/<YYYYMMDD_HHMMSS>/` containing:
- `workspace/` -- a self-contained git repo
- `logs/` -- per-turn markdown summaries, JSONL event streams, generated sandbox settings
- `AGENT_ROSTER.json` -- persisted roster state for resume

Runs are never deleted automatically. Resume with `--resume <dir_name> --rounds N`.

## Resume

On resume (`--resume <dir>`):
1. `AGENT_ROSTER.json` is loaded to restore the full roster including dynamically recruited agents
2. Git log is parsed to detect the last complete round and any partially-completed round
3. If a round was interrupted mid-way, the remaining agents in that round go first
4. `--rounds N` means N additional rounds from the resume point

## Graceful Shutdown

- `SIGPIPE` set to `SIG_DFL` to prevent `BrokenPipeError` when piped through `tee`
- First Ctrl-C sets a flag; current agent finishes, then the experiment stops
- Second Ctrl-C force-quits
- `BrokenPipeError` caught in the `log()` function
- `KeyboardInterrupt` caught during subprocess readline; waits up to 30s for agent to finish

## Rate Limit Detection

The `result` event's `is_error` flag is checked. If true and the message contains "hit your limit", a `RateLimitError` is raised, pausing the experiment with a resume command printed to the terminal.

## Stream Processing

Agents run with `--output-format stream-json --verbose`. The orchestrator reads events via `select()` + `readline()` with a deadline timeout (prevents pipe deadlock if the subprocess hangs):

- `assistant` events with `tool_use` blocks: logged with tool hints (Bash descriptions, Edit snippets, Read ranges)
- `assistant` events with `text` blocks: collected as fallback if result is empty
- `result` event: primary source of agent's final text output
- Non-success subtypes (e.g., `error_max_budget_usd`): logged as warnings

Tool hints show contextual information per tool type:
- **Bash**: description field or first line of command
- **Edit**: file path + first line of old_string snippet
- **Read**: file path + line range if offset/limit specified
- **Grep**: pattern + path
- **Glob**: pattern
- **Agent**: subagent type + description

## CLI Flags

| Flag | Purpose |
|------|---------|
| `--rounds N` | Number of implementation rounds (default: 3) |
| `--resume <dir>` | Resume a previous run |
| `--config <name>` | Agent roster from `agents/` (default: `default`) |
| `--list-configs` | Show available agent configurations |
| `--no-facilitator` | Disable the Facilitator meta-agent |
| `--planning-rounds N` | Planning rounds before coding (default: 3, 0 to skip) |
| `--facilitator-every N` | Run Facilitator every N rounds (default: 1) |

Claude CLI flags used per agent invocation:

| Flag | Purpose |
|------|---------|
| `--permission-mode bypassPermissions` | No interactive prompts, respects sandbox |
| `--disallowedTools X,Y,Z` | Blocks specific tools (works with bypassPermissions) |
| `--output-format stream-json` | Real-time event streaming (requires `--verbose`) |
| `--settings <path>` | Sandbox config + hooks |
| `--no-session-persistence` | Don't save agent sessions to disk |

Note: `--tools` and `--allowedTools` do NOT reliably restrict tools when `bypassPermissions` is active. Use `--disallowedTools` instead.
