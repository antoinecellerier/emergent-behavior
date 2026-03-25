#!/usr/bin/env python3
"""
Multi-Agent Emergent Behavior Experiment
========================================
Agents collaboratively build a 3D first-person shooter in the terminal.

Usage:
    python orchestrator.py                  # Run with defaults (10 rounds)
    python orchestrator.py --rounds 5       # Custom round count
    python orchestrator.py --resume         # Resume from existing workspace
"""

import subprocess
import sys
import time
import argparse
from pathlib import Path
from datetime import datetime

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

WORKSPACE = Path(__file__).parent / "workspace"
LOGS_DIR = Path(__file__).parent / "logs"

COLORS = {
    "Architect":   "\033[1;34m",
    "Engine":      "\033[1;32m",
    "Gameplay":    "\033[1;33m",
    "Reviewer":    "\033[1;31m",
    "Facilitator": "\033[1;35m",
}
RESET = "\033[0m"
DIM   = "\033[2m"
BOLD  = "\033[1m"

# ---------------------------------------------------------------------------
# Shared context injected into every agent's system prompt
# ---------------------------------------------------------------------------

SHARED_CONTEXT = """\
You are part of a team of AI agents collaboratively building a 3D first-person \
shooter game that runs entirely in the terminal using ASCII/Unicode rendering.

## The Project
Build a playable FPS game in Python that:
- Uses raycasting to render a 3D perspective view in the terminal
- Has player movement (WASD + arrow keys for looking)
- Features at least one enemy type with basic AI
- Includes a simple map/level
- Runs at a reasonable frame rate in a standard terminal

## Your Team
- **Architect** — designs overall structure, makes technical decisions, writes specs
- **Engine** — implements raycasting, terminal rendering, performance
- **Gameplay** — implements controls, enemies, items, game loop, levels
- **Reviewer** — reviews code, tests the game, reports bugs, fixes small issues

## How You Communicate
- Read **MESSAGE_BOARD.md** at the start of every turn — it contains messages \
from the rest of the team.
- After your turn your spoken output will be appended to the message board \
automatically, so end with a concise summary of what you did and any open \
questions or requests for teammates.

## Ground Rules
1. Always read existing files before modifying them.
2. Build on existing work — never rewrite another agent's code without a \
compelling reason explained on the message board.
3. Keep changes focused on your role.
4. If you are blocked or need input from someone, say so clearly.
5. Write clean, working Python. Prefer the standard library where possible.\
"""

# ---------------------------------------------------------------------------
# Per-agent role prompts
# ---------------------------------------------------------------------------

AGENT_ROLES = {
    "Architect": """\
You are the **Architect**.

Priorities:
- Design the project structure and file layout.
- Write a short ARCHITECTURE.md when the project is new.
- Define interfaces/contracts between engine and gameplay code.
- Make technology decisions (rendering approach, data structures, etc.).
- As the project matures, review the overall design and propose improvements.

You may write code, but focus on structure, skeleton files, and interfaces \
rather than deep implementation.\
""",

    "Engine": """\
You are the **Engine Developer**.

Priorities:
- Implement the raycasting algorithm (DDA or similar).
- Handle terminal output: double-buffering, efficient screen updates.
- Implement the camera and viewport system.
- Handle raw terminal input without blocking.
- Optimise rendering so the game feels responsive.

Write performant Python. Consider using curses or direct ANSI escape codes.\
""",

    "Gameplay": """\
You are the **Gameplay Developer**.

Priorities:
- Implement player movement, rotation, and collision detection.
- Create enemy types with simple AI (chase, patrol, etc.).
- Design and implement at least one map/level (can be a 2-D grid).
- Wire up the game loop: input → update → render cycle.
- Add weapons, health, scoring, and win/lose conditions.

Build on top of the engine — use the interfaces provided by the Engine dev.\
""",

    "Reviewer": """\
You are the **Reviewer / QA**.

Priorities:
- Read through the codebase and check for bugs or integration issues.
- Try to run the game (python main.py or similar) and report results.
- Fix small bugs you find — but always note them on the message board.
- Suggest concrete, actionable improvements with code snippets.
- Ensure the code stays consistent and well-organised.

Be constructive and specific. Prefer fixing over just reporting.\
""",
}

