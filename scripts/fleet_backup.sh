#!/bin/bash
#
# MeshForge Fleet Backup
#
# Creates a backup archive of critical node state (identity, config, AI memory)
# and optionally pushes it to peer Pis for redundancy.
#
# This script is standalone — it works without Python or MeshForge installed,
# making it usable during disaster recovery bootstrap.
#
# What is BACKED UP:
#   /etc/reticulum/              (RNS config + transport identity + keys)
#   /etc/meshtasticd/            (radio daemon config + active HAT configs)
#   ~/.config/meshforge/         (gateway identity, settings, DBs)
#   ~/.claude/memory/            (AI memory — global)
#   ~/.claude/projects/*/memory/ (AI memory — project-specific)
#   ~/.claude/settings.json      (AI settings)
#
# What is NOT backed up (too large, rebuilds over time):
#   ~/.local/share/meshforge/    (5GB+ map/telemetry history)
#   ~/.claude/sessions/          (conversation history)
#
# Fleet config is read from ~/.config/meshforge/fleet.json (local only, never
# committed to the repo). Without fleet.json, only --local mode works.
#
# Usage:
#   sudo bash fleet_backup.sh --local       # Backup this node only
#   sudo bash fleet_backup.sh --push        # Backup + push to peer Pis
#   sudo bash fleet_backup.sh --list        # List available backups
#   sudo bash fleet_backup.sh --rotate      # Remove old backups (keep N)
#   sudo bash fleet_backup.sh --help        # Show help
#

set -euo pipefail

# Colors (matching reinstall.sh)
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

# Defaults
INSTALL_DIR="/opt/meshforge"
QUIET=false
ACTION=""

# ─────────────────────────────────────────────────────────────────
# Detect real user home (handles sudo)
# ─────────────────────────────────────────────────────────────────
if [[ -n "${SUDO_USER:-}" && "$SUDO_USER" != "root" ]]; then
    REAL_HOME=$(getent passwd "$SUDO_USER" | cut -d: -f6)
    REAL_USER="$SUDO_USER"
else
    REAL_HOME="$HOME"
    REAL_USER="${USER:-$(whoami)}"
fi

if [[ -z "$REAL_HOME" ]]; then
    echo -e "${RED}Error: Cannot determine user home directory${NC}"
    exit 1
fi

BACKUP_BASE="${REAL_HOME}/.meshforge-fleet-backups"
FLEET_CONFIG="${REAL_HOME}/.config/meshforge/fleet.json"
HOSTNAME_SHORT=$(hostname -s)

# ─────────────────────────────────────────────────────────────────
# Helper functions
# ─────────────────────────────────────────────────────────────────
log_info() {
    $QUIET || echo -e "  ${GREEN}+${NC} $1" >&2
}

log_warn() {
    echo -e "  ${YELLOW}!${NC} $1" >&2
}

log_error() {
    echo -e "  ${RED}x${NC} $1" >&2
}

log_header() {
    $QUIET || echo -e "${CYAN}$1${NC}" >&2
}

# Safe SQLite backup — avoids corrupt copies of WAL-mode databases
safe_sqlite_backup() {
    local src="$1"
    local dst="$2"

    if [[ ! -f "$src" ]]; then
        return 1
    fi

    if command -v sqlite3 &>/dev/null; then
        sqlite3 "$src" ".backup '$dst'" 2>/dev/null && return 0
    fi

    # Fallback: plain copy (better than nothing)
    cp -a "$src" "$dst"
}

# Read a key from fleet.json using python3 (available on RPi OS)
# Falls back gracefully if python3 or fleet.json is missing
fleet_config_get() {
    local key="$1"
    local default="${2:-}"

    if [[ ! -f "$FLEET_CONFIG" ]]; then
        echo "$default"
        return
    fi

    python3 -c "
import json, sys
try:
    with open('$FLEET_CONFIG') as f:
        cfg = json.load(f)
    keys = '$key'.split('.')
    val = cfg
    for k in keys:
        val = val[k]
    if isinstance(val, (dict, list)):
        print(json.dumps(val))
    else:
        print(val)
except (KeyError, TypeError, json.JSONDecodeError):
    print('$default')
" 2>/dev/null || echo "$default"
}

