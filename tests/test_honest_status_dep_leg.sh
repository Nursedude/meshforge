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
fails=0; checks=0

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
  local label="$1" fhome="$2" want="$3" out got; checks=$((checks+1))
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

# 13. Operator decision 2026-11-04 (dep-advisory review r5 #1): a run that
#     LOOKED but could not see PART of the fleet — the manager could not read
#     the security archive, a box was unreachable, one package's advisory
#     query timed out — writes its blind spots to the header's UNKNOWN list.
#     The leg read only the body's first line + the finding file, so a
#     partial view read as a clean bill. Partial blindness is UNKNOWN.
mk h13
printf '# fleet dependency advisories\n# boxes observed: 10/10  UNKNOWN: moc4/jinja2, manager:security-archive-unreachable\n# scope: fleet\nmoc apt - clean\n' \
  > "$TMP/h13/.meshforge-dep-advisories"
: > "$TMP/h13/.meshforge-dep-ranges"
check "partial view (header UNKNOWN list)" "$TMP/h13" UNKNOWN

# 14. ...and partial blindness outranks a finding beside it (same rule as 6).
mk h14
cp "$TMP/h13/.meshforge-dep-advisories" "$TMP/h14/"
: > "$TMP/h14/.meshforge-dep-ranges"
printf '# header\nkiai apt: 3 security update(s) pending\n' > "$TMP/h14/.meshforge-dep-ADVISORY"
check "partial view outranks a finding" "$TMP/h14" UNKNOWN

# 15. A complete view that SAYS so still passes — the new rule must key on
#     the UNKNOWN list, not on the presence of a `boxes observed` line.
mk h15
printf '# fleet dependency advisories\n# boxes observed: 10/10\n# scope: fleet\nmoc apt - clean\n' \
  > "$TMP/h15/.meshforge-dep-advisories"
: > "$TMP/h15/.meshforge-dep-ranges"
check "complete view with observed header" "$TMP/h15" PASS

# check_note: the verdict AND a substring (or, with a leading '!', its
# absence) in the leg's note — #7 is about what the note SAYS, not the verdict.
check_note() { # label, fake-home, expected-verdict, need, [forbid]
  local label="$1" fhome="$2" want="$3" need="$4" forbid="${5:-}" out got; checks=$((checks+1))
  out=$(HOME="$fhome" bash -c "source '$TMP/harness.sh'; source '$TMP/leg.sh'" 2>&1)
  got=$(printf '%s' "$out" | awk '{print $1}')
  if [ "$got" = "$want" ] && printf '%s' "$out" | grep -qF -- "$need" \
     && { [ -z "$forbid" ] || ! printf '%s' "$out" | grep -qF -- "$forbid"; }; then
    printf '  ok   %-38s %s\n' "$label" "$want"
  else
    printf '  FAIL %-38s expected=%s +"%s" -"%s" got=%s\n      %s\n' \
      "$label" "$want" "$need" "$forbid" "$got" "$out"
    fails=$((fails+1))
  fi
}

# 16-18. Dep-advisory review r6 #7: PARTIAL-VIEW `continue`d before counting,
#     so the findings the run DID see vanished from the note. The count must
#     come from THIS run — a partial run with no findings deliberately KEEPS
#     the previous finding file, so counting that file would report last
#     run's findings as current. The writer states `# findings this run: N`.
partial_hdr='# fleet dependency advisories\n# boxes observed: 10/11  UNKNOWN: lehua\n# scope: fleet\n'
mk h16
printf "${partial_hdr}"'# findings this run: 3\nkiai apt - FINDING\n' > "$TMP/h16/.meshforge-dep-advisories"
: > "$TMP/h16/.meshforge-dep-ranges"
printf '# header\na\nb\nc\n' > "$TMP/h16/.meshforge-dep-ADVISORY"
check_note "partial view keeps its finding count" "$TMP/h16" UNKNOWN "3 finding(s) among the boxes seen"

mk h17
printf "${partial_hdr}"'# findings this run: 0\nmoc apt - clean\n' > "$TMP/h17/.meshforge-dep-advisories"
: > "$TMP/h17/.meshforge-dep-ranges"
printf '# header\nold1\nold2\n' > "$TMP/h17/.meshforge-dep-ADVISORY"
check_note "partial: count from THIS run, not leftover" "$TMP/h17" UNKNOWN "0 finding(s) among the boxes seen" "2 finding"

mk h18
printf "${partial_hdr}"'moc apt - clean\n' > "$TMP/h18/.meshforge-dep-advisories"
: > "$TMP/h18/.meshforge-dep-ranges"
printf '# header\nold1\nold2\n' > "$TMP/h18/.meshforge-dep-ADVISORY"
check_note "partial, older writer: count said unknown" "$TMP/h18" UNKNOWN "findings uncounted" "2 finding"

# 19-20. r6 #8: the blind list was cut at 80 chars with NO marker, and the
#     manager entry — appended LAST, and a different class (the sweep's own
#     blindness, not a box's) — is the likeliest cut. A cut must say so with
#     the entry count, and the manager entry is always named.
long_list="moc4/jinja2@/usr/local/lib/python3.11/dist-packages, moc1/cryptography@/root/.local/x, kiai/urllib3, manager:security-archive-unreachable"
mk h19
printf "# fleet dependency advisories\n# boxes observed: 11/11  UNKNOWN: ${long_list}\n# scope: fleet\n# findings this run: 0\n" \
  > "$TMP/h19/.meshforge-dep-advisories"
: > "$TMP/h19/.meshforge-dep-ranges"
check_note "long blind list names the manager entry" "$TMP/h19" UNKNOWN "manager:security-archive-unreachable"
check_note "long blind list says it was cut" "$TMP/h19" UNKNOWN "(4 total"

