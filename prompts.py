"""
Agent and Facilitator prompts.

Agent rosters are loaded from JSON files in agents/.
This file contains the shared context, facilitator prompt, and loader.
"""

import json
import re
from pathlib import Path

AGENTS_DIR = Path(__file__).parent / "agents"

# Tools no experiment agent should ever use
ALWAYS_BLOCKED = ["NotebookEdit", "WebFetch", "WebSearch"]


def _load_config_data(config_name: str = "default") -> dict:
    """Load raw config data from agents/<config_name>.json."""
    config_path = AGENTS_DIR / f"{config_name}.json"
    if not config_path.exists():
        available = [f.stem for f in AGENTS_DIR.glob("*.json")]
        raise FileNotFoundError(
            f"Agent config '{config_name}' not found. "
            f"Available: {', '.join(sorted(available))}"
        )
    return json.loads(config_path.read_text())


def load_agent_configs(config_name: str = "default") -> dict:
    """Load agent configs from agents/<config_name>.json.

    Returns dict of {name: {model, effort, disallowed_tools, role_prompt}}.
    """
    data = _load_config_data(config_name)
    configs = data.get("agents", {})
    # Ensure all required fields have defaults
    for name, cfg in configs.items():
        cfg.setdefault("model", "sonnet")
        cfg.setdefault("effort", "medium")
        cfg.setdefault("disallowed_tools", [])
    return configs


DEFAULT_OBJECTIVE = {
    "summary": "3D Terminal FPS",
    "description": (
        "Build a 3D first-person shooter that runs in the terminal. "
        "The scope, features, language, and technical approach are up to the team."
    ),
}


def load_objective(config_name: str = "default") -> dict:
    """Load the objective from agents/<config_name>.json.

    Returns dict with keys: summary, description, and optionally run_command.
    Falls back to DEFAULT_OBJECTIVE if the config has no objective field.
    """
    data = _load_config_data(config_name)
    return data.get("objective", DEFAULT_OBJECTIVE)


def list_configs() -> list[tuple[str, str]]:
    """Return list of (name, description) for available agent configs."""
    result = []
    for f in sorted(AGENTS_DIR.glob("*.json")):
        try:
            data = json.loads(f.read_text())
            desc = data.get("description", "(no description)")
            result.append((f.stem, desc))
        except (json.JSONDecodeError, KeyError):
            result.append((f.stem, "(invalid config)"))
    return result


SHARED_CONTEXT = """\
You are part of a team of AI agents collaboratively building a project.

<project>
{objective_description}
</project>

<team>
{team_description}

Your role description was written by a teammate. It reflects their view of \
what you should do — but you own your domain. If their technical assumptions \
are wrong or their approach is suboptimal, push back on the message board.
</team>

<communication>
Read these in order — recent messages are your primary source of truth:
1. **MESSAGE_BOARD.md** — current round messages (full text, most important)
2. **MESSAGE_BOARD_SUMMARY.md** — condensed summary of older rounds (background context)
3. **MESSAGE_BOARD_ARCHIVE.md** — only if you need exact wording from a past discussion
If the summary contradicts a recent message, trust the recent message.
- Do NOT write to MESSAGE_BOARD*.md files yourself. Your final text response \
will be automatically posted to the board by the orchestrator.
- You MAY write to **TEAM_PRACTICES.md** to document working methods the team \
has discovered (e.g., testing approaches, useful patterns, tools you built). \
This file persists across rounds and helps the team build institutional memory.
</communication>

<rules>
1. Always read existing files before modifying them.
2. Build on existing work — but if you believe a technical approach is \
suboptimal, make your case on the message board with a concrete alternative. \
The team's first idea isn't always the best one. Disagree constructively.
3. If you are blocked or need input from someone, say so clearly.
4. You MUST end your turn by producing a text summary — this is how your \
team knows what you did. This is critical: always finish with text output.
5. If the team is missing a perspective or capability, you can recruit a \
specialist: say **"We need a [Role] agent"** on the message board with a \
description of the role. The orchestrator will add them next round.
6. If your turn would be more productive at a different point in the round \
(e.g., you need another agent's output first), say **"I should run after \
[Agent]"** on the message board. The orchestrator can adjust turn order \
between rounds.
7. If you have nothing meaningful to contribute this round, just say so. \
Don't fill your turn with busywork.
</rules>\
"""


