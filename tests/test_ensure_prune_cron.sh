#!/usr/bin/env bash
# Behavior test for scripts/ensure_prune_cron.sh (2026-10-18).
#
# The pytest_tmp_prune cron was hand-installed box by box; one fleet box never
# got it and nothing in the repo would have noticed. The helper adds it when — and
# only when — no line mentions the script. The properties that matter: it never
# duplicates (another minute, the MeshAnchor twin's path), it honours a
# deliberate absence (a commented line), and it never alters another line.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
SCRIPT="$HERE/../scripts/ensure_prune_cron.sh"
REPO="$(cd "$HERE/.." && pwd)"
fails=0

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
STORE="$TMP/crontab.store"
STUB="$TMP/crontab"
# A fake crontab(1): `-l` prints the store (or the real "no crontab" refusal),
# `<file>` installs it. LIST_FAIL=1 simulates an unreadable crontab.
cat > "$STUB" <<EOF
#!/usr/bin/env bash
if [ "\$1" = "-l" ]; then
  [ "\${LIST_FAIL:-0}" = 1 ] && { echo "crontab: permission denied" >&2; exit 1; }
  [ -f "$STORE" ] || { echo "no crontab for tester" >&2; exit 1; }
  cat "$STORE"; exit 0
fi
cp "\$1" "$STORE"
EOF
chmod +x "$STUB"

run() { CRONTAB_BIN="$STUB" HOME="$TMP/home" bash "$SCRIPT" "$@" 2>&1; }
check() { if [ -n "$2" ]; then echo "PASS: $1"; else echo "FAIL: $1"; fails=1; fi; }
count() { grep -c 'pytest_tmp_prune\.sh' "$STORE" 2>/dev/null || echo 0; }
mkdir -p "$TMP/home"

# ── no crontab at all: dry-run says so and writes nothing ────────────────
rm -f "$STORE"
out="$(run)"; rc=$?
check "dry-run on an empty crontab reports would_add" "$(echo "$out" | grep -q '^would_add' && echo ok)"
check "dry-run exits 0" "$([ "$rc" = 0 ] && echo ok)"
check "dry-run writes nothing" "$([ -f "$STORE" ] && echo '' || echo ok)"

# ── --apply adds exactly one line, wired to the verdict + capture idiom ──
out="$(run --apply)"; rc=$?
check "--apply on an empty crontab reports added" "$(echo "$out" | grep -q '^added' && echo ok)"
check "exactly one prune line" "$([ "$(count)" = 1 ] && echo ok)"
check "the line runs THIS repo's script" "$(grep -q "$REPO/scripts/pytest_tmp_prune.sh" "$STORE" && echo ok)"
check "the line carries the wrapper_crashed guard" \
  "$(grep -q 'cron_verdict.sh pytest_tmp_prune FAIL wrapper_crashed' "$STORE" && echo ok)"
check "the capture dir the line redirects into exists" \
  "$([ -d "$TMP/home/.local/state/meshforge/cron_out" ] && echo ok)"

# ── idempotent ──────────────────────────────────────────────────────────
out="$(run --apply)"
check "second --apply reports present" "$(echo "$out" | grep -q '^present' && echo ok)"
check "second --apply still one line" "$([ "$(count)" = 1 ] && echo ok)"

# ── present at another minute / the twin's path: never a duplicate ──────
OTHER='# keep me: 100% literal "quotes" and $VARS
*/5 * * * * /usr/bin/true >/dev/null 2>&1'
printf '%s\n40 */6 * * * /opt/meshanchor/scripts/pytest_tmp_prune.sh >/dev/null 2>&1\n' "$OTHER" > "$STORE"
cp "$STORE" "$TMP/before"
out="$(run --apply)"
check "the twin's prune line counts as present" "$(echo "$out" | grep -q '^present' && echo ok)"
check "a present crontab is left byte-identical" "$(cmp -s "$STORE" "$TMP/before" && echo ok)"

# ── a deliberate absence (commented line) is honoured ───────────────────
printf '%s\n# pytest_tmp_prune: absent — no suite runs here\n' "$OTHER" > "$STORE"
cp "$STORE" "$TMP/before"
out="$(run --apply)"
check "a commented mention reports opted_out" "$(echo "$out" | grep -q '^opted_out' && echo ok)"
check "an opted-out crontab is left byte-identical" "$(cmp -s "$STORE" "$TMP/before" && echo ok)"

# ── adding preserves every other line byte-for-byte, and backs up first ──
printf '%s\n' "$OTHER" > "$STORE"
out="$(run --apply)"
check "other lines are preserved verbatim" \
  "$([ "$(head -n 2 "$STORE")" = "$OTHER" ] && echo ok)"
check "the prune line is appended" "$([ "$(count)" = 1 ] && echo ok)"
check "a backup of the prior crontab is written" \
  "$(ls "$TMP/home/.local/state/meshforge/"crontab.bak-* >/dev/null 2>&1 && echo ok)"

