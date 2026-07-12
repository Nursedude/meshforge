#!/bin/bash
# router_scout_pull.sh — manager-side collector for meshforge-scout ticks.
#
# Runs on the fleet manager box (cron); reaches the router over the ssh
# channel that already exists (for the NATed OpenWrt One that is the
# reverse tunnel it dials to this box). The router target is operator-
# specific and MUST NOT live in this repo (MF014) — pass it via SCOUT_SSH
# from a local, untracked wrapper, e.g.:
#
#     #!/bin/bash
#     export SCOUT_SSH="-p 2222 root@127.0.0.1"
#     exec /opt/meshforge/scripts/router_scout_pull.sh "$@"
#
# and wire that wrapper to cron_verdict.sh (exit code drives the verdict):
#
#     7,37 * * * * $HOME/router_scout_pull >/dev/null 2>&1; \
#         /opt/meshforge/scripts/cron_verdict.sh router_scout $?
#
# Behaviour (honest_failure_modes: absence ≠ recovery):
#   valid tick, thresholds ok   -> mirror it atomically, OK (exit 0)
#   valid tick, threshold trip  -> mirror it, FAIL (exit 1) with the reason
#   unreachable / garbage tick  -> FAIL (exit 1); the PREVIOUS mirrored
#       tick is NEVER touched — it ages honestly and the watchdog's
#       router_scout_degraded probe sees a stale mirror for what it is.
#
# Consumers of the mirrored tick:
#   ~/.local/share/meshforge/router_scout/<device>_tick.json
#     -> kilo collect (scout leg, src/kilo/ingest.py collect_scout_all)
#     -> probe_router_scout_degraded (src/utils/watchdog_probes_drift.py)
# The dirname is test-pinned against kilo's SCOUT_MIRROR_SUBDIR — one
# constant, two consumers (honest_failure_modes #5).

set -u

SSH_TARGET="${SCOUT_SSH:-}"
REMOTE_TICK="${SCOUT_REMOTE_TICK:-/etc/meshforge/scout_tick.json}"
MIRROR_DIR="${SCOUT_MIRROR_DIR:-$HOME/.local/share/meshforge/router_scout}"
LOG="${SCOUT_PULL_LOG:-$HOME/router_scout_pull.log}"
AGENT_STALE_S="${SCOUT_AGENT_STALE_S:-2700}"   # 3 × the */15 agent cadence
MAPS_FAIL="${SCOUT_MAPS_FAIL:-55000}"
TS="$(date -Is)"

log() { printf '%s router_scout %s\n' "$TS" "$*" >> "$LOG"; }

# --- external seam (overridable for hermetic tests) --------------------------
# Print the raw remote tick JSON on stdout; nonzero exit = unreachable.
fetch_tick() {
    if [ -n "${SCOUT_PULL_CMD:-}" ]; then "$SCOUT_PULL_CMD"; return; fi
    # -n: never read local stdin — an empty/garbled remote command must
    # fail fast, not block on a stdin-reading remote `cat`.
    # shellcheck disable=SC2086 — SCOUT_SSH deliberately word-splits
    ssh -n -o ConnectTimeout=10 -o BatchMode=yes $SSH_TARGET "cat $REMOTE_TICK" 2>/dev/null
}

# Fail loud on local misconfiguration (MF014: no target in repo) — a config
# error must not masquerade as router unreachability.
if [ -z "${SCOUT_PULL_CMD:-}" ] && [ -z "$SSH_TARGET" ]; then
    log "FAIL config: SCOUT_SSH unset (no router target)"
    exit 1
fi
if [ -z "$REMOTE_TICK" ]; then
    log "FAIL config: SCOUT_REMOTE_TICK set but empty"
    exit 1
fi

RAW="$(fetch_tick)"
rc=$?
if [ "$rc" -ne 0 ] || [ -z "$RAW" ]; then
    log "FAIL unreachable: fetch rc=$rc, ${#RAW} bytes — previous mirror left untouched"
    exit 1
fi

# Validate + evaluate in one python pass (system python3 on the manager box;
# no repo imports — this script must run from any checkout). The payload
# rides an env var, NOT stdin — the heredoc already owns stdin as the
# program text. Emits one line:
#   VALID <device> <verdict> <detail...>      verdict ∈ OK|FAIL
#   INVALID <reason...>
EVAL="$(SCOUT_RAW="$RAW" python3 - "$AGENT_STALE_S" "$MAPS_FAIL" <<'PYEOF'
import json
import os
import re
import sys
import time

stale_s, maps_fail = float(sys.argv[1]), int(sys.argv[2])
try:
    tick = json.loads(os.environ.get("SCOUT_RAW", ""))
except ValueError as e:
    print(f"INVALID not json: {e}")
    sys.exit(0)
if not isinstance(tick, dict):
    print(f"INVALID tick is {type(tick).__name__}, not an object")
    sys.exit(0)
device = str(tick.get("device") or "")
cap = tick.get("captured_at")
if not device or isinstance(cap, bool) or not isinstance(cap, (int, float)):
    print("INVALID missing device/captured_at — writer/reader shape drift?")
    sys.exit(0)
# device becomes a filename component — refuse anything path-shaped.
if not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", device):
    print(f"INVALID device {device!r} not filename-safe")
    sys.exit(0)

fails = []
age = time.time() - float(cap)
if age > stale_s:
    fails.append(f"tick stale ({int(age)}s old > {int(stale_s)}s) — agent cron dark on the router")
mtd = tick.get("meshtasticd") or {}
maps = mtd.get("maps")
if isinstance(maps, (int, float)) and not isinstance(maps, bool) and maps > maps_fail:
    fails.append(f"maps={int(maps)} > {maps_fail} (#10468 leak line)")
persist = tick.get("persistence") or {}
if persist.get("ok") is False:
    fails.append("meshtasticd data_dir on tmpfs — config vanishes on reboot")
if tick.get("ok") is False:
    errs = tick.get("errors") or []
    first = errs[0] if errs else "unspecified"
    fails.append(f"agent self-report ok=false ({len(errs)} witness(es): {first})")

verdict = "FAIL" if fails else "OK"
detail = "; ".join(fails) if fails else (
    f"age={int(age)}s maps={maps} vsz_kb={mtd.get('vsz_kb')}")
print(f"VALID {device} {verdict} {detail}")
PYEOF
)"

case "$EVAL" in
    VALID\ *) ;;
    *)
        log "FAIL invalid tick: ${EVAL#INVALID } — previous mirror left untouched"
        exit 1
        ;;
esac

# one builtin split — three separate extractors of the same line WILL
# drift when a field is added (and cost three forks for nothing)
read -r _ device verdict detail <<EOF
$EVAL
EOF

# A validated tick always lands (even on FAIL — a threshold trip is fresh,
# real data the probe and kilo must see); only unreachable/garbage holds
# the previous mirror.
mkdir -p "$MIRROR_DIR"
tmp="$(mktemp "$MIRROR_DIR/.${device}_tick.XXXXXX")" || { log "FAIL mktemp in $MIRROR_DIR"; exit 1; }
printf '%s\n' "$RAW" > "$tmp" && mv "$tmp" "$MIRROR_DIR/${device}_tick.json"

if [ "$verdict" = "OK" ]; then
    log "OK $device $detail"
    exit 0
fi
log "FAIL $device $detail"
exit 1