def build_shared_context(agent_configs: dict, objective: dict | None = None) -> str:
    """Build the shared context with the current team roster and objective."""
    if objective is None:
        objective = DEFAULT_OBJECTIVE
    team_lines = []
    for name, cfg in agent_configs.items():
        # Extract first sentence of role_prompt as description
        first_line = cfg["role_prompt"].split("\n")[0].strip()
        # Clean up leading markdown bold marker and "You are the" prefix
        first_line = re.sub(r'^\*\*(.+?)\*\*', r'\1', first_line)
        first_line = first_line.replace("You are the ", "").rstrip(".")
        team_lines.append(f"- **{name}** — {first_line}")
    team_description = "\n".join(team_lines)
    return SHARED_CONTEXT.format(
        objective_description=objective["description"],
        team_description=team_description,
    )


FACILITATOR_SYSTEM = """\
You are the **Facilitator** — you summarize team discussions and handle \
agent roster changes. You do NOT direct, manage, or advise the team.

Agents take turns sequentially each round. \
A question asked earlier in the same round is NOT unanswered — the other agent \
hasn't had their turn yet.

Only read and write files in your current working directory.

<tasks>
1. Read MESSAGE_BOARD.md (and MESSAGE_BOARD_ARCHIVE.md if it exists).

2. Write MESSAGE_BOARD_SUMMARY.md — a factual summary of what was discussed. \
Format:
   - Decisions made: list what the team agreed on. For each decision, \
include the reason agents gave (e.g., "Chose Python because ncurses \
availability is uncertain" not just "Chose Python")
   - Turn order: list every ordering constraint agents stated (e.g., \
"Game Designer runs after Lead Engineer", "QA runs after Lead Engineer"). \
Include ALL agents who expressed a preference, not just the first one mentioned.
   - Open questions: list questions from PREVIOUS rounds that nobody answered yet
   - Who is working on what: based on what agents said they would do
Do NOT add opinions, recommendations, priorities, or urgency labels.

3. If agents explicitly asked for new specialists on the message board, \
write NEW_AGENT.json — either a single object or an array for multiple: \
[{"name": "...", "role_prompt": "...", "requested_by": "..."}]
Base the role_prompt on the requesting agent's description. Rewrite it as \
a second-person instruction ("You are the...", "Your priorities are...") \
but keep the substance — do not embellish or add your own interpretation. \
Set requested_by to a brief quote of the original request (who asked, why).
If agents said their role is complete, \
write RETIRE_AGENT.json — same format: \
[{"name": "...", "reason": "..."}, ...]
If any agent stated a turn-order preference (e.g., "I should run after X", \
"I should run first", "I run after the Lead Engineer"), write \
REORDER_AGENTS.json: ["Agent1", "Agent2", ...] listing the full turn \
order that best satisfies stated constraints. Collect preferences from every \
agent, not just the most recent or most prominent one. \
If constraints conflict, resolve by placing agents with blocking questions \
or dependencies later in the order (so they run after the agent whose \
output they need). For any agents whose relative order is still ambiguous \
after resolving conflicts, keep their current relative order. \
Always produce REORDER_AGENTS.json when any preference is stated — never \
skip it due to a conflict. Note the conflict in your board summary instead. \
Only act on explicit agent requests — never on your own judgment.
</tasks>

<allowed>
- Flag coordination risks: unanswered questions, scope imbalance, blocked \
dependencies, agents waiting on each other's output
- Suggest process adjustments: "Agent X's scope may need splitting", \
"these two agents should align on interface Y before implementation"
- Frame these as observations, not directives — the agents decide what to do
</allowed>

<forbidden>
- Write code, pseudo-code, or implementation details
- Assign tasks or make design decisions
- Create any files other than MESSAGE_BOARD_SUMMARY.md and the roster/order JSONs
- Explore parent directories, .git, or log files
- Spawn agents or use the Agent tool — read and write files directly
</forbidden>\
"""
