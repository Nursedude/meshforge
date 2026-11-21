#!/usr/bin/env bash
# stock_rns_witness.sh — a STOCK upstream RNS instance that measures whether the
# fleet (RNS 1.3.9+mf) is still heard by current upstream. Lab boxes only.
#
# Why (2026-11-21, research report XupyHiRycirvkaM5eWNwrN, claims re-measured
# against the pinned source + the rns 1.6.1 sdist): upstream RNS 1.6.x appends
# a destination stamp to every announce, and has STAMPS_ENFORCED switches
# (off in 1.6.1; release notes say 1.6.2 enforces). A stamp-enforcing node will
# stop hearing unstamped 1.3.9 announces. This witness is the instrument: its
# own RNS instance (never attached to a fleet rnsd), one TCP leg to the
# regional entry point, NomadNet as the stock LXMF client.
#
# Slice 1 = a MEASUREMENT run as a transient `systemd-run --user` unit (the lab
# role's convention for experiments; docs/fleet_roles.yaml `lab`). No persistent
# unit, no role change. It never enables transport, never touches the fleet's
# RNS config, and refuses to run on a box that has one (MF009: port and
# identity collisions).
#
# Usage:
#   stock_rns_witness.sh install            # venv with pinned stock versions
#   stock_rns_witness.sh render-config      # print the RNS config it would write
#   stock_rns_witness.sh start [--enforce]  # write config, start the transient unit
#   stock_rns_witness.sh status             # versions, unit, interfaces, table rows
#   stock_rns_witness.sh probe NAME=HASH... # drop cached path, request fresh: FOUND n / NOT_FOUND / UNKNOWN
#   stock_rns_witness.sh stop
#   stock_rns_witness.sh uninstall          # stop + remove venv (identity kept)
set -euo pipefail

RNS_VER="1.6.1"
LXMF_VER="1.3.1"
NOMADNET_VER="1.5.1"

BASE="${STOCK_WITNESS_BASE:-$HOME/.local/share/meshforge/stock-witness}"
VENV="$BASE/venv"
RNSDIR="$BASE/rns"
NNDIR="$BASE/nomadnet"
UNIT="meshforge-stock-witness"

# The regional TCP entry point. No default: a guessed host would point the
# witness at the wrong network. Set it in the env or in $BASE/entry.env
# (STOCK_WITNESS_ENTRY_HOST=... / STOCK_WITNESS_ENTRY_PORT=...).
[ -f "$BASE/entry.env" ] && . "$BASE/entry.env"
ENTRY_HOST="${STOCK_WITNESS_ENTRY_HOST:-}"
ENTRY_PORT="${STOCK_WITNESS_ENTRY_PORT:-4242}"
# Non-default local instance ports: a fleet rnsd uses 37428/37429 (MF009).
SHARED_PORT=37528
CONTROL_PORT=37529

die() { echo "stock_rns_witness: $*" >&2; exit 2; }

refuse_fleet_rns() {
    # A box that runs the fleet's RNS must never host this (identity + ports).
    local c
    for c in /etc/reticulum/config "$HOME/.reticulum/config"; do
        [ -e "$c" ] && die "refusing: $c exists — this box runs the fleet's RNS. Lab boxes only."
    done
    return 0
}

render_config() {
    local enforce="${1:-no}"
    [ -n "$ENTRY_HOST" ] || die "STOCK_WITNESS_ENTRY_HOST is not set (env or $BASE/entry.env)"
    case "$ENTRY_PORT" in *[!0-9]*|"") die "STOCK_WITNESS_ENTRY_PORT must be a number, got '$ENTRY_PORT'";; esac
    case "$enforce" in yes|no) ;; *) die "enforce must be yes|no, got '$enforce'";; esac
    cat <<EOF
# Written by scripts/stock_rns_witness.sh — stock RNS ${RNS_VER} witness. Do not hand-edit.
[reticulum]
  enable_transport = No
  share_instance = Yes
  shared_instance_port = ${SHARED_PORT}
  instance_control_port = ${CONTROL_PORT}
  panic_on_interface_error = No

[stamps]
  enforce_stamps = ${enforce}

[logging]
  loglevel = 4

[interfaces]
  [[Regional entry point]]
    type = TCPClientInterface
    enabled = yes
    target_host = ${ENTRY_HOST}
    target_port = ${ENTRY_PORT}
EOF
}

versions() {
    "$VENV/bin/python" -I -c 'import importlib.metadata as m; print(" ".join(f"{p}={m.version(p)}" for p in ("rns","lxmf","nomadnet")))'
}

cmd_install() {
    refuse_fleet_rns
    mkdir -p "$BASE"
    [ -x "$VENV/bin/python" ] || python3 -m venv "$VENV"
    "$VENV/bin/pip" install --no-cache-dir --quiet \
        "rns==${RNS_VER}" "lxmf==${LXMF_VER}" "nomadnet==${NOMADNET_VER}"
    local want="rns=${RNS_VER} lxmf=${LXMF_VER} nomadnet=${NOMADNET_VER}" got
    got="$(versions)"
    [ "$got" = "$want" ] || die "installed versions '$got' != pinned '$want'"
    echo "installed: $got"
}