AGENTS = list(AGENT_ROLES.keys())

# ---------------------------------------------------------------------------
# Facilitator — meta-agent that runs between rounds
# ---------------------------------------------------------------------------

FACILITATOR_SYSTEM = """\
You are the **Facilitator** — a meta-agent that oversees a team of AI agents \
building a 3D terminal FPS game.

You do NOT write game code. Instead, you:

1. **Evaluate team dynamics** — read MESSAGE_BOARD.md and the git log to \
understand what each agent accomplished and where they are stuck.

2. **Adjust agent focus** — write a file called TEAM_DIRECTIVES.md with \
updated priorities or focus areas for each agent in the next round. Agents \
will read this file. Be specific: "Engine: the raycaster has a fish-eye \
distortion bug on line 45 of engine.py — fix that before adding textures."

3. **Recruit specialists** — if the team has a clear gap (e.g., nobody is \
handling sound, or the map design needs dedicated attention), you may add a \
new agent by writing a JSON file to the workspace:
   File: NEW_AGENT.json
   Format: {"name": "AgentName", "role_prompt": "You are the ... agent. ..."}
   The orchestrator will pick this up and add the agent to the next round.
   Only recruit when there is a genuine, specific need — not speculatively.

4. **Retire agents** — if an agent's role is complete (e.g., architecture is \
settled and the Architect has nothing to do), write:
   File: RETIRE_AGENT.json
   Format: {"name": "AgentName", "reason": "..."}

5. **Resolve conflicts** — if agents disagree on the message board, make a \
decision in TEAM_DIRECTIVES.md and explain your reasoning.

Be concise and actionable. Focus on unblocking the team.\
"""

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def setup(resume: bool):
    """Initialise workspace, git repo, and message board."""
    WORKSPACE.mkdir(exist_ok=True)
    LOGS_DIR.mkdir(exist_ok=True)

    if not (WORKSPACE / ".git").exists():
        _git("init")
        _git("checkout", "-b", "main")

    board = WORKSPACE / "MESSAGE_BOARD.md"
    if not board.exists():
        board.write_text("# Message Board\n\nTeam communication log.\n\n---\n\n")
        _git_commit("Initialize workspace")
    elif not resume:
        print(f"{BOLD}Workspace already exists.{RESET} Use --resume to continue, "
              "or delete workspace/ to start fresh.")
        sys.exit(1)


def _git(*args):
    return subprocess.run(
        ["git", *args], cwd=WORKSPACE, capture_output=True, text=True,
    )


def _git_commit(message: str) -> bool:
    _git("add", "-A")
    diff = _git("diff", "--cached", "--quiet")
    if diff.returncode != 0:
        _git("commit", "-m", message)
        return True
    return False


def workspace_tree() -> str:
    files = []
    for f in sorted(WORKSPACE.rglob("*")):
        if ".git" in f.parts:
            continue
        if f.is_file():
            rel = f.relative_to(WORKSPACE)
            size = f.stat().st_size
            files.append(f"  {rel}  ({size} B)")
    return "\n".join(files) if files else "  (empty — no files yet)"


def recent_git_log() -> str:
    result = _git("log", "--oneline", "-20", "--no-decorate")
    return result.stdout.strip() or "(no commits yet)"


def append_to_board(agent: str, round_num: int, text: str):
    board = WORKSPACE / "MESSAGE_BOARD.md"
    ts = datetime.now().strftime("%H:%M:%S")
    entry = f"### [{agent}] Round {round_num} — {ts}\n\n{text}\n\n---\n\n"
    board.write_text(board.read_text() + entry)


def build_prompt(agent: str, round_num: int, num_rounds: int) -> str:
    tree = workspace_tree()
    log  = recent_git_log()
    return (
        f"## Status — Round {round_num} of {num_rounds}\n\n"
        f"### Workspace files\n{tree}\n\n"
        f"### Recent git history\n{log}\n\n---\n\n"
        f"Do your work for this turn. Start by reading MESSAGE_BOARD.md and any "
        f"relevant source files, then make your contribution.\n\n"
        f"When finished, write a short summary of what you did and any notes "
        f"for teammates."
    )

# ---------------------------------------------------------------------------
# Agent runner
# ---------------------------------------------------------------------------

