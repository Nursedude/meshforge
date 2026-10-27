#!/usr/bin/env bash
# hs_skew_attr.awk — the running-code skew leg must attribute a unit to the
# repo whose code its process actually LOADS, never to the repo its NAME
# implies (2026-09-02).
#
# WHY: the leg decided both membership and repo from the unit-name prefix
# `^(meshforge|meshanchor)-`. A name says who MANAGES a unit; staleness is a
# property of the code the process LOADS. Audited across 50 active units on 10
# boxes, the convention broke BOTH ways:
#   * false positive — `meshforge-lxmd` runs the packaged /usr/local/bin/lxmd
#     (the pinned LXMF fork) and loads no repo code, yet was reported behind
#     under a header promising "they load it at next restart". It doesn't.
#   * false negative — `nomadnet-silence-watch.service` (13d behind, runs
#     /opt/meshforge/scripts/...) and `meshanchor.service` (17d, the
#     orchestrator) were INVISIBLE. The second is the worse one: its sibling
#     `meshanchor-daemon.service` IS matched, so the line looked complete.
#     Excluded for nothing but a missing hyphen after "meshanchor".
#
# The name dispatch was repaired twice for this same class (meshanchor-*
# 2026-08-09, meshforge-maps 2026-08-11) without the MECHANISM being
# questioned. These are BEHAVIOURAL pins — they run the program against
# synthetic records rather than grepping its source, because the previous
# guards were source-greps and a source-grep cannot tell a correct dispatch
# from a plausible-looking one.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
# Overridable so the pin can be DRILLED against a planted-violation copy
# (feedback_a_guard_that_never_failed_is_not_evidence).
AWKF="${HS_SKEW_ATTR:-$HERE/../scripts/hs_skew_attr.awk}"
fails=0
note() { printf '  %-58s %s\n' "$1" "$2"; }
fail() { note "$1" "FAIL"; fails=$((fails+1)); }
pass() { note "$1" "ok"; }
[ -r "$AWKF" ] || { echo "hs_skew_attr.awk unreadable at $AWKF"; exit 1; }

TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
# HEAD=2000 / CODE=2000 for every repo; a unit started at 1000 is behind.
run() { # $1 = record body
  printf '%s\n\n' "$1" | awk -v HTMF=2000 -v HCMF=2000 -v HTMA=3000 -v HCMA=3000 \
                             -v HTMM=4000 -v HCMM=4000 -f "$AWKF"
}
rec() { # $1=id $2=argv [$3=WorkingDirectory]  -> a systemctl show record
  # $3 added 2026-09-15: a `-m module` ExecStart carries NO repo path, so for
  # such a unit WorkingDirectory is the ONLY membership evidence the attributor
  # has. The fixture could not express it, so it modelled a unit that cannot
  # exist and would have blessed a real blind spot.
  printf 'Id=%s\nExecStart={ path=/x ; argv[]=%s ; }\nEnvironment=\nWorkingDirectory=%s\nActiveEnterTimestamp=@1000' "$1" "$2" "${3:-}"
}
expect() { # $1=label $2=expected $3=actual
  if [ "$2" = "$3" ]; then pass "$1"; else fail "$1"; printf '    want: [%s]\n    got : [%s]\n' "$2" "$3"; fi
}

# 1. THE FALSE POSITIVE. A unit named meshforge-* whose process runs a packaged
#    binary loads NO repo code and must not be judged at all — reporting it
#    "behind" tells the operator to restart something a restart cannot fix.
out=$(run "$(rec meshforge-lxmd.service '/usr/local/bin/lxmd -p --config /home/x/.config/meshforge/lxmd')")
expect "packaged binary under an mf- name is NOT judged" "" "$out"

