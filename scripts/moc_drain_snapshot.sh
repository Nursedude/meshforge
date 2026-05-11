#!/bin/bash
# moc_drain_snapshot.sh — one-shot snapshot of moc's node_history drain state.
#
# Designed to be invoked from a systemd-user timer. Output goes to stdout
# (journal when run as a service); each line is structured so a future
# Claude session or grep-pipe can parse the trajectory across snapshots.
#
# Companion to project_moc_post_drain_vacuum.md memo — the 4.7 GB DB on
# moc/moc1 is draining at ~120K rows/hour and expected to converge
# 2026-05-14/15. This script answers "did it converge?" in one shell line.
#
# Target host is passed as $1 (default: moc — the ssh alias). For moc1 or
# others with the same shape, pass the alias explicitly.
set -euo pipefail

TARGET="${1:-moc}"
ts="$(date -u +%FT%TZ)"

ssh -o BatchMode=yes -o ConnectTimeout=15 "$TARGET" '
  ts="'"$ts"'"
  host="$(hostname)"
  db=~/.local/share/meshforge/node_history.db
  if [ ! -f "$db" ]; then
      echo "$ts target=$host result=no-db"
      exit 0
  fi
  db_bytes=$(stat -c %s "$db" 2>/dev/null || echo 0)
  wal_bytes=$(stat -c %s "${db}-wal" 2>/dev/null || echo 0)
  db_gb=$(awk "BEGIN{printf \"%.2f\", $db_bytes/1024/1024/1024}")
  wal_mb=$(awk "BEGIN{printf \"%.0f\", $wal_bytes/1024/1024}")
  last_prune_line=$(sudo journalctl -u meshforge-map.service --since "2 hours ago" --no-pager 2>/dev/null \
      | grep "Node history auto-prune: deleted" \
      | tail -1)
  prune_deleted=$(echo "$last_prune_line" | grep -oE "deleted [0-9]+" | awk "{print \$2}")
  prune_cap=$(echo "$last_prune_line" | grep -oE "cycle cap reached at [0-9]+" | awk "{print \$NF}")
  prune_deleted=${prune_deleted:-unknown}
  prune_cap=${prune_cap:-none}
  load=$(awk "{print \$1}" /proc/loadavg)
  echo "$ts target=$host db_gb=$db_gb wal_mb=$wal_mb last_prune_deleted=$prune_deleted cap_hit=$prune_cap load1=$load"
'
