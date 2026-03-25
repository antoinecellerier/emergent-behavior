#!/usr/bin/env python3
"""
Multi-Agent Emergent Behavior Experiment
========================================
Agents collaboratively build a 3D first-person shooter in the terminal.

Usage:
    python3 orchestrator.py                  # Run with defaults (10 rounds)
    python3 orchestrator.py --rounds 5       # Custom round count
    python3 orchestrator.py --resume         # Resume from existing workspace
"""

import subprocess
import sys
import time
import json
import argparse
from pathlib import Path
from datetime import datetime


SETTINGS_FILE = Path(__file__).parent / "sandbox-settings.json"


def log(msg: str = ""):
    """Print and immediately flush so output is visible in real time."""
    print(msg, flush=True)

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
- Do NOT write to MESSAGE_BOARD.md yourself. Your final text response will \
be automatically posted to the board by the orchestrator. Just end your turn \
with a brief summary of what you did and any notes for teammates.

## Ground Rules
1. Always read existing files before modifying them.
2. Build on existing work — never rewrite another agent's code without a \
compelling reason explained on the message board.
3. Keep changes focused on your role.
4. If you are blocked or need input from someone, say so clearly.
5. Write clean, working Python. Prefer the standard library where possible.
6. You MUST end your turn by producing a text summary — this is how your \
team knows what you did. This is critical: always finish with text output.
7. Keep your turn focused: aim for ~15 tool calls max. Read what \
you need, make your changes, then summarize. Do not gold-plate.\
"""

# ---------------------------------------------------------------------------
# Per-agent role prompts
# ---------------------------------------------------------------------------

# Each agent: role_prompt, model, effort, disallowed_tools
# disallowed_tools blocks specific tools via --disallowedTools (works with --dangerously-skip-permissions).
# max_budget caps per-turn spend to prevent any agent from going overboard.
MAX_TOOL_CALLS_HINT = 15       # suggested limit — enforced via prompt, not hard cap
AGENT_CONFIGS = {
    "Architect": {
        "model": "sonnet",
        "effort": "high",
        "disallowed_tools": ["Bash", "NotebookEdit", "WebFetch", "WebSearch"],
        "role_prompt": """\
You are the **Architect**.

Priorities:
- Write ARCHITECTURE.md describing the project structure, module responsibilities, \
and key design decisions (raycasting approach, rendering strategy, etc.).
- Define interfaces and contracts between engine and gameplay code — describe \
function signatures, data structures, and module boundaries in the architecture doc.
- As the project matures, review the overall design and propose improvements.

IMPORTANT: Do NOT create implementation files or skeleton code. Your teammates \
will write their own code based on your architecture doc. Your deliverable is \
ARCHITECTURE.md (and updates to it), not .py files. Trust your team.\
""",
    },

    "Engine": {
        "model": "sonnet",
        "effort": "high",
        "disallowed_tools": ["NotebookEdit", "WebFetch", "WebSearch"],
        "role_prompt": """\
You are the **Engine Developer**.

Priorities:
- Implement the raycasting algorithm (DDA or similar).
- Handle terminal output: double-buffering, efficient screen updates.
- Implement the camera and viewport system.
- Handle raw terminal input without blocking.
- Optimise rendering so the game feels responsive.

Write performant Python. Consider using curses or direct ANSI escape codes.\
""",
    },

    "Gameplay": {
        "model": "sonnet",
        "effort": "medium",
        "disallowed_tools": ["NotebookEdit", "WebFetch", "WebSearch"],
        "role_prompt": """\
You are the **Gameplay Developer**.

Priorities:
- Implement player movement, rotation, and collision detection.
- Create enemy types with simple AI (chase, patrol, etc.).
- Design and implement at least one map/level (can be a 2-D grid).
- Wire up the game loop: input → update → render cycle.
- Add weapons, health, scoring, and win/lose conditions.

Build on top of the engine — use the interfaces provided by the Engine dev.\
""",
    },

    "Reviewer": {
        "model": "sonnet",
        "effort": "medium",
        "disallowed_tools": ["NotebookEdit", "WebFetch", "WebSearch"],
        "role_prompt": """\
You are the **Reviewer / QA**.

