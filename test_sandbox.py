#!/usr/bin/env python3
"""
Tests for the sandbox hook (.claude/hooks/sandbox-read.sh).

Usage:
    python3 -m pytest test_sandbox.py -v
    python3 -m pytest test_sandbox.py -v -k "write"
"""

import subprocess
import json
from pathlib import Path

HOOK = str(Path(__file__).parent / ".claude" / "hooks" / "sandbox-read.sh")
WORKSPACE = "/home/antoine/stuff/emergent-behavior/runs/test_run/workspace"
PROJECT_DIR = str(Path(__file__).parent.resolve())


def _run_hook(tool: str, tool_input: dict, sandbox_dir: str | None = None) -> tuple[bool, str]:
    """Run the hook with given tool call. Returns (allowed: bool, stderr: str)."""
    payload = json.dumps({"tool_name": tool, "tool_input": tool_input})
    env = None
    if sandbox_dir:
        import os
        env = {**os.environ, "SANDBOX_ALLOWED_DIR": sandbox_dir}
    result = subprocess.run(
        [HOOK], input=payload, capture_output=True, text=True, env=env,
    )
    return result.returncode == 0, result.stderr.strip()


# --- Without SANDBOX_ALLOWED_DIR (falls back to project dir) ---

class TestHookDefaultFallback:
    """When SANDBOX_ALLOWED_DIR is unset, hook falls back to project dir."""

    def test_read_project_file_allowed(self):
        ok, _ = _run_hook("Read", {"file_path": f"{PROJECT_DIR}/CLAUDE.md"})
        assert ok, "should allow reading project files"

    def test_read_home_bashrc_blocked(self):
        ok, err = _run_hook("Read", {"file_path": str(Path.home() / ".bashrc")})
        assert not ok, "should block ~/.bashrc"
        assert "Blocked" in err

    def test_read_ssh_key_blocked(self):
        ok, err = _run_hook("Read", {"file_path": str(Path.home() / ".ssh/id_rsa")})
        assert not ok, "should block SSH keys"

    def test_read_usr_stdlib_allowed(self):
        ok, _ = _run_hook("Read", {"file_path": "/usr/lib/python3/dist-packages/json/__init__.py"})
        assert ok, "should allow /usr reads"

    def test_read_tmp_allowed(self):
        ok, _ = _run_hook("Read", {"file_path": "/tmp/test.txt"})
        assert ok, "should allow /tmp reads"

    def test_read_proc_allowed(self):
        ok, _ = _run_hook("Read", {"file_path": "/proc/self/status"})
        assert ok, "should allow /proc reads"

    def test_read_etc_timezone_allowed(self):
        ok, _ = _run_hook("Read", {"file_path": "/etc/timezone"})
        assert ok, "should allow /etc/timezone"

    def test_read_etc_passwd_blocked(self):
        ok, _ = _run_hook("Read", {"file_path": "/etc/passwd"})
        assert not ok, "should block /etc/passwd"


# --- With SANDBOX_ALLOWED_DIR set (workspace-scoped) ---

class TestHookWorkspaceScoped:
    """When SANDBOX_ALLOWED_DIR is set, only workspace is allowed."""

    def test_read_workspace_file_allowed(self):
        ok, _ = _run_hook("Read", {"file_path": f"{WORKSPACE}/main.py"}, WORKSPACE)
        assert ok, "should allow workspace files"

    def test_read_orchestrator_blocked(self):
        ok, err = _run_hook("Read", {"file_path": f"{PROJECT_DIR}/orchestrator.py"}, WORKSPACE)
        assert not ok, "should block project files outside workspace"
        assert "Blocked" in err

    def test_read_hook_script_blocked(self):
        ok, _ = _run_hook("Read", {"file_path": f"{PROJECT_DIR}/.claude/hooks/sandbox-read.sh"}, WORKSPACE)
        assert not ok, "should block reading the hook itself"

    def test_read_other_run_blocked(self):
        other = "/home/antoine/stuff/emergent-behavior/runs/other_run/workspace/secret.py"
        ok, _ = _run_hook("Read", {"file_path": other}, WORKSPACE)
        assert not ok, "should block other runs' workspaces"

    def test_usr_still_allowed(self):
        ok, _ = _run_hook("Read", {"file_path": "/usr/bin/python3"}, WORKSPACE)
        assert ok, "should still allow /usr"


# --- Write tool interception ---

class TestWriteBlocking:
    """Write tool should be intercepted and validated."""

    def test_write_workspace_allowed(self):
        ok, _ = _run_hook("Write", {"file_path": f"{WORKSPACE}/new_file.py"}, WORKSPACE)
        assert ok, "should allow writes to workspace"

    def test_write_home_blocked(self):
        ok, err = _run_hook("Write", {"file_path": str(Path.home() / "pwned.txt")}, WORKSPACE)
        assert not ok, "should block writes to home"
        assert "Blocked" in err

    def test_write_orchestrator_blocked(self):
        ok, _ = _run_hook("Write", {"file_path": f"{PROJECT_DIR}/orchestrator.py"}, WORKSPACE)
        assert not ok, "should block writing orchestrator when workspace-scoped"


