#!/usr/bin/env bash
# One-shot: activate the meshforge_from_avatar_seed gateway LXMF field after the
# mf.5 / gateway soak completes.
#
# Background: the LXMFace-avatar deploy (MeshForge e8b4786) added a pure-metadata
# field meshforge_from_avatar_seed to bridged Mesh->RNS messages in
# _rns_bridge_xform.py. It only activates on a meshforge-gateway RESTART, which
# was deliberately deferred because the gateway/mf.5 soak ran 06-10..06-23 (final
# verdict renders 06-24). This script does that restart, GATED on a green+fresh
# soak verdict, on the gateway boxes passed as arguments.
#
# Usage:  activate_avatar_seed_after_soak.sh <gateway-host> [<gateway-host> ...]
#
# Wired as a one-shot crontab entry on the manager box (07:00 HST 2026-06-24,
# after the daily mf5_soak_watch verdict at 06:10), e.g.:
#   0 7 24 6 * /opt/meshforge/scripts/activate_avatar_seed_after_soak.sh moc moc3 >> ~/activate_avatar_seed.log 2>&1
# Self-removes its crontab line after a definitive run (true one-shot). Pages
# ntfy (topic from ~/.config/fleet_push_topic) with the outcome either way,
# records a cron verdict, and closes its deferred-work backstop entry on success.
set -uo pipefail

VERDICT_LOG="$HOME/cron_verdicts.log"
GW_BOXES="$*"
LOGTAG="avatar_seed_activate"
LEDGER_ID="gateway-avatar-seed-restart"
TODAY_UTC="$(date -u +%Y-%m-%d)"

if [ -z "$GW_BOXES" ]; then
  echo "usage: $0 <gateway-host> [<gateway-host> ...]" >&2
  exit 2
fi

page() {  # priority tags message — delegates to the fleet ntfy SSOT helper
  /opt/meshforge/scripts/fleet_ntfy_push.sh "Gateway avatar-seed activation" "$1" "$2" "$3"
}

remove_self_cron() {
  # True one-shot: drop this script's crontab line so it never fires again.
  ( crontab -l 2>/dev/null | grep -vF "activate_avatar_seed_after_soak.sh" | crontab - ) 2>/dev/null || true
}

finish() {  # verdict detail
  /opt/meshforge/scripts/cron_verdict.sh "$LOGTAG" "$1" "$2" 2>/dev/null || true
  remove_self_cron
}

# Close the deferred-work backstop entry on success so the daily
# deferred_work_watch does NOT also page. Atomic, best-effort: a failure here
# must never break the run, and must never corrupt the ledger (which would blind
# the watcher). Only the success path calls this; an abort leaves it 'blocked'
# so the backstop reminds.
mark_ledger_done() {  # done_note
  local LEDGER="$HOME/deferred_work.json"
  [ -f "$LEDGER" ] || return 0
  python3 - "$LEDGER" "$LEDGER_ID" "$1" <<'PY' 2>/dev/null || true
import json, os, sys
path, tid, note = sys.argv[1], sys.argv[2], sys.argv[3]
try:
    with open(path) as f:
        data = json.load(f)
    changed = False
    for t in data.get("tasks", []):
        if t.get("id") == tid and t.get("status") != "done":
            t["status"] = "done"
            t["done_note"] = note
            changed = True
    if changed:
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp, path)
except Exception:
    pass
PY
}

# ── 1) Gate on the soak verdict ──────────────────────────────────────────
# Latest mf5_soak_watch verdict must be OK/PASS *and* fresh (today), so a stale
# green from a prior day (e.g. if this morning's soak-watch cron crashed) can't
# wave the restart through. Unobservable / not-green => abort, never restart.
LAST="$(grep 'mf5_soak_watch' "$VERDICT_LOG" 2>/dev/null | tail -1)"
LAST_DATE="${LAST:0:10}"
if ! printf '%s' "$LAST" | grep -qE 'mf5_soak_watch (OK|PASS)'; then
  page high "warning,rotating_light" \
    "ABORT: soak verdict not green — gateway NOT restarted, avatar-seed field stays inert. Last: ${LAST:-<none>}. Once soak is green, run manually: for h in $GW_BOXES; do ssh \$h sudo systemctl restart meshforge-gateway; done"
  finish CONCERN "soak not green; restart skipped"
  exit 0
fi
if [ "$LAST_DATE" != "$TODAY_UTC" ]; then
  page high "warning,rotating_light" \
    "ABORT: latest mf5_soak_watch verdict is STALE (dated $LAST_DATE, today $TODAY_UTC) — soak-watch may not have run. Gateway NOT restarted; check, then restart [$GW_BOXES] manually."
  finish CONCERN "stale soak verdict ($LAST_DATE); restart skipped"
  exit 0
fi

# ── 2) Gated restart — each gateway box in turn, verify each ──────────────
FAILED=""
for h in $GW_BOXES; do
  ssh -o ConnectTimeout=10 -o BatchMode=yes "$h" "sudo systemctl restart meshforge-gateway" 2>/dev/null || FAILED="$FAILED $h(restart-cmd-failed)"
  sleep 6
  st="$(ssh -o ConnectTimeout=10 -o BatchMode=yes "$h" "systemctl is-active meshforge-gateway" 2>/dev/null)"
  [ "$st" = "active" ] || FAILED="$FAILED $h(=${st:-unreachable})"
done

# ── 3) Outcome ───────────────────────────────────────────────────────────
if [ -n "$FAILED" ]; then
  page high "warning,rotating_light" \
    "PARTIAL/FAIL: meshforge-gateway restart issue on:$FAILED. Check those boxes (avatar-seed field is live only where the gateway is active)."
  finish FAIL "restart unhealthy:$FAILED"
  exit 1
fi

page default "white_check_mark" \
  "OK: meshforge-gateway restarted on [$GW_BOXES], all active. meshforge_from_avatar_seed (e8b4786) is now LIVE on bridged Mesh->RNS messages. Soak complete."
mark_ledger_done "$(date -u +%Y-%m-%d): one-shot OK — [$GW_BOXES] restarted, avatar-seed field live"
finish OK "[$GW_BOXES] active; avatar-seed field live"
exit 0
