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
# ── ONE IMPLEMENTATION OF THE RULE (2026-09-09) ───────────────────────────
# This file used to re-implement MF022's discriminator in bash, and its header
# claimed `tests/test_exit_code_mask_guard.sh` pinned the two to the same
# verdicts. That test NEVER EXISTED (`git log --all -- <path>` returns nothing),
# and the two implementations had already DRIFTED — measured on a 7-case corpus:
#
#   python3 -m pytest tests/ | grep -v ok | tail -3; rc=$?
#       MF022 FIRES · this guard was SILENT   <- false negative, common shape
#   echo "never do: pytest tests/ | tail -3; rc=$?"
#       MF022 clean · this guard FIRED        <- false positive on a fix-hint
#
# The bash regex used `[^|]*`, which cannot cross an intervening pipe, and had
# no quote-awareness. So the guard that actually runs was blind to the shape
# lint catches — exactly the unpinned pair honest_failure_modes #5 predicts.
#
# The cure is #5's STRONGEST form: derive, don't duplicate. The slow path now
# hands the command to lint's real MF022 predicate, inheriting quote-awareness
# and the next-line `$?` lookahead for free. `tests/test_exit_code_mask_guard.sh`
# now exists and pins the two to identical verdicts on that corpus.
#
# ── LATENCY ───────────────────────────────────────────────────────────────
# Measured on VolcanoAI (Pi 5, 2026-09-07): the existing psk_leak_guard costs
# 55.5 ms/call, bare python3 startup 21.0 ms, `import lint` 83.7 ms. So the rule
# is NOT imported on the fast path — that would put it on EVERY Bash call. A
# bash-only pre-filter (two greps, no interpreter) runs first on the RAW
# payload; python is invoked only for the rare command that could actually
# match, and that path already paid for an interpreter. Re-measured 2026-09-09:
# bare python3 19 ms, +import lint 72 ms — so the change costs ~53 ms on the
# slow path only, and 0 ms on the per-call cost of the session.
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

# ── slow path (rare): defer to lint's MF022, the single implementation ────
REPO="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." 2>/dev/null && pwd)}"
# The payload rides an ENV VAR, not stdin: a `<<HEREDOC` redirects stdin, so a
# `printf ... | python3 - <<PY` pipeline silently feeds the SCRIPT to the script
# and the real payload is discarded (measured — the first draft of this change
# went dark on every case). Guard the size before exporting.
[ "${#payload}" -lt 200000 ] || exit 0
verdict="$(EXIT_CODE_MASK_PAYLOAD="$payload" timeout 10 python3 - "${REPO:-.}" <<'PY' 2>/dev/null || true
import json, os, re, sys, tempfile

FALLBACK_CMDS = ('pytest', 'python3', 'python', 'gh', 'git', 'systemctl',
                 'curl', 'ssh', 'lint.py', 'honest_status.sh')


def fallback(cmd):
    """Used only when lint cannot be imported (another repo, moved file).
    Deliberately the OLD inline rule minus the `[^|]*` bug: answering with a
    slightly noisy verdict beats going dark."""
    rx = re.compile(r'\b(' + '|'.join(re.escape(c) for c in FALLBACK_CMDS)
                    + r')\b.*\|\s*(tail|head)\b')
    return bool(rx.search(cmd)) and '$?' in cmd


try:
    d = json.loads(os.environ.get("EXIT_CODE_MASK_PAYLOAD", ""))
    if d.get("tool_name") != "Bash":
        sys.exit(0)
    cmd = d.get("tool_input", {}).get("command", "")
except Exception:
    sys.exit(0)

if not cmd or len(cmd) >= 200000:
    sys.exit(0)

fired = None
try:
    sys.path.insert(0, os.path.join(sys.argv[1], 'scripts'))
    import lint
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, 'agent_cmd.sh')
        with open(p, 'w', encoding='utf-8') as fh:
            fh.write(cmd + "\n")
        issues = lint.check_pip_invocations_in_files([p], repo_root=td)
    fired = any(i.code == "MF022" for i in issues)
except Exception:
    fired = None

if fired is None:
    fired = fallback(cmd)

print("MASKED-EXIT-CODE" if fired else "near-miss")
print(cmd.replace("\n", " ")[:160])
PY
)" || true

note="$(printf '%s' "$verdict" | sed -n 1p)"
cmdline="$(printf '%s' "$verdict" | sed -n 2p)"
[ -n "$note" ] || exit 0

if [ "$note" = "MASKED-EXIT-CODE" ]; then
  printf '⚠️  exit-code mask: a verdict-bearing command is piped to tail/head and $? is then read — that is the PIPE'"'"'s exit code, not the command'"'"'s. Capture it first: `cmd >log 2>&1; rc=$?` then read the log. (WARN only; nothing was blocked.)\n' >&2
fi

# Witness, always — a guard that leaves no trace cannot be audited later.
{
  printf '%s\t%s\t%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$note" "$cmdline"
} >> "$WITNESS" 2>/dev/null || true
if [ -f "$WITNESS" ]; then
  lines="$(wc -l < "$WITNESS" 2>/dev/null || echo 0)"
  if [ "${lines:-0}" -gt "$MAX_WITNESS_LINES" ] 2>/dev/null; then
    tail -n "$MAX_WITNESS_LINES" "$WITNESS" > "$WITNESS.tmp" 2>/dev/null \
      && mv "$WITNESS.tmp" "$WITNESS" 2>/dev/null || true
  fi
fi
exit 0
