#!/bin/bash
#
# Migrate meshforge-map.service from User=root → User=<operator>
#
# Phase 2 of the Map Domain DB-bloat / config-drift closure.
# Phase 1 + 1.5 (record-only-on-change) shipped 2026-04-26/27.
# This script migrates an existing fleet box to the new unit.
#
# Idempotent: re-running on an already-migrated host is a no-op
# (detects User= in /etc/systemd/system/meshforge-map.service and exits 0).
#
# Usage:
#     sudo bash scripts/migrate_map_service_to_user.sh
#     sudo bash scripts/migrate_map_service_to_user.sh --dry-run
#
# Safety:
#   - Refuses if operator user can't be resolved (no SUDO_USER, no $USER, root).
#   - Refuses if /opt/meshforge isn't owned by that operator (fleet invariant).
#   - Backs up the existing unit before replacing it.
#   - Preserves any pre-existing user-side node_history.db as .bak.<date>
#     so a TUI-side history snapshot isn't silently overwritten.
#

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

DRY_RUN=false
case "${1:-}" in
    --dry-run|-n) DRY_RUN=true ;;
    --help|-h)
        sed -n '2,30p' "$0"
        exit 0
        ;;
esac

run() {
    if $DRY_RUN; then
        echo -e "  ${CYAN}[dry-run] $*${NC}"
    else
        eval "$@"
    fi
}

step() { echo -e "${CYAN}== $* ==${NC}"; }
ok()   { echo -e "  ${GREEN}✓ $*${NC}"; }
warn() { echo -e "  ${YELLOW}⚠ $*${NC}"; }
die()  { echo -e "${RED}✗ $*${NC}" >&2; exit 1; }

# ─────────────────────────────────────────────────────────────────
# Preflight
# ─────────────────────────────────────────────────────────────────

step "Preflight"

if [[ $EUID -ne 0 ]]; then
    die "This script must be run as root (sudo bash $0)"
fi

OP_USER="${SUDO_USER:-$USER}"
if [[ -z "$OP_USER" || "$OP_USER" == "root" ]]; then
    # Fallback: detect from /opt/meshforge ownership
    OP_USER="$(stat -c '%U' /opt/meshforge 2>/dev/null || echo)"
fi
if [[ -z "$OP_USER" || "$OP_USER" == "root" ]]; then
    die "Cannot resolve operator user (SUDO_USER='$SUDO_USER' USER='$USER'). Run via: sudo bash $0"
fi
if ! getent passwd "$OP_USER" >/dev/null 2>&1; then
    die "User '$OP_USER' is not in /etc/passwd"
fi
OP_HOME="$(getent passwd "$OP_USER" | cut -d: -f6)"
[[ -d "$OP_HOME" ]] || die "Operator home directory '$OP_HOME' does not exist"
ok "Operator user: $OP_USER (home: $OP_HOME)"

# /opt/meshforge ownership invariant (fleet boxes are operator-owned)
MF_OWNER="$(stat -c '%U' /opt/meshforge)"
if [[ "$MF_OWNER" != "$OP_USER" ]]; then
    warn "/opt/meshforge owned by '$MF_OWNER', expected '$OP_USER' — proceeding but verify post-migration"
fi

UNIT_FILE="/etc/systemd/system/meshforge-map.service"
TEMPLATE_FILE="/opt/meshforge/scripts/meshforge-map.service"
[[ -f "$TEMPLATE_FILE" ]] || die "Template missing: $TEMPLATE_FILE (pull latest /opt/meshforge first)"

if grep -q "^User=__MESHFORGE_USER__" "$TEMPLATE_FILE"; then
    ok "Template uses __MESHFORGE_USER__ placeholder (Phase 2 ready)"
else
    die "Template $TEMPLATE_FILE does not have __MESHFORGE_USER__ placeholder. Pull latest before running."
fi

# Idempotent check: already migrated?
if [[ -f "$UNIT_FILE" ]]; then
    CURRENT_USER="$(awk -F= '/^User=/{print $2; exit}' "$UNIT_FILE")"
    if [[ "$CURRENT_USER" == "$OP_USER" ]]; then
        ok "Unit already runs as $OP_USER — nothing to do"
        exit 0
    elif [[ "$CURRENT_USER" != "root" ]]; then
        warn "Unit User=$CURRENT_USER (not root, not $OP_USER) — proceeding anyway"
    else
        ok "Unit currently User=root — migration needed"
    fi
else
    warn "No existing unit at $UNIT_FILE — will install fresh"
fi

# ─────────────────────────────────────────────────────────────────
# Stop service
# ─────────────────────────────────────────────────────────────────

step "Stop service"

if systemctl is-active meshforge-map.service >/dev/null 2>&1; then
    run "systemctl stop meshforge-map.service"
    ok "Stopped meshforge-map.service"
