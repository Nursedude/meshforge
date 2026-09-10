#!/usr/bin/env bash
# Pin: .claude/hooks/exit_code_mask_guard.sh and lint's MF022 exit-code-mask
# predicate must return the SAME verdict on one corpus, and every degraded
# path of the hook must leave a witness row and exit 0.
#
# WHY THIS FILE EXISTS (2026-09-09). The hook's own header claimed this file
# already pinned the two implementations, citing honest_failure_modes #5 by
# name. It did not exist, and the pair had already drifted:
#
#   python3 -m pytest tests/ | grep -v ok | tail -3; rc=$?
#       MF022 FIRED · the hook was SILENT      <- false NEGATIVE, common shape
#   echo "never do: pytest tests/ | tail -3; rc=$?"
#       MF022 clean · the hook FIRED           <- false positive on a fix-hint
#
# The cure was #5's strongest form — derive, don't duplicate. This file is the
# regression pin, written so it FAILED before each fix
# (feedback_a_guard_that_never_failed_is_not_evidence). Review pass 4 (same
# day) found the first pin could not fail on two of its own cases (findings
# 13/14): an EMPTY witness read as CLEAN, so "hook went dark" and "judged
# near-miss" were the same verdict; and the fallback case fired under both
# paths. Now: an empty witness is a third state, DARK, that fails every
# expectation; the fallback case is the discriminating one (lint CLEAN /
# fallback FIRE) and runs from `/` so the CWD cannot supply lint.
#
# ECMG_HOOK_UNDER_TEST overrides the hook path so the pin can be run against a
# saved pre-fix copy to quote how many cases it catches.
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
HOOK="${ECMG_HOOK_UNDER_TEST:-$REPO/.claude/hooks/exit_code_mask_guard.sh}"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
fails=0; nfail=0
check() { if [ -n "$2" ]; then echo "PASS: $1"; else echo "FAIL: $1"; fails=1; nfail=$((nfail+1)); fi; }

# ── verdict of record: lint's shared predicate on the command text ───────
lint_verdict() {
  ( cd "$REPO" && LINT_CASE="$1" python3 -I - <<'PY' 2>/dev/null
import os, sys
sys.path.insert(0, "scripts")
import lint
print("FIRE" if lint.mf022_exit_code_mask_findings(os.environ["LINT_CASE"]) else "CLEAN")
PY
  )
}

# ── run the hook on a command; witness path in $W, payload in $P ─────────
payload_for() {  # $1=command -> path
  local p; p="$(mktemp "$TMP/pay.XXXXXX")"
  HOOK_CASE="$1" python3 -I - > "$p" <<'PY'
import json, os
print(json.dumps({"tool_name": "Bash",
                  "tool_input": {"command": os.environ["HOOK_CASE"]}}))
PY
  echo "$p"
}
run_hook() {  # $1=payload path; extra env via caller; sets W, OUT, RC
  W="$(mktemp "$TMP/wit.XXXXXX")"; OUT="$(mktemp "$TMP/out.XXXXXX")"
  EXIT_CODE_MASK_WITNESS="$W" EXIT_CODE_MASK_TRACE=1 CLAUDE_PROJECT_DIR="${HOOK_PROJECT_DIR:-$REPO}" \
    bash "$HOOK" < "$1" > "$OUT" 2>/dev/null
  RC=$?
}
# Field 2 of the LAST witness row. An EMPTY witness is DARK — a third state
# that no expectation accepts (finding 14: it used to read as CLEAN). The
# TRACE seam makes a pre-filter exit write `prefiltered`, which is CLEAN.
witness_state() { awk -F'\t' 'END{print (NR ? $2 : "DARK")}' "$1" 2>/dev/null; }
witness_src()   { awk -F'\t' 'END{print (NR ? $4 : "")}' "$1" 2>/dev/null; }
witness_line()  { awk -F'\t' 'END{print (NR ? $3 : "")}' "$1" 2>/dev/null; }
hook_verdict() {
  local p; p="$(payload_for "$1")"; run_hook "$p"
  case "$(witness_state "$W")" in
    MASKED-EXIT-CODE) echo FIRE ;; near-miss|prefiltered) echo CLEAN ;; *) echo DARK ;;
  esac
}

