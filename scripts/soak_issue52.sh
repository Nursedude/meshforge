#!/usr/bin/env bash
# Soak check for Issue #52 (lock-scope fix on moc1 :5000).
# Designed for non-interactive scheduled execution (systemd-run / cron).
# Writes a timestamped report to ~/.local/share/meshforge/soak/.
#
# Reads only — no fleet state mutation. Safe to re-run anytime.
# Cross-references: project_db_recurring_class.md, project_issue_52_lock_scope.md.

set -u
TARGET_HOST="moc1"
TARGET_URL="http://192.168.86.249:5000"
TS=$(date +%Y%m%d-%H%M%S)
OUT_DIR="${HOME}/.local/share/meshforge/soak"
OUT="${OUT_DIR}/issue52_${TS}.log"
mkdir -p "${OUT_DIR}"

log() { printf '%s\n' "$*" | tee -a "${OUT}"; }
sec() { log ""; log "=== $* ==="; }

log "MeshForge Issue #52 soak check — ${TS}"
log "Host: $(hostname) | Target: ${TARGET_HOST} (${TARGET_URL})"
log "Memories to consult on regression: project_db_recurring_class.md, project_issue_52_lock_scope.md, project_test_bed_moc1.md"

sec "1. /api/status latency × 10 (cache hit path)"
for i in $(seq 1 10); do
  curl -sS -m 30 -o /dev/null -w "  #%02d HTTP %{http_code} time=%{time_total}s\n" \
    -A "soak-issue52" "${TARGET_URL}/api/status" 2>&1 || log "  #${i} FAILED"
done | tee -a "${OUT}"

sec "2. /api/nodes/directory latency × 5 (full dump, ~22MB)"
for i in $(seq 1 5); do
  curl -sS -m 60 -o /dev/null -w "  #%02d HTTP %{http_code} time=%{time_total}s size=%{size_download}\n" \
    -A "soak-issue52" "${TARGET_URL}/api/nodes/directory" 2>&1 || log "  #${i} FAILED"
done | tee -a "${OUT}"

sec "3. moc1 DB sizes"
ssh -o ConnectTimeout=5 -o BatchMode=yes "${TARGET_HOST}" \
  'ls -lh ~/.local/share/meshforge/node_history.db* 2>&1' 2>&1 | tee -a "${OUT}"

sec "4. moc1 directory + observation stats (in-process query)"
ssh -o ConnectTimeout=5 -o BatchMode=yes "${TARGET_HOST}" \
  '/opt/meshforge/venv/bin/python -c "
import sys, time, json
sys.path.insert(0, \"/opt/meshforge/src\")
from utils.node_history import NodeHistoryDB
from pathlib import Path
import os
h = NodeHistoryDB(db_path=Path(os.path.expanduser(\"~/.local/share/meshforge/node_history.db\")))
out = {}
t0 = time.time(); s = h.get_stats(); out[\"get_stats\"] = {\"elapsed_s\": round(time.time()-t0, 2), **s}
t0 = time.time(); d = h.get_directory_stats(); out[\"get_directory_stats\"] = {\"elapsed_s\": round(time.time()-t0, 2), **{k: v for k, v in d.items() if not isinstance(v, dict) or k in (\"by_network\", \"by_source_origin\")}}
print(json.dumps(out, indent=2, default=str))
"' 2>&1 | tee -a "${OUT}"

sec "5. FederationCollector poll cadence (last hour)"
ssh -o ConnectTimeout=5 -o BatchMode=yes "${TARGET_HOST}" \
  'sudo journalctl -u meshforge-map --since "1 hour ago" --no-pager 2>/dev/null \
   | grep "FederationCollector poll" | tail -20' 2>&1 | tee -a "${OUT}"

sec "6. Thread states — D-state count (SD throttling indicator)"
ssh -o ConnectTimeout=5 -o BatchMode=yes "${TARGET_HOST}" '
PID=$(systemctl show meshforge-map -p MainPID --value 2>/dev/null)
if [ -z "$PID" ] || [ "$PID" = "0" ]; then echo "service not running"; exit 0; fi
echo "PID=$PID"
D_COUNT=0
FUTEX_COUNT=0
TOTAL=0
for tid in $(sudo ls /proc/$PID/task 2>/dev/null); do
  TOTAL=$((TOTAL+1))
  STATE=$(sudo cat /proc/$PID/task/$tid/status 2>/dev/null | awk "/^State:/ {print \$2}")
  STACK=$(sudo cat /proc/$PID/task/$tid/stack 2>/dev/null | head -1)
  if [ "$STATE" = "D" ]; then
    D_COUNT=$((D_COUNT+1))
    echo "  D-state thread $tid stack: $STACK"
  fi
  if echo "$STACK" | grep -q futex_wait; then
    FUTEX_COUNT=$((FUTEX_COUNT+1))
  fi
done
echo "summary: total=$TOTAL d_state=$D_COUNT futex_wait=$FUTEX_COUNT"
' 2>&1 | tee -a "${OUT}"

sec "7. moc1 service age + memory"
ssh -o ConnectTimeout=5 -o BatchMode=yes "${TARGET_HOST}" \
  'systemctl status meshforge-map --no-pager 2>&1 | head -8' 2>&1 | tee -a "${OUT}"

sec "8. Triage hints"
log "If status > 1s p95 → re-check #52 lock scope; federation may have regressed reads."
log "If directory > 10s → likely SD throttling or prune backlog. Check journal for prune deletes."
log "If observations.total > 5M → prune is falling further behind; consider raising prune_batch_limit OR skipping obs writes for external bulk."
log "If d_state > 0 → SD bandwidth saturated. Federation poll cadence too aggressive for the hardware."
log "If federation poll row counts diverged from 50001 → upstream peer set changed; not necessarily a regression."

log ""
log "Report written to ${OUT}"
log "To analyze with claude: cat ${OUT} | claude -p 'audit this Issue #52 soak report — fix held? which dimension is showing pressure? any net-new symptoms?'"
