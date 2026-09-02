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
rec() { # $1=id $2=argv  -> a systemctl show record
  printf 'Id=%s\nExecStart={ path=/x ; argv[]=%s ; }\nEnvironment=\nWorkingDirectory=\nActiveEnterTimestamp=@1000' "$1" "$2"
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
out=$(run "$(rec nomadnet-silence-watch.service '/usr/bin/python3 /opt/meshforge/scripts/nomadnet_silence_watch.py')")
expect "un-prefixed unit running repo code IS judged" "B /opt/meshforge nomadnet-silence-watch.service 0" "$out"
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