mk h20
printf "# fleet dependency advisories\n# boxes observed: 11/11  UNKNOWN: lehua\n# scope: fleet\n# findings this run: 0\n" \
  > "$TMP/h20/.meshforge-dep-advisories"
: > "$TMP/h20/.meshforge-dep-ranges"
check_note "short blind list: no cut marker" "$TMP/h20" UNKNOWN "unknown: lehua;" "total"

# 22-28. r7 #8 (operator 2026-11-05): an unjudgeable update-stamp KEEPS the run
#     UNKNOWN; when the running-code skew leg (run earlier in this script)
#     MEASURED a stamp-blind box's clock off vs the manager, the note says so
#     ONCE, listing host(delta). Co-occurrence, never a cause (r7b review: the
#     token also means "old reporter"; the delta may be unreadable or AHEAD;
#     the clock is read now, the stamp judged at sweep time). Exact host
#     match (moc != moc1); built from the FULL list, so the 80-char cut
#     cannot drop it.
stamp_hdr() { printf "# fleet dependency advisories\n# boxes observed: 11/11  UNKNOWN: %s\n# scope: fleet\n# findings this run: 0\n" "$1"; }
CLOCK_PTR="clock is off vs this manager NOW"
mk h22; stamp_hdr "moc/apt-update-stamp" > "$TMP/h22/.meshforge-dep-advisories"; : > "$TMP/h22/.meshforge-dep-ranges"
export skew_clock_desc=" kiai(clock unreadable) moc(-700s)"
check_note "stamp unknown + that box's clock skewed" "$TMP/h22" UNKNOWN "${CLOCK_PTR}: moc(-700s) (" "kiai("
check_note "pointer claims no cause" "$TMP/h22" UNKNOWN "not when the stamp was judged" "likely reason"
export skew_clock_desc=" kiai(-700s)"
check_note "stamp unknown, OTHER box skewed: silent" "$TMP/h22" UNKNOWN "unknown: moc/apt-update-stamp" "$CLOCK_PTR"
mk h23; stamp_hdr "moc1/apt-update-stamp" > "$TMP/h23/.meshforge-dep-advisories"; : > "$TMP/h23/.meshforge-dep-ranges"
export skew_clock_desc=" moc(-700s)"
check_note "host match is exact (moc != moc1)" "$TMP/h23" UNKNOWN "unknown: moc1/apt-update-stamp" "$CLOCK_PTR"
mk h24; stamp_hdr "${long_list}, kiai/apt-update-stamp" > "$TMP/h24/.meshforge-dep-advisories"; : > "$TMP/h24/.meshforge-dep-ranges"
export skew_clock_desc=" kiai(-90000s)"
check_note "pointer survives the 80-char cut" "$TMP/h24" UNKNOWN "${CLOCK_PTR}: kiai(-90000s) ("
mk h25; stamp_hdr "kiai/urllib3" > "$TMP/h25/.meshforge-dep-advisories"; : > "$TMP/h25/.meshforge-dep-ranges"
check_note "skewed box, non-stamp blind spot: silent" "$TMP/h25" UNKNOWN "unknown: kiai/urllib3" "$CLOCK_PTR"
# Same-host unreadable / ahead: reported as measured, never as "behind".
mk h26; stamp_hdr "kiai/apt-update-stamp, moc/apt-update-stamp" > "$TMP/h26/.meshforge-dep-advisories"; : > "$TMP/h26/.meshforge-dep-ranges"
export skew_clock_desc=" kiai(clock unreadable) moc(900s)"
check_note "same-host unreadable+ahead, said as measured" "$TMP/h26" UNKNOWN "${CLOCK_PTR}: kiai(clock unreadable) moc(900s) (" "behind"
out26=$(HOME="$TMP/h26" bash -c "source '$TMP/harness.sh'; source '$TMP/leg.sh'" 2>&1)
n26=$(printf '%s' "$out26" | grep -o "$CLOCK_PTR" | wc -l)
checks=$((checks+1))
if [ "$n26" = 1 ]; then printf '  ok   %-38s %s\n' "two hosts, ONE pointer" "1"
else printf '  FAIL %-38s want 1 pointer, got %s\n      %s\n' "two hosts, ONE pointer" "$n26" "$out26"; fails=$((fails+1)); fi
# A glob character in a blind token is not expanded against the CWD: run the
# leg from a dir holding the file the glob WOULD match, so the old unquoted
# word-split turns `moc/apt-update-stam?` into a stamp token and fires.
mk h27; stamp_hdr "moc/apt-update-stam?" > "$TMP/h27/.meshforge-dep-advisories"; : > "$TMP/h27/.meshforge-dep-ranges"
mkdir -p "$TMP/g27/moc"; : > "$TMP/g27/moc/apt-update-stamp"
export skew_clock_desc=" moc(-700s)"
out27=$(cd "$TMP/g27" && HOME="$TMP/h27" bash -c "source '$TMP/harness.sh'; source '$TMP/leg.sh'" 2>&1)
checks=$((checks+1))
if ! printf "%s" "$out27" | grep -qF -- "-700s"; then printf '  ok   %-38s %s\n' "glob chars in a token stay literal" "UNKNOWN"
else printf '  FAIL %-38s token was glob-expanded\n      %s\n' "glob chars in a token stay literal" "$out27"; fails=$((fails+1)); fi
unset skew_clock_desc

if [ "$fails" -ne 0 ]; then
  echo "test_honest_status_dep_leg: $fails failure(s)"
  exit 1
fi
echo "ALL PASS — test_honest_status_dep_leg: all ${checks} states behaved as specified"
