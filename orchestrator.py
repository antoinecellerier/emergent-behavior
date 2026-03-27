#!/usr/bin/env python3
"""
Multi-Agent Emergent Behavior Experiment
========================================
Spawns multiple Claude Code agents to collaboratively build a project.
The objective is defined in the agent config JSON (agents/*.json).

Usage:
    python3 orchestrator.py --rounds 3              # fresh: planning + 3 rounds
    python3 orchestrator.py --resume <dir> --rounds 2  # add 2 more rounds
"""

import sys
import os
import json
import signal
import argparse
from pathlib import Path
from datetime import datetime

# Prevent "Exception ignored on flushing sys.stdout: BrokenPipeError"
# when piped through tee and tee dies on Ctrl-C
signal.signal(signal.SIGPIPE, signal.SIG_IGN)

from prompts import load_agent_configs, load_objective, list_configs
from agents import (
    log, git, git_commit, run_agent, run_facilitator,
    collect_facilitator_files,
    check_for_new_agents, check_for_retirements, check_for_reorder,
    detect_resume_state, save_roster, load_roster,
    RateLimitError, APIError, AgentTimeoutError, BOLD, RESET, DIM,
)
from board import init_board

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

RUNS_DIR = Path(__file__).parent / "runs"
SANDBOX_SETTINGS_TEMPLATE = Path(__file__).parent / "sandbox-settings.json"
PROJECT_DIR = str(Path(__file__).parent.resolve())

# ---------------------------------------------------------------------------
# Graceful shutdown
# ---------------------------------------------------------------------------

_shutdown_requested = False

def _handle_sigint(signum, frame):
    global _shutdown_requested
    if _shutdown_requested:
        log(f"\n{BOLD}Force quit.{RESET}")
        sys.exit(1)
    _shutdown_requested = True
    log(f"\n{BOLD}Shutdown requested — finishing current agent, then stopping.{RESET}")

signal.signal(signal.SIGINT, _handle_sigint)

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

def _generate_settings(logs_dir: Path) -> Path:
    """Generate sandbox settings with hooks for this run."""
    base = json.loads(SANDBOX_SETTINGS_TEMPLATE.read_text())
    hook_path = str(Path(PROJECT_DIR) / ".claude" / "hooks" / "sandbox-read.sh")
    base["hooks"] = [
        {"event": "PreToolUse", "handler": {"command": [hook_path]}}
    ]
    settings_file = logs_dir / "sandbox-settings.json"
    settings_file.write_text(json.dumps(base, indent=2) + "\n")
    return settings_file


def _write_claude_md_excludes(workspace: Path) -> None:
    """Prevent agents from loading CLAUDE.md files from parent directories.

    Claude walks up the directory tree and would find the orchestrator's
    CLAUDE.md, leaking project paths and wasting tokens. The exclusion
    must live in .claude/settings.local.json (--settings doesn't support it).
    """
    claude_dir = workspace / ".claude"
    claude_dir.mkdir(exist_ok=True)
    settings_local = claude_dir / "settings.local.json"

    excludes = [
        str(Path(PROJECT_DIR) / "CLAUDE.md"),
        str(Path(PROJECT_DIR) / ".claude" / "**"),
        str(Path.home() / ".claude" / "**"),
    ]

    # Merge with existing settings if present
    if settings_local.exists():
        try:
            existing = json.loads(settings_local.read_text())
        except json.JSONDecodeError:
            existing = {}
    else:
        existing = {}
    existing["claudeMdExcludes"] = excludes
    settings_local.write_text(json.dumps(existing, indent=2) + "\n")


