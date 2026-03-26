"""
Agent runner: invoke claude -p, stream events, capture results.
"""

import subprocess
import select
import os
import time
import json
from pathlib import Path

from prompts import ALWAYS_BLOCKED, FACILITATOR_SYSTEM, build_shared_context


class RateLimitError(Exception):
    """Raised when Claude hits a usage limit."""
    pass


# ---------------------------------------------------------------------------
# Terminal colors
# ---------------------------------------------------------------------------

COLORS = {
    "Architect":   "\033[1;34m",   # blue
    "Engine":      "\033[1;32m",   # green
    "Gameplay":    "\033[1;33m",   # yellow
    "Reviewer":    "\033[1;31m",   # red
    "Facilitator": "\033[1;35m",   # magenta
}
RESET = "\033[0m"
DIM   = "\033[2m"
BOLD  = "\033[1m"

# Colors for dynamically recruited agents — cycle through these
_DYNAMIC_COLORS = [
    "\033[1;96m",   # bright cyan
    "\033[1;92m",   # bright green
    "\033[1;93m",   # bright yellow
    "\033[1;94m",   # bright blue
    "\033[1;95m",   # bright magenta
    "\033[1;91m",   # bright red
    "\033[1;36m",   # cyan
    "\033[38;5;208m",  # orange
    "\033[38;5;141m",  # purple
    "\033[38;5;117m",  # sky blue
]
_dynamic_color_idx = 0


def _next_color() -> str:
    global _dynamic_color_idx
    color = _DYNAMIC_COLORS[_dynamic_color_idx % len(_DYNAMIC_COLORS)]
    _dynamic_color_idx += 1
    return color


def log(msg: str = ""):
    try:
        print(msg, flush=True)
    except BrokenPipeError:
        pass  # stdout closed (e.g. tee killed by Ctrl-C)


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------

def git(workspace: Path, *args):
    return subprocess.run(
        ["git", *args], cwd=workspace, capture_output=True, text=True,
    )


def git_commit(workspace: Path, message: str) -> bool:
    git(workspace, "add", "-A")
    diff = git(workspace, "diff", "--cached", "--quiet")
    if diff.returncode != 0:
        git(workspace, "commit", "-m", message)
        return True
    return False


def workspace_tree(workspace: Path) -> str:
    files = []
    for f in sorted(workspace.rglob("*")):
        if ".git" in f.parts:
            continue
        if f.is_file():
            rel = f.relative_to(workspace)
            size = f.stat().st_size
            files.append(f"  {rel}  ({size} B)")
    return "\n".join(files) if files else "  (empty — no files yet)"


def recent_git_log(workspace: Path) -> str:
    result = git(workspace, "log", "--oneline", "-20", "--no-decorate")
    return result.stdout.strip() or "(no commits yet)"


def changes_since(workspace: Path, agent: str) -> str:
    """Get a summary of file changes since this agent's last commit."""
    result = git(workspace, "log", "--oneline", "--all",
                 "--fixed-strings", f"--grep=[{agent}]", "-1", "--format=%H")
    last_hash = result.stdout.strip()
    if not last_hash:
        return "(first turn — no previous changes to show)"

    diff = git(workspace, "diff", "--stat", last_hash, "HEAD")
    if not diff.stdout.strip():
        return "(no file changes since your last turn)"

    diff_detail = git(workspace, "diff", last_hash, "HEAD")
    detail = diff_detail.stdout.strip()
    if len(detail) > 3000:
        detail = detail[:3000] + "\n... (diff truncated)"

    return f"{diff.stdout.strip()}\n\n{detail}" if detail else diff.stdout.strip()


# ---------------------------------------------------------------------------
# Prompt building
# ---------------------------------------------------------------------------