# 2. THE FALSE NEGATIVES. Membership must not require the name prefix.
#    The real shape since 2026-09-15: no meshforge- name prefix AND a `-m`
#    ExecStart with no repo path in it — so membership rests entirely on
#    WorkingDirectory. Moving this daemon into src/ closed a deploy gap and
#    would have opened a REPORTING blind spot without that.
out=$(run "$(rec nomadnet-silence-watch.service '/usr/bin/python3 -m monitoring.nomadnet_silence_watch' '/opt/meshforge/src')")
expect "un-prefixed -m unit is judged via WorkingDirectory" "B /opt/meshforge nomadnet-silence-watch.service 0" "$out"
#    ...and specifically the missing-hyphen shape that hid the orchestrator.
out=$(run "$(rec meshanchor.service '/opt/meshanchor/venv/bin/python -m core.orchestrator')")
expect "meshanchor.service (no hyphen) IS judged, against MA" "B /opt/meshanchor meshanchor.service 0" "$out"

# 3. PREFIX ORDERING. /opt/meshforge is a proper prefix of /opt/meshforge-maps,
#    so the maps test must come FIRST or every maps unit is judged against
#    MeshForge's head — the exact 2026-08-11 defect, in its new form.
out=$(run "$(rec meshforge-maps.service '/opt/meshforge-maps/venv/bin/python -m src.main')")
expect "maps is not eaten by the /opt/meshforge prefix" "B /opt/meshforge-maps meshforge-maps.service 0" "$out"
#    ...and the SINGULAR meshforge-map is a different service from the MF repo.
out=$(run "$(rec meshforge-map.service '/opt/meshforge/venv/bin/python -m utils.map_data_service')")
expect "singular meshforge-map still judged against MeshForge" "B /opt/meshforge meshforge-map.service 0" "$out"

# 4. INDIRECT LOAD. A script outside every repo that INJECTS a repo path onto
#    sys.path does load repo code (meshforge-digest is exactly this shape, and
#    nothing in its unit file says so).
printf 'import sys\nsys.path.insert(0, "/opt/meshforge/src")\n' > "$TMP/inject.py"
out=$(run "$(rec digest.service "/usr/bin/python3 $TMP/inject.py")")
expect "a sys.path injection counts as loading repo code" "B /opt/meshforge digest.service 0" "$out"

# 5. A MENTION IS NOT A LOAD — the false positive this fix itself produced on
#    first draft, caught by live drill. nomadnet_wrapper.py names
#    /opt/meshforge twice in user-facing HELP TEXT while running nomadnet from
#    a pipx venv; a scan matching any occurrence invented a 5d-stale unit.
#    Keying on a proxy instead of the thing is the class this fix exists to end.
printf 'import os\n# run: sudo /opt/meshforge/scripts/rns_alignment.py normalize\nHELP = "see /opt/meshforge/README"\n' > "$TMP/mention.py"
out=$(run "$(rec nomadnet.service "/usr/bin/python3 $TMP/mention.py")")
expect "a repo path in help text is NOT a load" "" "$out"

# 6. FAIL-SAFE. An unresolvable code-head must collapse to that repo's HEAD --
#    never to "no code changes", which would silently move every unit into the
#    benign bucket and turn a stale fleet green (honest_failure_modes #1).
out=$(printf '%s\n\n' "$(rec z.service '/opt/meshforge/x')" \
      | awk -v HTMF=2000 -v HCMF="" -v HTMA=3000 -v HCMA=3000 -v HTMM=4000 -v HCMM=4000 -f "$AWKF")
expect "empty code-head falls back to HEAD, stays behind" "B /opt/meshforge z.service 0" "$out"
out=$(printf '%s\n\n' "$(rec z.service '/opt/meshforge/x')" \
      | awk -v HTMF=2000 -v HCMF=nonsense -v HTMA=3000 -v HCMA=3000 -v HTMM=4000 -v HCMM=4000 -f "$AWKF")
expect "non-numeric code-head falls back to HEAD too" "B /opt/meshforge z.service 0" "$out"

