"""
Message board: append, archive, and summarize agent messages.
"""

import re
from pathlib import Path
from datetime import datetime

BOARD_HEADER = "# Message Board\n\nTeam communication log.\n\n---\n\n"


def init_board(workspace: Path):
    """Create MESSAGE_BOARD.md if it doesn't exist."""
    board = workspace / "MESSAGE_BOARD.md"
    if not board.exists():
        board.write_text(BOARD_HEADER)


def append_to_board(workspace: Path, agent: str, label: str, text: str):
    """Add an agent's message to the board."""
    board = workspace / "MESSAGE_BOARD.md"
    ts = datetime.now().strftime("%H:%M:%S")
    entry = f"### [{agent}] {label} — {ts}\n\n{text}\n\n---\n\n"
    board.write_text(board.read_text() + entry)


def archive_message_board(workspace: Path, *,
                           keep_round: int | None = None,
                           keep_plan: int | None = None):
    """Archive old messages, keep only the most recent round's messages.

    Pass keep_round=N for implementation rounds, keep_plan=N for planning rounds.
    Messages older than the keep threshold are moved to the archive.
    Returns (archived_count, kept_count) or None if nothing to do.
    """
    assert not (keep_round is not None and keep_plan is not None), \
        "pass keep_round or keep_plan, not both"

    board = workspace / "MESSAGE_BOARD.md"
    archive = workspace / "MESSAGE_BOARD_ARCHIVE.md"

    if not board.exists():
        return None

    content = board.read_text()
    if content.strip() == BOARD_HEADER.strip():
        return None

    # Split into individual entries on the ### [...] header pattern
    entry_pattern = re.compile(r'(### \[.+?\] .+?)(?=### \[|\Z)', re.DOTALL)
    entries = entry_pattern.findall(content)

    if not entries:
        return None

    round_pattern = re.compile(r'### \[.+?\] Round (\d+)')
    plan_pattern = re.compile(r'### \[.+?\] Planning (\d+)/\d+')
    old_entries = []
    keep_entries = []

    for entry in entries:
        m_round = round_pattern.match(entry)
        m_plan = plan_pattern.match(entry)

        if m_round:
            if keep_round is not None and int(m_round.group(1)) < keep_round:
                old_entries.append(entry)
            else:
                keep_entries.append(entry)
        elif m_plan:
            if keep_plan is not None and int(m_plan.group(1)) < keep_plan:
                old_entries.append(entry)
            elif keep_round is not None:
                # Implementation started — archive all planning entries
                old_entries.append(entry)
            else:
                keep_entries.append(entry)
        else:
            # Unknown format — keep to be safe
            keep_entries.append(entry)

    if not old_entries:
        return None

    existing_archive = archive.read_text() if archive.exists() else ""
    archive.write_text(existing_archive + "".join(old_entries) + "\n")
    board.write_text(BOARD_HEADER + "".join(keep_entries))

    return len(old_entries), len(keep_entries)
