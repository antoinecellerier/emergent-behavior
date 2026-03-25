#!/bin/bash
# Default-deny read hook: only allow reads within the project directory.
# Exit 0 = allow, exit 2 = block.

PROJECT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"

hook_input=$(cat)
tool=$(echo "$hook_input" | jq -r '.tool_name // .tool // empty')

# Only intercept Read, Edit, Glob, Grep
case "$tool" in
  Read|Edit|Glob|Grep) ;;
  *) exit 0 ;;
esac

# Extract path from tool input
path=$(echo "$hook_input" | jq -r '.tool_input.file_path // .tool_input.path // .tool_input.pattern // empty')

# If no path, allow (some tools have optional paths)
[ -z "$path" ] && exit 0

# Resolve to absolute path
if [[ "$path" = /* ]]; then
  abs_path="$path"
else
  abs_path="$(pwd)/$path"
fi

# Normalize (remove .., trailing slashes)
abs_path=$(realpath -m "$abs_path" 2>/dev/null || echo "$abs_path")

# Allowed path prefixes
ALLOWED=(
  "$PROJECT_DIR"   # project workspace
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
echo "Blocked: $tool access to $abs_path (outside project)" >&2
exit 2