def setup(workspace: Path, logs_dir: Path, resume: bool) -> Path:
    """Initialise workspace, git repo, message board. Returns settings file path."""
    workspace.mkdir(exist_ok=True)
    logs_dir.mkdir(exist_ok=True)
    settings_file = _generate_settings(logs_dir)

    if not (workspace / ".git").exists():
        git(workspace, "init")
        git(workspace, "checkout", "-b", "main")

    _write_claude_md_excludes(workspace)

    init_board(workspace)
    if not (workspace / "MESSAGE_BOARD.md").exists():
        # init_board already created it, but commit if new
        pass
    if not resume and (workspace / "MESSAGE_BOARD.md").stat().st_size < 50:
        git_commit(workspace, "Initialize workspace")
    elif not resume and len(list(workspace.iterdir())) > 2:
        # Has more than .git + MESSAGE_BOARD.md — probably leftover
        log(f"{BOLD}Workspace already exists.{RESET} Use --resume to continue.")
        sys.exit(1)

    return settings_file


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Multi-agent emergent-behavior experiment")
    parser.add_argument("--rounds", type=int, default=3,
                        help="Number of rounds to run (default: 3)")
    parser.add_argument("--resume", type=str, metavar="RUN_DIR",
                        help="Resume a previous run (directory name under runs/)")
    parser.add_argument("--config", type=str, default="default",
                        help="Agent roster config from agents/ (default: default)")
    parser.add_argument("--list-configs", action="store_true",
                        help="List available agent configurations and exit")
    parser.add_argument("--no-facilitator", action="store_true",
                        help="Disable the Facilitator meta-agent")
    parser.add_argument("--planning-rounds", type=int, default=3,
                        help="Number of planning rounds before coding (default: 3, 0 to skip)")
    parser.add_argument("--facilitator-every", type=int, default=1,
                        help="Run Facilitator every N rounds (default: 1)")
    args = parser.parse_args()

    if args.list_configs:
        log(f"{BOLD}Available agent configurations:{RESET}")
        for name, desc in list_configs():
            log(f"  {name:20s} {desc}")
        sys.exit(0)

    agent_configs = load_agent_configs(args.config)
    objective = load_objective(args.config)

    # --- Run directory ---
    RUNS_DIR.mkdir(exist_ok=True)

    if args.resume:
        run_dir = RUNS_DIR / args.resume
        if not run_dir.exists():
            log(f"{BOLD}Run directory not found: {run_dir}{RESET}")
            sys.exit(1)
        resume = True
    else:
        run_dir = RUNS_DIR / datetime.now().strftime("%Y%m%d_%H%M%S")
        run_dir.mkdir(exist_ok=False)
        resume = False

    workspace = run_dir / "workspace"
    logs_dir  = run_dir / "logs"
    use_facilitator = not args.no_facilitator

    # On resume, restore the full agent roster (including dynamically recruited agents)
    if resume:
        saved = load_roster(run_dir)
        if saved:
            agent_configs, active_agents = saved[0], saved[1]
            log(f"{BOLD}  Restored roster: {', '.join(active_agents)}{RESET}")
        else:
            active_agents = list(agent_configs.keys())
    else:
        active_agents = list(agent_configs.keys())

    settings_file = setup(workspace, logs_dir, resume)

    # --- Round range ---
    partial_agents = []
    if resume:
        last_complete, partial_agents = detect_resume_state(workspace, active_agents)
        start_round = last_complete + 1
        end_round = last_complete + args.rounds
        if partial_agents:
            log(f"{BOLD}  Resuming interrupted round {start_round} "
                f"({', '.join(partial_agents)} still to go){RESET}")
    else:
        start_round = 1
        end_round = args.rounds

    log(f"""{BOLD}
 ╔═══════════════════════════════════════════════════════════╗
 ║   Multi-Agent Emergent Behavior Experiment               ║
 ║   Project: {objective['summary']:45s} ║
 ╚═══════════════════════════════════════════════════════════╝{RESET}

  Run         : {run_dir.name}
  Agents      : {', '.join(active_agents)}
  Planning    : {'skip (resuming)' if resume else f"{args.planning_rounds} rounds"}
  Facilitator : {'every ' + str(args.facilitator_every) + ' rounds' if use_facilitator else 'disabled'}
  Rounds      : {start_round} to {end_round}{f' (resuming)' if resume else ''}
  Workspace   : {workspace}
""")

    try:
        # --- Planning ---
        if not resume and args.planning_rounds > 0:
            for plan_round in range(1, args.planning_rounds + 1):
                log(f"\n{BOLD}{'#' * 60}")
                log(f"  PLANNING {plan_round}/{args.planning_rounds} — no code, just coordination")
                log(f"  Active agents: {', '.join(active_agents)}")
                log(f"{'#' * 60}{RESET}")

                for agent in active_agents:
                    run_agent(workspace, logs_dir, settings_file,
                              agent, agent_configs, 0, end_round,
                              planning=True, plan_round=plan_round,
                              plan_total=args.planning_rounds,
                              objective=objective)
                    if _shutdown_requested:
                        break

                log(f"\n{DIM}Planning round {plan_round} complete.{RESET}")

                if _shutdown_requested:
                    break

                if use_facilitator:
                    run_facilitator(workspace, logs_dir, settings_file,
                                    0, end_round, active_agents,
                                    plan_round=plan_round)
                    collect_facilitator_files(workspace, run_dir)
                    # Reorder first (written against current roster), then
                    # add/remove agents so new agents go to end of order
                    active_agents = check_for_reorder(run_dir, workspace, active_agents)
                    active_agents = check_for_new_agents(run_dir, workspace, agent_configs, active_agents)
                    active_agents = check_for_retirements(run_dir, workspace, active_agents)
                    save_roster(run_dir, agent_configs, active_agents)

        # --- Implementation ---
        if not _shutdown_requested:
            for round_num in range(start_round, end_round + 1):
                if partial_agents and round_num == start_round:
                    agents_this_round = partial_agents
                else:
                    agents_this_round = active_agents

                log(f"\n{BOLD}{'#' * 60}")
                log(f"  ROUND {round_num} of {end_round}")
                log(f"  Active agents: {', '.join(agents_this_round)}")
                log(f"{'#' * 60}{RESET}")

                for agent in agents_this_round:
                    run_agent(workspace, logs_dir, settings_file,
                              agent, agent_configs, round_num, end_round,
                              objective=objective)
                    if _shutdown_requested:
                        break

                log(f"\n{DIM}Round {round_num} complete.{RESET}")

                if _shutdown_requested:
                    break

                if use_facilitator and round_num % args.facilitator_every == 0:
                    run_facilitator(workspace, logs_dir, settings_file,
                                    round_num, end_round, active_agents)
                    collect_facilitator_files(workspace, run_dir)
                    # Reorder first (written against current roster), then
                    # add/remove agents so new agents go to end of order
                    active_agents = check_for_reorder(run_dir, workspace, active_agents)
                    active_agents = check_for_new_agents(run_dir, workspace, agent_configs, active_agents)
                    active_agents = check_for_retirements(run_dir, workspace, active_agents)
                    save_roster(run_dir, agent_configs, active_agents)

        if _shutdown_requested:
            log(f"\n{BOLD}Experiment stopped after current agent finished.{RESET}")

        status = "complete"
    except RateLimitError as e:
        status = "paused"
        log(f"\n{BOLD}Experiment paused — rate limit hit.{RESET}")
    except APIError as e:
        status = "paused"
        log(f"\n{BOLD}Experiment paused — Claude API error (retries exhausted).{RESET}")
        log(f"{DIM}{e}{RESET}")
        log(f"{DIM}Check https://status.anthropic.com/ then resume.{RESET}")
    except AgentTimeoutError as e:
        status = "paused"
        log(f"\n{BOLD}Experiment paused — agent timed out ({e}).{RESET}")
    except Exception as e:
        status = "error"
        import traceback
        log(f"\n{BOLD}Experiment error: {e}{RESET}")
        traceback.print_exc()

    run_hint = f"\n  Run       : cd {workspace} && {objective['run_command']}" if objective.get("run_command") else ""
    heading = "Experiment complete." if status == "complete" else f"Experiment {status}."
    summary = f"""
{heading}
  Run       : {run_dir.name}
  Workspace : {workspace}
  Logs      : {logs_dir}
  Git log   : cd {workspace} && git log --oneline{run_hint}
  Resume    : python3 orchestrator.py --resume {run_dir.name} --rounds N
"""
    log(f"{BOLD}{summary}{RESET}")
    # Also print to stderr so it's visible even when stdout is piped and broken
    if status != "complete":
        print(summary, file=sys.stderr)


if __name__ == "__main__":
    main()