# 7. UNKNOWN IS NOT CURRENT. A unit with no start time cannot be judged; it must
#    read U, never be silently dropped into the healthy set.
out=$(run "$(printf 'Id=y.service\nExecStart={ argv[]=/opt/meshforge/x ; }\nEnvironment=\nWorkingDirectory=\nActiveEnterTimestamp=')")
expect "missing start time reads U, never current" "U /opt/meshforge y.service" "$out"
out=$(printf '%s\n\n' "$(rec w.service '/opt/meshforge/x')" \
      | awk -v HTMF=SKIP -v HCMF=SKIP -v HTMA=3000 -v HCMA=3000 -v HTMM=4000 -v HCMM=4000 -f "$AWKF")
expect "unreachable repo HEAD reads U, never current" "U /opt/meshforge w.service" "$out"

# 8. A unit newer than both heads is current and prints nothing.
out=$(printf 'Id=n.service\nExecStart={ argv[]=/opt/meshforge/x ; }\nEnvironment=\nWorkingDirectory=\nActiveEnterTimestamp=@9999\n\n' \
      | awk -v HTMF=2000 -v HCMF=2000 -v HTMA=3000 -v HCMA=3000 -v HTMM=4000 -v HCMM=4000 -f "$AWKF")
expect "a unit started after both heads is silent" "" "$out"

# 9. THE PROSE BUCKET survives: behind HEAD but at/after CODE-head is P, not B.
out=$(printf 'Id=p.service\nExecStart={ argv[]=/opt/meshforge/x ; }\nEnvironment=\nWorkingDirectory=\nActiveEnterTimestamp=@1500\n\n' \
      | awk -v HTMF=2000 -v HCMF=1000 -v HTMA=3000 -v HCMA=3000 -v HTMM=4000 -v HCMM=4000 -f "$AWKF")
expect "behind HEAD but current on CODE reads P (prose)" "P /opt/meshforge p.service 0" "$out"

# 10. ATTRIBUTION IS READ FROM Environment AND WorkingDirectory TOO, not just
#     argv — meshforge-watchdog runs `python3 -m utils.watchdog_runner` with the
#     repo only in WorkingDirectory, and judging it against no repo would drop a
#     core observability daemon out of coverage entirely.
out=$(printf 'Id=meshforge-watchdog.service\nExecStart={ argv[]=/usr/bin/python3 -m utils.watchdog_runner ; }\nEnvironment=\nWorkingDirectory=/opt/meshforge/src\nActiveEnterTimestamp=@1000\n\n' \
      | awk -v HTMF=2000 -v HCMF=2000 -v HTMA=3000 -v HCMA=3000 -v HTMM=4000 -v HCMM=4000 -f "$AWKF")
expect "WorkingDirectory alone resolves the repo" "B /opt/meshforge meshforge-watchdog.service 0" "$out"

