# Architecture

## Overview

The orchestrator (`orchestrator.py`) spawns Claude Code agents sequentially via `claude -p`, each working in a shared workspace directory. Agents communicate through a message board file and observe each other's work through the filesystem and git history.

```
orchestrator.py
  |
  |-- Planning rounds (no code, discussion only)
  |     Architect -> Engine -> Gameplay -> Reviewer
  |     [Facilitator summarizes between rounds]
  |
  |-- Implementation rounds
  |     Architect -> Engine -> Gameplay -> Reviewer
  |     [Facilitator summarizes every N rounds]
  |
  v
runs/<timestamp>/
  workspace/     # shared git repo where agents build the game
  logs/          # per-turn .md summaries + .jsonl event streams
```

## Agent Lifecycle

Each agent turn is a single `claude -p` invocation:

1. Orchestrator builds a prompt with workspace state (file tree, git log)
2. Prompt is piped via stdin to `claude -p` (avoids CLI arg length limits)
3. Agent reads files, writes code, and produces a text summary
4. Orchestrator captures the summary from the stream-json `result` event
5. Summary is appended to `MESSAGE_BOARD.md`
6. All workspace changes are committed to git with the summary as message
7. Next agent's turn begins

## Communication Model

Agents communicate through three channels, in priority order:

1. **MESSAGE_BOARD.md** — current round messages at full fidelity (primary)
2. **MESSAGE_BOARD_SUMMARY.md** — factual summary of older rounds (written by Facilitator)
3. **MESSAGE_BOARD_ARCHIVE.md** — complete history if exact wording is needed

Agents also communicate implicitly through the code and docs they write in the workspace. The git history provides a log of who changed what.

The Facilitator is a meta-agent (sonnet) that runs between rounds. It reads the board and writes a factual summary. It does NOT direct agents — it only summarizes and handles agent roster changes when explicitly requested.

## Sandbox Model

Two-layer isolation:

**Layer 1 — Bubblewrap (Bash commands):**
- Filesystem: `allowRead: ["."]`, `denyRead: ["~"]`
- Network: `allowedDomains: []`, `allowManagedDomainsOnly: true` (blocks all network)
- `allowUnsandboxedCommands: false`

**Layer 2 — PreToolUse hook (Read/Write/Edit/Glob/Grep):**
- Default-deny: blocks all file access outside allowed prefixes
- Allowed: workspace directory (via `SANDBOX_ALLOWED_DIR` env var), `/usr`, `/tmp`, `/proc`, `/etc/timezone`
- Path traversal protection via `realpath -m`, fail-closed if `realpath` unavailable
- Per-tool field extraction (no fragile fallback chains)

**Permission mode:** `bypassPermissions` (no interactive prompts, respects sandbox boundaries)

## Run Isolation

Each experiment creates `runs/<YYYYMMDD_HHMMSS>/` containing:
- `workspace/` — a self-contained git repo
- `logs/` — per-turn markdown summaries, JSONL event streams, generated sandbox settings

Runs are never deleted automatically. Resume with `--resume <dir_name>`.

## Stream Processing

Agents run with `--output-format stream-json --verbose`. The orchestrator reads events line-by-line via `readline()` (not `for line in proc.stdout` which buffers on pipes):

- `assistant` events with `tool_use` blocks: logged as `[ToolName]` for live progress
- `assistant` events with `text` blocks: collected as fallback if result is empty
- `result` event: primary source of agent's final text output
- Non-success subtypes (e.g., `error_max_budget_usd`): logged as warnings

## Key CLI Flags

| Flag | Purpose |
|------|---------|
| `--permission-mode bypassPermissions` | No interactive prompts, respects sandbox |
| `--disallowedTools X,Y,Z` | Blocks specific tools (works with bypassPermissions) |
| `--output-format stream-json` | Real-time event streaming (requires `--verbose`) |
| `--settings <path>` | Sandbox config + hooks |
| `--no-session-persistence` | Don't save agent sessions to disk |

Note: `--tools` and `--allowedTools` do NOT reliably restrict tools when `bypassPermissions` is active. Use `--disallowedTools` instead.
