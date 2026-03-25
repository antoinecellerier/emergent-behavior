#!/bin/bash
# Default-deny file access hook: only allow access within allowed paths.
# Exit 0 = allow, exit 2 = block.

# SANDBOX_ALLOWED_DIR is set by the orchestrator to the run's workspace.
# Fall back to project dir if unset (e.g. running outside orchestrator).
SANDBOX_DIR="${SANDBOX_ALLOWED_DIR:-$(cd "$(dirname "$0")/../.." && pwd)}"

hook_input=$(cat)
tool=$(echo "$hook_input" | jq -r '.tool_name // .tool // empty')

# Intercept all file-access tools
case "$tool" in
  Read|Edit|Write|Glob|Grep) ;;
  *) exit 0 ;;
esac

# Extract path — different tools use different field names
case "$tool" in
  Read|Edit|Write)
    path=$(echo "$hook_input" | jq -r '.tool_input.file_path // empty') ;;
  Glob)
    path=$(echo "$hook_input" | jq -r '.tool_input.path // empty') ;;
  Grep)
    path=$(echo "$hook_input" | jq -r '.tool_input.path // empty') ;;
esac

# If no path, allow (some tools have optional paths, default to cwd)
[ -z "$path" ] && exit 0

# Resolve to absolute path
if [[ "$path" = /* ]]; then
  abs_path="$path"
else
  abs_path="$(pwd)/$path"
fi

# Normalize (remove .., trailing slashes) — fail closed if realpath unavailable
abs_path=$(realpath -m "$abs_path" 2>/dev/null) || exit 2

# Allowed path prefixes
ALLOWED=(
  "$SANDBOX_DIR"   # run workspace (or project root as fallback)
  "/usr"           # system docs, stdlib source, man pages
  "/tmp"           # temp files from agent tests
  "/proc"          # terminal info, system state
  "/etc/timezone"  # locale info
  "/etc/localtime"
)

for prefix in "${ALLOWED[@]}"; do
  if [[ "$abs_path" = "$prefix"* ]]; then
    exit 0
  fi
done

# Block everything else
echo "Blocked: $tool access to $abs_path (outside allowed paths)" >&2
exit 2