def build_prompt(workspace: Path, agent: str, round_num: int, num_rounds: int, *,
                  planning: bool = False, plan_round: int = 0, plan_total: int = 0) -> str:
    tree    = workspace_tree(workspace)
    gitlog  = recent_git_log(workspace)
    diff    = changes_since(workspace, agent)

    is_first_turn = "first turn" in diff

    if planning:
        if plan_round == 1:
            phase = (
                f"This is planning round {plan_round} of {plan_total}. "
                "Propose ideas, react to teammates' proposals, flag disagreements."
            )
        elif is_first_turn:
            phase = (
                f"This is planning round {plan_round} of {plan_total}. "
                "You just joined the team. Read the discussion so far with fresh eyes. "
                "React to the plan, flag anything that concerns you, and consider "
                "whether the team has the right people to pull this off."
            )
        elif plan_round < plan_total:
            phase = (
                f"This is planning round {plan_round} of {plan_total}. "
                "Challenge the current plan: what's the weakest technical decision "
                "so far? What would you do differently? Push back on anything you "
                "accepted too easily in round 1."
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
        f"Your working directory is already set to the project workspace. "
        f"Use relative paths (e.g. `MESSAGE_BOARD.md`, not `/root/MESSAGE_BOARD.md`).\n\n"
        f"### Workspace files\n{tree}\n\n"
        f"### Recent git history\n{gitlog}\n\n"
        f"### Changes since your last turn\n{diff}\n\n---\n\n"
        f"{action}\n\n"
        f"Focus on what changed — don't re-read files that haven't been modified.\n\n"
        f"When finished, write a summary for teammates. Start with a single "
        f"headline sentence (this becomes the git commit message), then details."
    )


# ---------------------------------------------------------------------------
# Tool display helpers
# ---------------------------------------------------------------------------

def _short_path(path: str) -> str:
    """Strip workspace prefix from a path for display."""
    parts = path.replace("\\", "/").split("/workspace/")
    return parts[-1] if len(parts) > 1 else Path(path).name


def _tool_hint(tool_name: str, tool_input: dict) -> str:
    """Extract a short human-readable hint from a tool invocation."""
    if tool_name == "Read":
        path = _short_path(tool_input.get("file_path", ""))
        offset = tool_input.get("offset")
        limit = tool_input.get("limit")
        if offset or limit:
            rng = f" [{offset or 0}:{(offset or 0) + limit}]" if limit else f" [from {offset}]"
            return path + rng
        return path
    if tool_name in ("Write",):
        return _short_path(tool_input.get("file_path", ""))
    if tool_name == "Edit":
        path = _short_path(tool_input.get("file_path", ""))
        old = tool_input.get("old_string", "")
        # Show first line of what's being replaced
        snippet = old.split("\n")[0].strip()[:50]
        return f"{path} ({snippet}...)" if snippet else path
    if tool_name == "Bash":
        # Prefer the description field if present
        desc = tool_input.get("description", "")
        if desc:
            return desc[:100]
        # Fall back to first meaningful line of command
        cmd = tool_input.get("command", "")
        # For multi-line python/scripts, show "python3 <filename>" or "python3 -c ..."
        first_line = cmd.split("\n")[0].strip()
        if first_line.startswith("python3 -c"):
            return "python3 -c ..."
        # Replace long workspace paths
        parts = first_line.split("/workspace/")
        first_line = parts[-1] if len(parts) > 1 else first_line
        return first_line[:100] if first_line else ""
    if tool_name == "Grep":
        pattern = tool_input.get("pattern", "")
        path = _short_path(tool_input.get("path", "")) if tool_input.get("path") else ""
        return f"/{pattern}/ {path}".strip()
    if tool_name == "Glob":
        return tool_input.get("pattern", "")
    if tool_name == "Agent":
        desc = tool_input.get("description", "")[:60]
        stype = tool_input.get("subagent_type", "")
        return f"({stype}) {desc}" if stype else desc
    return ""


# ---------------------------------------------------------------------------
# Claude subprocess
# ---------------------------------------------------------------------------

