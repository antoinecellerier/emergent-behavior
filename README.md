# Emergent Behavior

A multi-agent orchestrator that spawns [Claude Code](https://docs.anthropic.com/en/docs/claude-code) agents to collaboratively build software. The goal is emergent collaboration — agents self-organize through a shared message board, recruit new teammates, and negotiate design decisions, with minimal top-down control.

The default objective is building a 3D terminal FPS, but agent configs can target any project.

## How it works

The orchestrator runs agents in sequential turns within rounds:

1. **Planning rounds** — agents discuss and design (code tools blocked)
2. **Implementation rounds** — agents write code in a shared git workspace
3. **Facilitator** — a meta-agent that summarizes the message board between rounds and handles roster changes

Agents communicate through `MESSAGE_BOARD.md` in the workspace and implicitly through code and git history. They can recruit new agents, request turn order changes, and disagree with each other's proposals.

```
orchestrator.py
  │
  ├── Planning rounds (Write/Edit/Bash blocked)
  │     Agent1 → Agent2 → ... → AgentN
  │     [Facilitator summarizes between rounds]
  │
  └── Implementation rounds
        Agent1 → Agent2 → ... → AgentN
        [Facilitator summarizes between rounds]
```

## Quick start

Requires [Claude Code](https://docs.anthropic.com/en/docs/claude-code) (`claude` CLI) installed and authenticated.

```bash
# Run an experiment (3 implementation rounds, 3 planning rounds)
python3 orchestrator.py --rounds 3

# Use a different team config
python3 orchestrator.py --config publisher --rounds 3

# Resume a previous run
python3 orchestrator.py --resume 20260328_080743 --rounds 2

# List available team configs
python3 orchestrator.py --list-configs
```

Each run creates `runs/<timestamp>/` with the workspace, logs, and agent roster.

## Agent configurations

Team rosters live in `agents/*.json`:

| Config | Description |
|--------|-------------|
| `default` | Architect, Engine, Gameplay, Reviewer — the standard 4-agent team |
| `minimal` | Engine + Reviewer only |
| `game-designer` | Solo designer that must recruit its own team |
| `publisher` | Solo publisher that must assemble a dev team |
| `publisher-terminal-game` | Publisher with a terminal game brief |
| `publisher-novel-terminal-game` | Publisher pushing for a novel terminal game |

## Sandbox

Agents run in a two-layer sandbox:

- **Bubblewrap** isolates Bash commands (no network, no filesystem access outside workspace)
- **PreToolUse hook** restricts Read/Write/Edit/Glob/Grep to the workspace directory

See [docs/security.md](docs/security.md) for the full threat model.

## Documentation

- [Architecture](docs/architecture.md) — system design, prompt structure, CLI flags
- [Agent Behavior](docs/agent-behavior.md) — prompt design, emergent patterns, the Facilitator problem
- [Security](docs/security.md) — threat model, sandbox layers
- [Lessons Learned](docs/lessons-learned.md) — practical insights from iterating on the system

## Tests

```bash
python3 -m pytest test_runner.py test_sandbox.py -v
```