# Get list of backup target hostnames for this node
get_backup_targets() {
    if [[ ! -f "$FLEET_CONFIG" ]]; then
        return
    fi

    python3 -c "
import json
try:
    with open('$FLEET_CONFIG') as f:
        cfg = json.load(f)
    this_host = cfg.get('this_host', '')
    peers = cfg.get('peers', {})
    if this_host in peers:
        targets = peers[this_host].get('backup_targets', [])
        for t in targets:
            if t in peers:
                print(f\"{t} {peers[t]['ip']}\")
except (json.JSONDecodeError, KeyError):
    pass
" 2>/dev/null
}

# ─────────────────────────────────────────────────────────────────
# Create local backup
# ─────────────────────────────────────────────────────────────────
do_local_backup() {
    local timestamp
    timestamp=$(date +%Y%m%d-%H%M%S)
    local archive_name="${HOSTNAME_SHORT}-${timestamp}"
    local tmpdir
    tmpdir=$(mktemp -d "/tmp/meshforge-backup-XXXXXX")

    # Ensure cleanup on exit
    trap "rm -rf '$tmpdir'" EXIT

    local staging="${tmpdir}/${archive_name}"
    mkdir -p "$staging"

    log_header "[1/4] Collecting node state..."

    local backed_up=0

    # --- Reticulum config + identity ---
    if [[ -d /etc/reticulum ]]; then
        mkdir -p "$staging/etc/reticulum/storage"

        # Config file
        if [[ -f /etc/reticulum/config ]]; then
            cp -a /etc/reticulum/config "$staging/etc/reticulum/config"
            log_info "/etc/reticulum/config"
            backed_up=$((backed_up + 1))
        fi

        # Transport identity (CRITICAL — irreplaceable)
        if [[ -f /etc/reticulum/storage/transport_identity ]]; then
            cp -a /etc/reticulum/storage/transport_identity "$staging/etc/reticulum/storage/transport_identity"
            log_info "/etc/reticulum/storage/transport_identity (CRITICAL)"
            backed_up=$((backed_up + 1))
        fi

        # Service identities
        if [[ -d /etc/reticulum/storage/identities ]]; then
            cp -a /etc/reticulum/storage/identities "$staging/etc/reticulum/storage/identities"
            log_info "/etc/reticulum/storage/identities/"
            backed_up=$((backed_up + 1))
        fi

        # Key ratchets
        if [[ -d /etc/reticulum/storage/ratchets ]]; then
            cp -a /etc/reticulum/storage/ratchets "$staging/etc/reticulum/storage/ratchets"
            log_info "/etc/reticulum/storage/ratchets/"
            backed_up=$((backed_up + 1))
        fi

        # Known destinations (peer address book)
        if [[ -f /etc/reticulum/storage/known_destinations ]]; then
            cp -a /etc/reticulum/storage/known_destinations "$staging/etc/reticulum/storage/known_destinations"
            log_info "/etc/reticulum/storage/known_destinations"
            backed_up=$((backed_up + 1))
        fi

        # Interfaces directory (plugins like Meshtastic_Interface.py)
        if [[ -d /etc/reticulum/interfaces ]]; then
            cp -a /etc/reticulum/interfaces "$staging/etc/reticulum/interfaces"
            log_info "/etc/reticulum/interfaces/"
            backed_up=$((backed_up + 1))
        fi
    fi

    # --- meshtasticd config ---
    if [[ -d /etc/meshtasticd ]]; then
        mkdir -p "$staging/etc/meshtasticd/config.d"

        if [[ -f /etc/meshtasticd/config.yaml ]]; then
            cp -a /etc/meshtasticd/config.yaml "$staging/etc/meshtasticd/config.yaml"
            log_info "/etc/meshtasticd/config.yaml"
            backed_up=$((backed_up + 1))
        fi

        # Active HAT configs
        if ls /etc/meshtasticd/config.d/*.yaml &>/dev/null 2>&1; then
            cp -a /etc/meshtasticd/config.d/*.yaml "$staging/etc/meshtasticd/config.d/"
            local hat_count
            hat_count=$(ls /etc/meshtasticd/config.d/*.yaml 2>/dev/null | wc -l)
            log_info "/etc/meshtasticd/config.d/ (${hat_count} HAT configs)"
            backed_up=$((backed_up + 1))
        fi
    fi

    # --- MeshForge user config ---
    local mf_config="${REAL_HOME}/.config/meshforge"
    if [[ -d "$mf_config" ]]; then
        mkdir -p "$staging/home/config/meshforge"

        # Copy non-DB files (json, yaml, identity, ini, etc.)
        for f in "$mf_config"/*.json "$mf_config"/*.yaml "$mf_config"/*.ini; do
            if [[ -f "$f" ]]; then
                cp -a "$f" "$staging/home/config/meshforge/"
            fi
        done

        # Gateway identity (CRITICAL)
        if [[ -f "$mf_config/gateway_identity" ]]; then
            cp -a "$mf_config/gateway_identity" "$staging/home/config/meshforge/gateway_identity"
            log_info "gateway_identity (CRITICAL)"
        fi

        # SQLite databases — safe backup
        for db in "$mf_config"/*.db; do
            if [[ -f "$db" ]]; then
                local dbname
                dbname=$(basename "$db")
                safe_sqlite_backup "$db" "$staging/home/config/meshforge/${dbname}"
            fi
        done

        # Subdirectories (plugins, lxmf_storage, etc.)
        for subdir in plugins lxmf_storage backups; do
            if [[ -d "$mf_config/$subdir" ]]; then
                cp -a "$mf_config/$subdir" "$staging/home/config/meshforge/$subdir"
            fi
        done

        log_info "~/.config/meshforge/ (configs + databases)"
        backed_up=$((backed_up + 1))
    fi

    # --- Claude Code AI memory ---
    local claude_dir="${REAL_HOME}/.claude"
    if [[ -d "$claude_dir" ]]; then
        # Global memory
        if [[ -d "$claude_dir/memory" ]]; then
            mkdir -p "$staging/home/claude/memory"
            cp -a "$claude_dir/memory/"*.md "$staging/home/claude/memory/" 2>/dev/null || true
            local mem_count
            mem_count=$(ls "$claude_dir/memory/"*.md 2>/dev/null | wc -l)
            log_info "~/.claude/memory/ (${mem_count} global memory files)"
            backed_up=$((backed_up + 1))
        fi

        # Project-specific memories (only memory subdirs, not sessions/history)
        if [[ -d "$claude_dir/projects" ]]; then
            mkdir -p "$staging/home/claude/projects"
            for proj_dir in "$claude_dir/projects"/*/; do
                if [[ -d "${proj_dir}memory" ]]; then
                    local proj_name
                    proj_name=$(basename "$proj_dir")
                    mkdir -p "$staging/home/claude/projects/${proj_name}/memory"
                    cp -a "${proj_dir}memory/"*.md "$staging/home/claude/projects/${proj_name}/memory/" 2>/dev/null || true
                fi
            done
            log_info "~/.claude/projects/*/memory/ (project memories)"
            backed_up=$((backed_up + 1))
        fi

        # Claude settings
        if [[ -f "$claude_dir/settings.json" ]]; then
            mkdir -p "$staging/home/claude"
            cp -a "$claude_dir/settings.json" "$staging/home/claude/settings.json"
            log_info "~/.claude/settings.json"
            backed_up=$((backed_up + 1))
        fi
    fi

    # --- Project-level Claude config ---
    if [[ -f "$INSTALL_DIR/.claude.json" ]]; then
        mkdir -p "$staging/opt/meshforge"
        cp -a "$INSTALL_DIR/.claude.json" "$staging/opt/meshforge/.claude.json"
        log_info "/opt/meshforge/.claude.json"
        backed_up=$((backed_up + 1))
    fi

    if [[ $backed_up -eq 0 ]]; then
        log_warn "No state found to back up"
        rm -rf "$tmpdir"
        trap - EXIT
        return 1
    fi

    # ─────────────────────────────────────────────────────────────
    # Create manifest
    # ─────────────────────────────────────────────────────────────
    log_header "[2/4] Writing manifest..."

    local mf_version="unknown"
    if [[ -f "$INSTALL_DIR/src/__version__.py" ]]; then
        mf_version=$(python3 -c "
import sys; sys.path.insert(0, '$INSTALL_DIR/src')
from __version__ import __version__; print(__version__)
" 2>/dev/null || echo "unknown")
    fi

    local mf_commit="unknown"
    if [[ -d "$INSTALL_DIR/.git" ]]; then
        mf_commit=$(git -C "$INSTALL_DIR" rev-parse --short HEAD 2>/dev/null || echo "unknown")
    fi

    # Build manifest with python3 for proper JSON
    python3 -c "
import json, os, hashlib, datetime

manifest = {
    'hostname': '${HOSTNAME_SHORT}',
    'timestamp': '$(date -Iseconds)',
    'meshforge_version': '${mf_version}',
    'meshforge_commit': '${mf_commit}',
    'backup_type': 'fleet_config',
    'items_backed_up': ${backed_up},
    'created_by': 'fleet_backup.sh',
}

# Walk staging dir for file inventory
files = []
staging = '${staging}'
for root, dirs, fnames in os.walk(staging):
    for fname in fnames:
        fpath = os.path.join(root, fname)
        relpath = os.path.relpath(fpath, staging)
        stat = os.stat(fpath)
        with open(fpath, 'rb') as f:
            sha = hashlib.sha256(f.read()).hexdigest()
        files.append({
            'path': relpath,
            'size': stat.st_size,
            'sha256': sha,
        })

manifest['files'] = sorted(files, key=lambda x: x['path'])
manifest['total_size_bytes'] = sum(f['size'] for f in files)

with open(os.path.join(staging, 'manifest.json'), 'w') as f:
    json.dump(manifest, f, indent=2)
" 2>/dev/null

    log_info "manifest.json"

    # ─────────────────────────────────────────────────────────────
    # Create archive
    # ─────────────────────────────────────────────────────────────
    log_header "[3/4] Creating archive..."

    mkdir -p "$BACKUP_BASE/${HOSTNAME_SHORT}"

    local archive_path="${BACKUP_BASE}/${HOSTNAME_SHORT}/${archive_name}.tar.gz"

    tar -czf "$archive_path" -C "$tmpdir" "$archive_name" 2>/dev/null

    # Set restrictive permissions (contains identity keys)
    chmod 600 "$archive_path"

    # Update 'latest' symlink
    local latest_link="${BACKUP_BASE}/${HOSTNAME_SHORT}/latest.tar.gz"
    ln -sf "$(basename "$archive_path")" "$latest_link"

    # Fix ownership if running as sudo. We must chown BACKUP_BASE itself
    # (not only the host subdir); otherwise when a peer SSHs in as
    # $REAL_USER to push its backup, it can't create its own subdir under
    # the root-owned parent and every incoming push fails as "unreachable".
    if [[ -n "${SUDO_USER:-}" && "$SUDO_USER" != "root" ]]; then
        chown "$SUDO_USER:$SUDO_USER" "$BACKUP_BASE" 2>/dev/null || true
        chown -R "$SUDO_USER:$SUDO_USER" "$BACKUP_BASE/${HOSTNAME_SHORT}" 2>/dev/null || true
    fi

    local archive_size
    archive_size=$(du -h "$archive_path" | cut -f1)

    log_header "[4/4] Backup complete"
    log_info "Archive: ${archive_path}"
    log_info "Size: ${archive_size}"
    log_info "Items: ${backed_up} categories"

    # Cleanup
    rm -rf "$tmpdir"
    trap - EXIT

    echo "$archive_path"
}

# ─────────────────────────────────────────────────────────────────
# Push backup to peer Pis
# ─────────────────────────────────────────────────────────────────
do_push() {
    local archive_path="$1"

    if [[ ! -f "$FLEET_CONFIG" ]]; then
        log_warn "No fleet config at $FLEET_CONFIG — skipping push"
        log_warn "Create fleet.json to enable cross-Pi backup replication"
        return 0
    fi

    local ssh_key
    ssh_key=$(fleet_config_get "ssh_key" "")
    ssh_key="${ssh_key/#\~/$REAL_HOME}"

    if [[ -z "$ssh_key" || ! -f "$ssh_key" ]]; then
        log_warn "SSH key not found at $ssh_key — skipping push"
        return 0
    fi

    local archive_name
    archive_name=$(basename "$archive_path")
    local push_ok=0
    local push_fail=0

    log_header "Pushing to peer Pis..."

    while IFS=' ' read -r peer_name peer_ip; do
        if [[ -z "$peer_name" || -z "$peer_ip" ]]; then
            continue
        fi

        local remote_dir=".meshforge-fleet-backups/${HOSTNAME_SHORT}"

        # Create remote directory + copy archive
        # Note: ssh/scp need stdin redirected from /dev/null; otherwise
        # they consume the while-read loop's input and only the first
        # peer is processed.
        if ssh -n -i "$ssh_key" \
               -o ConnectTimeout=10 \
               -o BatchMode=yes \
               -o StrictHostKeyChecking=no \
               "$REAL_USER@$peer_ip" \
               "mkdir -p ~/${remote_dir}" 2>/dev/null; then

            if scp -i "$ssh_key" \
                   -o ConnectTimeout=10 \
                   -o BatchMode=yes \
                   -o StrictHostKeyChecking=no \
                   "$archive_path" \
                   "$REAL_USER@$peer_ip:~/${remote_dir}/${archive_name}" </dev/null 2>/dev/null; then

                # Update latest symlink on remote
                ssh -n -i "$ssh_key" \
                    -o ConnectTimeout=10 \
                    -o BatchMode=yes \
                    -o StrictHostKeyChecking=no \
                    "$REAL_USER@$peer_ip" \
                    "cd ~/${remote_dir} && ln -sf '${archive_name}' latest.tar.gz" 2>/dev/null || true

                log_info "${peer_name} (${peer_ip}) — pushed"
                push_ok=$((push_ok + 1))
            else
                log_warn "${peer_name} (${peer_ip}) — SCP failed"
                push_fail=$((push_fail + 1))
            fi
        else
            log_warn "${peer_name} (${peer_ip}) — unreachable"
            push_fail=$((push_fail + 1))
        fi
    done < <(get_backup_targets)

    if [[ $push_ok -gt 0 ]]; then
        log_info "Pushed to ${push_ok} peer(s)"
    fi
    if [[ $push_fail -gt 0 ]]; then
        log_warn "${push_fail} peer(s) unreachable (will retry next backup)"
    fi
}

# ─────────────────────────────────────────────────────────────────
# List backups
# ─────────────────────────────────────────────────────────────────
do_list() {
    if [[ ! -d "$BACKUP_BASE" ]]; then
        echo -e "${YELLOW}No backups found at ${BACKUP_BASE}${NC}"
        return 0
    fi

    echo -e "${CYAN}╔════════════════════════════════════════════════════════╗${NC}"
    echo -e "${CYAN}║          MeshForge Fleet Backups                       ║${NC}"
    echo -e "${CYAN}╚════════════════════════════════════════════════════════╝${NC}"
    echo ""

    local total=0

    for host_dir in "$BACKUP_BASE"/*/; do
        if [[ ! -d "$host_dir" ]]; then
            continue
        fi

        local host_name
        host_name=$(basename "$host_dir")
        local count
        count=$(find "$host_dir" -maxdepth 1 -name '*.tar.gz' ! -type l 2>/dev/null | wc -l)

        if [[ $count -eq 0 ]]; then
            continue
        fi

        echo -e "  ${BOLD}${host_name}${NC} (${count} backups):"

        # Show backups newest first (skip symlinks)
        ls -t "$host_dir"/*.tar.gz 2>/dev/null | while read -r archive; do
            # Skip the "latest" symlink
            if [[ -L "$archive" ]]; then
                continue
            fi
            local fname
            fname=$(basename "$archive")
            local fsize
            fsize=$(du -h "$archive" | cut -f1)
            local fdate
            fdate=$(stat -c '%y' "$archive" 2>/dev/null | cut -d. -f1)

            local latest_marker=""
            if [[ -L "$host_dir/latest.tar.gz" ]]; then
                local link_target
                link_target=$(readlink "$host_dir/latest.tar.gz")
                if [[ "$link_target" == "$fname" ]]; then
                    latest_marker=" ${GREEN}(latest)${NC}"
                fi
            fi

            echo -e "    ${fname}  ${fsize}  ${fdate}${latest_marker}"
        done

        echo ""
        total=$((total + count))
    done

    if [[ $total -eq 0 ]]; then
        echo -e "  ${YELLOW}No backups found${NC}"
    else
        echo -e "  ${BOLD}Total: ${total} backup(s)${NC}"
    fi
}

# ─────────────────────────────────────────────────────────────────
# Rotate old backups
# ─────────────────────────────────────────────────────────────────
do_rotate() {
    local keep
    keep=$(fleet_config_get "keep_backups" "7")

    if [[ ! -d "$BACKUP_BASE" ]]; then
        echo -e "${YELLOW}No backups to rotate${NC}"
        return 0
    fi

    log_header "Rotating backups (keeping ${keep} per host)..."

    local removed_total=0

    for host_dir in "$BACKUP_BASE"/*/; do
        if [[ ! -d "$host_dir" ]]; then
            continue
        fi

        local host_name
        host_name=$(basename "$host_dir")

        # List archives newest first, skip the first $keep
        local to_remove
        to_remove=$(ls -t "$host_dir"/*.tar.gz 2>/dev/null | tail -n "+$((keep + 1))")

        if [[ -n "$to_remove" ]]; then
            local count
            count=$(echo "$to_remove" | wc -l)
            echo "$to_remove" | while read -r old_archive; do
                rm -f "$old_archive"
            done
            log_info "${host_name}: removed ${count} old backup(s)"
            removed_total=$((removed_total + count))
        fi
    done

    if [[ $removed_total -eq 0 ]]; then
        log_info "Nothing to rotate"
    else
        log_info "Removed ${removed_total} old backup(s) total"
    fi
}

# ─────────────────────────────────────────────────────────────────
# Parse arguments
# ─────────────────────────────────────────────────────────────────
show_help() {
    echo "MeshForge Fleet Backup"
    echo ""
    echo "Usage: sudo bash $0 [action] [options]"
    echo ""
    echo "Actions:"
    echo "  --local          Backup this node only (local archive)"
    echo "  --push           Backup + push to peer Pis (requires fleet.json)"
    echo "  --list           List all available backups"
    echo "  --rotate         Remove old backups (keep N per host)"
    echo ""
    echo "Options:"
    echo "  --quiet, -q      Suppress progress output"
    echo "  --help, -h       Show this help"
    echo ""
    echo "Fleet config: ${FLEET_CONFIG}"
    echo "Backup store: ${BACKUP_BASE}"
    echo ""
    echo "Without fleet.json, only --local mode is available."
    echo "See docs for fleet.json format and setup."
}

while [[ $# -gt 0 ]]; do
    case $1 in
        --local)
            ACTION="local"
            shift
            ;;
        --push)
            ACTION="push"
            shift
            ;;
        --list)
            ACTION="list"
            shift
            ;;
        --rotate)
            ACTION="rotate"
            shift
            ;;
        --quiet|-q)
            QUIET=true
            shift
            ;;
        --help|-h)
            show_help
            exit 0
            ;;
        *)
            echo -e "${RED}Unknown option: $1${NC}"
            echo "Use --help for usage"
            exit 1
            ;;
    esac
done

if [[ -z "$ACTION" ]]; then
    show_help
    exit 1
fi

# ─────────────────────────────────────────────────────────────────
# Dispatch
# ─────────────────────────────────────────────────────────────────
case "$ACTION" in
    local)
        $QUIET || echo -e "${CYAN}"
        $QUIET || echo "╔════════════════════════════════════════════════════════╗"
        $QUIET || echo "║          MeshForge Fleet Backup — Local                ║"
        $QUIET || echo "╚════════════════════════════════════════════════════════╝"
        $QUIET || echo -e "${NC}"
        $QUIET || echo -e "  Host: ${BOLD}${HOSTNAME_SHORT}${NC}"
        $QUIET || echo ""
        do_local_backup
        ;;
    push)
        $QUIET || echo -e "${CYAN}"
        $QUIET || echo "╔════════════════════════════════════════════════════════╗"
        $QUIET || echo "║          MeshForge Fleet Backup — Push                 ║"
        $QUIET || echo "╚════════════════════════════════════════════════════════╝"
        $QUIET || echo -e "${NC}"
        $QUIET || echo -e "  Host: ${BOLD}${HOSTNAME_SHORT}${NC}"
        $QUIET || echo ""
        archive_path=$(do_local_backup)
        echo ""
        do_push "$archive_path"
        ;;
    list)
        do_list
        ;;
    rotate)
        do_rotate
        ;;
esac
