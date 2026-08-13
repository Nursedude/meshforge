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

# 6. THE SAME CLASS PIN, for the code-heads added 2026-08-12. The leg now
#    collects a SECOND timestamp per repo — the newest commit touching
#    src/requirements/templates — so behind-on-CODE can be told from
#    behind-on-prose. That doubles the number of per-repo values a fourth
#    repo must wire, and an HC<X> collected without a dispatch arm would
#    silently reuse another repo's code-head: the 08-09/08-11 bug exactly,
#    one layer down. Same maximal-munch [A-Z]+ and case-arm shape as #5.
hcdef=$(printf '%s' "$src" | grep -oE 'HC[A-Z]+=\\\$\(hs_codehead' | grep -oE 'HC[A-Z]+' | sort -u)
hcuse=$(printf '%s' "$src" | grep -oE '; *HC=\\\$HC[A-Z]+' | grep -oE 'HC[A-Z]+$' | sort -u)
if [ -n "$hcdef" ] && [ "$hcdef" = "$hcuse" ]; then
  pass "every collected repo CODE-head has a dispatch arm ($(printf '%s' "$hcdef" | tr '\n' ' '))"
else
  fail "every collected repo CODE-head has a dispatch arm"
  printf '    defined: %s\n' "$(printf '%s' "$hcdef" | tr '\n' ' ')"
  printf '    used   : %s\n' "$(printf '%s' "$hcuse" | tr '\n' ' ')"
fi

# 7. Every repo with a HEAD must also have a code-head, and vice versa. #5 and
#    #6 each check their own family is internally consistent; a repo present in
#    one family and missing from the other passes BOTH and is still half-wired.
htrepos=$(printf '%s' "$defined" | sed 's/^HT//' | sort -u)
hcrepos=$(printf '%s' "$hcdef" | sed 's/^HC//' | sort -u)
if [ "$htrepos" = "$hcrepos" ]; then
  pass "HEAD and CODE-head cover the same repo set ($(printf '%s' "$htrepos" | tr '\n' ' '))"
else
  fail "HEAD and CODE-head cover the same repo set"
  printf '    HEAD-only: %s\n' "$(comm -23 <(printf '%s\n' "$htrepos") <(printf '%s\n' "$hcrepos") | tr '\n' ' ')"
  printf '    CODE-only: %s\n' "$(comm -13 <(printf '%s\n' "$htrepos") <(printf '%s\n' "$hcrepos") | tr '\n' ' ')"
fi

# 8. THE FAIL-SAFE. An unresolvable code-head must fall back to that repo's
#    HEAD — never to "no code changes", which would silently demote every unit
#    into the benign prose bucket and turn a real stale-code fleet green. Two
#    independent guards are required: the per-repo `|| HC<X>=$HT<X>` at collect
#    time, and the non-numeric catch at dispatch time.
nfall=$(printf '%s' "$src" | grep -cE '\[ -n \\"\\\$HC[A-Z]+\\" \] \|\| HC[A-Z]+=\\\$HT[A-Z]+')
if [ "$nfall" -ge 1 ] && printf '%s' "$src" | grep -qE "case \\\\\"\\\\\\\$HC\\\\\" in ''\|\*\[!0-9\]\*\) HC=\\\\\\\$H;;"; then
  pass "unresolvable code-head falls back to HEAD, not to 'no code changes'"
else
  fail "unresolvable code-head falls back to HEAD, not to 'no code changes'"
  printf '    per-repo fallbacks found: %s\n' "$nfall"
fi

# 9. It LABELS, it does not FILTER. The prose bucket must be COUNTED and
#    PRINTED, not dropped: mini-dudeai's offline_oracle indexes .claude/**.md
#    and docs/*.md as its corpus, so a "docs-only" commit can genuinely make a
#    resident mini stale. Silently discarding those units would be a real
#    blindness sold as noise reduction — and it must ride the CLEAN line too,
#    or "0 behind on code" prints alone while six units are behind on corpus.
if printf '%s' "$src" | grep -q 'skew_prose=$((skew_prose+np))' \
   && printf '%s' "$src" | grep -q 'behind on NON-code only'; then
  pass "prose-bucket units are counted and disclosed, never filtered out"
else
  fail "prose-bucket units are counted and disclosed, never filtered out"
fi
nclean=$(printf '%s' "$src" | grep -c 'started at/after its own repo.s newest CODE commit${prose_note}')
if [ "$nclean" -ge 1 ]; then
  pass "the CLEAN skew line still carries the prose bucket"
else
  fail "the CLEAN skew line still carries the prose bucket"
fi

# 10. The CODE pathspec exists as TWO hardcodes — hs_codehead inside the
#     remote heredoc here, and the verbatim copy in
#     test_honest_status_skew_codehead.sh — and two consumers of one constant
#     must not drift (honest_failure_modes #5; 2026-08-12 review). Extract
#     the pathspec (between `--` and the redirect) from both and compare, so
#     widening/narrowing the real one while the behavioural test keeps
#     validating the stale copy fails HERE instead of staying green.
CODEHEAD_TEST="$HERE/test_honest_status_skew_codehead.sh"
spec_real=$(printf '%s' "$src" | grep -o 'log -1 --format=%ct -- [^2]*2>/dev/null' | head -1 | sed 's/.*-- //; s/ *2>\/dev\/null//')
spec_copy=$(grep -o 'log -1 --format=%ct -- [^2]*2>/dev/null' "$CODEHEAD_TEST" 2>/dev/null | head -1 | sed 's/.*-- //; s/ *2>\/dev\/null//')
if [ -n "$spec_real" ] && [ "$spec_real" = "$spec_copy" ]; then
  pass "hs_codehead pathspec matches its copy in the behavioural test ($spec_real)"
else
  fail "hs_codehead pathspec matches its copy in the behavioural test"
  printf '    real: %s\n    copy: %s\n' "${spec_real:-<none>}" "${spec_copy:-<none>}"
fi

# The wrapper (test_honest_status_shell.py) requires this exact line as well as
# exit 0 — a harness that exits 0 without reaching its end asserts nothing, and
# the line is what proves it got here.
if [ "$fails" = 0 ]; then echo "ALL PASS"; exit 0; else echo "FAILED: $fails assertion(s)"; exit 1; fi