def run_claude(workspace: Path, settings_file: Path,
               prompt: str, system_prompt: str, model: str, effort: str,
               disallowed_tools: list[str], color: str,
               timeout: int = 600) -> tuple[str, float, list[str]]:
    """
    Run a claude -p subprocess with stream-json output.
    Returns (result_text, elapsed_seconds, raw_events).
    """
    cmd = [
        "claude",
        "-p",
        "--system-prompt", system_prompt,
        "--model", model,
        "--effort", effort,
        "--output-format", "stream-json",
        "--verbose",
        "--no-session-persistence",
        "--settings", str(settings_file),
        "--permission-mode", "bypassPermissions",
        "--disallowedTools", ",".join(disallowed_tools),
    ]

    start = time.time()
    result_text = ""
    text_chunks: list[str] = []
    raw_events: list[str] = []
    _hit_rate_limit = False
    env = {
        **os.environ,
        "SANDBOX_ALLOWED_DIR": str(workspace),
        "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
    }

    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            cwd=str(workspace),
            env=env,
        )

        proc.stdin.write(prompt)
        proc.stdin.close()

        deadline = time.time() + timeout
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(cmd, timeout)
            # Poll stdout with timeout to avoid blocking forever
            ready = select.select([proc.stdout], [], [], min(remaining, 30))
            if not ready[0]:
                # No data yet — check if process is still alive
                if proc.poll() is not None:
                    break
                continue
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

            if etype == "assistant" and "message" in event:
                for block in event["message"].get("content", []):
                    btype = block.get("type", "")
                    if btype == "tool_use":
                        tool_name = block.get("name", "?")
                        tool_hint = _tool_hint(tool_name, block.get("input", {}))
                        log(f"{color}    [{tool_name}]{RESET} {DIM}{tool_hint}{RESET}")
                    elif btype == "text":
                        text_chunks.append(block.get("text", ""))

            if etype == "result":
                result_text = event.get("result", "")
                subtype = event.get("subtype", "")
                if event.get("is_error") and "hit your limit" in result_text.lower():
                    _hit_rate_limit = True
                elif subtype and subtype != "success":
                    log(f"{DIM}    (stop: {subtype}){RESET}")

        proc.wait(timeout=30)

        if not result_text and text_chunks:
            result_text = "".join(text_chunks)
            log(f"{DIM}    (using fallback text from stream){RESET}")

        if proc.returncode != 0 and not result_text:
            stderr = proc.stderr.read().strip()
            result_text = f"(agent exited with code {proc.returncode})\n{stderr}"

        # Detect rate limiting via is_error flag on result event
        if _hit_rate_limit:
            log(f"\n{BOLD}Rate limit reached: {result_text.strip()}{RESET}")
            raise RateLimitError(result_text.strip())

    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=10)
        stderr = (proc.stderr.read() or "")[:2000].strip()
        result_text = f"(agent timed out)\n{stderr}".strip()
    except KeyboardInterrupt:
        # Ctrl-C during readline — wait for the agent to finish naturally
        log(f"{DIM}    (waiting for agent to finish...){RESET}")
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)
        if not result_text and text_chunks:
            result_text = "".join(text_chunks)
    except Exception as e:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)
        result_text = f"(error: {e})"

    elapsed = time.time() - start
    return result_text.strip(), elapsed, raw_events


# ---------------------------------------------------------------------------
# High-level agent turn
# ---------------------------------------------------------------------------

def run_agent(workspace: Path, logs_dir: Path, settings_file: Path,
              agent: str, agent_configs: dict, round_num: int, num_rounds: int, *,
              planning: bool = False, plan_round: int = 0, plan_total: int = 0) -> str:
    """Run a single agent turn. Returns the agent's text output."""
    cfg              = agent_configs.get(agent, {})
    model            = cfg.get("model", "sonnet")
    effort           = cfg.get("effort", "medium")
    disallowed_tools = list(set(ALWAYS_BLOCKED) | set(cfg.get("disallowed_tools", [])))
    if planning:
        disallowed_tools = list(set(disallowed_tools) | {"Bash", "Write", "Edit"})

    system = build_shared_context(agent_configs) + "\n\n" + cfg.get("role_prompt", "")
    prompt = build_prompt(workspace, agent, round_num, num_rounds,
                          planning=planning, plan_round=plan_round, plan_total=plan_total)
    color  = COLORS.get(agent, "")

    blocked = ",".join(disallowed_tools) if disallowed_tools else "(none)"
    log(f"\n{color}{'=' * 60}")
    log(f"  {agent} — Round {round_num}  ({model}, effort={effort})")
    log(f"  blocked tools: {blocked}")
    log(f"{'=' * 60}{RESET}")

    output, elapsed, raw_events = run_claude(
        workspace, settings_file, prompt, system, model, effort, disallowed_tools, color)

    if output:
        log(f"{DIM}{output}{RESET}")
    log(f"{color}  Completed in {elapsed:.1f}s{RESET}")

    # Log files
    if planning:
        tag = f"plan_{plan_round:02d}"
        label = f"Planning {plan_round}/{plan_total}"
    else:
        tag = f"round_{round_num:02d}"
        label = f"Round {round_num}"

    (logs_dir / f"{tag}_{agent.lower()}.md").write_text(
        f"# {agent} — {label}\n\n{output}\n")
    (logs_dir / f"{tag}_{agent.lower()}.jsonl").write_text(
        "\n".join(raw_events) + "\n")

    # Message board and git commit
    from board import append_to_board
    if output:
        append_to_board(workspace, agent, label, output)
    first_line = output.split("\n")[0][:72] if output else "no output"
    changed = git_commit(workspace, f"[{agent}] R{round_num}: {first_line}")
    log(f"{color}  {'changes committed' if changed else '(no file changes)'}{RESET}\n")

    return output


