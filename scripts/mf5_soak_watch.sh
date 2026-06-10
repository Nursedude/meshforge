#!/usr/bin/env bash
# mf5_soak_watch.sh — daily mf.5/mf.4 soak evidence + verdicts.
#
# Runs scripts/mf5_soak_watch.py (criteria C1–C5 declared in its header;
# window 2026-06-10..2026-06-23) and records BOTH verdict lines:
#   mf5_soak_watch    — the daily evidence verdict (every run)
#   mf5_soak_verdict  — the final PASS/FAIL (once, on/after 2026-06-24)
# Wired on the federator box (the literal `cron_verdict.sh mf5_soak_watch`
# MUST appear in the crontab line — #78's probe detects wiring by regex
# over the crontab, not wrapper internals):
#   10 6 * * * /opt/meshforge/scripts/mf5_soak_watch.sh >/dev/null 2>&1 || /opt/meshforge/scripts/cron_verdict.sh mf5_soak_watch FAIL wrapper_crashed
# This wrapper writes detailed verdicts itself and exits 0 once written;
# the crontab fallback fires only if the wrapper never ran. Silence is
# guarded twice: probe_cron_verdict_stale (#78) + the hourly freshness
# ntfy monitor.
set -u

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE_DIR="${MF5_SOAK_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/meshforge/mf5_soak}"
mkdir -p "$STATE_DIR"
out="$STATE_DIR/last_run.log"

python3 "$REPO_ROOT/scripts/mf5_soak_watch.py" >"$out" 2>&1
rc=$?

daily="$(grep '^DAILY ' "$out" | tail -1)"
if [ -n "$daily" ]; then
    status="$(printf '%s' "$daily" | awk '{print $2}')"
    detail="$(printf '%s' "$daily" | cut -d' ' -f3-)"
else
    status="FAIL"
    detail="soak watch crashed without verdict (rc=$rc) — see $out"
fi
"$REPO_ROOT/scripts/cron_verdict.sh" mf5_soak_watch "$status" "$detail"

final="$(grep '^FINAL ' "$out" | tail -1)"
if [ -n "$final" ]; then
    fstatus="$(printf '%s' "$final" | awk '{print $2}')"
    fdetail="$(printf '%s' "$final" | cut -d' ' -f3-)"
    "$REPO_ROOT/scripts/cron_verdict.sh" mf5_soak_verdict "$fstatus" "$fdetail"
fi
# Exit 0 — verdicts above carry the truth; nonzero is reserved for
# "wrapper never wrote a verdict" (crontab || fallback).
exit 0