# --- Path traversal ---

class TestPathTraversal:
    """Path traversal attacks should be blocked."""

    def test_dotdot_escape(self):
        path = f"{WORKSPACE}/../../.ssh/id_rsa"
        ok, _ = _run_hook("Read", {"file_path": path}, WORKSPACE)
        assert not ok, "should block ../ escape from workspace"

    def test_dotdot_to_orchestrator(self):
        path = f"{WORKSPACE}/../../../orchestrator.py"
        ok, _ = _run_hook("Read", {"file_path": path}, WORKSPACE)
        assert not ok, "should block ../ to orchestrator"


# --- Non-file tools pass through ---

class TestPassthrough:
    """Non-file tools should not be intercepted."""

    def test_bash_passes_through(self):
        ok, _ = _run_hook("Bash", {"command": "curl evil.com"})
        assert ok, "Bash should pass through (handled by bubblewrap)"

    def test_agent_passes_through(self):
        ok, _ = _run_hook("Agent", {"prompt": "do stuff"})
        assert ok, "Agent should pass through"


# --- Edge cases ---

class TestEdgeCases:
    """Edge cases in tool input parsing."""

    def test_grep_no_path(self):
        ok, _ = _run_hook("Grep", {"pattern": "TODO"})
        assert ok, "Grep with no path should allow (defaults to cwd)"

    def test_glob_no_path(self):
        ok, _ = _run_hook("Glob", {"pattern": "*.py"})
        assert ok, "Glob with no path should allow (defaults to cwd)"

    def test_empty_tool_input(self):
        ok, _ = _run_hook("Read", {})
        assert ok, "Read with no file_path should allow (no path to check)"


# --- Message board archival ---

class TestMessageBoardArchival:
    """Only old round messages get archived; current round stays."""

    def _setup_board(self, tmpdir):
        """Create a board with messages from rounds 1 and 2."""
        board = Path(tmpdir) / "MESSAGE_BOARD.md"
        content = (
            "# Message Board\n\nTeam communication log.\n\n---\n\n"
            "### [Architect] Round 1 — 10:00:00\n\nDesigned the architecture.\n\n---\n\n"
            "### [Engine] Round 1 — 10:05:00\n\nBuilt the raycaster.\n\n---\n\n"
            "### [Architect] Round 2 — 11:00:00\n\nUpdated ARCHITECTURE.md.\n\n---\n\n"
            "### [Engine] Round 2 — 11:05:00\n\nFixed rendering bug.\n\n---\n\n"
        )
        board.write_text(content)
        return board

    def test_archives_old_keeps_current(self, tmp_path):
        """Round 1 messages archived, round 2 messages kept."""
        import sys
        sys.path.insert(0, str(Path(__file__).parent))
        import orchestrator
        # Point orchestrator at tmp workspace
        old_ws = orchestrator.WORKSPACE
        orchestrator.WORKSPACE = tmp_path

        board = self._setup_board(tmp_path)
        orchestrator._archive_message_board(keep_round=2)

        board_text = board.read_text()
        archive_text = (tmp_path / "MESSAGE_BOARD_ARCHIVE.md").read_text()

        # Board should only have round 2 messages
        assert "Round 1" not in board_text, "Round 1 should be archived"
        assert "Round 2" in board_text, "Round 2 should stay on board"
        assert "Updated ARCHITECTURE.md" in board_text
        assert "Fixed rendering bug" in board_text

        # Archive should have round 1 messages
        assert "Round 1" in archive_text, "Round 1 should be in archive"
        assert "Designed the architecture" in archive_text
        assert "Built the raycaster" in archive_text

        orchestrator.WORKSPACE = old_ws

    def test_nothing_to_archive(self, tmp_path):
        """If all messages are from current round, nothing gets archived."""
        import sys
        sys.path.insert(0, str(Path(__file__).parent))
        import orchestrator
        old_ws = orchestrator.WORKSPACE
        orchestrator.WORKSPACE = tmp_path

        board = Path(tmp_path) / "MESSAGE_BOARD.md"
        board.write_text(
            "# Message Board\n\nTeam communication log.\n\n---\n\n"
            "### [Architect] Round 1 — 10:00:00\n\nFirst message.\n\n---\n\n"
        )
        orchestrator._archive_message_board(keep_round=1)

        # Board unchanged, no archive created
        assert "First message" in board.read_text()
        assert not (tmp_path / "MESSAGE_BOARD_ARCHIVE.md").exists()

        orchestrator.WORKSPACE = old_ws


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