# ── opt-out needs a DELIBERATE token, not any passing mention (review F3) ─
printf '%s\n# TODO pytest_tmp_prune later\n' "$OTHER" > "$STORE"
out="$(run --apply)"
check "a passing comment mention is NOT an opt-out" "$(echo "$out" | grep -q '^added' && echo ok)"
check "…so the cron is added" "$([ "$(count)" = 1 ] && echo ok)"
printf '# pytest_tmp_prune: capture via cron_capture_wire\n11 */6 * * * /opt/x/scripts/pytest_tmp_prune.sh >/dev/null 2>&1\n' > "$STORE"
out="$(run --apply)"
check "a doc comment above a live line reads present, not opted_out" \
  "$(echo "$out" | grep -q '^present' && echo ok)"
printf '#11 */6 * * * /opt/x/scripts/pytest_tmp_prune.sh >/dev/null 2>&1\n' > "$STORE"
out="$(run --apply)"
check "a commented-out invocation is a deliberate absence" "$(echo "$out" | grep -q '^opted_out' && echo ok)"
printf '0 1 * * * logrotate /var/log/pytest_tmp_prune.sh.log\n' > "$STORE"
out="$(run --apply)"
check "a file merely NAMED like the script is not the cron" "$(echo "$out" | grep -q '^added' && echo ok)"

# ── every live invocation SHAPE reads present — a miss APPENDS A DUPLICATE
# cron on the next deploy, and the same miss on a commented-out line undoes a
# deliberate absence (second review N1: quoted paths and `)` were missed).
for shape in '"/opt/x/scripts/pytest_tmp_prune.sh" >/dev/null 2>&1' \
             "'/opt/x/scripts/pytest_tmp_prune.sh' >/dev/null 2>&1" \
             '(cd /opt/x && scripts/pytest_tmp_prune.sh)' \
             '/usr/bin/env bash "/opt/x/scripts/pytest_tmp_prune.sh"' \
             'bash /opt/x/scripts/pytest_tmp_prune.sh' \
             'FOO=1 /opt/x/scripts/pytest_tmp_prune.sh 2>&1' \
             '/opt/x/scripts/pytest_tmp_prune.sh>/dev/null'; do
  printf '11 */6 * * * %s\n' "$shape" > "$STORE"
  out="$(run --apply)"
  check "live shape reads present: $shape" "$(echo "$out" | grep -q '^present' && echo ok)"
  printf '# 11 */6 * * * %s\n' "$shape" > "$STORE"
  out="$(run --apply)"
  check "commented-out shape reads opted_out: $shape" "$(echo "$out" | grep -q '^opted_out' && echo ok)"
done
# The opt-out token is matched case-insensitively (second review N2).
printf '# PYTEST_TMP_PRUNE: ABSENT\n' > "$STORE"
out="$(run --apply)"
check "the opt-out token is case-insensitive" "$(echo "$out" | grep -q '^opted_out' && echo ok)"

# ── update.sh's caller: under set -e a helper error must not abort the
# update, and only the three healthy statuses may print a green tick
# (review F1/F2). The block is extracted from update.sh, so this pins the
# real caller, not a copy of it.
UPD="$HERE/../scripts/update.sh"
FAKE="$TMP/fakeinstall"; mkdir -p "$FAKE/scripts"
upd_run() {  # $1 = what the stub helper prints, $2 = its exit code
  printf '#!/usr/bin/env bash\necho "%s"\nexit %s\n' "$1" "$2" > "$FAKE/scripts/ensure_prune_cron.sh"
  { echo 'set -e'; echo "INSTALL_DIR='$FAKE'; REAL_USER=\"\$(id -un)\"; GREEN=; YELLOW=; NC="
    sed -n '/^# The pytest_tmp_prune cron (\/tmp is RAM/,/^fi$/p' "$UPD"
    echo 'echo AFTER_BLOCK'; } > "$TMP/upd_frag.sh"
  bash "$TMP/upd_frag.sh" 2>&1
}
check "update.sh carries the prune block (extraction is not empty)" \
  "$(sed -n '/^# The pytest_tmp_prune cron (\/tmp is RAM/,/^fi$/p' "$UPD" | grep -q ensure_prune_cron && echo ok)"
out="$(upd_run 'error: crontab unreadable' 1)"
check "a helper error does not abort update.sh" "$(echo "$out" | grep -q AFTER_BLOCK && echo ok)"
check "…and is reported with a warning, not a tick" "$(echo "$out" | grep -q '⚠ prune cron: error' && echo ok)"
out="$(upd_run 'sudo: a password is required' 1)"
check "unrecognised output is a warning, never a green tick" \
  "$(echo "$out" | grep -q '⚠' && ! echo "$out" | grep -q '✓' && echo ok)"
out="$(upd_run 'added: 11 */6 ...' 0)"
check "a healthy status prints the tick" "$(echo "$out" | grep -q '✓ prune cron: added' && echo ok)"

# ── an unreadable crontab is an ERROR, never read as empty ──────────────
printf '%s\n' "$OTHER" > "$STORE"; cp "$STORE" "$TMP/before"
out="$(LIST_FAIL=1 run --apply)"; rc=$?
check "unreadable crontab exits nonzero" "$([ "$rc" != 0 ] && echo ok)"
check "unreadable crontab says error" "$(echo "$out" | grep -q '^error' && echo ok)"
check "unreadable crontab is not overwritten" "$(cmp -s "$STORE" "$TMP/before" && echo ok)"

echo "---"
if [ "$fails" = 0 ]; then echo "ALL PASS"; exit 0; else echo "FAILED"; exit 1; fi