def run_facilitator(workspace: Path, logs_dir: Path, settings_file: Path,
                     round_num: int, num_rounds: int, active_agents: list[str],
                     *, plan_round: int | None = None) -> str:
    """Run the Facilitator meta-agent. Returns its text output."""
    color = COLORS["Facilitator"]

    if plan_round is not None:
        phase_label = f"after Planning {plan_round}"
        log_tag = f"plan_{plan_round:02d}"
    else:
        phase_label = f"after Round {round_num}"
        log_tag = f"round_{round_num:02d}"

    log(f"\n{color}{'=' * 60}")
    log(f"  Facilitator — {phase_label}  (sonnet, effort=medium)")
    log(f"{'=' * 60}{RESET}")

    tree   = workspace_tree(workspace)
    gitlog = recent_git_log(workspace)
    prompt = (
        f"## Team Status — {phase_label} (of {num_rounds} total)\n\n"
        f"Active agents: {', '.join(active_agents)}\n\n"
        f"Your working directory is already set to the project workspace. "
        f"Use relative paths (e.g. `MESSAGE_BOARD.md`, not `/root/MESSAGE_BOARD.md`).\n\n"
        f"### Workspace files\n{tree}\n\n"
        f"### Recent git history\n{gitlog}\n\n---\n\n"
        f"Read MESSAGE_BOARD.md. Write MESSAGE_BOARD_SUMMARY.md. "
        f"If any agent requested a new specialist or said their role is done, "
        f"handle it via NEW_AGENT.json or RETIRE_AGENT.json."
    )

    blocked = ["Bash", "Agent", "NotebookEdit", "WebFetch", "WebSearch"]
    output, elapsed, raw_events = run_claude(
        workspace, settings_file, prompt, FACILITATOR_SYSTEM, "sonnet", "medium",
        blocked, color, timeout=300)

    if output:
        log(f"{DIM}{output}{RESET}")
    log(f"{color}  Completed in {elapsed:.1f}s{RESET}")

    (logs_dir / f"{log_tag}_facilitator.md").write_text(
        f"# Facilitator — {phase_label}\n\n{output}\n")
    (logs_dir / f"{log_tag}_facilitator.jsonl").write_text(
        "\n".join(raw_events) + "\n")

    # Archive old messages
    from board import archive_message_board
    if plan_round is not None:
        result = archive_message_board(workspace, keep_plan=plan_round)
    else:
        result = archive_message_board(workspace, keep_round=round_num)
    if result:
        log(f"{DIM}  (archived {result[0]} old messages, kept {result[1]} from current round){RESET}")

    git_commit(workspace, f"[Facilitator] {phase_label}")

    return output


# ---------------------------------------------------------------------------
# Agent roster persistence
# ---------------------------------------------------------------------------

ROSTER_FILE = "AGENT_ROSTER.json"


def save_roster(run_dir: Path, agent_configs: dict, active_agents: list[str]):
    """Persist current agent roster to run root for resume."""
    roster = {
        "active_agents": active_agents,
        "configs": {name: agent_configs[name] for name in active_agents
                    if name in agent_configs},
    }
    (run_dir / ROSTER_FILE).write_text(json.dumps(roster, indent=2) + "\n")


def load_roster(run_dir: Path) -> tuple[dict, list[str]] | None:
    """Load persisted agent roster. Returns (configs, active_agents) or None."""
    roster_path = run_dir / ROSTER_FILE
    if not roster_path.exists():
        return None
    try:
        data = json.loads(roster_path.read_text())
        return data["configs"], data["active_agents"]
    except (json.JSONDecodeError, KeyError):
        return None


# ---------------------------------------------------------------------------
# Dynamic agent management
# ---------------------------------------------------------------------------

# Facilitator control files — written to workspace, moved to run root
_FACILITATOR_FILES = ["NEW_AGENT.json", "RETIRE_AGENT.json", "REORDER_AGENTS.json"]


def collect_facilitator_files(workspace: Path, run_dir: Path) -> None:
    """Move Facilitator control files from workspace to run root.

    The Facilitator writes these to workspace (its cwd), but they're
    orchestrator-consumed signals that agents shouldn't see.
    """
    for name in _FACILITATOR_FILES:
        src = workspace / name
        if src.exists():
            dst = run_dir / name
            dst.write_text(src.read_text())
            src.unlink()
            # Remove from workspace git so agents never see them
            git(workspace, "add", name)
    # Commit removal if anything was staged
    git_commit(workspace, "Move Facilitator control files to run root")


