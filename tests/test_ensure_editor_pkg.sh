#!/usr/bin/env bash
# Behavior test for scripts/ensure_editor_pkg.sh (2026-10-30).
#
# The TUI's in-app config editor needs `dialog` — whiptail has no --editbox,
# and from 06-16 to 10-30 "Edit Config" silently did nothing on every
# whiptail-only box. install_noc.sh now installs dialog on a NEW box; this
# helper converges the EXISTING fleet (update.sh + fleet_sync.sh). Properties
# that matter: dry-run changes nothing; it installs only when absent; it
# never prompts (sudo -n); a stale index gets ONE apt-get update; every
# failure is a loud `error:` line, never a quiet success.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
SCRIPT="$HERE/../scripts/ensure_editor_pkg.sh"
fails=0

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
LOG="$TMP/calls.log"
BIN="$TMP/bin"; mkdir -p "$BIN"
# Fake apt-get: logs its argv. APT_FAIL is a space-separated set of modes:
# locate / fetch404 fail the FIRST install as a stale index; hard fails every
# install; ghost exits 0 WITHOUT putting dialog on PATH; update_fail fails
# apt-get update. Modes compose ("locate update_fail").
cat > "$BIN/apt-get" <<EOF
#!/usr/bin/env bash
has() { case " \${APT_FAIL:-} " in *" \$1 "*) return 0;; esac; return 1; }
echo "apt-get \$*" >> "$LOG"
if [ "\$1" = install ]; then
  n=\$(grep -c '^apt-get install' "$LOG")
  has hard && { echo "E: Could not get lock" >&2; exit 100; }
  has locate && [ "\$n" = 1 ] && { echo "E: Unable to locate package dialog" >&2; exit 100; }
  has fetch404 && [ "\$n" = 1 ] && { echo "E: Failed to fetch http://deb/dialog.deb  404  Not Found" >&2; echo "E: Unable to fetch some archives, maybe run apt-get update or try with --fix-missing?" >&2; exit 100; }
  has ghost && exit 0
  touch "$TMP/dialog_installed"
fi
if [ "\$1" = update ] && has update_fail; then
  echo "E: Could not resolve deb.debian.org" >&2; exit 100
fi
exit 0
EOF
# Fake sudo: logs, then runs the rest — or refuses like `sudo -n` with no
# cached credential when SUDO_REFUSE=1.
cat > "$BIN/sudo" <<EOF
#!/usr/bin/env bash
echo "sudo \$*" >> "$LOG"
[ "\${SUDO_REFUSE:-0}" = 1 ] && { echo "sudo: a password is required" >&2; exit 1; }
[ "\$1" = -n ] && shift
exec "\$@"
EOF
chmod +x "$BIN/apt-get" "$BIN/sudo"

# "dialog on PATH" == the marker file exists (the fake apt creates it), so
# the helper's AFTER-install re-probe is exercised, not assumed.
run() {
  : > "$LOG"; rm -f "$TMP/dialog_installed"
  [ "${DIALOG_PRESENT:-0}" = 1 ] && touch "$TMP/dialog_installed"
  APT_GET_BIN="$BIN/apt-get" SUDO_BIN="$BIN/sudo" \
  DIALOG_PROBE_FILE="$TMP/dialog_installed" EUID_OVERRIDE="${AS_UID:-1000}" \
    bash "$SCRIPT" "$@" 2>&1
}
check() { if [ -n "$2" ]; then echo "PASS: $1"; else echo "FAIL: $1"; fails=1; fi; }

# ── already present: nothing runs ────────────────────────────────────────
out="$(DIALOG_PRESENT=1 run --apply)"; rc=$?
check "present -> 'present'" "$(echo "$out" | grep -q '^present' && echo ok)"
check "present -> exit 0" "$([ "$rc" = 0 ] && echo ok)"
check "present -> apt never called" "$([ ! -s "$LOG" ] && echo ok)"

# ── absent, dry-run: says so, installs nothing ───────────────────────────
out="$(run)"; rc=$?
check "dry-run -> would_add" "$(echo "$out" | grep -q '^would_add' && echo ok)"
check "dry-run -> exit 0" "$([ "$rc" = 0 ] && echo ok)"
check "dry-run -> apt never called" "$([ ! -s "$LOG" ] && echo ok)"

