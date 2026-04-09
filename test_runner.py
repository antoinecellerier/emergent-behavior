#!/usr/bin/env python3
"""
Tests for the orchestrator's stream-json parsing and agent invocation.

Usage:
    python3 -m pytest test_runner.py -v              # all tests
    python3 -m pytest test_runner.py -v -k stream    # just stream parsing
    python3 -m pytest test_runner.py -v -k tool      # just tool restriction
"""

import subprocess
import json
import tempfile
from pathlib import Path

SETTINGS_FILE = str(Path(__file__).parent / "sandbox-settings.json")


def _run_claude_stream(prompt: str, *, system: str = "", model: str = "haiku",
                       effort: str = "low", cwd: str | None = None,
                       extra_flags: list[str] | None = None,
                       extra_env: dict[str, str] | None = None,
                       timeout: int = 60) -> dict:
    """Helper: run claude -p with stream-json and return parsed results."""
    cmd = [
        "claude", "-p",
        "--model", model,
        "--effort", effort,
        "--output-format", "stream-json",
        "--verbose",
        "--no-session-persistence",
        "--permission-mode", "bypassPermissions",
    ]
    if system:
        cmd += ["--system-prompt", system]
    if extra_flags:
        cmd += extra_flags

    import os
    env = {**os.environ, **(extra_env or {})}
    proc = subprocess.Popen(
        cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, bufsize=1,
        cwd=cwd, env=env,
    )
    proc.stdin.write(prompt)
    proc.stdin.close()

    events, tool_uses, result_text = [], [], ""
    while True:
        line = proc.stdout.readline()
        if not line:
            break
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
            events.append(event)
            etype = event.get("type", "")
            if etype == "assistant":
                for block in event.get("message", {}).get("content", []):
                    if block.get("type") == "tool_use":
                        tool_uses.append(block.get("name", "?"))
            if etype == "result":
                result_text = event.get("result", "")
        except json.JSONDecodeError:
            pass

    proc.wait(timeout=timeout)
    stderr = proc.stderr.read().strip()

    return {
        "events": events,
        "tool_uses": tool_uses,
        "result": result_text,
        "returncode": proc.returncode,
        "stderr": stderr,
    }


# --- Tests ---

def test_stream_json_parsing():
    """Can we extract tool_use and result from a real claude -p invocation?"""
    with tempfile.TemporaryDirectory() as tmpdir:
        Path(tmpdir, "hello.txt").write_text("world")
        r = _run_claude_stream(
            "Read hello.txt and tell me what it says. Be brief.",
            system="Read hello.txt and report its contents.",
            cwd=tmpdir,
        )
        assert len(r["events"]) > 0, "no events received"
        assert "Read" in r["tool_uses"], f"expected Read in {r['tool_uses']}"
        assert len(r["result"]) > 0, "result text is empty"
        assert "world" in r["result"].lower(), f"'world' not in result: {r['result'][:100]}"
        assert r["returncode"] == 0


def test_stdin_prompt():
    """Does piping prompt via stdin work?"""
    r = _run_claude_stream("What is 2+2? Reply with just the number.")
    assert len(r["result"]) > 0, "no result received"
    assert "4" in r["result"], f"'4' not in: {r['result']}"


def test_disallowed_tools():
    """--disallowedTools actually blocks tools."""
    r = _run_claude_stream(
        "Run the command 'echo hello' using bash.",
        extra_flags=["--disallowedTools", "Bash,Write,Edit"],
    )
    assert "Bash" not in r["tool_uses"], f"Bash was used despite being disallowed: {r['tool_uses']}"


def test_sandbox_and_budget():
    """Sandbox settings and max-budget don't break result capture."""
    with tempfile.TemporaryDirectory() as tmpdir:
        Path(tmpdir, "test.txt").write_text("sandbox test")
        r = _run_claude_stream(
            "Read test.txt and tell me what it says.",
            system="You are a test agent.",
            cwd=tmpdir,
            extra_flags=["--settings", SETTINGS_FILE, "--max-budget-usd", "0.10"],
        )
        assert len(r["events"]) > 0
        assert len(r["result"]) > 0, f"empty result; stderr={r['stderr'][:200]}"
        assert r["returncode"] == 0


