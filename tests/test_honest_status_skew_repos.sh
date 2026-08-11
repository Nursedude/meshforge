#!/usr/bin/env bash
# honest_status.sh — the running-code skew leg must judge EVERY unit against
# the repo it actually runs from (2026-08-11).
#
# WHY: the leg dispatches on the unit-NAME prefix. It knew two repos, so
# `meshforge-maps.service` — which runs /opt/meshforge-maps/venv/bin/python -m
# src.main, its own repo that fleet_pull.sh does not even touch — matched the
# catch-all and was judged against /opt/meshforge's HEAD. MeshForge commits
# constantly, so those units read "behind" forever regardless of their own
# repo. Measured 2026-08-11: it reported maps(4d) for three units whose repo
# had had ZERO commits since they started (they began FOUR MINUTES after their
# own HEAD), and maps(17d) for a moc4 unit that was current with its disk.
# Live-drilled: reverting only the dispatch flips the same three units from
# 0d to 4d, with nothing else changed.
#
# This had already been fixed ONCE for meshanchor-* on 2026-08-09 and the
# third repo was missed — so the pin below is written for the CLASS, not this
# instance: every repo HEAD the leg collects must have a dispatch arm that
# uses it. Adding a fourth repo without wiring its arm fails here.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
# Overridable so the pin itself can be DRILLED against a planted-violation
# copy (feedback_a_guard_that_never_failed_is_not_evidence).
SCRIPT="${HS_SKEW_SCRIPT:-$HERE/../scripts/honest_status.sh}"
fails=0

note() { printf '  %-58s %s\n' "$1" "$2"; }
fail() { note "$1" "FAIL"; fails=$((fails+1)); }
pass() { note "$1" "ok"; }

[ -r "$SCRIPT" ] || { echo "honest_status.sh unreadable"; exit 1; }
src="$(cat "$SCRIPT")"

# 1. The third repo's HEAD is collected at all.
if printf '%s' "$src" | grep -q 'git -C /opt/meshforge-maps show -s --format=%ct HEAD'; then
  pass "collects /opt/meshforge-maps HEAD"
else
  fail "collects /opt/meshforge-maps HEAD"
fi

# 2. meshforge-maps units dispatch to it — and the arm must precede the
#    catch-all, or the catch-all eats them (the original defect).
if printf '%s' "$src" | grep -qE 'meshforge-maps\.service\|meshforge-maps@\*\) *H=\\\$HTMM'; then
  pass "meshforge-maps dispatches to its own repo"
else
  fail "meshforge-maps dispatches to its own repo"
fi

# 3. The singular meshforge-map (a DIFFERENT service, from the MeshForge repo)
#    must NOT be captured by the maps arm.
if printf '%s' "$src" | grep -qE 'meshforge-maps\.service\|meshforge-maps@\*'; then
  pass "the arm is anchored so meshforge-map (singular) is unaffected"
else
  fail "the arm is anchored so meshforge-map (singular) is unaffected"
fi

# 4. Display carries a prefix identifying WHICH repo judged the unit; a
#    maps unit shown as mf: would tell the operator the wrong repo.
if printf '%s' "$src" | grep -q 'mm:maps'; then
  pass "maps units display an mm: repo prefix"
else
  fail "maps units display an mm: repo prefix"
fi

# 5. THE CLASS PIN. Every repo HEAD the leg collects (HT<X..>=$(git -C ...))
#    must be consumed by a CASE-ARM dispatch (`) H=$HT<X..>`). A new repo
#    added without an arm is exactly how this bug happened twice.
#    Tightened 2026-08-11 (frontier review): the old regexes pinned the var
#    name to exactly two capitals, so a repo collected as e.g. HTMAPS escaped
#    "defined" entirely while `H=\$HTMAPS` substring-matched into "used" as a
#    false HTMA — the pin passed around a wholly unpinned repo. And "used"
#    accepted ANY text containing `H=\$HTxx` (an alias assignment like
#    `NEWH=\$HTMM`, or a comment), so consumption did not prove a dispatch
#    arm. Now: var names are [A-Z]+ (maximal munch kills the substring hole),
#    and "used" requires the `) H=\$HT...` case-arm shape.
defined=$(printf '%s' "$src" | grep -oE 'HT[A-Z]+=\\\$\(git -C' | grep -oE 'HT[A-Z]+' | sort -u)
used=$(printf '%s' "$src" | grep -oE '\) *H=\\\$HT[A-Z]+' | grep -oE 'HT[A-Z]+' | sort -u)
if [ -n "$defined" ] && [ "$defined" = "$used" ]; then
  pass "every collected repo HEAD has a dispatch arm ($(printf '%s' "$defined" | tr '\n' ' '))"
else
  fail "every collected repo HEAD has a dispatch arm"
  printf '    defined: %s\n' "$(printf '%s' "$defined" | tr '\n' ' ')"
  printf '    used   : %s\n' "$(printf '%s' "$used" | tr '\n' ' ')"
fi

# The wrapper (test_honest_status_shell.py) requires this exact line as well as
# exit 0 — a harness that exits 0 without reaching its end asserts nothing, and
# the line is what proves it got here.
if [ "$fails" = 0 ]; then echo "ALL PASS"; exit 0; else echo "FAILED: $fails assertion(s)"; exit 1; fi