# ── absent, --apply as the operator: sudo -n, -y, the one package ────────
out="$(run --apply)"; rc=$?
check "apply -> added" "$(echo "$out" | grep -q '^added' && echo ok)"
check "apply -> exit 0" "$([ "$rc" = 0 ] && echo ok)"
check "apply -> NON-interactive sudo (-n) straight to apt-get (no env: a" \
  "$(grep -qE '^sudo -n [^ ]*apt-get install' "$LOG" && echo ok)"
check "  sudoers rule scoped to apt-get must suffice)" \
  "$(grep -q '^sudo -n env' "$LOG" && echo '' || echo ok)"
# --no-upgrade: never lifts an already-installed shared lib, so needrestart
# has no reason to restart services (rnsd, #69) in the middle of a deploy.
check "apply -> installs exactly dialog with -y --no-upgrade" \
  "$(grep -qx 'apt-get install -y --no-upgrade dialog' "$LOG" && echo ok)"
check "apply -> no apt-get update when not needed" \
  "$(grep -q '^apt-get update' "$LOG" && echo '' || echo ok)"

# ── absent, --apply as root: no sudo at all ──────────────────────────────
out="$(AS_UID=0 run --apply)"; rc=$?
check "root apply -> added without sudo" \
  "$(echo "$out" | grep -q '^added' && ! grep -q '^sudo' "$LOG" && echo ok)"

# ── stale index: ONE update, then one retry ──────────────────────────────
out="$(APT_FAIL=locate run --apply)"; rc=$?
check "stale index -> added after refresh" "$(echo "$out" | grep -q '^added' && echo ok)"
check "stale index -> exactly one apt-get update" \
  "$([ "$(grep -c '^apt-get update' "$LOG")" = 1 ] && echo ok)"

# ── 404 stale index (the commoner shape): one update, one retry ──────────
out="$(APT_FAIL=fetch404 run --apply)"; rc=$?
check "404 stale index -> added after refresh" "$(echo "$out" | grep -q '^added' && echo ok)"
check "404 stale index -> exactly one apt-get update" \
  "$([ "$(grep -c '^apt-get update' "$LOG")" = 1 ] && echo ok)"

# ── stale index AND update fails: error naming both ──────────────────────
out="$(APT_FAIL="locate update_fail" run --apply)"; rc=$?
check "update fails after stale index -> error: ... update failed" \
  "$(echo "$out" | grep -q '^error:.*update failed' && echo ok)"
check "update fails after stale index -> exit 1" "$([ "$rc" = 1 ] && echo ok)"

# ── apt says 0 but dialog is still not on PATH: MEASURED, not asserted ───
out="$(APT_FAIL=ghost run --apply)"; rc=$?
check "rc 0 but no dialog -> error:, never 'added'" \
  "$(echo "$out" | grep -q '^error:' && ! echo "$out" | grep -q '^added' && echo ok)"
check "rc 0 but no dialog -> exit 1" "$([ "$rc" = 1 ] && echo ok)"

# ── apt fails: error line, exit 1, never 'added' ─────────────────────────
out="$(APT_FAIL=hard run --apply)"; rc=$?
check "apt failure -> error:" "$(echo "$out" | grep -q '^error:' && echo ok)"
check "apt failure -> exit 1" "$([ "$rc" = 1 ] && echo ok)"
check "apt failure -> carries apt's reason" "$(echo "$out" | grep -q 'Could not get lock' && echo ok)"

# ── sudo refuses (no NOPASSWD): error, never hangs on a prompt ───────────
out="$(SUDO_REFUSE=1 run --apply)"; rc=$?
check "sudo refusal -> error:" "$(echo "$out" | grep -q '^error:' && echo ok)"
check "sudo refusal -> exit 1" "$([ "$rc" = 1 ] && echo ok)"

# ── no apt on this box: its own status, not an error, not 'added' ────────
out="$(APT_GET_BIN="$TMP/nope" SUDO_BIN="$BIN/sudo" DIALOG_PROBE_FILE="$TMP/never" bash "$SCRIPT" --apply 2>&1)"; rc=$?
check "no apt-get -> no_apt" "$(echo "$out" | grep -q '^no_apt' && echo ok)"
check "no apt-get -> exit 0" "$([ "$rc" = 0 ] && echo ok)"

if [ "$fails" = 0 ]; then echo "ALL PASS"; else echo "SOME FAILED"; fi
exit "$fails"
