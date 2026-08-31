#!/bin/sh
# rtk PreToolUse hook shim.
#
# rtk rewrites commands to a bare `rtk ...`, which assumes rtk is on PATH.
# Here rtk ships inside .claude/bin, so the bare prefix is replaced with the
# absolute path of the vendored binary.
#
# Every failure path exits 0 with no output, which Claude Code reads as
# "no decision" and runs the original command unchanged. Never block Bash.

set -u

DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd) || exit 0
RTK="$DIR/rtk"

[ -x "$RTK" ] || exit 0
command -v jq >/dev/null 2>&1 || exit 0

out=$("$RTK" hook claude 2>/dev/null) || exit 0
[ -n "$out" ] || exit 0

printf '%s' "$out" | jq -c --arg rtk "$RTK" '
  if (.hookSpecificOutput.updatedInput.command? // "") | startswith("rtk ")
  then .hookSpecificOutput.updatedInput.command =
         ($rtk | @sh) + (.hookSpecificOutput.updatedInput.command | ltrimstr("rtk"))
  else .
  end
' 2>/dev/null || exit 0
