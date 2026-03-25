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
                       timeout: int = 60) -> dict:
    """Helper: run claude -p with stream-json and return parsed results."""
    cmd = [
        "claude", "-p",
        "--model", model,
        "--effort", effort,
        "--output-format", "stream-json",
        "--verbose",
        "--no-session-persistence",
        "--dangerously-skip-permissions",
    ]
    if system:
        cmd += ["--system-prompt", system]
    if extra_flags:
        cmd += extra_flags

    proc = subprocess.Popen(
        cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, bufsize=1,
        cwd=cwd,
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


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
