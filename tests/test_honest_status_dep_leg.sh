#!/usr/bin/env bash
# Behavior test for honest_status.sh's DEPENDENCY FINDINGS leg (2026-09-05).
#
# WHY: the leg exists because both dependency checks were writers with no
# reader — dep_advisory_check.py (2026-09-04) and dep_range_check.py
# (2026-09-05) each wrote a finding file that nothing consumed. The leg is that
# reader, so the thing it must never do is read "no finding file" as safety
# when the truth is "the check has not run in days". Those look identical on
# disk, and collapsing them would let a dead timer render as a clean bill —
# the same shape as the six-month pin this whole arc came from.
#
# HOW: the leg's only inputs are $HOME and four files, so this drives it with a
# purpose-built HOME per state. It sources the leg's REAL source text, cut from
# the real script — not a copy — so a rename of ok/unk/warnf or a move of the
# block is caught by the extraction guard below rather than silently producing
# a vacuous pass. This tests the leg's LOGIC, not its integration with the rest
# of the gate; the integration is covered by the gate actually running it, and
# a full-script drive here would cost a 10-minute suite leg per state.
set -u
# Pin ambient state (2026-09-07): a caller that exported an override — a full
# honest_status run under HONEST_BOXES drives this very suite — made the
# SSOT cases here fail, and an exported marker path was overwritten mid-suite.
unset HONEST_BOXES MESHFORGE_FLEET_HOSTS HONEST_VERDICT_PATH HONEST_WD_PATH HONEST_WD_STALE_S
HERE="$(cd "$(dirname "$0")" && pwd)"
SCRIPT="$HERE/../scripts/honest_status.sh"
fails=0

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT

# --- extraction, with the anti-vacuous guard -------------------------------
sed -n '/^# --- dependency findings/,/^esac$/p' "$SCRIPT" > "$TMP/leg.sh"
nlines=$(wc -l < "$TMP/leg.sh")
if [ "$nlines" -lt 20 ]; then
  echo "FAIL: extracted only $nlines line(s) of the dependency leg from $SCRIPT."
  echo "      The leg was renamed, moved, or deleted — every assertion below"
  echo "      would pass vacuously against an empty file."
  exit 1
fi
for helper in 'ok()' 'unk()' 'warnf()'; do
  if ! grep -q "${helper%()}" "$TMP/leg.sh"; then
    echo "FAIL: extracted leg never calls ${helper} — the stubs below would not"
    echo "      observe anything. Extraction is out of sync with the script."
    exit 1
  fi
done

cat > "$TMP/harness.sh" <<'EOF'
pass=0; fail=0; unknown=0; warns=0
ok()    { printf 'PASS %s\n' "$2"; }
unk()   { printf 'UNKNOWN %s\n' "$2"; }
warnf() { printf 'WARN %s\n' "$2"; }
EOF

check() { # label, fake-home, expected-verdict
  local label="$1" fhome="$2" want="$3" out got
  out=$(HOME="$fhome" bash -c "source '$TMP/harness.sh'; source '$TMP/leg.sh'" 2>&1)
  got=$(printf '%s' "$out" | awk '{print $1}')
  if [ "$got" = "$want" ]; then
    printf '  ok   %-38s %s\n' "$label" "$want"
  else
    printf '  FAIL %-38s expected=%s got=%s\n      %s\n' "$label" "$want" "$got" "$out"
    fails=$((fails+1))
  fi
}

mk() { mkdir -p "$TMP/$1"; printf '%s' "$1" > /dev/null; }

# An installed-stock record the leg may READ must declare itself a fleet run
# (2026-09-06: a `--host X` spot-check overwrote the fleet file twice in one
# session; the script now stamps the canonical pair and writes narrow runs to
# `.scoped` siblings). These fixtures are what the unnarrowed daily run writes.
fleet_status() { printf '# fleet dependency advisories\n# scope: fleet\n' > "$1/.meshforge-dep-advisories"; }

# 1. Both halves fresh with no findings — the only state that may pass.
mk h1
fleet_status "$TMP/h1"
: > "$TMP/h1/.meshforge-dep-ranges"
check "both fresh, no findings" "$TMP/h1" PASS

# 2. Installed-side findings present. A real fleet condition, not a broken
#    gate, so WARN — the same treatment the watchdog leg gives a degraded box.
mk h2
fleet_status "$TMP/h2"
: > "$TMP/h2/.meshforge-dep-ranges"
printf '# header\nmoc cryptography 46.0.5: GHSA-x(high)\nmoc1 urllib3 2.6.3: GHSA-y(high)\n' \
  > "$TMP/h2/.meshforge-dep-ADVISORY"
check "installed findings present" "$TMP/h2" WARN

