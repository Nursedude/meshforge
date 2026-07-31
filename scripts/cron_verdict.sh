#!/bin/bash
# cron_verdict.sh — every fleet cron leaves a dated, greppable verdict.
#
# WHY: silence is the failure mode (the fleet's recurring lesson — a dead
# cron is indistinguishable from a healthy one). Every organ leaves one
# line per run in ~/cron_verdicts.log; cron_verdict_freshness.sh (on the
# federator) flags any organ whose last verdict is older than expected.
#
# Usage (crontab idiom — no script edits needed, captures the exit code):
#   */30 * * * * /path/job.sh >/dev/null 2>&1; /opt/meshforge/scripts/cron_verdict.sh job $?
#
# PREFERRED idiom — same thing, but a FAIL can name its cause (see "evidence
# capture" below). scripts/cron_capture_wire.py rewrites the /dev/null form to
# this one in place:
#   */30 * * * * /path/job.sh >$HOME/.local/state/meshforge/cron_out/job.out 2>&1; /opt/meshforge/scripts/cron_verdict.sh job $?
#
# Or from inside a script, with a message:
#   cron_verdict.sh my_job OK "3 peers checked, all green"
#   cron_verdict.sh my_job CONCERN "2/3 peers slow"
#
# Args:
#   $1  name           short cron identifier (no spaces)
#   $2  status         numeric exit code (0->OK, nonzero->FAIL(n)) or
#                      literal OK | FAIL | CONCERN
#   $3+ message        optional free text
#
# Log shape: <ISO-8601-UTC> <name> <STATUS> <message>
# Self-truncating (SD-card friendly) — but retention is PER-NAME aware: the
# newest KEEP_PER_NAME lines of every cron survive truncation even when
# high-churn crons (5-min cadence) push them past MAX_LINES. Without this, a
# daily cron's single verdict scrolled out ~23.5h after its run and
# probe_cron_verdict_stale (Issue #78) read healthy crons as "silent: never"
# — the reader/writer retention drift found 2026-07-09 (the retention floor
# must exceed the slowest wired cadence x the probe's CADENCE_MULT).
# Bound: MAX_LINES + (#names x KEEP_PER_NAME).

LOG="${CRON_VERDICT_LOG:-$HOME/cron_verdicts.log}"
MAX_LINES=1000
KEEP_PER_NAME=30

name="${1:?usage: cron_verdict.sh <name> <exit-code|OK|FAIL|CONCERN> [msg]}"
raw="${2:?missing status}"
shift 2
msg="$*"

case "$raw" in
    0)            status="OK" ;;
    OK|FAIL|CONCERN) status="$raw" ;;
    ''|*[!0-9]*)  status="FAIL"; msg="bad status '$raw'${msg:+ — $msg}" ;;
    *)            status="FAIL($raw)" ;;
esac

LOCK="${CRON_VERDICT_LOCK:-${LOG}.lock}"
# CRON_VERDICT_TS is a TEST hook: capture filenames embed the timestamp at
# 1-second granularity, so a test looping same-second invocations collapses
# every capture onto one path and the prune loop it asserts on never runs
# (ultra review 2026-07-31 — the prune test passed vacuously at len=1).
ts="${CRON_VERDICT_TS:-$(date -u +%Y-%m-%dT%H:%M:%SZ)}"

# ── evidence capture (2026-07-31) ────────────────────────────────────────────
# WHY: a FAIL verdict named the cron but never its CAUSE. On 2026-07-30
# harness_audit logged FAIL(1) and which of its 14 checks went red was already
# unrecoverable by the next morning — the crontab idiom redirects job output to
# /dev/null, so the ONE run with something to say threw it away, and the verdict
# log stores only name+status (honest_failure_modes #9: every swallow gets a
# witness). Crons wired for capture redirect into $OUT_DIR/<name>.out; a non-OK
# verdict PRESERVES that file under a timestamped name (the live .out is
# overwritten by the next run, so the evidence of a FAIL has a lifetime of one
# cadence unless copied) and names the path in the verdict line.
#
# Tri-state, never collapsed into a single "nothing to report" (#1/#2):
#   uncaptured      no .out file — this cron is not wired for capture, or logs
#                   elsewhere. NOT the same as "the job produced no output".
#   empty           wired, ran, said nothing.
#   <path>          preserved; the verdict line says where the evidence lives.
#   capture_failed  we tried and could not — loud, never silently omitted.
#
# OK runs capture nothing: keeping every healthy run's output would grow without
# bound on an SD card to say what the OK already says.
#
# ⚠️ The wired crontab redirects INTO $OUT_DIR, so if that directory is ever
# removed the shell cannot create the file and THE JOB DOES NOT RUN (measured:
# `sh -c 'job >missing/x.out 2>&1'` exits 2 without executing job). That failure
# is LOUD by construction — every wired cron starts logging FAIL(2) and
# probe_cron_verdict_stale fires — which is why it is accepted rather than
# guarded. DECISION TELL: every wired cron failing at once with FAIL(2) means
# the capture dir vanished, not that the fleet broke. Re-create it with
# `scripts/cron_capture_wire.py --apply` (it mkdirs before writing).
OUT_DIR="${CRON_VERDICT_OUT_DIR:-$HOME/.local/state/meshforge/cron_out}"
KEEP_CAPTURES="${CRON_VERDICT_KEEP_CAPTURES:-5}"