# 11. START TIME FROM THE MONOTONIC CLOCK (2026-10-27). `ActiveEnterTimestamp`
#     is the wall clock AT START and systemd never rewrites it after NTP steps
#     the clock. kiai, RTC-less, booted 10-26 21:44 with its clock still
#     reading 09-27: echo + mini-dudeai were stamped 09-27 and honest_status
#     reported them "29d behind" while `ps` (btime-derived) showed them 5 h old
#     on current code. The BOXNOW guard cannot see this — the clock was correct
#     by the time we asked. Date the start the way ps does:
#     start = BOXNOW - (BOXUP - ActiveEnterTimestampMonotonic/1e6).
mrec() { # $1=id $2=realtime stamp $3=monotonic usec
  printf 'Id=%s\nExecStart={ argv[]=/opt/meshforge/x ; }\nEnvironment=\nWorkingDirectory=\nActiveEnterTimestamp=@%s\nActiveEnterTimestampMonotonic=%s' "$1" "$2" "$3"
}
mrun() { # $1=record $2=BOXNOW $3=BOXUP (box CLOCK_MONOTONIC now, seconds)
  printf '%s\n\n' "$1" | awk -v HTMF=2000 -v HCMF=2000 -v HTMA=3000 -v HCMA=3000 \
      -v HTMM=4000 -v HCMM=4000 -v BOXNOW="$2" -v BOXUP="$3" -v BOXSUSP="${4:-0}" -v MONOSLACK=60 -f "$AWKF"
}
#     The kiai shape: stale-EARLY stamp (1000) but the unit really started at
#     10000-(5000-4000)=9000, after the code head -> current, silent.
out=$(mrun "$(mrec kiai-echo.service 1000 4000000000)" 10000 5000.12)
expect "stale-early wall stamp, monotonic says current -> silent" "" "$out"
#     The FALSE-GREEN direction: clock AHEAD at boot stamps 9999, but the unit
#     really started at 3000-(2500-500)=1000, before the code head -> behind.
out=$(mrun "$(mrec ahead.service 9999 500000000)" 3000 2500)
expect "stale-late wall stamp, monotonic says behind -> B" "B /opt/meshforge ahead.service 0" "$out"
#     No monotonic dating possible: the realtime stamp is all we have, and it
#     is used — but a W witness says so, or a regression to the forgeable
#     clock would be byte-identical to the fixed path (review 2026-10-27).
#     (BOXNOW present, monotonic now unreadable = python3 failed on the box.
#     An EMPTY BOXNOW is `date` failing, which the manager already reports as
#     "clock unreadable" and reclassifies to U — no second witness needed.)
out=$(mrun "$(mrec old.service 1000 4000000000)" 10000 "")
expect "mono-now unreadable -> realtime stamp used, W witness" "W old.service
B /opt/meshforge old.service 0" "$out"
out=$(mrun "$(mrec zero.service 1000 0)" 10000 5000)
expect "monotonic 0 (unset) -> realtime stamp used, W witness" "W zero.service
B /opt/meshforge zero.service 0" "$out"
#     Legacy callers that pass no box clock at all (every older case above)
#     must not change: the W is emitted only when the caller asked for
#     monotonic dating by supplying BOXNOW — checked by the 0..10 cases.
#     Inconsistent: started far beyond the monotonic now. Cannot be dated ->
#     U, never silently current.
out=$(mrun "$(mrec bad.service 1000 9000000000)" 10000 5000)
expect "monotonic start far beyond mono-now -> U" "U /opt/meshforge bad.service" "$out"
#     SUSPEND: the box measured BOOTTIME-MONOTONIC beyond the slack, so a
#     monotonic age is short by the suspended time -> cannot be dated -> U.
out=$(mrun "$(mrec susp.service 1000 4000000000)" 10000 5000 172800)
expect "box suspended beyond slack -> U, never current" "U /opt/meshforge susp.service" "$out"
#     W is a claim about a DATED unit: one that is U anyway gets no W (review
#     2026-10-27 #2 — the two notes contradicted each other).
out=$(printf '%s\n\n' "$(printf 'Id=nots.service\nExecStart={ argv[]=/opt/meshforge/x ; }\nEnvironment=\nWorkingDirectory=\nActiveEnterTimestamp=')" \
      | awk -v HTMF=2000 -v HCMF=2000 -v HTMA=3000 -v HCMA=3000 -v HTMM=4000 -v HCMM=4000 -v BOXNOW=10000 -v BOXUP="" -v BOXSUSP=0 -v MONOSLACK=60 -f "$AWKF")
expect "unit with no stamp at all -> U only, no W" "U /opt/meshforge nots.service" "$out"
#     The RACE: the unit (re)started a moment AFTER the probe read mono-now.
#     Within MONOSLACK it is a real, current start — not incoherent.
out=$(mrun "$(mrec race.service 1000 5002000000)" 10000 5000)
expect "start just after mono-now (restart race) -> current" "" "$out"
#     The P bucket and a NONZERO day count on the monotonic path: started at
#     BOXNOW-(BOXUP-mono) = 200000-(190000-10000) = 20000 s epoch. With
#     CODE=1000 and HEAD=200000 that is current on code, 2 days behind prose.
out=$(printf '%s\n\n' "$(mrec p.service 1 10000000000)" | awk -v HTMF=200000 -v HCMF=1000 \
      -v HTMA=3000 -v HCMA=3000 -v HTMM=4000 -v HCMM=4000 -v BOXNOW=200000 -v BOXUP=190000 -v BOXSUSP=0 -v MONOSLACK=60 -f "$AWKF")
