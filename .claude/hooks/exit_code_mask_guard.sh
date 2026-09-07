#!/usr/bin/env bash
# exit_code_mask_guard — PreToolUse(Bash) guard for the exit-code MASK idiom in
# the AGENT-PROCESS lane, where lint cannot reach: lint sees files in the repo,
# it never sees the command I am about to run in a tool call.
#
# The class (defect 3 of .claude/plans/exit_code_gate_and_harness_audit.md):
#   python3 -m pytest ... | tail -3
#   echo "rc=$?"            <-- that is TAIL's exit code, not pytest's
# It recurred on 2026-09-07 in a session that had just named it, and produced a
# reported-green suite that had actually failed two tests.
#
# ── WARN-ONLY, DELIBERATELY ───────────────────────────────────────────────
# A PreToolUse hook's blast radius is EVERY Bash call, including the ones the
# session needs to fix a mistake this hook makes
# ([[feedback_never_arm_a_guard_that_can_kill_the_session]]: never arm a guard
# whose blast radius holds the session arming it). So:
#   * it NEVER emits a deny decision and ALWAYS exits 0;
#   * every path is guarded so a bug degrades to silence, not to a wedged
#     session;
#   * it leaves a WITNESS either way, because "did the guard even run?" must be
#     answerable after the fact (honest_failure_modes #9) — and the witness log
#     is what will later justify (or refuse) promoting this to a deny.
#
# ── LATENCY ───────────────────────────────────────────────────────────────
# Measured on VolcanoAI (Pi 5, 2026-09-07): the existing psk_leak_guard costs
# 55.5 ms/call, bare python3 startup 21.0 ms, `import lint` 83.7 ms. So the
# rule is NOT shared by importing lint.py — it would nearly double the
# per-call hook cost of the whole session. Instead a bash-only pre-filter runs
# first on the RAW payload; python is invoked only for the rare command that
# could actually match. `tests/test_exit_code_mask_guard.sh` pins this guard
# and lint's MF022 to the SAME verdicts on one corpus, which is what keeps two
# implementations of one rule from drifting (honest_failure_modes #5: derive,
# import, or TEST-PIN them together).

set -u
WITNESS="${EXIT_CODE_MASK_WITNESS:-$HOME/.claude/hooks/exit_code_mask_witness.log}"
MAX_WITNESS_LINES=500

payload="$(cat 2>/dev/null)" || exit 0
[ -n "$payload" ] || exit 0

# ── fast path: two string greps, no interpreter ───────────────────────────
# Cheap enough to sit in front of every Bash call. A command with no pipe into
# head/tail, or that never reads $?, cannot be this defect.
printf '%s' "$payload" | grep -Eq '\|[[:space:]]*(tail|head)\b' || exit 0
printf '%s' "$payload" | grep -q '[$]?' || exit 0

# ── slow path (rare): parse the command out and decide precisely ──────────
cmd="$(printf '%s' "$payload" | timeout 5 python3 -c 'import json,sys
try:
    d = json.load(sys.stdin)
    if d.get("tool_name") != "Bash":
        sys.exit(0)
    print(d.get("tool_input", {}).get("command", ""))
except Exception:
    sys.exit(0)' 2>/dev/null)" || exit 0
[ -n "$cmd" ] && [ "${#cmd}" -lt 200000 ] || exit 0

VERDICT_CMDS='pytest|python3|python|gh|git|systemctl|curl|ssh|lint\.py|honest_status\.sh'
if printf '%s' "$cmd" \
     | grep -Eq "(${VERDICT_CMDS})[^|]*\|[[:space:]]*(tail|head)\b" \
   && printf '%s' "$cmd" | grep -q '[$]?'; then
  note="MASKED-EXIT-CODE"
  printf '⚠️  exit-code mask: a verdict-bearing command is piped to tail/head and $? is then read — that is the PIPE'"'"'s exit code, not the command'"'"'s. Capture it first: `cmd >log 2>&1; rc=$?` then read the log. (WARN only; nothing was blocked.)\n' >&2
else
  note="near-miss"
fi

# Witness, always — a guard that leaves no trace cannot be audited later.
{
  printf '%s\t%s\t%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$note" \
         "$(printf '%s' "$cmd" | tr '\n' ' ' | cut -c1-160)"
} >> "$WITNESS" 2>/dev/null || true
if [ -f "$WITNESS" ]; then
  lines="$(wc -l < "$WITNESS" 2>/dev/null || echo 0)"
  if [ "${lines:-0}" -gt "$MAX_WITNESS_LINES" ] 2>/dev/null; then
    tail -n "$MAX_WITNESS_LINES" "$WITNESS" > "$WITNESS.tmp" 2>/dev/null \
      && mv "$WITNESS.tmp" "$WITNESS" 2>/dev/null || true
  fi
fi
exit 0