# One flattened, clamped line. A CONVENIENCE for the log — the preserved file is
# the record of truth, which is why the evidence string names its path too.
_excerpt() {
    line=$(grep -aiE 'fail|error|traceback|refused|timed out|timeout' "$1" 2>/dev/null | tail -1)
    [ -n "$line" ] || line=$(grep -av '^[[:space:]]*$' "$1" 2>/dev/null | tail -1)
    printf '%s' "$line" | tr '\n\r\t' '   ' | cut -c1-160
}

_capture() {
    src="$OUT_DIR/$name.out"
    [ -e "$src" ] || { evidence="out=uncaptured"; return 0; }
    [ -s "$src" ] || { evidence="out=empty"; return 0; }
    slug=$(printf '%s' "$status" | tr 'A-Z' 'a-z' | tr -cd 'a-z')
    dst="$OUT_DIR/$name.$slug-$ts.out"
    if cp "$src" "$dst" 2>/dev/null; then
        evidence="out=$dst | $(_excerpt "$src")"
        # Prune this NAME's older captures only. The literal dot after $name
        # anchors underscore-suffix neighbours (brain_backup vs
        # brain_backup_extra) but NOT dot-suffix ones: for name `sync`, the
        # glob `sync.`*-*.out also matches `sync.extra.fail-<ts>.out` — a
        # frequently-failing `sync` would rotate away `sync.extra`'s preserved
        # evidence (adversarial review 2026-07-31, finding 10). The slug this
        # script writes is dot-free [a-z]+, so filter candidates to exactly
        # <name>.<slug>-<ts>: a dotted neighbour's extra `.` segment cannot
        # match `[a-z]+-`. $name's own dots are regex-escaped.
        esc=$(printf '%s' "$name" | sed 's/\./\\./g')
        ls -1t "$OUT_DIR/$name."*-*.out 2>/dev/null \
            | grep -E "/${esc}\.[a-z]+-[0-9]{4}-" \
            | tail -n +$((KEEP_CAPTURES + 1)) \
            | while IFS= read -r old; do rm -f "$old"; done
    else
        evidence="out=capture_failed"
    fi
}

evidence=""
case "$status" in
    OK) ;;
    *)  mkdir -p "$OUT_DIR" 2>/dev/null || true
        _capture || evidence="out=capture_failed" ;;
esac
[ -z "$evidence" ] || msg="${msg:+$msg }$evidence"

_append() { printf '%s %s %s %s\n' "$ts" "$name" "$status" "$msg" >> "$LOG"; }

# Keep the newest MAX_LINES overall PLUS the newest KEEP_PER_NAME lines of every
# name, so a slow-cadence cron's verdicts survive high-churn neighbors.
_truncate() {
    lines=$(wc -l < "$LOG" 2>/dev/null || echo 0)
    [ "$lines" -gt "$MAX_LINES" ] || return 0
    tmp=$(mktemp "${LOG}.XXXXXX") || return 0
    awk -v max="$MAX_LINES" -v keep="$KEEP_PER_NAME" '
        { line[NR] = $0; nm[NR] = $2 }
        END {
            start = NR - max + 1; if (start < 1) start = 1
            for (i = NR; i >= 1; i--) {
                seen[nm[i]]++
                if (i >= start || seen[nm[i]] <= keep) keep_line[i] = 1
            }
            for (i = 1; i <= NR; i++) if (i in keep_line) print line[i]
        }' "$LOG" > "$tmp" && mv "$tmp" "$LOG"
}

# Append + truncate is a read-modify-write on a SHARED file: _truncate snapshots
# $LOG with awk, then `mv`s a rebuilt copy over it. A concurrent writer's atomic
# append that lands on the OLD inode AFTER that snapshot but BEFORE the mv is
# discarded when mv overwrites — a verdict the caller logged (exit 0) that then
# VANISHES (honest_failure_modes #8; the 2026-07-16 manager_deadman
# missing-verdict shape). Serialize the whole append+truncate under an exclusive
# flock. The lock is a SEPARATE file so the mv of $LOG never swaps the locked
# inode out from under a waiter; closing fd 9 (block scope) releases it.
#
# Degrade gracefully — the verdict is ALWAYS recorded (silence is the worse
# failure, honest_failure_modes #9): if the lock file can't be opened, or flock
# is absent / stays wedged past the timeout, fall through to a bare append
# (atomic, O_APPEND). The `:` probe uses a SIMPLE command so a failed 9>"$LOCK"
# only sets its status — it never exits the shell (unlike a bare `exec`).
if : 2>/dev/null 9>"$LOCK"; then
    {
        if flock -w 10 9 2>/dev/null; then
            _append
            _truncate || true
        else
            _append   # flock missing / wedged past timeout — record, skip trunc
        fi
    } 9>"$LOCK"
else
    _append           # lock file unopenable — record anyway
fi
exit 0