# ── the corpus ────────────────────────────────────────────────────────────
pin() {
  local name="$1" cmd="$2" want="$3" a b
  a="$(lint_verdict "$cmd")"; b="$(hook_verdict "$cmd")"
  check "$name: MF022=$a hook=$b agree" "$([ "$a" = "$b" ] && echo ok)"
  check "$name: verdict is $want (not dark)" "$([ "$a" = "$want" ] && [ "$b" = "$want" ] && echo ok)"
}
NL=$'\n'
pin "plain mask"            'python3 -m pytest tests/ | tail -3; rc=$?'                FIRE
pin "display only, no rc"   'python3 -m pytest tests/ | tail -3'                       CLEAN
pin "captured rc, no pipe"  'python3 -m pytest tests/ > log 2>&1; rc=$?'               CLEAN
pin "INTERVENING pipe"      'python3 -m pytest tests/ | grep -v ok | tail -3; rc=$?'   FIRE
pin "quoted fix-hint"       'echo "never do: pytest tests/ | tail -3; rc=$?"'          CLEAN
pin "head not tail"         'git log --oneline | head -5; echo $?'                     FIRE
pin "non-verdict command"   'cat foo.txt | tail -3; rc=$?'                             CLEAN
pin "honest_status piped"   'bash scripts/honest_status.sh | tail -20; rc=$?'          FIRE
pin "ssh piped"             'ssh box systemctl is-active rnsd | tail -1; rc=$?'        FIRE
# finding 5: consumption without naming $?
pin "&& echo GREEN"         'python3 -m pytest tests/ -q | tail -3 && echo GREEN'      FIRE
pin "if ... | tail; then"   'if python3 -m pytest tests/ -q | tail -3; then echo ok; fi' FIRE
pin "|| exit 1"             'python3 -m pytest tests/ -q | tail -3 || exit 1'          FIRE
pin "|| true is a discard"  'git log | head -3 || true'                                CLEAN
# finding 6: apostrophe inside double quotes earlier on the line
pin "apostrophe in dq"      'echo "Bob'"'"'s run"; git log | head -3; rc=$?'          FIRE
# finding 7: the $? read behind a comment / blank lines / a continuation
pin "rc behind comment"     "python3 -m pytest tests/ | tail -3${NL}# grab rc${NL}rc=\$?"   FIRE
pin "rc after blank lines"  "python3 -m pytest tests/ | tail -3${NL}${NL}${NL}rc=\$?"       FIRE
pin "backslash continuation" "python3 -m pytest tests/ \\${NL}  | tail -3${NL}rc=\$?"     FIRE
# finding 8: the $? must belong to the pipeline
pin "rc read BEFORE pipe"   'python3 -m pytest tests/ > log 2>&1; rc=$?; cat log | tail -3' CLEAN
pin "pipefail exempts"      'set -o pipefail; python3 -m pytest tests/ | tail -3; rc=$?' CLEAN
pin "intervening statement" "python3 -m pytest tests/ | tail -3${NL}ls${NL}rc=\$?"       CLEAN
pin "pure echo between"     'curl -s http://x/api | head -c 4000; echo; echo "EXIT:$?"' FIRE
# finding 10: pipe at end of line, tail on the next (raw JSON carries \n)
pin "pipe at EOL"           "python3 -m pytest tests/ |${NL}tail -3${NL}rc=\$?"          FIRE
# finding 4: lint's OTHER MF022 sub-rules are not an exit-code mask
pin "bare pip is not a mask" "pip install --user foo${NL}ls | head -3${NL}echo \$?"      CLEAN

# ── finding 1: the warning rides a channel the model sees, without blocking ─
p="$(payload_for 'python3 -m pytest tests/ | tail -3; rc=$?')"; run_hook "$p"
check "masked: exit 0" "$([ "$RC" = 0 ] && echo ok)"
check "masked: stdout is PreToolUse JSON with additionalContext, no decision" \
  "$(JSON_OUT="$OUT" python3 -I - <<'PY' 2>/dev/null
import json, os
d = json.load(open(os.environ["JSON_OUT"]))
h = d["hookSpecificOutput"]
assert h["hookEventName"] == "PreToolUse"
assert "exit-code mask" in h["additionalContext"]
assert "permissionDecision" not in h and "decision" not in d
print("ok")
PY
)"
p="$(payload_for 'python3 -m pytest tests/ | tail -3')"; run_hook "$p"
check "near-miss: stdout empty" "$([ ! -s "$OUT" ] && echo ok)"

# ── finding 2: witness never carries a secret; file is 0600 ──────────────
p="$(payload_for 'curl -s -H "Authorization: Bearer hunter2secret" https://x/api?token=tok3nvalue | head -c 400; rc=$?')"
run_hook "$p"
check "secret: verdict still fires" "$([ "$(witness_state "$W")" = MASKED-EXIT-CODE ] && echo ok)"
check "secret: bearer value redacted" "$(! grep -q 'hunter2secret' "$W" && echo ok)"
check "secret: token= value redacted" "$(! grep -q 'tok3nvalue' "$W" && echo ok)"
check "secret: command SHAPE kept" "$(grep -q 'curl -s -H' "$W" && echo ok)"
check "witness file is 0600" "$([ "$(stat -c %a "$W")" = 600 ] && echo ok)"