expect "monotonic path, P bucket, 2 days" "P /opt/meshforge p.service 2" "$out"
out=$(printf '%s\n\n' "$(mrec b.service 999999 10000000000)" | awk -v HTMF=300000 -v HCMF=300000 \
      -v HTMA=3000 -v HCMA=3000 -v HTMM=4000 -v HCMM=4000 -v BOXNOW=200000 -v BOXUP=190000 -v BOXSUSP=0 -v MONOSLACK=60 -f "$AWKF")
expect "monotonic path, B bucket, 3 days (wall stamp ignored)" "B /opt/meshforge b.service 3" "$out"

# 12. WIRING. The awk cases above inject the fields directly, so they would all
#     still pass if honest_status stopped ASKING for them — the live leg would
#     fall back to the forgeable stamp in silence. Pin the consumer's wiring.
HS="$HERE/../scripts/honest_status.sh"
expect "both scopes request ActiveEnterTimestampMonotonic" "2" \
  "$(grep -c -- '-p ActiveEnterTimestamp -p ActiveEnterTimestampMonotonic --timestamp=unix' "$HS")"
expect "HSV passes BOXNOW, BOXUP, BOXSUSP and MONOSLACK" "1" \
  "$(grep -cF -- '-v BOXNOW=\$HSNOW -v BOXUP=\$HSMONO -v BOXSUSP=\$HSSUSP -v MONOSLACK=$MF_MONO_SLACK_S' "$HS")"
expect "the remote reader is the lib's mf_mono_now, shipped (one definition)" "yes" \
  "$(grep -q 'declare -f mf_mono_now' "$HS" && echo yes || echo no)"
expect "the W witness is consumed by the manager" "yes" \
  "$(grep -q "grep -c '^W '" "$HS" && echo yes || echo no)"

# The wrapper (test_honest_status_shell.py) requires this exact line as well as

# N. THE BLIND-SPOT WITNESS (2026-09-02). A repo `pip install -e`'d into a venv
#    outside /opt loads repo code through a .pth in site-packages — invisible in
#    ExecStart/Environment/WorkingDirectory, and a `-m module` ExecStart has no
#    .py to scan. Dropping it silently makes "blind" and "correctly untracked"
#    the SAME output, which is the class this whole file exists to end.
out=$(run "$(rec someapp.service '/home/x/venvs/app/bin/python -m someapp.daemon')")
expect "venv interpreter, no repo resolved -> N disclosure" \
       "N someapp.service /home/x/venvs/app/bin/python" "$out"

out=$(run "$(rec someapp.service '/home/x/venvs/app/bin/python3.11 -m someapp.daemon')")
expect "versioned venv interpreter also discloses" \
       "N someapp.service /home/x/venvs/app/bin/python3.11" "$out"

# A SYSTEM interpreter that resolves to no repo is genuinely untracked, not
# blind — it has no venv that could hide an editable install. Must stay silent,
# or the disclosure becomes noise on every box.
out=$(run "$(rec sys-thing.service '/usr/bin/python3 /usr/share/foo/run.py')")
expect "system interpreter, no repo -> still silent" "" "$out"

out=$(run "$(rec sys-thing.service '/usr/local/bin/python3 /usr/share/foo/run.py')")
expect "/usr/local interpreter, no repo -> still silent" "" "$out"

