#!/usr/bin/env bash
# Behavior test for honest_status.sh's FLEET BOX LIST derivation (2026-07-28).
#
# WHY: the list was a second hardcode ("moc moc1 moc2 moc3 moc5"), independent
# of the fleet_hosts SSOT that fleet_pull.sh and fleet_dup_collector read. It
# drifted exactly as honest_failure_modes #5 predicts — the file grew to 8
# boxes, this stayed at 5 — and the gate printed "fleet SHA drift PASS 5/5",
# which READS as whole-fleet coverage while 3 boxes were never checked.
#
# Drives the REAL script with a stub ssh so each box's answer is scriptable,
# and asserts the derivation + the dispositions that make widening safe.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
SCRIPT="$HERE/../scripts/honest_status.sh"
REAL_PYTHON3="$(command -v python3)"; export REAL_PYTHON3
fails=0

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
SB="$TMP/bin"; mkdir -p "$SB"

# Fake repo (see test_honest_status_preserve.sh — the real repo's venv python
# is an absolute path that routes around the PATH stub).
FAKE_REPO="$TMP/repo"; mkdir -p "$FAKE_REPO/tests" "$FAKE_REPO/scripts"
printf 'import sys\nsys.exit(0)\n' > "$FAKE_REPO/scripts/lint.py"

cat > "$SB/python3" <<'EOF'
#!/usr/bin/env bash
for a in "$@"; do
  if [ "$a" = "pytest" ]; then echo "1 passed in 0.10s"; exit 0; fi
done
exec "$REAL_PYTHON3" "$@"
EOF

# Stub ssh: the box name selects a canned personality. The command string is
# the last arg; we only need to know which leg is asking.
cat > "$SB/ssh" <<'EOF'
#!/usr/bin/env bash
box=""; for a in "$@"; do case "$a" in -*|*=*) ;; *) box="$a"; break;; esac; done
cmd="${!#}"
case "$box" in
  box-down) exit 255 ;;                       # unreachable
  box-norepo)                                  # up, but no repo and no watchdog
    case "$cmd" in
      *rev-parse*) echo "HSUP" ;;
      *WDSEP*) echo "1700000000"; echo "inactive"; echo "---WDSEP---" ;;
    esac ;;
  box-good)
    case "$cmd" in
      *rev-parse*) echo "HSUP"; echo "$FAKE_HEAD" ;;
      *WDSEP*) echo "$(date +%s)"; echo "active"; echo "---WDSEP---"
               echo "{\"ts\": $(date +%s), \"signals\": []}" ;;
    esac ;;
  box-mute)                                    # watchdog ACTIVE but no state
    case "$cmd" in
      *rev-parse*) echo "HSUP"; echo "$FAKE_HEAD" ;;
      *WDSEP*) echo "$(date +%s)"; echo "active"; echo "---WDSEP---" ;;
    esac ;;
esac
exit 0
EOF
for c in gh curl; do printf '#!/usr/bin/env bash\nexit 1\n' > "$SB/$c"; done
chmod +x "$SB"/*

FAKE_HOME="$TMP/home"; mkdir -p "$FAKE_HOME/.config/meshforge"

run() {  # env: HONEST_BOXES / MESHFORGE_FLEET_HOSTS as needed
  PATH="$SB:$PATH" HOME="$FAKE_HOME" XDG_STATE_HOME="" \
    MESHFORGE_REPO="$FAKE_REPO" FAKE_HEAD="$FAKE_HEAD" \
    bash "$SCRIPT" --quick "$@" 2>&1
}

# The script compares each box's answer to ITS OWN HEAD; with a repo-less fake
# repo that is "?", so scripted boxes echo the same literal to count as matched.
FAKE_HEAD="?"; export FAKE_HEAD

check() { if [ -n "$2" ]; then echo "PASS: $1"; else echo "FAIL: $1"; fails=1; fi; }

# ── 1. the list comes from the fleet_hosts SSOT, not a hardcode ──────────
hosts="$TMP/fleet_hosts"
printf '# comment\nbox-good\n\nbox-norepo\n' > "$hosts"
out="$(MESHFORGE_FLEET_HOSTS="$hosts" run)"
check "provenance line names the SSOT file" \
  "$(echo "$out" | grep -q "source: $hosts" && echo ok)"
check "list = file hosts + self (3 boxes, not a hardcoded 5)" \
  "$(echo "$out" | grep -q 'fleet legs cover 3 box(es)' && echo ok)"
check "no box from the retired hardcode appears" \
  "$(echo "$out" | grep -q 'moc1' && echo '' || echo ok)"

# ── 2. missing SSOT narrows LOUDLY, it does not invent a fleet ───────────
out="$(MESHFORGE_FLEET_HOSTS="$TMP/nope" run)"
check "absent fleet_hosts says SELF ONLY out loud" \
  "$(echo "$out" | grep -q 'SELF ONLY' && echo ok)"

# ── 3. up-but-no-repo is its own state, not 'unreachable' ────────────────
printf 'box-good\nbox-norepo\n' > "$hosts"
out="$(MESHFORGE_FLEET_HOSTS="$hosts" HONEST_BOXES="box-good box-norepo" run)"
check "no-repo box reported as no-repo, not unreach" \
  "$(echo "$out" | grep -q 'box-norepo:no-repo' && echo ok)"
check "no-repo box excluded from the drift denominator (1/1, not 1/2)" \
  "$(echo "$out" | grep -E 'fleet SHA drift' | grep -q '1/1' && echo ok)"

# ── 4. a DOWN box is UNKNOWN — absence of evidence is not convergence ────
out="$(HONEST_BOXES="box-good box-down" run)"
check "down box keeps the drift leg UNKNOWN" \
  "$(echo "$out" | grep -E 'fleet SHA drift' | grep -q 'UNKNOWN\|reachable' && echo ok)"

# ── 5. watchdog: absent organ vs ACTIVE-but-silent are DIFFERENT ─────────
out="$(HONEST_BOXES="box-good box-norepo" run)"
check "box running no watchdog is excluded, not counted blind" \
  "$(echo "$out" | grep -E 'watchdog signals' | grep -q '1/1 clean' && echo ok)"
check "and it is still named in the detail" \
  "$(echo "$out" | grep -q 'box-norepo:no-watchdog' && echo ok)"

out="$(HONEST_BOXES="box-good box-mute" run)"
check "watchdog ACTIVE but no state is UNKNOWN-loud, never excused" \
  "$(echo "$out" | grep -q 'ACTIVE-but-no-state' && echo ok)"

echo "---"
if [ "$fails" = 0 ]; then echo "ALL PASS"; exit 0; else echo "FAILED"; exit 1; fi