# ── finding 12: the offending LINE is logged, not the first 160 chars ─────
long="$(printf 'echo preamble-%03d;' $(seq 1 40))"
p="$(payload_for "${long}${NL}python3 -m pytest tests/ | tail -3${NL}rc=\$?")"; run_hook "$p"
check "offending line logged" "$(witness_line "$W" | grep -q 'pytest tests/ | tail -3' && echo ok)"
check "source is lint" "$([ "$(witness_src "$W")" = lint ] && echo ok)"

# ── fail-safe invariants: this hook's blast radius is EVERY Bash call ─────
# It must ALWAYS exit 0 and every degraded path must leave a DARK-<reason>
# row (finding 12: "leaves a witness either way" was false on all of them).
dark_case() {  # $1=name $2=payload-text $3=expected state ; extra env via caller
  W="$(mktemp "$TMP/dk.XXXXXX")"; OUT="$(mktemp "$TMP/dko.XXXXXX")"
  EXIT_CODE_MASK_WITNESS="$W" CLAUDE_PROJECT_DIR="$REPO" bash "$HOOK" > "$OUT" 2>/dev/null <<<"$2"
  RC=$?
  check "$1: exits 0" "$([ "$RC" = 0 ] && echo ok)"
  check "$1: witness state $3" "$([ "$(witness_state "$W")" = "$3" ] && echo ok)"
  check "$1: no stdout" "$([ ! -s "$OUT" ] && echo ok)"
}
dark_case "malformed JSON" 'not json | tail -3 $?' DARK-badjson
dark_case "non-Bash tool" '{"tool_name":"Read","tool_input":{"command":"pytest | tail; rc=$?"}}' skip-nonbash
dark_case "missing command" '{"tool_name":"Bash","tool_input":{"description":"x | tail; rc=$?"}}' DARK-nocmd
big="$(python3 -I - <<'PY'
import json
print(json.dumps({"tool_name": "Bash", "tool_input": {"command": "python3 x | tail -3; rc=$?\n" + "#" * 125000}}))
PY
)"
dark_case "oversize payload" "$big" DARK-oversize
EXIT_CODE_MASK_PYTHON=/bin/false dark_case "python crashes" '{"tool_name":"Bash","tool_input":{"command":"python3 x | tail -3; rc=$?"}}' DARK-pycrash
printf '#!/bin/sh\nsleep 5\n' > "$TMP/slowpy"; chmod +x "$TMP/slowpy"
EXIT_CODE_MASK_PYTHON="$TMP/slowpy" EXIT_CODE_MASK_TIMEOUT=1 \
  dark_case "python times out" '{"tool_name":"Bash","tool_input":{"command":"python3 x | tail -3; rc=$?"}}' DARK-timeout
check "empty payload exits 0" "$(bash "$HOOK" >/dev/null 2>&1 <<<""; [ $? = 0 ] && echo ok)"

# ── finding 11: HOME unset must not unbind the hook on every Bash call ────
p="$(payload_for 'python3 -m pytest tests/ | tail -3; rc=$?')"
W="$(mktemp "$TMP/nh.XXXXXX")"
env -u HOME EXIT_CODE_MASK_WITNESS="$W" CLAUDE_PROJECT_DIR="$REPO" bash "$HOOK" < "$p" >/dev/null 2>&1
check "HOME unset: exits 0" "$([ $? = 0 ] && echo ok)"
check "HOME unset: still judges" "$([ "$(witness_state "$W")" = MASKED-EXIT-CODE ] && echo ok)"
env -u HOME -u EXIT_CODE_MASK_WITNESS CLAUDE_PROJECT_DIR="$REPO" bash "$HOOK" < "$p" >/dev/null 2>&1
check "HOME unset, no witness path: exits 0" "$([ $? = 0 ] && echo ok)"

# ── finding 3: lint is imported only from the repo this hook was pinned in ─
# (a) a decoy lint.py in the CWD must never be executed (python -I).
mkdir -p "$TMP/cwd"; printf 'import sys\nprint("HELLO-FROM-FOREIGN-LINT")\nsys.exit(3)\n' > "$TMP/cwd/lint.py"
p="$(payload_for 'echo "never do: pytest tests/ | tail -3; rc=$?"')"
W="$(mktemp "$TMP/cw.XXXXXX")"; OUT="$(mktemp "$TMP/cwo.XXXXXX")"
( cd "$TMP/cwd" && EXIT_CODE_MASK_WITNESS="$W" CLAUDE_PROJECT_DIR="$REPO" bash "$HOOK" < "$p" > "$OUT" 2>&1 )
check "decoy CWD lint: real lint judged (CLEAN via lint)" \
  "$([ "$(witness_state "$W")" = near-miss ] && [ "$(witness_src "$W")" = lint ] && echo ok)"