# A packaged non-python binary must stay silent (guards test 1 from regressing
# into a disclosure).
out=$(run "$(rec meshforge-lxmd.service '/usr/local/bin/lxmd -p --config /home/x/c')")
expect "packaged binary emits no disclosure" "" "$out"

# A venv INSIDE a repo already resolves by path and must be JUDGED, never
# disclosed — this is the live MeshAnchor orchestrator's exact shape.
out=$(run "$(rec meshanchor.service '/opt/meshanchor/venv/bin/python -m core.orchestrator --start')")
expect "in-repo venv is judged, not disclosed" "B /opt/meshanchor meshanchor.service 0" "$out"

# E. EDITABLE INSTALL INSIDE A VENV (2026-09-02, second pass). A repo
#    `pip install -e`'d into a venv leaves a .pth / __editable__* entry in
#    site-packages and NOTHING in ExecStart/Environment/WorkingDirectory. These
#    use real fixture dirs because the logic reads the filesystem — a mock here
#    would stand in for the exact layer under test (the 2026-07-25 lesson).
mkvenv() { # $1=name $2=optional pth content
  mkdir -p "$TMP/$1/bin" "$TMP/$1/lib/python3.11/site-packages"
  : > "$TMP/$1/bin/python3.11"
  [ -n "${2:-}" ] && printf '%s\n' "$2" > "$TMP/$1/lib/python3.11/site-packages/__editable__.demo.pth"
  return 0
}

mkvenv ve_mf "/opt/meshforge/src"
out=$(run "$(rec editable-mf.service "$TMP/ve_mf/bin/python3.11 -m demo.daemon")")
expect "editable install into /opt/meshforge is JUDGED" \
       "B /opt/meshforge editable-mf.service 0" "$out"

mkvenv ve_ma "/opt/meshanchor/src"
out=$(run "$(rec editable-ma.service "$TMP/ve_ma/bin/python3.11 -m demo.daemon")")
expect "editable install resolves to the RIGHT repo" \
       "B /opt/meshanchor editable-ma.service 0" "$out"

# /opt/meshforge is a prefix of /opt/meshforge-maps — maps must win, same
# precedence as the direct resolver.
mkvenv ve_mm "/opt/meshforge-maps/src"
out=$(run "$(rec editable-mm.service "$TMP/ve_mm/bin/python3.11 -m demo.daemon")")
expect "maps is not eaten by the /opt/meshforge prefix (editable path)" \
       "B /opt/meshforge-maps editable-mm.service 0" "$out"

# THE FURNITURE FIX. A venv we CAN read, holding no repo, is ANSWERED: silent,
# not disclosed. This is the live nomadnet/pipx shape — before this pass it
# printed N on every run forever.
mkvenv ve_none "/home/x/.local/share/pipx/shared/lib/python3.11/site-packages"
out=$(run "$(rec pipx-app.service "$TMP/ve_none/bin/python3.11 /home/x/wrapper.py")")
expect "readable venv with no repo -> silent, NOT disclosed" "" "$out"

# ...and a venv we CANNOT inspect still discloses. That is the only thing N
# means now.
mkdir -p "$TMP/ve_dark/bin"; : > "$TMP/ve_dark/bin/python3.11"
out=$(run "$(rec dark-venv.service "$TMP/ve_dark/bin/python3.11 -m demo.daemon")")
expect "venv with NO site-packages -> N (uninspectable)" \
       "N dark-venv.service $TMP/ve_dark/bin/python3.11" "$out"

# A path we refuse to interpolate into a shell is uninspectable, not silent.
out=$(run "$(rec odd-venv.service "/tmp/we'ird/bin/python3.11 -m demo.daemon")")
expect "shell-unsafe interpreter path -> N, never silent" \
       "N odd-venv.service /tmp/we'ird/bin/python3.11" "$out"

# exit 0 — a harness that exits 0 without reaching its end asserts nothing.
if [ "$fails" = 0 ]; then echo "ALL PASS"; exit 0; else echo "FAILED: $fails assertion(s)"; exit 1; fi