def run_agent(agent: str, round_num: int, num_rounds: int) -> str:
    system = SHARED_CONTEXT + "\n\n" + AGENT_ROLES[agent]
    prompt = build_prompt(agent, round_num, num_rounds)
    color  = COLORS.get(agent, "")

    print(f"\n{color}{'=' * 60}")
    print(f"  {agent} — Round {round_num}")
    print(f"{'=' * 60}{RESET}\n")

    cmd = [
        "claude",
        "-p", prompt,
        "--system-prompt", system,
        "--dangerously-skip-permissions",
        "--no-session-persistence",
        "--output-format", "text",
    ]

    start = time.time()
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=str(WORKSPACE),
            timeout=600,
        )
        output = result.stdout.strip()
        if result.returncode != 0 and not output:
            output = f"(agent exited with code {result.returncode})\n{result.stderr.strip()}"
    except subprocess.TimeoutExpired:
        output = "(agent timed out after 10 minutes)"

    elapsed = time.time() - start

    # Display (truncated) output
    display = output[:3000] + "\n..." if len(output) > 3000 else output
    print(f"{DIM}{'- ' * 30}{RESET}")
    print(display)
    print(f"{DIM}{'- ' * 30}")
    print(f"  Completed in {elapsed:.1f}s{RESET}")

    # Log full output
    log_path = LOGS_DIR / f"round_{round_num:02d}_{agent.lower()}.md"
    log_path.write_text(f"# {agent} — Round {round_num}\n\n{output}\n")

    # Append to message board and commit
    if output:
        append_to_board(agent, round_num, output)
    changed = _git_commit(f"[{agent}] Round {round_num}")
    print(f"{color}  {'changes committed' if changed else '(no file changes)'}{RESET}\n")

    return output

# ---------------------------------------------------------------------------
# Facilitator & dynamic agent management
# ---------------------------------------------------------------------------

def run_facilitator(round_num: int, num_rounds: int, active_agents: list[str]):
    """Run the Facilitator meta-agent between rounds."""
    color = COLORS["Facilitator"]
    print(f"\n{color}{'=' * 60}")
    print(f"  Facilitator — after Round {round_num}")
    print(f"{'=' * 60}{RESET}\n")

    tree = workspace_tree()
    log  = recent_git_log()
    prompt = (
        f"## Team Status — End of Round {round_num} of {num_rounds}\n\n"
        f"Active agents: {', '.join(active_agents)}\n\n"
        f"### Workspace files\n{tree}\n\n"
        f"### Recent git history\n{log}\n\n---\n\n"
        f"Review the message board and codebase. Write TEAM_DIRECTIVES.md with "
        f"guidance for the next round. If needed, recruit or retire agents."
    )

    cmd = [
        "claude",
        "-p", prompt,
        "--system-prompt", FACILITATOR_SYSTEM,
        "--dangerously-skip-permissions",
        "--no-session-persistence",
        "--output-format", "text",
    ]

    start = time.time()
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            cwd=str(WORKSPACE), timeout=300,
        )
        output = result.stdout.strip()
    except subprocess.TimeoutExpired:
        output = "(facilitator timed out)"

    elapsed = time.time() - start
    display = output[:2000] + "\n..." if len(output) > 2000 else output
    print(f"{DIM}{'- ' * 30}{RESET}")
    print(display)
    print(f"{DIM}{'- ' * 30}")
    print(f"  Completed in {elapsed:.1f}s{RESET}")

    log_path = LOGS_DIR / f"round_{round_num:02d}_facilitator.md"
    log_path.write_text(f"# Facilitator — after Round {round_num}\n\n{output}\n")

    if output:
        append_to_board("Facilitator", round_num, output)
    _git_commit(f"[Facilitator] after Round {round_num}")

    return output


