# Security Model

## Threat Model

Autonomous AI agents with file system and shell access working in a shared workspace. Risks:
- Agents reading sensitive files outside the workspace (SSH keys, credentials, configs)
- Agents writing outside the workspace (modifying the orchestrator, other users' files)
- Agents making network requests (data exfiltration, downloading code)
- Cross-agent manipulation via prompt injection in the message board
- Dynamic agent injection with malicious role prompts

## Defense Layers

### Layer 1: Tool Restriction (--disallowedTools)

Per-agent tool blocking via Claude Code's `--disallowedTools` flag. The Architect has Bash blocked; all agents have NotebookEdit, WebFetch, WebSearch blocked (`ALWAYS_BLOCKED` in `prompts.py`). During planning rounds, Write, Edit, and Bash are additionally blocked for all agents.

Important: `--tools` and `--allowedTools` do NOT work with `--permission-mode bypassPermissions`. Only `--disallowedTools` reliably blocks tools.

### Layer 2: Bubblewrap Sandbox (Bash commands)

OS-level isolation for all Bash subprocesses via bubblewrap:
- Filesystem: write restricted to workspace (`.`), read denied for `/home` (all users), allow overrides deny for the workspace path chain
- Network: all domains blocked (`allowedDomains: []`, `allowManagedDomainsOnly: true`)
- Unsandboxed commands disabled (`allowUnsandboxedCommands: false`)

This only applies to Bash and its child processes. Built-in tools (Read, Write, Edit) bypass bubblewrap.

### Layer 3: PreToolUse Hook (built-in file tools)

Default-deny hook (`.claude/hooks/sandbox-read.sh`) intercepts Read, Write, Edit, Glob, Grep:
- Allowed: workspace dir (via `SANDBOX_ALLOWED_DIR` env), `/usr`, `/tmp`, `/proc`, `/etc/timezone`, `/etc/localtime`
- Everything else blocked (exit code 2)
- Path traversal protection: `realpath -m` normalizes `../` attacks, fails closed if unavailable
- Per-tool field extraction: Read/Edit/Write use `file_path`, Glob/Grep use `path`
- Optional paths (e.g., Grep with no path) are allowed through (default to cwd which is the workspace)

The sandbox settings file is generated per-run in the logs directory, with the hook path injected at runtime.

### Layer 4: Permission Mode

`--permission-mode bypassPermissions` -- no interactive prompts, but respects sandbox and hook boundaries. This is distinct from `--dangerously-skip-permissions` which bypasses everything including the sandbox.

### Layer 5: Context Isolation

Agents are prevented from seeing orchestrator internals and user state:
- `claudeMdExcludes` in `workspace/.claude/settings.local.json` blocks the parent repo's CLAUDE.md and `.claude/` directory from being loaded into agent context
- `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1` env var prevents agents from reading or writing to the user's `~/.claude/projects/` auto-memory files
- Note: `claudeMdExcludes` only works via `.claude/settings.local.json`, not the `--settings` flag

### Dynamic Agent Constraints

Dynamically recruited agents (via `NEW_AGENT.json`) are constrained:
- Model and effort locked to sonnet/medium (not configurable by the recruiting agent or Facilitator)
- Role prompt capped at 2000 characters
- Inherit `ALWAYS_BLOCKED` tools (NotebookEdit, WebFetch, WebSearch)
- Subject to the same sandbox layers as all other agents

## Security Review Findings

A comprehensive security review identified 11 findings. Addressed:

| # | Finding | Severity | Status |
|---|---------|----------|--------|
| 1 | Agents could read entire project (orchestrator, hooks, other runs) | HIGH | Fixed -- hook scoped to workspace via env var |
| 2 | Dynamic agent injection with arbitrary prompts and no tool restrictions | HIGH | Fixed -- model/effort locked, prompt capped at 2000 chars, inherits ALWAYS_BLOCKED |
| 3 | bypassPermissions + Bash = unrestricted shell | HIGH | Accepted -- mitigated by bubblewrap + network isolation |
| 4 | Hook didn't intercept Write tool; field extraction was fragile | MEDIUM | Fixed -- Write added, per-tool extraction |
| 5 | Sandbox used ambiguous relative paths | MEDIUM | Accepted -- tested and working |
| 6 | Agents can read security infrastructure | MEDIUM | Fixed -- workspace-scoped hook |
| 7 | No network restriction for Bash | MEDIUM | Fixed -- bubblewrap blocks all domains |
| 8 | System prompt visible in `ps aux` | LOW | Accepted -- agents run sequentially |
| 9 | realpath fallback didn't fail closed | LOW | Fixed -- `exit 2` on failure |
| 10 | No cost/budget controls | LOW | Accepted -- budget cap caused worse problems (cutting off agents mid-work) |
| 11 | Message board as prompt injection vector | MEDIUM | Accepted -- inherent to multi-agent design |

## Accepted Risks

- **Bash access for most agents:** Required for agents to test code. Mitigated by bubblewrap filesystem and network isolation.
- **Cross-agent prompt injection:** Agents read each other's message board posts, which could contain adversarial content. Inherent to the collaborative design.
- **No budget cap:** Removing `--max-budget-usd` was intentional -- the cap cut agents off before they could produce their summary, causing empty results.

## Testing

27 pytest tests in `test_sandbox.py` covering:
- Default fallback behavior (project root when no env var)
- Workspace-scoped read/write blocking
- Write tool interception
- Path traversal attacks (`../../.ssh/id_rsa`)
- Non-file tool passthrough (Bash, Agent)
- Edge cases (missing paths, empty inputs)
- Message board archival logic

8 integration tests in `test_runner.py` covering:
- Stream-json parsing and result capture
- Tool restriction (`--disallowedTools`)
- Sandbox settings and budget
- Network isolation (curl blocked by bubblewrap)
- Bubblewrap blocks Bash from reading home directory
- `claudeMdExcludes` prevents parent CLAUDE.md leakage
