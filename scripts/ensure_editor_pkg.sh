#!/usr/bin/env bash
# ensure_editor_pkg.sh — install `dialog` where it is missing.
#
# WHY (2026-10-30): the TUI's in-app config editor needs `dialog`. whiptail —
# the backend the TUI prefers — has NO --editbox; from 06-16 to 10-30 every
# "Edit Config" on a whiptail-only box silently did nothing (exit 1 read as a
# cancel). No installer ever installed dialog. install_noc.sh now does for a
# NEW box; this converges the EXISTING fleet. Called by update.sh (any box,
# as root) and fleet_sync.sh (every fleet box, as the operator) so the next
# box cannot be forgotten. The TUI also offers the install in-app — this
# just means nobody has to accept that prompt box by box.
#
# Rules:
#   * dialog on PATH        -> present
#   * no apt-get here       -> no_apt   (not Debian-family; nothing to do)
#   * absent, dry-run       -> would_add
#   * absent, --apply       -> added | error: <apt's reason>
#     A stale/empty index ("Unable to locate package", or a 404 "Failed to
#     fetch / Unable to fetch some archives") gets ONE apt-get update and
#     ONE retry. Non-root goes through `sudo -n apt-get` directly (no env,
#     so a sudoers rule scoped to apt-get suffices) — a deploy must never
#     hang on a password prompt; a refusal is an error line.
#     --no-upgrade: never lifts an installed shared lib, so needrestart has
#     no reason to restart services (rnsd, #69) mid-deploy; a dependency
#     that NEEDS an upgrade fails loudly instead.
#     "added" only when dialog is then MEASURED on PATH — apt rc 0 alone
#     is an assertion (held/diverted package, odd PATH).
#   Each apt call is capped at 200 s, so the worst case (install + update +
#   install = 600 s) stays inside the callers' 700 s outer timeout.
#
# Output: one status line. Exit 0 on present/no_apt/would_add/added, 1 on
# error. Dry-run by default.
#
# Test seams (tests/test_ensure_editor_pkg.sh): APT_GET_BIN, SUDO_BIN,
# DIALOG_PROBE_FILE (dialog "on PATH" iff this file exists), EUID_OVERRIDE.
#
# Usage: scripts/ensure_editor_pkg.sh [--apply]
set -u

APPLY=0
[ "${1:-}" = "--apply" ] && APPLY=1
PKG="dialog"
APT="${APT_GET_BIN:-$(command -v apt-get 2>/dev/null || echo /usr/bin/apt-get)}"
SUDO="${SUDO_BIN:-sudo}"
UID_NOW="${EUID_OVERRIDE:-$EUID}"

has_dialog() {
    if [ -n "${DIALOG_PROBE_FILE:-}" ]; then
        [ -e "$DIALOG_PROBE_FILE" ]
    else
        command -v "$PKG" >/dev/null 2>&1 || [ -x "/usr/bin/$PKG" ]
    fi
}
clip() { local t; t="$(printf "%s" "$1" | tail -n 2 | tr "\n" " ")"; printf "%s" "${t:0:160}"; }

if has_dialog; then
    echo "present: $PKG on PATH"
    exit 0
fi
if [ ! -x "$APT" ]; then
    echo "no_apt: $PKG absent and no apt-get here — the TUI editor stays unavailable"
    exit 0
fi
if [ "$APPLY" = 0 ]; then
    echo "would_add: $PKG (the TUI in-app config editor)"
    exit 0
fi

apt_run() {   # apt_run <apt args...>; output on stdout, rc preserved
    if [ "$UID_NOW" = 0 ]; then
        timeout 200 "$APT" "$@" 2>&1
    else
        timeout 200 "$SUDO" -n "$APT" "$@" 2>&1
    fi
}
STALE="Unable to locate package|Failed to fetch|Unable to fetch some archives|--fix-missing"

out="$(apt_run install -y --no-upgrade "$PKG")"; rc=$?
if [ "$rc" != 0 ] && printf "%s" "$out" | grep -qE "$STALE"; then
    up="$(apt_run update)"; urc=$?
    if [ "$urc" != 0 ]; then
        echo "error: $PKG install hit a stale index and apt-get update failed — $(clip "$up")"
        exit 1
    fi
    out="$(apt_run install -y --no-upgrade "$PKG")"; rc=$?
fi
if [ "$rc" != 0 ]; then
    echo "error: apt-get install $PKG rc=$rc — $(clip "$out")"
    exit 1
fi
if ! has_dialog; then
    echo "error: apt-get reported success but $PKG is still not on PATH — $(clip "$out")"
    exit 1
fi
echo "added: $PKG"
exit 0
