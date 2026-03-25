#!/usr/bin/env python3
"""
Targeted test: reproduce the empty-result bug with realistic agent conditions.
Run: python3 test_real_agent.py
"""

import subprocess
import json
import tempfile
from pathlib import Path

SETTINGS_FILE = str(Path(__file__).parent / "sandbox-settings.json")

# Use a realistic system prompt (shortened but similar structure)
SYSTEM = """\
You are part of a team of AI agents building a terminal FPS game.

## Your Team
- Architect — designs structure
- Engine — implements raycasting
- Gameplay — implements mechanics
- Reviewer — reviews code

## Rules
- Read MESSAGE_BOARD.md first
- Do NOT write to MESSAGE_BOARD.md yourself
- You MUST end your turn by producing a text summary. This is critical.
- Keep your turn focused: aim for ~15 tool calls max.

You are the Architect. Write ARCHITECTURE.md only. Do NOT create .py files.\
"""

PROMPT = """\
## Status — Round 1 of 3

### Workspace files
  MESSAGE_BOARD.md  (47 B)

### Recent git history
(no commits yet)

---

Do your work. Start by reading MESSAGE_BOARD.md. When finished, write a text summary.\
"""

with tempfile.TemporaryDirectory() as tmpdir:
    Path(tmpdir, "MESSAGE_BOARD.md").write_text("# Message Board\n\n---\n\n")

    cmd = [
        "claude", "-p",
        "--system-prompt", SYSTEM,
        "--model", "sonnet",
        "--effort", "high",
        "--output-format", "stream-json",
        "--verbose",
        "--no-session-persistence",
        "--settings", SETTINGS_FILE,
        # no --max-budget-usd — rely on prompt to limit scope
        "--permission-mode", "bypassPermissions",
        "--disallowedTools", "Bash,NotebookEdit,WebFetch,WebSearch",
    ]

    proc = subprocess.Popen(
        cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, bufsize=1, cwd=tmpdir,
    )
    proc.stdin.write(PROMPT)
    proc.stdin.close()

    tool_uses = []
    result_text = ""
    result_event = None

    while True:
        line = proc.stdout.readline()
        if not line:
            break
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
            etype = event.get("type", "")

            if etype == "assistant":
                for block in event.get("message", {}).get("content", []):
                    if block.get("type") == "tool_use":
                        tool_uses.append(block.get("name", "?"))
                        print(f"  [TOOL] {block.get('name')}")

            if etype == "result":
                result_event = event
                result_text = event.get("result", "")
                print(f"  [RESULT] len={len(result_text)}, text={result_text[:200]!r}")

        except json.JSONDecodeError:
            pass

    proc.wait(timeout=300)
    stderr = proc.stderr.read().strip()

    print(f"\n--- Summary ---")
    print(f"  Tools used: {tool_uses}")
    print(f"  Result text length: {len(result_text)}")
    print(f"  Return code: {proc.returncode}")
    print(f"  Stderr: {stderr[:200]!r}" if stderr else "  Stderr: (empty)")

    if result_event and not result_text:
        print(f"\n  RESULT EVENT (no result field):")
        print(f"  {json.dumps(result_event, indent=2)[:500]}")

    # Files created
    files = [str(p.relative_to(tmpdir)) for p in Path(tmpdir).rglob("*") if p.is_file()]
    print(f"  Files created: {files}")

    if result_text:
        print(f"\n  PASS — result captured")
    else:
        print(f"\n  FAIL — result is empty!")
