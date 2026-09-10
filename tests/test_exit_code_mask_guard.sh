#!/usr/bin/env bash
# Pin: .claude/hooks/exit_code_mask_guard.sh and lint's MF022 must return the
# SAME verdict on one corpus.
#
# WHY THIS FILE EXISTS (2026-09-09). The hook's own header claimed this file
# already pinned the two implementations, citing honest_failure_modes #5 ("two
# consumers of one artifact share ONE constant") by name. It did not exist —
# `git log --all -- tests/test_exit_code_mask_guard.sh` returned nothing, so it
# never had. And the pair had already drifted, exactly as #5 predicts:
#
#   python3 -m pytest tests/ | grep -v ok | tail -3; rc=$?
#       MF022 FIRED · the hook was SILENT      <- false NEGATIVE, common shape
#   echo "never do: pytest tests/ | tail -3; rc=$?"
#       MF022 clean · the hook FIRED           <- false positive on a fix-hint
#
# The hook's bash regex used `[^|]*`, which cannot cross an intervening pipe,
# and had no quote-awareness. The guard that actually runs on every Bash call
# was blind to the shape lint catches.
#
# The cure was #5's strongest form — derive, don't duplicate: the hook's slow
# path now calls lint's real MF022 predicate. This file is the regression pin
# on that, and it is deliberately written so it would have FAILED before the
# fix (feedback_a_guard_that_never_failed_is_not_evidence).
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
HOOK="$REPO/.claude/hooks/exit_code_mask_guard.sh"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
fails=0
check() { if [ -n "$2" ]; then echo "PASS: $1"; else echo "FAIL: $1"; fails=1; fi; }

# ── verdict of record: lint's MF022 on the command, as a shell line ───────
lint_verdict() {
  local d; d="$(mktemp -d "$TMP/lint.XXXXXX")"
  printf '%s\n' "$1" > "$d/case.sh"
  ( cd "$REPO" && LINT_CASE="$d/case.sh" python3 - <<'PY' 2>/dev/null
import os, sys
sys.path.insert(0, "scripts")
import lint
p = os.environ["LINT_CASE"]
issues = lint.check_pip_invocations_in_files([p], repo_root=os.path.dirname(p))
print("FIRE" if any(i.code == "MF022" for i in issues) else "CLEAN")
PY
  )
}

# ── verdict of the hook, read from its own witness (its only output) ──────
hook_verdict() {
  local w p
  w="$(mktemp "$TMP/wit.XXXXXX")"; p="$(mktemp "$TMP/pay.XXXXXX")"
  HOOK_CASE="$1" python3 - > "$p" <<'PY'
import json, os
print(json.dumps({"tool_name": "Bash",
                  "tool_input": {"command": os.environ["HOOK_CASE"]}}))
PY
  EXIT_CODE_MASK_WITNESS="$w" CLAUDE_PROJECT_DIR="$REPO" \
    bash "$HOOK" < "$p" >/dev/null 2>&1
  if grep -q 'MASKED-EXIT-CODE' "$w" 2>/dev/null; then echo FIRE; else echo CLEAN; fi
}

# ── the corpus ────────────────────────────────────────────────────────────
# Cases 4 and 5 are the measured divergences; they are the reason this file
# exists and must never silently agree again by both going dark.
pin() {
  local name="$1" cmd="$2" want="$3" a b
  a="$(lint_verdict "$cmd")"; b="$(hook_verdict "$cmd")"
  check "$name: MF022=$a hook=$b agree" "$([ "$a" = "$b" ] && echo ok)"
  check "$name: verdict is $want (not both-dark)" "$([ "$a" = "$want" ] && echo ok)"
}

pin "plain mask"            'python3 -m pytest tests/ | tail -3; rc=$?'                FIRE
pin "display only, no rc"   'python3 -m pytest tests/ | tail -3'                       CLEAN
pin "captured rc, no pipe"  'python3 -m pytest tests/ > log 2>&1; rc=$?'               CLEAN
pin "INTERVENING pipe"      'python3 -m pytest tests/ | grep -v ok | tail -3; rc=$?'   FIRE
pin "quoted fix-hint"       'echo "never do: pytest tests/ | tail -3; rc=$?"'          CLEAN
pin "head not tail"         'git log --oneline | head -5; echo $?'                     FIRE
pin "non-verdict command"   'cat foo.txt | tail -3; rc=$?'                             CLEAN
pin "honest_status piped"   'bash scripts/honest_status.sh | tail -20; rc=$?'          FIRE
pin "ssh piped"             'ssh box systemctl is-active rnsd | tail -1; rc=$?'        FIRE

# ── fail-safe invariants: this hook's blast radius is EVERY Bash call ─────
# feedback_never_arm_a_guard_that_can_kill_the_session — it must ALWAYS exit 0
# and must degrade to silence, never to a wedge.
safe_exit() {
  local w; w="$(mktemp "$TMP/se.XXXXXX")"
  EXIT_CODE_MASK_WITNESS="$w" bash "$HOOK" >/dev/null 2>&1 <<<"$1"
  echo "$?"
}
check "malformed JSON exits 0"   "$([ "$(safe_exit 'not json | tail -3 $?')" = 0 ] && echo ok)"
check "empty payload exits 0"    "$([ "$(safe_exit '')" = 0 ] && echo ok)"
check "non-Bash tool exits 0"    "$([ "$(safe_exit '{"tool_name":"Read","tool_input":{"command":"pytest | tail; rc=$?"}}')" = 0 ] && echo ok)"
check "missing command exits 0"  "$([ "$(safe_exit '{"tool_name":"Bash","tool_input":{}}')" = 0 ] && echo ok)"

# With lint unreachable the hook must FALL BACK, not go dark — an unobservable
# guard is not a passing guard (honest_failure_modes #2).
w="$(mktemp "$TMP/fb.XXXXXX")"; p="$(mktemp "$TMP/fbp.XXXXXX")"
HOOK_CASE='python3 -m pytest tests/ | tail -3; rc=$?' python3 - > "$p" <<'PY'
import json, os
print(json.dumps({"tool_name": "Bash",
                  "tool_input": {"command": os.environ["HOOK_CASE"]}}))
PY
EXIT_CODE_MASK_WITNESS="$w" CLAUDE_PROJECT_DIR="$TMP/no-such-repo" \
  bash "$HOOK" < "$p" >/dev/null 2>&1
fb_rc=$?
check "lint unimportable still exits 0" "$([ "$fb_rc" = 0 ] && echo ok)"
check "lint unimportable FALLS BACK (fires), does not go dark" \
  "$(grep -q 'MASKED-EXIT-CODE' "$w" 2>/dev/null && echo ok)"

# ── the OTHER unpinned pair (found 2026-09-09) ───────────────────────────
# The hook that actually RUNS is ~/.claude/hooks/exit_code_mask_guard.sh, wired
# from ~/.claude/settings.json — a COPY of the repo file. Two files, one rule:
# the same #5 shape one level up. Where the copy is installed it must match.
# Where it is absent (CI, a fresh box) that is absent-by-design, not drift —
# report it as such rather than letting a skip look like agreement.
USER_HOOK="$HOME/.claude/hooks/exit_code_mask_guard.sh"
if [ -f "$USER_HOOK" ]; then
  check "installed user copy is identical to the repo copy" \
    "$(cmp -s "$USER_HOOK" "$HOOK" && echo ok)"
else
  echo "PASS: user copy not installed here (absent-by-design, not drift)"
fi

if [ "$fails" = 0 ]; then echo "ALL PASS"; exit 0; else echo "FAILED"; exit 1; fi
