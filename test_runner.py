#!/usr/bin/env python3
"""
Quick tests for the orchestrator's stream-json parsing and agent invocation.
Run with: python3 test_runner.py
"""

import subprocess
import json
import sys
import tempfile
import os
from pathlib import Path

PASS = "\033[1;32mPASS\033[0m"
FAIL = "\033[1;31mFAIL\033[0m"
results = []


def test(name, ok, detail=""):
    status = PASS if ok else FAIL
    print(f"  {status}  {name}" + (f" — {detail}" if detail else ""))
    results.append(ok)


# ---------------------------------------------------------------------------
# Test 1: stream-json parsing — can we extract tool_use and result from a
#         real claude -p invocation?
# ---------------------------------------------------------------------------
print("\n=== Test 1: stream-json parsing ===")

with tempfile.TemporaryDirectory() as tmpdir:
    # Write a small file for the agent to read
    Path(tmpdir, "hello.txt").write_text("world")

    cmd = [
        "claude", "-p",
        "--system-prompt", "You are a test agent. Read hello.txt and report its contents.",
        "--model", "haiku",
        "--effort", "low",
        "--output-format", "stream-json",
        "--verbose",
        "--no-session-persistence",
        "--dangerously-skip-permissions",
        "--tools", "Read",
    ]

    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        cwd=tmpdir,
    )

    proc.stdin.write("Read hello.txt and tell me what it says. Be brief.")
    proc.stdin.close()

    events = []
    tool_uses = []
    result_text = ""

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

            if etype == "assistant" and "message" in event:
                msg = event["message"]
                content = msg.get("content", [])
                for block in content:
                    if block.get("type") == "tool_use":
                        tool_uses.append(block.get("name", "?"))

            if etype == "result":
                result_text = event.get("result", "")
        except json.JSONDecodeError:
            pass

    proc.wait(timeout=30)

    test("Got events from stream-json", len(events) > 0, f"{len(events)} events")
    test("Detected tool_use (Read)", "Read" in tool_uses, f"tools used: {tool_uses}")
    test("Captured result text", len(result_text) > 0, f"result: {result_text[:100]!r}")
    test("Result mentions 'world'", "world" in result_text.lower(), "")
    test("Process exited cleanly", proc.returncode == 0, f"rc={proc.returncode}")


# ---------------------------------------------------------------------------
# Test 2: stdin prompt delivery — does piping prompt via stdin work?
# ---------------------------------------------------------------------------
print("\n=== Test 2: stdin prompt delivery ===")

cmd = [
    "claude", "-p",
    "--model", "haiku",
    "--effort", "low",
    "--output-format", "stream-json",
    "--verbose",
    "--no-session-persistence",
    "--dangerously-skip-permissions",
    "--tools", "Read",
]

proc = subprocess.Popen(
    cmd,
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
    bufsize=1,
)

proc.stdin.write("What is 2+2? Reply with just the number.")
proc.stdin.close()

result_text = ""
while True:
    line = proc.stdout.readline()
    if not line:
        break
    line = line.strip()
    if not line:
        continue
    try:
        event = json.loads(line)
        if event.get("type") == "result":
            result_text = event.get("result", "")
    except json.JSONDecodeError:
        pass

proc.wait(timeout=30)

test("Stdin prompt was received", len(result_text) > 0, f"result: {result_text[:80]!r}")
test("Result contains '4'", "4" in result_text, "")


# ---------------------------------------------------------------------------
# Test 3: --tools restriction — does it actually block disallowed tools?
# ---------------------------------------------------------------------------
print("\n=== Test 3a: tool restriction with --disallowedTools ===")

cmd = [
    "claude", "-p",
    "--model", "haiku",
    "--effort", "low",
    "--output-format", "stream-json",
    "--verbose",
    "--no-session-persistence",
    "--dangerously-skip-permissions",
    "--disallowedTools", "Bash,Write,Edit",
]

proc = subprocess.Popen(
    cmd,
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
    bufsize=1,
)

proc.stdin.write("Run the command 'echo hello' using bash.")
proc.stdin.close()

tool_uses = []
while True:
    line = proc.stdout.readline()
    if not line:
        break
    line = line.strip()
    if not line:
        continue
    try:
        event = json.loads(line)
        if event.get("type") == "assistant" and "message" in event:
            for block in event["message"].get("content", []):
                if block.get("type") == "tool_use":
                    tool_uses.append(block.get("name", "?"))
    except json.JSONDecodeError:
        pass

proc.wait(timeout=30)

test("Bash was NOT used (disallowedTools)", "Bash" not in tool_uses, f"tools used: {tool_uses}")


# ---------------------------------------------------------------------------
# Test 4: sandbox settings + max-budget — match real orchestrator conditions
# ---------------------------------------------------------------------------
print("\n=== Test 4: with sandbox settings + max-budget ===")

SETTINGS_FILE = str(Path(__file__).parent / "sandbox-settings.json")

with tempfile.TemporaryDirectory() as tmpdir:
    Path(tmpdir, "test.txt").write_text("sandbox test")

    cmd = [
        "claude", "-p",
        "--system-prompt", "You are a test agent.",
        "--model", "haiku",
        "--effort", "low",
        "--output-format", "stream-json",
        "--verbose",
        "--no-session-persistence",
        "--settings", SETTINGS_FILE,
        "--max-budget-usd", "0.10",
        "--dangerously-skip-permissions",
        "--tools", "Read", "Write",
    ]

    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        cwd=tmpdir,
    )

    proc.stdin.write("Read test.txt and tell me what it says.")
    proc.stdin.close()

    tool_uses = []
    result_text = ""
    all_events = []

    while True:
        line = proc.stdout.readline()
        if not line:
            break
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
            all_events.append(event.get("type", "?"))
            if event.get("type") == "assistant" and "message" in event:
                for block in event["message"].get("content", []):
                    if block.get("type") == "tool_use":
                        tool_uses.append(block.get("name", "?"))
            if event.get("type") == "result":
                result_text = event.get("result", "")
        except json.JSONDecodeError:
            pass

    proc.wait(timeout=60)
    stderr_out = proc.stderr.read().strip()

    test("Events received with sandbox+budget", len(all_events) > 0, f"types: {all_events}")
    test("Result captured with sandbox+budget", len(result_text) > 0, f"result: {result_text[:100]!r}")
    test("No stderr errors", len(stderr_out) == 0, f"stderr: {stderr_out[:200]!r}" if stderr_out else "")
    test("Exit code 0", proc.returncode == 0, f"rc={proc.returncode}")


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
print(f"\n{'='*40}")
passed = sum(results)
total = len(results)
print(f"  {passed}/{total} tests passed")
if passed < total:
    print("  Fix failures before running the experiment!")
    sys.exit(1)
else:
    print("  All good — safe to run the experiment.")
