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
- Resume: `python3 orchestrator.py --resume <run_dir_name> --start-round N --rounds M`
- List runs: `ls runs/`
- Run all tests: `python3 -m pytest test_runner.py -v`
- Run single test: `python3 -m pytest test_runner.py -v -k "test_name"`
- Targeted agent test: `python3 test_real_agent.py`

## Key files
- `orchestrator.py` — main experiment runner
- `test_runner.py` — pytest suite for CLI integration (stream-json parsing, tool restriction, sandbox)
- `test_real_agent.py` — reproduces real agent conditions (long prompts, tool use, result capture)
- `sandbox-settings.json` — bubblewrap filesystem isolation config
- `workspace/` — agents' shared workspace (gitignored, has its own git repo)
- `logs/` — per-turn logs (.md) and full event streams (.jsonl)

## Workflow rules

### Test before running experiments
IMPORTANT: Always validate changes with targeted cheap tests (haiku, low effort) before running expensive multi-agent experiments. Write a pytest test that reproduces the exact conditions, verify it passes, then run the experiment. Never re-run the full test suite to check one hypothesis — use `-k` to select the specific test.

### Don't destroy run data
Each experiment creates a timestamped directory under `runs/`. NEVER delete run directories without asking. Use `--resume <dir>` to continue a previous run.

### Iterate on prompts, not machinery
Most behavior changes come from prompt edits, not new code. Prefer adjusting system prompts and shared context over adding orchestrator features. Keep the orchestrator simple.

### Claude CLI flags
- `--disallowedTools` is the correct flag for tool restriction (works with `--dangerously-skip-permissions`)
- `--tools` and `--allowedTools` do NOT reliably restrict tools
- `--output-format stream-json` requires `--verbose`
- Pipe prompts via stdin to `claude -p` (avoid long CLI arguments)
- Use `readline()` loop, not `for line in proc.stdout` (avoids pipe buffering)

### Commit discipline
- Experiment workspace commits use agent summary as message: `[Agent] RN: first line of summary`
- Orchestrator commits to parent repo are separate
- Never commit on behalf of the user without being asked