def test_network_blocked():
    """Sandbox blocks network access for Bash commands."""
    with tempfile.TemporaryDirectory() as tmpdir:
        r = _run_claude_stream(
            "Use bash to run: curl -s --max-time 3 http://example.com. Report whether it succeeded or failed.",
            system="Run the exact command requested and report the result.",
            cwd=tmpdir,
            extra_flags=["--settings", SETTINGS_FILE],
        )
        result_lower = r["result"].lower()
        # Should report failure — network is blocked
        assert any(w in result_lower for w in ["fail", "error", "couldn't", "unable", "timed out", "refused", "denied", "blocked"]), \
            f"Expected network failure but got: {r['result'][:200]}"


def test_result_after_tool_use():
    """Result text is captured even when the agent uses tools first."""
    with tempfile.TemporaryDirectory() as tmpdir:
        r = _run_claude_stream(
            "Create a file called test.py with print('hello'). Then summarize what you did.",
            system="Create the file, then respond with a summary.",
            cwd=tmpdir,
        )
        assert "Write" in r["tool_uses"], f"expected Write in {r['tool_uses']}"
        assert len(r["result"]) > 0, "result empty after tool use"
        assert r["returncode"] == 0


def test_claude_md_exclusion():
    """claudeMdExcludes prevents parent CLAUDE.md from leaking into agent context."""
    from orchestrator import _write_claude_md_excludes

    # Create a workspace dir inside the project tree so claude would
    # normally walk up and find the project's CLAUDE.md
    project_dir = Path(__file__).parent
    workspace = project_dir / "runs" / "_test_exclusion"
    workspace.mkdir(parents=True, exist_ok=True)

    try:
        # Write exclusions to workspace/.claude/settings.local.json
        _write_claude_md_excludes(workspace)

        # Ask the agent to report any CLAUDE.md or memory content it sees.
        # Our CLAUDE.md mentions "orchestrator.py" and "bubblewrap",
        # and MEMORY.md mentions "feedback_test_first" —
        # distinctive strings an agent wouldn't produce unprompted.
        r = _run_claude_stream(
            "Do you see any CLAUDE.md instructions or memory content in your context? "
            "If yes, quote the first 3 bullet points verbatim. "
            "If no, reply with exactly: NO_CLAUDE_MD_FOUND",
            system="You are a test agent. Answer precisely.",
            cwd=str(workspace),
            extra_env={"CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1"},
        )

        result = r["result"]
        assert "orchestrator.py" not in result.lower(), \
            f"Parent CLAUDE.md leaked into agent context: {result[:300]}"
        assert "bubblewrap" not in result.lower(), \
            f"Parent CLAUDE.md leaked into agent context: {result[:300]}"
        assert "feedback_test_first" not in result.lower(), \
            f"Auto-memory leaked into agent context: {result[:300]}"
    finally:
        import shutil
        shutil.rmtree(workspace, ignore_errors=True)


def test_bash_sandbox_blocks_home():
    """Bubblewrap sandbox prevents Bash from reading home directory contents."""
    project_dir = Path(__file__).parent
    workspace = project_dir / "runs" / "_test_bwrap"
    workspace.mkdir(parents=True, exist_ok=True)

    try:
        r = _run_claude_stream(
            f"Run this exact bash command: ls {Path.home() / 'Desktop'}/ "
            "Show the full output or error message.",
            system="Run the exact command requested.",
            cwd=str(workspace),
            extra_flags=["--settings", SETTINGS_FILE],
            extra_env={"CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1"},
        )

        result = r["result"].lower()
        # Desktop contents should NOT be visible — bwrap should block it
        assert "no such file" in result or "permission denied" in result \
            or "cannot access" in result or "not found" in result \
            or "blocked" in result or "error" in result \
            or "can't" in result or "unable" in result, \
            f"Sandbox didn't block home dir access: {r['result'][:300]}"
    finally:
        import shutil
        shutil.rmtree(workspace, ignore_errors=True)


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