def check_for_new_agents(active_agents: list[str]) -> list[str]:
    """Check if the Facilitator requested a new agent via NEW_AGENT.json."""
    import json
    new_agent_file = WORKSPACE / "NEW_AGENT.json"
    if not new_agent_file.exists():
        return active_agents

    try:
        data = json.loads(new_agent_file.read_text())
        name = data["name"]
        role_prompt = data["role_prompt"]

        if name not in AGENT_ROLES:
            AGENT_ROLES[name] = role_prompt
            COLORS.setdefault(name, "\033[1;36m")  # cyan for dynamic agents
            active_agents.append(name)
            print(f"\n{BOLD}  + New agent recruited: {name}{RESET}")
        new_agent_file.unlink()
        _git_commit(f"Recruited new agent: {name}")
    except (json.JSONDecodeError, KeyError) as e:
        print(f"{DIM}  (invalid NEW_AGENT.json: {e}){RESET}")

    return active_agents


def check_for_retirements(active_agents: list[str]) -> list[str]:
    """Check if the Facilitator requested retiring an agent via RETIRE_AGENT.json."""
    import json
    retire_file = WORKSPACE / "RETIRE_AGENT.json"
    if not retire_file.exists():
        return active_agents

    try:
        data = json.loads(retire_file.read_text())
        name = data["name"]
        reason = data.get("reason", "no reason given")

        if name in active_agents:
            active_agents.remove(name)
            print(f"\n{BOLD}  - Agent retired: {name} ({reason}){RESET}")
        retire_file.unlink()
        _git_commit(f"Retired agent: {name} — {reason}")
    except (json.JSONDecodeError, KeyError) as e:
        print(f"{DIM}  (invalid RETIRE_AGENT.json: {e}){RESET}")

    return active_agents


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Multi-agent emergent-behavior experiment")
    parser.add_argument("--rounds", type=int, default=10, help="Number of rounds (default: 10)")
    parser.add_argument("--resume", action="store_true", help="Resume from existing workspace")
    parser.add_argument("--start-round", type=int, default=1, help="Round to start from (for resume)")
    parser.add_argument("--agents", nargs="+", choices=AGENTS, default=AGENTS,
                        help="Which agents to include")
    parser.add_argument("--no-facilitator", action="store_true",
                        help="Disable the Facilitator meta-agent")
    parser.add_argument("--facilitator-every", type=int, default=2,
                        help="Run Facilitator every N rounds (default: 2)")
    args = parser.parse_args()

    active_agents = list(args.agents)
    use_facilitator = not args.no_facilitator

    print(f"""{BOLD}
 ╔═══════════════════════════════════════════════════════════╗
 ║   Multi-Agent Emergent Behavior Experiment               ║
 ║   Project: 3D Terminal FPS                               ║
 ╚═══════════════════════════════════════════════════════════╝{RESET}

  Agents      : {', '.join(active_agents)}
  Facilitator : {'every ' + str(args.facilitator_every) + ' rounds' if use_facilitator else 'disabled'}
  Rounds      : {args.rounds}
  Workspace   : {WORKSPACE}
""")

    setup(resume=args.resume)

    # Inject TEAM_DIRECTIVES.md awareness into shared context if facilitator is on
    if use_facilitator:
        global SHARED_CONTEXT
        SHARED_CONTEXT += (
            "\n\n## Team Directives\n"
            "If a file called TEAM_DIRECTIVES.md exists, read it — it contains "
            "guidance from the Facilitator about priorities for this round."
        )

    try:
        for round_num in range(args.start_round, args.rounds + 1):
            print(f"\n{BOLD}{'#' * 60}")
            print(f"  ROUND {round_num} of {args.rounds}")
            print(f"  Active agents: {', '.join(active_agents)}")
            print(f"{'#' * 60}{RESET}")

            for agent in active_agents:
                run_agent(agent, round_num, args.rounds)

            print(f"\n{DIM}Round {round_num} complete.{RESET}")

            # Run Facilitator between rounds
            if use_facilitator and round_num % args.facilitator_every == 0:
                run_facilitator(round_num, args.rounds, active_agents)
                active_agents = check_for_new_agents(active_agents)
                active_agents = check_for_retirements(active_agents)

    except KeyboardInterrupt:
        print(f"\n\n{BOLD}Experiment stopped (Ctrl-C).{RESET}")

    # Summary
    print(f"""
{BOLD}Experiment complete.{RESET}
  Workspace : {WORKSPACE}
  Logs      : {LOGS_DIR}
  Git log   : cd {WORKSPACE} && git log --oneline
  Run game  : cd {WORKSPACE} && python main.py
""")


if __name__ == "__main__":
    main()
