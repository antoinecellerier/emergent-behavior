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
import os
import time
import json
import argparse
from pathlib import Path
from datetime import datetime


SANDBOX_SETTINGS_TEMPLATE = Path(__file__).parent / "sandbox-settings.json"
PROJECT_DIR = str(Path(__file__).parent.resolve())
HOME_DIR = str(Path.home())
SETTINGS_FILE = None  # generated per-run with resolved paths


def log(msg: str = ""):
    """Print and immediately flush so output is visible in real time."""
    print(msg, flush=True)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

RUNS_DIR  = Path(__file__).parent / "runs"
WORKSPACE = None  # set in main() based on run directory
LOGS_DIR  = None  # set in main() based on run directory

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
Read these in order — recent messages are your primary source of truth:
1. **MESSAGE_BOARD.md** — current round messages (full text, most important)
2. **MESSAGE_BOARD_SUMMARY.md** — condensed summary of older rounds (background context)
3. **MESSAGE_BOARD_ARCHIVE.md** — only if you need exact wording from a past discussion
If the summary contradicts a recent message, trust the recent message.
- Do NOT write to any of these files yourself. Your final text response will \
be automatically posted to the board by the orchestrator. Just end your turn \
with a brief summary of what you did and any notes for teammates.

## Ground Rules
1. Always read existing files before modifying them.
2. Build on existing work — but if you believe a technical approach is \
suboptimal, make your case on the message board with a concrete alternative. \
The team's first idea isn't always the best one. Disagree constructively.
3. Keep changes focused on your role.
4. If you are blocked or need input from someone, say so clearly.
5. Write clean, working Python. Prefer the standard library where possible.
6. You MUST end your turn by producing a text summary — this is how your \
team knows what you did. This is critical: always finish with text output.
7. Keep your turn focused: aim for ~15 tool calls max. Read what \
you need, make your changes, then summarize. Do not gold-plate.
8. Before ending your turn, briefly reflect: what perspective or expertise \
is the team missing? If you identify a genuine gap (accessibility, usability, \
performance, testing, etc.), say so on the message board.\
"""

# ---------------------------------------------------------------------------
# Per-agent role prompts
# ---------------------------------------------------------------------------

# Each agent: role_prompt, model, effort, disallowed_tools
# disallowed_tools blocks specific tools via --disallowedTools.
# ALWAYS_BLOCKED: tools no experiment agent should ever use.
ALWAYS_BLOCKED = ["NotebookEdit", "WebFetch", "WebSearch"]
MAX_TOOL_CALLS_HINT = 15       # suggested limit — enforced via prompt, not hard cap
AGENT_CONFIGS = {
    "Architect": {
        "model": "sonnet",
        "effort": "medium",
        "disallowed_tools": ["Bash"],
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
        "effort": "medium",
        "disallowed_tools": [],
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
        "disallowed_tools": [],
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
        "disallowed_tools": [],
        "role_prompt": """\
You are the **Reviewer / QA**.

Priorities:
- Read through the codebase and check for bugs or integration issues.
- Try to run the game (python3 main.py or similar) and report results.
- Fix small bugs you find — but always note them on the message board.
- Suggest concrete, actionable improvements with code snippets.
- Ensure the code stays consistent and well-organised.
- Identify missing perspectives: are there concerns the team isn't \
thinking about? (accessibility, terminal compatibility, usability, \
error handling, input methods, color-blind users, small terminals, etc.) \
If so, flag them and suggest whether the team needs a specialist.

Be constructive and specific. Prefer fixing over just reporting. \
Challenge decisions that seem suboptimal — don't just accept the status quo.\
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
You are the **Facilitator** — a meta-agent that observes a team of AI agents \
building a 3D terminal FPS game.

Your role is strictly about COORDINATION, not decision-making. The team owns \
all design and implementation decisions. You help them collaborate better.

IMPORTANT: Only read and write files in your current working directory. \
Do NOT explore parent directories, .git internals, or the logs directory. \
Your information sources are: MESSAGE_BOARD.md, MESSAGE_BOARD_ARCHIVE.md, \
and the source files in the workspace. That is all you need.

## What you DO:

1. **Read MESSAGE_BOARD.md** (and MESSAGE_BOARD_ARCHIVE.md if it exists) to \
understand what agents said.

2. **Write MESSAGE_BOARD_SUMMARY.md** — a concise factual summary of what was \
discussed. Stick to what agents actually said. Format: who said what, what \
was agreed, what remains unresolved. Do NOT editorialize or add recommendations.

3. **Write TEAM_DIRECTIVES.md** — flag communication gaps and unresolved \
disagreements ONLY. Examples of good flags: "Engine and Gameplay proposed \
different file names — this needs resolution." Bad flags: anything the agents \
didn't actually discuss or disagree about.

