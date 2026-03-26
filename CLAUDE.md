# Emergent Behavior — Multi-Agent Experiment

## What this project is
An orchestrator (`orchestrator.py`) that spawns multiple Claude Code agents (`claude -p`) to collaboratively build a 3D terminal FPS. The goal is emergent collaboration, not just a working game.

## Environment
- `python3` (not `python`)
- No `sudo` access
- `bubblewrap` and `socat` available for sandboxing
- pytest installed via apt: `python3 -m pytest test_runner.py -v`

## Commands
- New experiment: `python3 orchestrator.py --rounds 3` (creates `runs/<timestamp>/`)
- Resume: `python3 orchestrator.py --resume <run_dir_name> --rounds M`
- List runs: `ls runs/`
- Run all tests: `python3 -m pytest test_runner.py -v`
- Run single test: `python3 -m pytest test_runner.py -v -k "test_name"`
- Targeted agent test: `python3 test_real_agent.py`

## Key files
- `orchestrator.py` — main experiment runner (setup + main loop)
- `prompts.py` — all agent and facilitator prompts (most frequently edited)
- `agents.py` — claude subprocess, streaming, git helpers, agent turns
- `board.py` — message board append/archive logic
- `test_runner.py` — pytest suite for CLI integration (stream-json parsing, tool restriction, sandbox)
- `test_real_agent.py` — reproduces real agent conditions (long prompts, tool use, result capture)
- `test_sandbox.py` — pytest tests for the sandbox hook (read/write blocking, path traversal, archival)
- `sandbox-settings.json` — bubblewrap filesystem isolation config
- `.claude/hooks/sandbox-read.sh` — default-deny file access hook
- `workspace/` — agents' shared workspace (gitignored, has its own git repo)
- `logs/` — per-turn logs (.md) and full event streams (.jsonl)

## Documentation
- `docs/architecture.md` — system design, sandbox model, stream processing, CLI flags
- `docs/agent-behavior.md` — prompt design, emergent patterns, Facilitator evolution
- `docs/security.md` — threat model, defense layers, security review findings
- `docs/lessons-learned.md` — practical insights from iterating on the system

## Workflow rules

### Test before running experiments
IMPORTANT: Always validate changes with targeted cheap tests (haiku, low effort) before running expensive multi-agent experiments. Write a pytest test that reproduces the exact conditions, verify it passes, then run the experiment. Never re-run the full test suite to check one hypothesis — use `-k` to select the specific test.

### Don't destroy run data
Each experiment creates a timestamped directory under `runs/`. NEVER delete run directories without asking. Use `--resume <dir>` to continue a previous run.

### Iterate on prompts, not machinery
Most behavior changes come from prompt edits, not new code. Prefer adjusting system prompts and shared context over adding orchestrator features. Keep the orchestrator simple.

### Keep docs current
When making significant changes (new features, architectural changes, security updates, new lessons), update the relevant docs/ file in the same commit or immediately after. Don't let docs drift — 38 commits behind is too many.

### Claude CLI flags
- `--disallowedTools` is the correct flag for tool restriction (works with `--permission-mode bypassPermissions`)
- `--tools` and `--allowedTools` do NOT reliably restrict tools
- `--output-format stream-json` requires `--verbose`
- Pipe prompts via stdin to `claude -p` (avoid long CLI arguments)
- Use `select()` + `readline()`, not `for line in proc.stdout` (avoids pipe buffering and deadlocks)

### Commit discipline
- **Always commit after completing a functional change** — don't leave work uncommitted
- Experiment workspace commits use agent summary as message: `[Agent] RN: first line of summary`
- Orchestrator commits to parent repo are separate
- Never commit on behalf of the user without being asked