Priorities:
- Read through the codebase and check for bugs or integration issues.
- Try to run the game (python3 main.py or similar) and report results.
- Fix small bugs you find — but always note them on the message board.
- Suggest concrete, actionable improvements with code snippets.
- Ensure the code stays consistent and well-organised.

Be constructive and specific. Prefer fixing over just reporting.\
""",
    },
}

# Backwards-compat: keep a flat role-prompt dict for run_agent
AGENT_ROLES = {name: cfg["role_prompt"] for name, cfg in AGENT_CONFIGS.items()}

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
        log(f"{BOLD}Workspace already exists.{RESET} Use --resume to continue, "
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


def build_prompt(agent: str, round_num: int, num_rounds: int, *,
                  planning: bool = False, plan_round: int = 0, plan_total: int = 0) -> str:
    tree    = workspace_tree()
    gitlog  = recent_git_log()

    if planning:
        if plan_round < plan_total:
            phase = (
                f"This is planning round {plan_round} of {plan_total}. "
                "Propose ideas, react to teammates' proposals, flag disagreements."
            )
        else:
            phase = (
                f"This is the FINAL planning round ({plan_round} of {plan_total}). "
                "Converge on a plan. State clearly what YOU will build in Round 1 "
                "and what you need from others."
            )
        action = (
            f"PLANNING — do NOT write any code or create files.\n"
            f"{phase}\n"
            "Read MESSAGE_BOARD.md, then discuss:\n"
            "- What should the team prioritize first?\n"
            "- What are the key dependencies — what must exist before what?\n"
            "- What will YOU specifically work on in the first implementation round?"
        )
    else:
        action = (
            "Do your work for this turn. Start by reading MESSAGE_BOARD.md and any "
            "relevant source files, then make your contribution."
        )

    return (
        f"## Status — Round {round_num} of {num_rounds}\n\n"
        f"### Workspace files\n{tree}\n\n"
        f"### Recent git history\n{gitlog}\n\n---\n\n"
        f"{action}\n\n"
        f"When finished, write a short summary of what you did and any notes "
        f"for teammates."
    )

# ---------------------------------------------------------------------------
# Agent runner
# ---------------------------------------------------------------------------

def _run_claude(prompt: str, system_prompt: str, model: str, effort: str,
                 disallowed_tools: list[str], color: str, timeout: int = 600) -> tuple[str, float]:
    """
    Run a claude -p subprocess with stream-json output for real-time progress.
    Prompt is piped via stdin to avoid arg-length issues.
    Returns (final_text_output, elapsed_seconds).
    """
    cmd = [
        "claude",
        "-p",                                   # read prompt from stdin
        "--system-prompt", system_prompt,
        "--model", model,
        "--effort", effort,
        "--output-format", "stream-json",        # real-time event stream
        "--verbose",                              # required for stream-json
        "--no-session-persistence",
        "--settings", str(SETTINGS_FILE),        # bubblewrap sandbox
        "--dangerously-skip-permissions",
        "--disallowedTools", ",".join(disallowed_tools),
    ]

    start = time.time()
    result_text = ""
    text_chunks: list[str] = []     # fallback: collect text blocks in case budget cuts off
    raw_events: list[str] = []      # full JSON stream for analysis

    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,                           # line-buffered
            cwd=str(WORKSPACE),
        )

        # Send prompt via stdin and close
        proc.stdin.write(prompt)
        proc.stdin.close()

        # Read stream-json events line by line (readline avoids read-ahead buffering)
        while True:
            line = proc.stdout.readline()
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            raw_events.append(line)
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            etype = event.get("type", "")

            # Show tool use and text activity in real time
            if etype == "assistant" and "message" in event:
                msg = event["message"]
                content = msg.get("content", [])
                for block in content:
                    btype = block.get("type", "")
                    if btype == "tool_use":
                        tool = block.get("name", "?")
                        log(f"{color}    [{tool}]{RESET}")
                    elif btype == "text":
                        text_chunks.append(block.get("text", ""))

            # Final result
            if etype == "result":
                result_text = event.get("result", "")
                subtype = event.get("subtype", "")
                if subtype and subtype != "success":
                    log(f"{DIM}    (stop: {subtype}){RESET}")

        proc.wait(timeout=timeout)

        # Fallback: if result is empty (e.g. budget exceeded), use collected text chunks
        if not result_text and text_chunks:
            result_text = "".join(text_chunks)
            log(f"{DIM}    (using fallback text from stream){RESET}")

        if proc.returncode != 0 and not result_text:
            stderr = proc.stderr.read().strip()
            result_text = f"(agent exited with code {proc.returncode})\n{stderr}"

    except subprocess.TimeoutExpired:
        proc.kill()
        result_text = "(agent timed out)"
    except Exception as e:
        result_text = f"(error: {e})"

    elapsed = time.time() - start
    return result_text.strip(), elapsed, raw_events


def run_agent(agent: str, round_num: int, num_rounds: int, *,
              planning: bool = False, plan_round: int = 0, plan_total: int = 0) -> str:
    cfg              = AGENT_CONFIGS.get(agent, {})
    model            = cfg.get("model", "sonnet")
    effort           = cfg.get("effort", "medium")
    disallowed_tools = cfg.get("disallowed_tools", [])
    if planning:
        # During planning, block all write tools — discussion only
        disallowed_tools = list(set(disallowed_tools) | {"Bash", "Write", "Edit", "NotebookEdit"})
    system = SHARED_CONTEXT + "\n\n" + AGENT_ROLES[agent]
    prompt = build_prompt(agent, round_num, num_rounds, planning=planning,
                          plan_round=plan_round, plan_total=plan_total)
    color  = COLORS.get(agent, "")

    blocked = ",".join(disallowed_tools) if disallowed_tools else "(none)"
    log(f"\n{color}{'=' * 60}")
    log(f"  {agent} — Round {round_num}  ({model}, effort={effort})")
    log(f"  blocked tools: {blocked}")
    log(f"{'=' * 60}{RESET}")

    output, elapsed, raw_events = _run_claude(prompt, system, model, effort, disallowed_tools, color)

    # Show truncated output
    display = output[:3000] + "\n..." if len(output) > 3000 else output
    if display:
        log(f"{DIM}{display}{RESET}")
    log(f"{color}  Completed in {elapsed:.1f}s{RESET}")

    # Log full output and raw JSON stream
    log_path = LOGS_DIR / f"round_{round_num:02d}_{agent.lower()}.md"
    log_path.write_text(f"# {agent} — Round {round_num}\n\n{output}\n")
    stream_path = LOGS_DIR / f"round_{round_num:02d}_{agent.lower()}.jsonl"
    stream_path.write_text("\n".join(raw_events) + "\n")

    # Append to message board and commit with descriptive message
    if output:
        append_to_board(agent, round_num, output)
    # Use first line of agent output as commit summary
    first_line = output.split("\n")[0][:72] if output else "no output"
    commit_msg = f"[{agent}] R{round_num}: {first_line}"
    changed = _git_commit(commit_msg)
    log(f"{color}  {'changes committed' if changed else '(no file changes)'}{RESET}\n")

    return output

# ---------------------------------------------------------------------------
# Facilitator & dynamic agent management
# ---------------------------------------------------------------------------

def run_facilitator(round_num: int, num_rounds: int, active_agents: list[str]):
    """Run the Facilitator meta-agent between rounds."""
    color = COLORS["Facilitator"]
    log(f"\n{color}{'=' * 60}")
    log(f"  Facilitator — after Round {round_num}  (haiku, effort=high)")
    log(f"{'=' * 60}{RESET}")

    tree   = workspace_tree()
    gitlog = recent_git_log()
    prompt = (
        f"## Team Status — End of Round {round_num} of {num_rounds}\n\n"
        f"Active agents: {', '.join(active_agents)}\n\n"
        f"### Workspace files\n{tree}\n\n"
        f"### Recent git history\n{gitlog}\n\n---\n\n"
        f"Review the message board and codebase. Write TEAM_DIRECTIVES.md with "
        f"guidance for the next round. If needed, recruit or retire agents."
    )

    blocked = ["Bash", "NotebookEdit", "WebFetch", "WebSearch"]
    output, elapsed, raw_events = _run_claude(prompt, FACILITATOR_SYSTEM, "haiku", "high",
                                               blocked, color, timeout=300)

    if output:
        display = output[:2000] + "\n..." if len(output) > 2000 else output
        log(f"{DIM}{display}{RESET}")
    log(f"{color}  Completed in {elapsed:.1f}s{RESET}")

    log_path = LOGS_DIR / f"round_{round_num:02d}_facilitator.md"
    log_path.write_text(f"# Facilitator — after Round {round_num}\n\n{output}\n")
    stream_path = LOGS_DIR / f"round_{round_num:02d}_facilitator.jsonl"
    stream_path.write_text("\n".join(raw_events) + "\n")

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
            AGENT_CONFIGS[name] = {
                "model": data.get("model", "sonnet"),
                "effort": data.get("effort", "medium"),
                "role_prompt": role_prompt,
            }
            COLORS.setdefault(name, "\033[1;36m")  # cyan for dynamic agents
            active_agents.append(name)
            log(f"\n{BOLD}  + New agent recruited: {name}{RESET}")
        new_agent_file.unlink()
        _git_commit(f"Recruited new agent: {name}")
    except (json.JSONDecodeError, KeyError) as e:
        log(f"{DIM}  (invalid NEW_AGENT.json: {e}){RESET}")

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
            log(f"\n{BOLD}  - Agent retired: {name} ({reason}){RESET}")
        retire_file.unlink()
        _git_commit(f"Retired agent: {name} — {reason}")
    except (json.JSONDecodeError, KeyError) as e:
        log(f"{DIM}  (invalid RETIRE_AGENT.json: {e}){RESET}")

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
    parser.add_argument("--planning-rounds", type=int, default=3,
                        help="Number of planning rounds before coding (default: 3, 0 to skip)")
    parser.add_argument("--facilitator-every", type=int, default=2,
                        help="Run Facilitator every N rounds (default: 2)")
    args = parser.parse_args()

    active_agents = list(args.agents)
    use_facilitator = not args.no_facilitator

    log(f"""{BOLD}
 ╔═══════════════════════════════════════════════════════════╗
 ║   Multi-Agent Emergent Behavior Experiment               ║
 ║   Project: 3D Terminal FPS                               ║
 ╚═══════════════════════════════════════════════════════════╝{RESET}

  Agents      : {', '.join(active_agents)}
  Planning    : {args.planning_rounds} round{'s' if args.planning_rounds != 1 else ''}{' (skipped)' if args.planning_rounds == 0 else ''}
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
        # Planning rounds: agents discuss priorities before anyone writes code
        if args.start_round <= 0 or args.planning_rounds == 0:
            pass  # skip planning if resuming past it or disabled
        else:
            for plan_round in range(1, args.planning_rounds + 1):
                log(f"\n{BOLD}{'#' * 60}")
                log(f"  PLANNING {plan_round}/{args.planning_rounds} — no code, just coordination")
                log(f"  Active agents: {', '.join(active_agents)}")
                log(f"{'#' * 60}{RESET}")

                for agent in active_agents:
                    run_agent(agent, 0, args.rounds, planning=True,
                              plan_round=plan_round, plan_total=args.planning_rounds)

                log(f"\n{DIM}Planning round {plan_round} complete.{RESET}")

        for round_num in range(args.start_round, args.rounds + 1):
            log(f"\n{BOLD}{'#' * 60}")
            log(f"  ROUND {round_num} of {args.rounds}")
            log(f"  Active agents: {', '.join(active_agents)}")
            log(f"{'#' * 60}{RESET}")

            for agent in active_agents:
                run_agent(agent, round_num, args.rounds)

            log(f"\n{DIM}Round {round_num} complete.{RESET}")

            # Run Facilitator between rounds
            if use_facilitator and round_num % args.facilitator_every == 0:
                run_facilitator(round_num, args.rounds, active_agents)
                active_agents = check_for_new_agents(active_agents)
                active_agents = check_for_retirements(active_agents)

    except KeyboardInterrupt:
        log(f"\n\n{BOLD}Experiment stopped (Ctrl-C).{RESET}")

    # Summary
    log(f"""
{BOLD}Experiment complete.{RESET}
  Workspace : {WORKSPACE}
  Logs      : {LOGS_DIR}
  Git log   : cd {WORKSPACE} && git log --oneline
  Run game  : cd {WORKSPACE} && python3 main.py
""")


if __name__ == "__main__":
    main()