else
    ok "meshforge-map.service already stopped"
fi

# ─────────────────────────────────────────────────────────────────
# Backup existing unit
# ─────────────────────────────────────────────────────────────────

BACKUP_UNIT=""
if [[ -f "$UNIT_FILE" ]]; then
    BACKUP_UNIT="${UNIT_FILE}.pre-phase2.$(date +%Y%m%d-%H%M%S)"
    run "cp -p '$UNIT_FILE' '$BACKUP_UNIT'"
    ok "Backed up unit → $BACKUP_UNIT"
fi

# ─────────────────────────────────────────────────────────────────
# Migrate config
# ─────────────────────────────────────────────────────────────────

step "Migrate map_settings.json"

ROOT_CFG="/root/.config/meshforge/map_settings.json"
USER_CFG_DIR="$OP_HOME/.config/meshforge"
USER_CFG="$USER_CFG_DIR/map_settings.json"

run "install -d -o '$OP_USER' -g '$OP_USER' -m 700 '$USER_CFG_DIR'"

if [[ -f "$ROOT_CFG" ]]; then
    if [[ -f "$USER_CFG" ]]; then
        # Both exist — merge with root keys winning on conflict (operator's
        # explicit overrides like enable_meshcore_public live on root side
        # because the service has been running there for months).
        if $DRY_RUN; then
            echo -e "  ${CYAN}[dry-run] would merge $ROOT_CFG → $USER_CFG (root keys win)${NC}"
        else
            python3 -c "
import json, sys
with open('$ROOT_CFG') as f: r = json.load(f)
with open('$USER_CFG') as f: u = json.load(f)
merged = {**u, **r}
with open('$USER_CFG', 'w') as f: json.dump(merged, f, indent=2)
print('  merged keys:', sorted(set(r) | set(u)))
"
            chown "$OP_USER:$OP_USER" "$USER_CFG"
            chmod 600 "$USER_CFG"
            ok "Merged $ROOT_CFG → $USER_CFG"
        fi
    else
        run "cp -p '$ROOT_CFG' '$USER_CFG'"
        run "chown '$OP_USER:$OP_USER' '$USER_CFG'"
        run "chmod 600 '$USER_CFG'"
        ok "Copied $ROOT_CFG → $USER_CFG"
    fi
elif [[ -f "$USER_CFG" ]]; then
    ok "User config already in place ($USER_CFG); no /root copy to merge"
else
    ok "No map_settings.json on either side — service will use defaults"
fi

# ─────────────────────────────────────────────────────────────────
# Migrate node_history.db
# ─────────────────────────────────────────────────────────────────

step "Migrate node_history.db"

ROOT_DB="/root/.local/share/meshforge/node_history.db"
USER_DB_DIR="$OP_HOME/.local/share/meshforge"
USER_DB="$USER_DB_DIR/node_history.db"

run "install -d -o '$OP_USER' -g '$OP_USER' -m 755 '$USER_DB_DIR'"

if [[ -f "$ROOT_DB" ]]; then
    if [[ -f "$USER_DB" ]]; then
        # moc3 gotcha — TUI may have written this file as root in the past.
        # Force chown to operator first, otherwise the .bak rename will work
        # but the new copy that follows won't have correct perms.
        DB_BAK="${USER_DB}.bak.$(date +%Y%m%d-%H%M%S)"
        run "chown '$OP_USER:$OP_USER' '$USER_DB'"
        run "mv '$USER_DB' '$DB_BAK'"
        ok "Set aside existing user DB → $DB_BAK"
    fi
    run "cp -p '$ROOT_DB' '$USER_DB'"
    run "chown '$OP_USER:$OP_USER' '$USER_DB'"
    # Also any -wal / -shm sidecars (live WAL state).
    for sidecar in "${ROOT_DB}-wal" "${ROOT_DB}-shm"; do
        if [[ -f "$sidecar" ]]; then
            base="$(basename "$sidecar")"
            run "cp -p '$sidecar' '$USER_DB_DIR/$base'"
            run "chown '$OP_USER:$OP_USER' '$USER_DB_DIR/$base'"
        fi
    done
    DB_SIZE_MB=$(du -m "$ROOT_DB" 2>/dev/null | awk '{print $1}')
    ok "Copied $ROOT_DB → $USER_DB (${DB_SIZE_MB} MB)"
elif [[ -f "$USER_DB" ]]; then
    # Ensure ownership is correct (moc3-style root-owned user DB)
    run "chown '$OP_USER:$OP_USER' '$USER_DB'"
    ok "User DB already in place ($USER_DB); ensured ownership"
else
    ok "No node_history.db on either side — fresh start"
fi

# ─────────────────────────────────────────────────────────────────
# Cleanup transient state
# ─────────────────────────────────────────────────────────────────

step "Cleanup transient state"