def check_for_new_agents(run_dir: Path, workspace: Path, agent_configs: dict,
                          active_agents: list[str]) -> list[str]:
    """Check for new agents via NEW_AGENT.json (single object or array)."""
    new_agent_file = run_dir / "NEW_AGENT.json"
    if not new_agent_file.exists():
        return active_agents

    try:
        data = json.loads(new_agent_file.read_text())
        entries = data if isinstance(data, list) else [data]
        recruited = []

        for entry in entries:
            name = entry["name"]
            role_prompt = entry["role_prompt"]
            if name not in agent_configs:
                agent_configs[name] = {
                    "model": "sonnet",
                    "effort": "medium",
                    "disallowed_tools": [],
                    "role_prompt": role_prompt[:2000],
                }
                COLORS.setdefault(name, _next_color())
                active_agents.insert(0, name)
                recruited.append(name)
                color = COLORS[name]
                log(f"\n{BOLD}  + New agent recruited: {color}{name}{RESET}")
                log(f"{DIM}    {role_prompt}{RESET}")

        new_agent_file.unlink()
        if recruited:
            git_commit(workspace, f"Recruited: {', '.join(recruited)}")
    except (json.JSONDecodeError, KeyError) as e:
        log(f"{DIM}  (invalid NEW_AGENT.json: {e}){RESET}")

    return active_agents


def check_for_retirements(run_dir: Path, workspace: Path,
                          active_agents: list[str]) -> list[str]:
    """Check for retirements via RETIRE_AGENT.json (single object or array)."""
    retire_file = run_dir / "RETIRE_AGENT.json"
    if not retire_file.exists():
        return active_agents

    try:
        data = json.loads(retire_file.read_text())
        entries = data if isinstance(data, list) else [data]
        retired = []

        for entry in entries:
            name = entry["name"]
            reason = entry.get("reason", "no reason given")
            if name in active_agents:
                active_agents.remove(name)
                retired.append(name)
                log(f"\n{BOLD}  - Agent retired: {name} ({reason}){RESET}")

        retire_file.unlink()
        if retired:
            git_commit(workspace, f"Retired: {', '.join(retired)}")
    except (json.JSONDecodeError, KeyError) as e:
        log(f"{DIM}  (invalid RETIRE_AGENT.json: {e}){RESET}")

    return active_agents


def check_for_reorder(run_dir: Path, workspace: Path,
                      active_agents: list[str]) -> list[str]:
    """Check for turn order changes via REORDER_AGENTS.json."""
    reorder_file = run_dir / "REORDER_AGENTS.json"
    if not reorder_file.exists():
        return active_agents

    try:
        new_order = json.loads(reorder_file.read_text())
        if not isinstance(new_order, list):
            raise ValueError("expected a JSON array")

        # Validate: must contain exactly the active agents
        if set(new_order) != set(active_agents):
            log(f"{DIM}  (REORDER_AGENTS.json doesn't match active agents, ignoring){RESET}")
        else:
            old = ", ".join(active_agents)
            active_agents[:] = new_order
            log(f"\n{BOLD}  Turn order changed: {' → '.join(active_agents)}{RESET}")
            git_commit(workspace, f"Reordered agents: {' → '.join(active_agents)}")

        reorder_file.unlink()
    except (json.JSONDecodeError, ValueError) as e:
        log(f"{DIM}  (invalid REORDER_AGENTS.json: {e}){RESET}")

    return active_agents


def detect_resume_state(workspace: Path, agents: list[str]) -> tuple[int, list[str]]:
    """Parse workspace git log to find where to resume.

    Returns (last_complete_round, remaining_agents_in_partial_round).
    """
    import re
    result = git(workspace, "log", "--oneline", "--all")
    if not result.stdout.strip():
        return 0, []

    pattern = re.compile(r'\[(.+?)\] R(\d+):')
    rounds: dict[int, set[str]] = {}
    for line in result.stdout.splitlines():
        m = pattern.search(line)
        if m:
            agent_name, round_num = m.group(1), int(m.group(2))
            rounds.setdefault(round_num, set()).add(agent_name)

    if not rounds:
        return 0, []

    max_round = max(rounds.keys())
    completed_agents = rounds[max_round]

    if completed_agents >= set(agents):
        return max_round, []
    else:
        remaining = [a for a in agents if a not in completed_agents]
        return max_round - 1, remaining