check "decoy CWD lint: never executed" "$(! grep -rq HELLO-FROM-FOREIGN-LINT "$W" "$OUT" && echo ok)"
# (b) a foreign CLAUDE_PROJECT_DIR whose scripts/lint.py is a decoy: no
# byte-identical hook there, so lint is NOT imported and the row says so.
mkdir -p "$TMP/foreign/scripts"; cp "$TMP/cwd/lint.py" "$TMP/foreign/scripts/lint.py"
W="$(mktemp "$TMP/fr.XXXXXX")"; OUT="$(mktemp "$TMP/fro.XXXXXX")"
( cd / && EXIT_CODE_MASK_WITNESS="$W" CLAUDE_PROJECT_DIR="$TMP/foreign" bash "$HOOK" < "$p" > "$OUT" 2>&1 )
check "foreign repo lint: not executed" "$(! grep -rq HELLO-FROM-FOREIGN-LINT "$W" "$OUT" && echo ok)"
check "foreign repo lint: row marked fallback" "$(witness_src "$W" | grep -q '^fallback:' && echo ok)"
# (c) a repo that DOES ship this hook + pin, whose lint raises SystemExit:
# BaseException is caught, the guard falls back and the row names it.
mkdir -p "$TMP/evil/scripts" "$TMP/evil/.claude/hooks" "$TMP/evil/tests"
cp "$HOOK" "$TMP/evil/.claude/hooks/exit_code_mask_guard.sh"; : > "$TMP/evil/tests/test_exit_code_mask_guard.sh"
printf 'MF022_EXITCODE_MASK = 1\nimport sys\nsys.exit(3)\n' > "$TMP/evil/scripts/lint.py"
W="$(mktemp "$TMP/ev.XXXXXX")"
( cd / && EXIT_CODE_MASK_WITNESS="$W" CLAUDE_PROJECT_DIR="$TMP/evil" bash "$HOOK" < "$p" >/dev/null 2>&1 )
check "lint raises SystemExit: exits 0" "$([ $? = 0 ] && echo ok)"
check "lint raises SystemExit: witness row, not dark" \
  "$([ "$(witness_src "$W")" = "fallback:import-SystemExit" ] && echo ok)"

# ── finding 13: lint unreachable FALLS BACK — on the DISCRIMINATING case ──
# The quoted fix-hint is CLEAN under lint and FIRES under the coarse fallback,
# so this case can tell the two paths apart (the plain mask fired under both).
W="$(mktemp "$TMP/fb.XXXXXX")"
( cd / && EXIT_CODE_MASK_WITNESS="$W" CLAUDE_PROJECT_DIR="$TMP/no-such-repo" bash "$HOOK" < "$p" >/dev/null 2>&1 )
check "lint unimportable still exits 0" "$([ $? = 0 ] && echo ok)"
check "lint unimportable FALLS BACK (fires on the hint), does not go dark" \
  "$([ "$(witness_state "$W")" = MASKED-EXIT-CODE ] && echo ok)"
check "fallback row is marked fallback:norepo" "$([ "$(witness_src "$W")" = "fallback:norepo" ] && echo ok)"

# ── finding 9: concurrent instances must not lose witness rows ────────────
W="$(mktemp "$TMP/cc.XXXXXX")"
for i in $(seq 1 505); do printf '2026-01-01T00:00:00Z\tnear-miss\tfiller %d\tlint\n' "$i"; done > "$W"
pids=""
for i in $(seq 1 8); do
  p="$(payload_for "python3 -m pytest tests/ | tail -3; rc=\$? # ccmark-$i")"
  EXIT_CODE_MASK_WITNESS="$W" CLAUDE_PROJECT_DIR="$REPO" bash "$HOOK" < "$p" >/dev/null 2>&1 &
  pids="$pids $!"
done
for pid in $pids; do wait "$pid"; done
got="$(grep -c 'ccmark-' "$W")"
check "8 concurrent instances: all 8 rows survive rotation (got $got)" "$([ "$got" = 8 ] && echo ok)"
check "witness rotated to the cap" "$([ "$(wc -l < "$W")" -le 500 ] && echo ok)"

# ── the OTHER unpinned pair: the installed copy must match the repo copy ──
USER_HOOK="${HOME:-/nonexistent}/.claude/hooks/exit_code_mask_guard.sh"
if [ -f "$USER_HOOK" ]; then
  check "installed user copy is identical to the repo copy" \
    "$(cmp -s "$USER_HOOK" "$REPO/.claude/hooks/exit_code_mask_guard.sh" && echo ok)"
else
  echo "PASS: user copy not installed here (absent-by-design, not drift)"
fi

if [ "$fails" = 0 ]; then echo "ALL PASS"; exit 0; else echo "FAILED ($nfail checks)"; exit 1; fi
