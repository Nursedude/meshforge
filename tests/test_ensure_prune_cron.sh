#!/usr/bin/env bash
# Behavior test for scripts/ensure_prune_cron.sh (2026-10-18).
#
# The pytest_tmp_prune cron was hand-installed box by box; lehua never got it
# and nothing in the repo would have noticed. The helper adds it when — and
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

# ── an unreadable crontab is an ERROR, never read as empty ──────────────
printf '%s\n' "$OTHER" > "$STORE"; cp "$STORE" "$TMP/before"
out="$(LIST_FAIL=1 run --apply)"; rc=$?
check "unreadable crontab exits nonzero" "$([ "$rc" != 0 ] && echo ok)"
check "unreadable crontab says error" "$(echo "$out" | grep -q '^error' && echo ok)"
check "unreadable crontab is not overwritten" "$(cmp -s "$STORE" "$TMP/before" && echo ok)"

echo "---"
if [ "$fails" = 0 ]; then echo "ALL PASS"; exit 0; else echo "FAILED"; exit 1; fi