cmd_start() {
    refuse_fleet_rns
    local enforce=no
    [ "${1:-}" = "--enforce" ] && enforce=yes
    [ -x "$VENV/bin/nomadnet" ] || die "not installed — run: $0 install"
    if systemctl --user is-active --quiet "$UNIT"; then die "$UNIT already running — stop it first"; fi
    mkdir -p "$RNSDIR" "$NNDIR"
    render_config "$enforce" > "$RNSDIR/config.tmp" && mv "$RNSDIR/config.tmp" "$RNSDIR/config"
    systemd-run --user --unit="$UNIT" --collect -p KillSignal=SIGINT \
        -E PYTHONUNBUFFERED=1 \
        "$VENV/bin/nomadnet" --daemon --config "$NNDIR" --rnsconfig "$RNSDIR"
    echo "started $UNIT (enforce_stamps=$enforce) $(versions)"
}

cmd_status() {
    echo "pinned:   rns=${RNS_VER} lxmf=${LXMF_VER} nomadnet=${NOMADNET_VER}"
    if [ -x "$VENV/bin/python" ]; then echo "venv:     $(versions)"; else echo "venv:     NOT INSTALLED"; fi
    echo "unit:     $UNIT $(systemctl --user is-active "$UNIT" 2>/dev/null || true)"
    [ -f "$RNSDIR/config" ] && echo "enforce:  $(awk '/enforce_stamps/{print $3}' "$RNSDIR/config")"
    [ -x "$VENV/bin/rnstatus" ] || return 0
    echo "--- rnstatus"
    timeout 20 "$VENV/bin/rnstatus" --config "$RNSDIR" 2>&1 | grep -E "Interface|Status|Traffic" || echo "UNKNOWN: rnstatus gave no interface lines"
    echo "--- path table (passive: what happened to announce; reachability is \`probe\`)"
    local table
    table="$(timeout 20 "$VENV/bin/rnpath" -t --config "$RNSDIR" 2>&1 || true)"
    echo "table rows: $(printf '%s\n' "$table" | count_table)"
}

# Rows of `rnpath -t` are `<32 hex> ...`. rns 1.6.1 prints `1h (?)`, never
# "hops" — the 11-21 count grepped 'hop' and reported 0 beside 5 rows.
count_table() { grep -c -E '^<[0-9a-f]{32}>' || true; }

# One verdict per rnpath run: FOUND <hops> | NOT_FOUND | UNKNOWN. No verdict
# line (timeout, traceback, empty) is UNKNOWN — never "not found" (hfm #2).
classify() {
    local out hops
    out="$(tr '\r' '\n')"
    hops="$(printf '%s\n' "$out" | grep -o -E 'Path found, destination <[0-9a-f]+> is [0-9]+ hops? away' | tail -1 | grep -o -E '[0-9]+ hops?' | grep -o -E '^[0-9]+' || true)"
    if [ -n "$hops" ]; then echo "FOUND $hops"
    elif printf '%s\n' "$out" | grep -q -E '^Path not found'; then echo "NOT_FOUND"
    else echo "UNKNOWN"; fi
}

# Reachability, measured: drop the cached path, then ask the network fresh
# (a cached path would answer without touching the network).
cmd_probe() {
    [ -x "$VENV/bin/rnpath" ] || die "not installed — run: $0 install"
    systemctl --user is-active --quiet "$UNIT" || die "$UNIT not running — start it first"
    echo "enforce: $(awk '/enforce_stamps/{print $3}' "$RNSDIR/config")  $(versions)"
    local h v found=0 total=0
    for h in "$@"; do
        local name="${h%%=*}" hash="${h#*=}"
        case "$hash" in *[!0-9a-f]*|"") echo "  skip invalid hash '$h'"; continue;; esac
        [ ${#hash} -eq 32 ] || { echo "  skip invalid hash '$h'"; continue; }
        timeout 15 "$VENV/bin/rnpath" -d --config "$RNSDIR" "$hash" >/dev/null 2>&1 || true
        v="$(timeout 30 "$VENV/bin/rnpath" -w 20 --config "$RNSDIR" "$hash" 2>&1 | classify)"
        total=$((total+1)); case "$v" in FOUND*) found=$((found+1));; esac
        printf '  %-10s %-20s %s\n' "$v" "$name" "$hash"
    done
    echo "found: $found/$total"
}

cmd_stop() { systemctl --user stop "$UNIT" 2>/dev/null && echo "stopped $UNIT" || echo "$UNIT not running"; }

cmd_uninstall() {
    cmd_stop
    [ -d "$VENV" ] && find "$VENV" -delete
    echo "venv removed; identity + config kept under $BASE (delete by hand if wanted)"
}

case "${1:-}" in
    install)       cmd_install ;;
    render-config) shift; render_config "${1:-no}" ;;
    start)         shift; cmd_start "$@" ;;
    status)        shift; cmd_status "$@" ;;
    probe)         shift; cmd_probe "$@" ;;
    count-table)   count_table ;;
    classify)      classify ;;
    stop)          cmd_stop ;;
    uninstall)     cmd_uninstall ;;
    *) sed -n '2,24p' "$0"; exit 2 ;;
esac