# 3. Declared-side findings present — an unpatchable pin, the 2026-09-05 case.
mk h3
fleet_status "$TMP/h3"
: > "$TMP/h3/.meshforge-dep-ranges"
printf '# header\nrequirements/rns.txt:62 cryptography>=45.0.7,<47 — UNPATCHABLE\n' \
  > "$TMP/h3/.meshforge-dep-RANGE-FINDING"
check "declared findings present" "$TMP/h3" WARN

# 4. THE test. A check that never ran is UNKNOWN, never a clean bill.
mk h4
: > "$TMP/h4/.meshforge-dep-advisories"
check "one half never ran" "$TMP/h4" UNKNOWN

# 5. Status file present but stale — a dead timer. Same claim as never-ran:
#    we cannot say anything current, so we must not say "clean".
mk h5
fleet_status "$TMP/h5"
: > "$TMP/h5/.meshforge-dep-ranges"
touch -d '5 days ago' "$TMP/h5/.meshforge-dep-ranges"
check "status stale (dead timer)" "$TMP/h5" UNKNOWN

# 6. Blindness OUTRANKS findings: if one half is unobservable, the leg must not
#    report the other half's WARN as though it were the whole picture.
mk h6
: > "$TMP/h6/.meshforge-dep-ranges"
printf '# header\nmoc cryptography 46.0.5: GHSA-x(high)\n' > "$TMP/h6/.meshforge-dep-ADVISORY"
check "blindness outranks a finding" "$TMP/h6" UNKNOWN

# 7. Neither half ever ran — the state a fresh box is in. Still UNKNOWN.
mk h7
check "neither half ever ran" "$TMP/h7" UNKNOWN

# 8. THE 2026-09-06 shape: a fresh installed record with NO fleet stamp beside
#    a finding file — a `--host kiai` spot-check that overwrote the fleet's
#    ten-box record (the leg reported 4, then 7 findings while the real number
#    was 22). Whatever it holds, it is not the fleet's answer: UNKNOWN, never
#    WARN-with-the-wrong-count and never PASS.
mk h8
printf '# fleet dependency advisories\n# boxes observed: 1/1\nkiai apt - FINDING\n' \
  > "$TMP/h8/.meshforge-dep-advisories"
: > "$TMP/h8/.meshforge-dep-ranges"
printf '# header\nkiai apt: 1 security update(s) pending\n' > "$TMP/h8/.meshforge-dep-ADVISORY"
check "installed record lacks the fleet stamp" "$TMP/h8" UNKNOWN

# 10. Finding 14c (2026-09-09): a finding file with NO findings (header +
#     blank line) yielded n=$'0\n0' from `grep -c … || echo 0` and flipped
#     the leg to WARN on zero findings. Zero findings is OK.
mk h10
fleet_status "$TMP/h10"
: > "$TMP/h10/.meshforge-dep-ranges"
printf '# header only\n\n' > "$TMP/h10/.meshforge-dep-ADVISORY"
check "finding file with zero findings" "$TMP/h10" PASS

# 11. Finding 14b: a status file stamped in the FUTURE is a stepped clock,
#     never a fresh run.
mk h11
fleet_status "$TMP/h11"
: > "$TMP/h11/.meshforge-dep-ranges"
touch -d '+3 days' "$TMP/h11/.meshforge-dep-ranges"
check "status stamped in the future" "$TMP/h11" UNKNOWN

# 12. Finding 14b: `stat … || echo 0` read an unreadable mtime as epoch-0 →
#     "STALE(49xxxxh)". Unreadable is its own state.
mk h12; mkdir -p "$TMP/nostat"
printf '#!/bin/sh\nexit 1\n' > "$TMP/nostat/stat"; chmod +x "$TMP/nostat/stat"
fleet_status "$TMP/h12"
: > "$TMP/h12/.meshforge-dep-ranges"
PATH="$TMP/nostat:$PATH" check "status mtime unreadable" "$TMP/h12" UNKNOWN

# 9. A stamped fleet run that never LOOKED (gh unauthenticated, host list
#    unreadable) writes UNKNOWN as its body. The finding file beside it is the
#    previous run's leftover, not this run's verdict.
mk h9
printf '# scope: fleet\nUNKNOWN: gh CLI missing or unauthenticated — the fleet was NOT checked\n' \
  > "$TMP/h9/.meshforge-dep-advisories"
: > "$TMP/h9/.meshforge-dep-ranges"
printf '# header\nmoc cryptography 46.0.5: GHSA-x(high)\n' > "$TMP/h9/.meshforge-dep-ADVISORY"
check "fleet run that never looked" "$TMP/h9" UNKNOWN

if [ "$fails" -ne 0 ]; then
  echo "test_honest_status_dep_leg: $fails failure(s)"
  exit 1
fi
echo "ALL PASS — test_honest_status_dep_leg: all 9 states behaved as specified"