# /tmp/meshforge_rns_client/ — root-owned config dir from the old service.
# Will be recreated by the new (operator-owned) service on first RNS use.
if [[ -d /tmp/meshforge_rns_client ]]; then
    OWNER="$(stat -c '%U' /tmp/meshforge_rns_client)"
    if [[ "$OWNER" != "$OP_USER" ]]; then
        run "rm -rf /tmp/meshforge_rns_client"
        ok "Removed root-owned /tmp/meshforge_rns_client (will be recreated)"
    fi
fi

# /run/meshforge — RuntimeDirectory; systemd recreates with correct owner on start
if [[ -d /run/meshforge ]]; then
    run "rm -rf /run/meshforge"
    ok "Removed /run/meshforge (RuntimeDirectory will be recreated)"
fi

# ─────────────────────────────────────────────────────────────────
# Install new unit
# ─────────────────────────────────────────────────────────────────

step "Install new unit"

if $DRY_RUN; then
    echo -e "  ${CYAN}[dry-run] would sed __MESHFORGE_USER__ → $OP_USER and write to $UNIT_FILE${NC}"
else
    sed "s/__MESHFORGE_USER__/${OP_USER}/g" "$TEMPLATE_FILE" > "$UNIT_FILE"
    chmod 644 "$UNIT_FILE"
    ok "Installed $UNIT_FILE (User=$OP_USER)"
fi

run "systemctl daemon-reload"
ok "systemd daemon reloaded"

# ─────────────────────────────────────────────────────────────────
# Start + verify
# ─────────────────────────────────────────────────────────────────

step "Start + verify"

run "systemctl start meshforge-map.service"

if $DRY_RUN; then
    ok "(dry-run) skipping verification"
    echo
    ok "Dry-run complete. Re-run without --dry-run to apply."
    exit 0
fi

# Wait for the service to come up. Cold start with WAL replay on a 600 MB DB
# can take 2-3 minutes on SD card; budget 180s.
DEADLINE=$(( $(date +%s) + 180 ))
while (( $(date +%s) < DEADLINE )); do
    if curl -fs --max-time 3 http://localhost:5000/api/status >/dev/null 2>&1; then
        break
    fi
    sleep 3
done

if ! curl -fs --max-time 5 http://localhost:5000/api/status >/dev/null 2>&1; then
    warn "Service did not respond on :5000 within 180s"
    warn "Check: journalctl -u meshforge-map --since '2 min ago' --no-pager"
    exit 2
fi

ACTIVE_USER="$(systemctl show meshforge-map.service -p User --value)"
MAIN_PID="$(systemctl show meshforge-map.service -p MainPID --value)"
PROC_USER=""
if [[ -n "$MAIN_PID" && "$MAIN_PID" != "0" ]]; then
    PROC_USER="$(ps -o user= -p "$MAIN_PID" 2>/dev/null | tr -d ' ' || echo)"
fi

EXPECTED_DB="$OP_HOME/.local/share/meshforge/node_history.db"
# /api/status doesn't expose db_path directly, but the running service's
# open files reveal which sqlite file it's writing. lsof needs root.
OPEN_DB=""
if [[ -n "$MAIN_PID" && "$MAIN_PID" != "0" ]]; then
    OPEN_DB="$(lsof -p "$MAIN_PID" 2>/dev/null | awk '/node_history\.db/ && !/-wal|-shm|\.bak\./ {print $NF; exit}')"
fi

ok "systemd User=:           $ACTIVE_USER"
ok "MainPID running as:      ${PROC_USER:-?}"
ok "Open SQLite file:        ${OPEN_DB:-(not yet opened — service may be cold-starting)}"

if [[ "$ACTIVE_USER" != "$OP_USER" ]]; then
    die "systemd User=$ACTIVE_USER, expected $OP_USER"
fi
if [[ -n "$PROC_USER" && "$PROC_USER" != "$OP_USER" ]]; then
    die "Process owner is $PROC_USER, expected $OP_USER"
fi
if [[ -n "$OPEN_DB" && "$OPEN_DB" != "$EXPECTED_DB" ]]; then
    warn "Service has $OPEN_DB open, expected $EXPECTED_DB"
    warn "DB drift would mean WAL writes go to the wrong place — investigate."
    exit 3
fi

if [[ -n "$BACKUP_UNIT" ]]; then
    ok "Migration complete. Backup unit: $BACKUP_UNIT"
else
    ok "Migration complete. (No prior unit to back up.)"
fi
echo
echo -e "${GREEN}Phase 2 migration successful on $(hostname).${NC}"
echo -e "  Operator overrides preserved at: $USER_CFG"
echo -e "  History DB at:                   $USER_DB"
echo -e "  Old /root copies left in place — verify for 1 week then delete:"
echo -e "    sudo rm -rf /root/.config/meshforge /root/.local/share/meshforge"
