#!/bin/bash
# fleet_admin_account.sh — give a lab/fleet box the fleet admin-account shape
#
# The fleet shape (measured on the Pi fleet, 2026-11-09): one admin user with
# NO password (login is by ssh key only, so passwordless sudo is reachable
# only through that key), in sudo adm dialout plugdev netdev, with
# /etc/sudoers.d/010-<user> = "<user> ALL=(ALL) NOPASSWD: ALL".
# First applied by hand to a Debian forky laptop whose installer had set a
# root password and left the first user with NO sudo at all — this script
# is that recipe, so the next box (a GMKtec, a Pi) gets it, not a retype.
#
# ⚠️ It installs NO ssh key, by design (operator 2026-11-09, S11 of the NOC
# read-grant security pass: nothing but scripts/box_truth_grant.py writes a
# box's ssh key list). A key that grants passwordless sudo is
# root-equivalent; the OPERATOR places it, by hand, as root. Until then the
# account has no password and no key: it exists but cannot log in.
#
# Usage, as root on the box:
#   bash fleet_admin_account.sh --host <expected-hostname> --user <admin-user> [--dry-run]
#   (no sudo yet? `su -c 'bash fleet_admin_account.sh ...'` in a real
#    terminal — su needs a TTY for the root password)
#
# Refuses (exit 1, nothing changed) when: an argument is missing or unknown;
# the user name is not a plain Linux name or is root; this box's hostname is
# not --host; it is not run as root (except --dry-run, which validates and
# prints the plan unprivileged).
# What it never touches: ssh keys, other users, their passwords or sudo,
# root, sshd. Disable sshd password auth as a SEPARATE step, after key login
# is proven. Safe to re-run: every step checks first.
set -euo pipefail

HOST="" U="" DRY=0
while [ $# -gt 0 ]; do
    case "$1" in
        --host) HOST="${2:-}"; shift 2 ;;
        --user) U="${2:-}"; shift 2 ;;
        --dry-run) DRY=1; shift ;;
        *) echo "FAIL: unknown argument '$1'"; exit 1 ;;
    esac
done
[ -n "$HOST" ] && [ -n "$U" ] || {
    echo "FAIL: --host and --user are both required"; exit 1; }
[[ "$U" =~ ^[a-z_][a-z0-9_-]{0,31}$ ]] || {
    echo "FAIL: '$U' is not a plain Linux user name"; exit 1; }
[ "$U" != root ] || { echo "FAIL: the admin user cannot be root"; exit 1; }

ACTUAL=$(hostname)
[ "$ACTUAL" = "$HOST" ] || {
    echo "FAIL: this is '$ACTUAL', not '$HOST' — nothing changed"; exit 1; }

SUDOERS=/etc/sudoers.d/010-$U
GROUPS_WANTED=sudo,adm,dialout,plugdev,netdev
if [ "$DRY" = 1 ]; then
    echo "DRY-RUN on $ACTUAL — would:"
    echo "  user   $U (no password) — $(id "$U" >/dev/null 2>&1 && echo exists || echo create)"
    echo "  groups $GROUPS_WANTED"
    echo "  sudo   $SUDOERS = '$U ALL=(ALL) NOPASSWD: ALL' (visudo-validated first)"
    echo "  ssh    NO key installed — the operator places it by hand"
    exit 0
fi
[ "$(id -u)" -eq 0 ] || { echo "FAIL: run as root (or --dry-run)"; exit 1; }

# 1. user — no password
if id "$U" >/dev/null 2>&1; then
    echo "ok   user $U exists (uid $(id -u "$U")) — left as is"
else
    adduser --disabled-password --gecos "" "$U" >/dev/null
    echo "made user $U (uid $(id -u "$U"), no password)"
fi

# 2. groups (only those this box has)
for g in ${GROUPS_WANTED//,/ }; do
    if getent group "$g" >/dev/null; then usermod -aG "$g" "$U"
    else echo "skip group $g (absent on this box)"; fi
done
echo "ok   groups: $(id -nG "$U")"

# 3. sudoers — validate a temp copy, install atomically, re-check the whole tree.
#    The temp name carries a '.', which sudo's includedir ignores, so a crash
#    mid-step can never load a half-written rule.
TMP=$(mktemp "/etc/sudoers.d/.010-$U.XXXXXX")
trap 'rm -f "$TMP"' EXIT
echo "$U ALL=(ALL) NOPASSWD: ALL" > "$TMP"
chmod 440 "$TMP"
visudo -cf "$TMP" >/dev/null || {
    echo "FAIL: sudoers line did not validate — nothing installed"; exit 1; }
mv -f "$TMP" "$SUDOERS"
trap - EXIT
visudo -c >/dev/null || {
    echo "FAIL: full sudoers check failed after install — removing $SUDOERS"
    rm -f "$SUDOERS"; exit 1; }
echo "ok   $SUDOERS installed + full visudo -c passed"

# proof, as the new user
su -s /bin/bash -c 'sudo -n true' "$U" && echo "PASS: $U has passwordless sudo" \
    || { echo "FAIL: $U sudo -n did not succeed"; exit 1; }
echo "NEXT (operator, by hand, as root): place the admin PUBLIC key in $U's ssh"
echo "     login keys under its home (dir 700, file 600, owned by $U), then prove"
echo "     key login from the manager BEFORE disabling sshd password auth."