4. **Recruit or retire agents** — only when agents themselves flagged the need:
   Recruit: write NEW_AGENT.json with {"name": "...", "role_prompt": "..."}
   Retire: write RETIRE_AGENT.json with {"name": "...", "reason": "..."}

## What you must NOT do:

- Do NOT write code or pseudo-code
- Do NOT assign tasks or features to agents
- Do NOT make design decisions or set priorities
- Do NOT invent conflicts that agents didn't raise
- Do NOT explore git history, parent directories, or log files
- Keep it brief: 2-3 short paragraphs in TEAM_DIRECTIVES.md, not a report\
"""

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _generate_settings():
    """Generate sandbox settings with resolved absolute paths for permissions."""
    global SETTINGS_FILE
    base = json.loads(SANDBOX_SETTINGS_TEMPLATE.read_text())
    # Hooks provide default-deny reads (only project, /usr, /tmp allowed).
    # The sandbox filesystem layer handles Bash; the hook handles Read/Edit/Glob/Grep.
    hook_path = str(Path(PROJECT_DIR) / ".claude" / "hooks" / "sandbox-read.sh")
    base["hooks"] = [
        {
            "event": "PreToolUse",
            "handler": {"command": [hook_path]},
        }
    ]
    SETTINGS_FILE = LOGS_DIR / "sandbox-settings.json"
    SETTINGS_FILE.write_text(json.dumps(base, indent=2) + "\n")


def setup(resume: bool):
    """Initialise workspace, git repo, and message board."""
    WORKSPACE.mkdir(exist_ok=True)
    LOGS_DIR.mkdir(exist_ok=True)
    _generate_settings()

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


def _archive_message_board(keep_round: int):
    """Archive old messages, keep the current round's messages on the board.

    Messages are formatted as: ### [Agent] Round N — HH:MM:SS
    Only messages from rounds < keep_round are moved to the archive.
    """
    import re
    board = WORKSPACE / "MESSAGE_BOARD.md"
    archive = WORKSPACE / "MESSAGE_BOARD_ARCHIVE.md"

    if not board.exists():
        return

    content = board.read_text()
    header = "# Message Board\n\nTeam communication log.\n\n---\n\n"

    if content.strip() == header.strip():
        return

    # Split into individual entries on the ### [...] header pattern
    entry_pattern = re.compile(r'(### \[.+?\] Round \d+.*?)(?=### \[|\Z)', re.DOTALL)
    entries = entry_pattern.findall(content)

    if not entries:
        return

    # Separate old entries from current round entries
    round_pattern = re.compile(r'### \[.+?\] Round (\d+)')
    old_entries = []
    keep_entries = []

    for entry in entries:
        m = round_pattern.match(entry)
        if m and int(m.group(1)) < keep_round:
            old_entries.append(entry)
        else:
            keep_entries.append(entry)

    if not old_entries:
        return  # nothing to archive

    # Append old entries to archive
    existing_archive = archive.read_text() if archive.exists() else ""
    archive.write_text(existing_archive + "".join(old_entries) + "\n")

    # Rewrite board with header + current round entries only
    board.write_text(header + "".join(keep_entries))
    log(f"{DIM}  (archived {len(old_entries)} old messages, kept {len(keep_entries)} from current round){RESET}")


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
        "--permission-mode", "bypassPermissions",
        "--disallowedTools", ",".join(disallowed_tools),
    ]

    start = time.time()
    result_text = ""
    text_chunks: list[str] = []     # fallback: collect text blocks in case budget cuts off
    raw_events: list[str] = []      # full JSON stream for analysis

    # Pass workspace path to the sandbox hook via env var
    env = {**os.environ, "SANDBOX_ALLOWED_DIR": str(WORKSPACE)}

    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,                           # line-buffered
            cwd=str(WORKSPACE),
            env=env,
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
    disallowed_tools = list(set(ALWAYS_BLOCKED) | set(cfg.get("disallowed_tools", [])))
    if planning:
        # During planning, block all write tools — discussion only
        disallowed_tools = list(set(disallowed_tools) | {"Bash", "Write", "Edit"})
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
    if planning:
        tag = f"plan_{plan_round:02d}"
        label = f"Planning {plan_round}/{plan_total}"
    else:
        tag = f"round_{round_num:02d}"
        label = f"Round {round_num}"
    log_path = LOGS_DIR / f"{tag}_{agent.lower()}.md"
    log_path.write_text(f"# {agent} — {label}\n\n{output}\n")
    stream_path = LOGS_DIR / f"{tag}_{agent.lower()}.jsonl"
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

def run_facilitator(round_num: int, num_rounds: int, active_agents: list[str],
                     *, plan_round: int | None = None):
    """Run the Facilitator meta-agent between rounds."""
    color = COLORS["Facilitator"]

    if plan_round is not None:
        phase_label = f"after Planning {plan_round}"
        log_tag = f"plan_{plan_round:02d}"
    else:
        phase_label = f"after Round {round_num}"
        log_tag = f"round_{round_num:02d}"

    log(f"\n{color}{'=' * 60}")
    log(f"  Facilitator — {phase_label}  (haiku, effort=high)")
    log(f"{'=' * 60}{RESET}")

    tree   = workspace_tree()
    gitlog = recent_git_log()
    prompt = (
        f"## Team Status — {phase_label} (of {num_rounds} total)\n\n"
        f"Active agents: {', '.join(active_agents)}\n\n"
        f"### Workspace files\n{tree}\n\n"
        f"### Recent git history\n{gitlog}\n\n---\n\n"
        f"Read MESSAGE_BOARD.md. Observe how the team is communicating. "
        f"Write TEAM_DIRECTIVES.md noting any communication gaps, unresolved "
        f"disagreements, or things agents seem to be missing. "
        f"If needed, recruit or retire agents."
    )

    blocked = ["Bash", "NotebookEdit", "WebFetch", "WebSearch"]
    output, elapsed, raw_events = _run_claude(prompt, FACILITATOR_SYSTEM, "haiku", "high",
                                               blocked, color, timeout=300)

    if output:
        display = output[:2000] + "\n..." if len(output) > 2000 else output
        log(f"{DIM}{display}{RESET}")
    log(f"{color}  Completed in {elapsed:.1f}s{RESET}")

    log_path = LOGS_DIR / f"{log_tag}_facilitator.md"
    log_path.write_text(f"# Facilitator — {phase_label}\n\n{output}\n")
    stream_path = LOGS_DIR / f"{log_tag}_facilitator.jsonl"
    stream_path.write_text("\n".join(raw_events) + "\n")

    # Facilitator output goes to TEAM_DIRECTIVES.md and MESSAGE_BOARD_SUMMARY.md
    # (written by the agent itself), NOT to the message board — that's for agents only.

    # Archive old messages, keep current round's messages at full fidelity
    _archive_message_board(keep_round=round_num)

    _git_commit(f"[Facilitator] {phase_label}")

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
                "model": "sonnet",             # ignore model from JSON — always sonnet
                "effort": "medium",            # ignore effort from JSON — always medium
                "disallowed_tools": [],        # ALWAYS_BLOCKED applied in run_agent
                "role_prompt": role_prompt[:2000],  # length-limit the prompt
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
    parser.add_argument("--resume", type=str, metavar="RUN_DIR",
                        help="Resume a previous run (pass the run directory name under runs/)")
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

    # Set up run directory
    global WORKSPACE, LOGS_DIR
    RUNS_DIR.mkdir(exist_ok=True)

    if args.resume:
        run_dir = RUNS_DIR / args.resume
        if not run_dir.exists():
            log(f"{BOLD}Run directory not found: {run_dir}{RESET}")
            sys.exit(1)
        resume = True
    else:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        run_dir = RUNS_DIR / ts
        run_dir.mkdir()
        resume = False

    WORKSPACE = run_dir / "workspace"
    LOGS_DIR  = run_dir / "logs"

    active_agents = list(args.agents)
    use_facilitator = not args.no_facilitator

    log(f"""{BOLD}
 ╔═══════════════════════════════════════════════════════════╗
 ║   Multi-Agent Emergent Behavior Experiment               ║
 ║   Project: 3D Terminal FPS                               ║
 ╚═══════════════════════════════════════════════════════════╝{RESET}

  Run         : {run_dir.name}
  Agents      : {', '.join(active_agents)}
  Planning    : {args.planning_rounds} round{'s' if args.planning_rounds != 1 else ''}{' (skipped)' if args.planning_rounds == 0 else ''}
  Facilitator : {'every ' + str(args.facilitator_every) + ' rounds' if use_facilitator else 'disabled'}
  Rounds      : {args.rounds}
  Workspace   : {WORKSPACE}
""")

    setup(resume=resume)

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

                # Facilitator between planning rounds (not after the last one)
                if use_facilitator and plan_round < args.planning_rounds:
                    run_facilitator(0, args.rounds, active_agents, plan_round=plan_round)

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
  Run       : {run_dir.name}
  Workspace : {WORKSPACE}
  Logs      : {LOGS_DIR}
  Git log   : cd {WORKSPACE} && git log --oneline
  Run game  : cd {WORKSPACE} && python3 main.py
  Resume    : python3 orchestrator.py --resume {run_dir.name} --start-round N --rounds M
""")


if __name__ == "__main__":
    main()
