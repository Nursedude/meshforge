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

# The attribution mechanism moved OUT of this shell into scripts/hs_skew_attr.awk
# (2026-09-02): dispatch is now on the repo a unit's process actually LOADS, not
# on its name. Assertions 2-8 follow it there. The BEHAVIOURAL pins for that
# program live in test_honest_status_skew_attr.sh — these remain the structural
# CLASS pins: every repo the leg collects a head for must be wired through.
ATTR="$HERE/../scripts/hs_skew_attr.awk"
if [ -r "$ATTR" ]; then
  pass "the attribution program exists ($(basename "$ATTR"))"
  attr="$(cat "$ATTR")"
else
  fail "the attribution program exists"
  attr=""
fi

# 2. maps dispatches to its OWN repo's heads, not the catch-all.
if printf '%s' "$attr" | grep -qE 'rp == "/opt/meshforge-maps"\) *\{ *H = HTMM; *HC = HCMM *\}'; then
  pass "meshforge-maps dispatches to its own repo"
else
  fail "meshforge-maps dispatches to its own repo"
fi

# 3. /opt/meshforge is a proper PREFIX of /opt/meshforge-maps, so the maps test
#    must be evaluated FIRST or every maps unit is attributed to MeshForge —
#    the 2026-08-11 defect in its new form. Pin the ORDER, not just presence.
mapsln=$(printf '%s' "$attr" | grep -n 'index(hay, "/opt/meshforge-maps")' | head -1 | cut -d: -f1)
mfln=$(printf '%s' "$attr" | grep -n 'index(hay, "/opt/meshforge")) rp' | head -1 | cut -d: -f1)
if [ -n "$mapsln" ] && [ -n "$mfln" ] && [ "$mapsln" -lt "$mfln" ]; then
  pass "maps is tested before its /opt/meshforge prefix (line $mapsln < $mfln)"
else
  fail "maps is tested before its /opt/meshforge prefix"
  printf '    maps@%s meshforge@%s\n' "${mapsln:-<none>}" "${mfln:-<none>}"
fi

# 4. Display carries a prefix identifying WHICH repo judged the unit; a maps
#    unit shown as mf: would tell the operator the wrong repo. The tag is now
#    derived from the RESOLVED repo (field 2), not the unit name.
if printf '%s' "$src" | grep -q 'mm:' && printf '%s' "$src" | grep -q '\$2=="/opt/meshforge-maps"'; then
  pass "maps units display an mm: repo prefix, keyed on the resolved repo"
else
  fail "maps units display an mm: repo prefix, keyed on the resolved repo"
fi

# 5. THE CLASS PIN. Every repo HEAD the leg collects (HT<X..>=$(git -C ...)) must
#    be consumed by a dispatch arm — now in the awk (`H = HT<X>`). A new repo
#    added without an arm is exactly how this bug happened twice.
defined=$(printf '%s' "$src" | grep -oE 'HT[A-Z]+=\\\$\(git -C' | grep -oE 'HT[A-Z]+' | sort -u)
used=$(printf '%s' "$attr" | grep -oE '\{ *H = HT[A-Z]+' | grep -oE 'HT[A-Z]+' | sort -u)
if [ -n "$defined" ] && [ "$defined" = "$used" ]; then
  pass "every collected repo HEAD has a dispatch arm ($(printf '%s' "$defined" | tr '\n' ' '))"
else
  fail "every collected repo HEAD has a dispatch arm"
  printf '    defined: %s\n' "$(printf '%s' "$defined" | tr '\n' ' ')"
  printf '    used   : %s\n' "$(printf '%s' "$used" | tr '\n' ' ')"
fi

# 6. THE SAME CLASS PIN for the code-heads. An HC<X> collected without an arm
#    would silently reuse another repo's code-head: the same bug, one layer down.
hcdef=$(printf '%s' "$src" | grep -oE 'HC[A-Z]+=\\\$\(hs_codehead' | grep -oE 'HC[A-Z]+' | sort -u)
hcuse=$(printf '%s' "$attr" | grep -oE 'HC = HC[A-Z]+' | grep -oE 'HC[A-Z]+$' | sort -u)
if [ -n "$hcdef" ] && [ "$hcdef" = "$hcuse" ]; then
  pass "every collected repo CODE-head has a dispatch arm ($(printf '%s' "$hcdef" | tr '\n' ' '))"
else
  fail "every collected repo CODE-head has a dispatch arm"
  printf '    defined: %s\n' "$(printf '%s' "$hcdef" | tr '\n' ' ')"
  printf '    used   : %s\n' "$(printf '%s' "$hcuse" | tr '\n' ' ')"
fi

# 7. Every repo with a HEAD must also have a code-head, and vice versa. #5 and #6
#    each check their own family is internally consistent; a repo present in one
#    and missing from the other passes BOTH and is still half-wired. Guard the
#    VACUOUS pass too: empty sets are equal to each other and prove nothing.
htrepos=$(printf '%s' "$defined" | sed 's/^HT//' | sort -u)
hcrepos=$(printf '%s' "$hcdef" | sed 's/^HC//' | sort -u)
if [ -n "$htrepos" ] && [ "$htrepos" = "$hcrepos" ]; then
  pass "HEAD and CODE-head cover the same repo set ($(printf '%s' "$htrepos" | tr '\n' ' '))"
else
  fail "HEAD and CODE-head cover the same repo set"
  printf '    HEAD: %s\n    CODE: %s\n' "$(printf '%s' "$htrepos" | tr '\n' ' ')" "$(printf '%s' "$hcrepos" | tr '\n' ' ')"
fi

# 8. THE FAIL-SAFE, now in BOTH halves. An unresolvable code-head must fall back
#    to that repo's HEAD — never to "no code changes", which would silently
#    demote every unit into the benign bucket and turn a stale fleet green.
nfall=$(printf '%s' "$src" | grep -cE '\[ -n \\"\\\$HC[A-Z]+\\" \] \|\| HC[A-Z]+=\\\$HT[A-Z]+')
if [ "$nfall" -ge 1 ] && printf '%s' "$attr" | grep -qE 'if \(HC == "" \|\| HC ~ /\[\^0-9\]/\) HC = H'; then
  pass "unresolvable code-head falls back to HEAD, not to 'no code changes'"
else
  fail "unresolvable code-head falls back to HEAD, not to 'no code changes'"
  printf '    per-repo fallbacks found: %s\n' "$nfall"
fi

# 8b. MEMBERSHIP must not be decided by the unit NAME. The leg used to filter
#     `^(meshforge|meshanchor)-` before judging, which hid nomadnet-silence-watch
#     (13d) and meshanchor.service (17d — no hyphen) while reporting
#     meshforge-lxmd (a packaged binary that loads no repo code) as behind.
if printf '%s' "$src" | grep -qE "grep -E .\^\(sys\|usr\) \(meshforge\|meshanchor\)-"; then
  fail "membership is not filtered by unit-name prefix"
else
  pass "membership is not filtered by unit-name prefix"
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

# 11. The prose_note sentence's parenthetical names the CODE pathspec's
#     COMPLEMENT — a third, human-facing hardcode of the same constant, and
#     the one with no test to redden (only the operator's eyes; a misread of
#     an instrument is a bug report against the instrument). The 08-12
#     commit-pair is the proof it drifts: the sentence had to drop 'scripts'
#     when scripts/ joined the pathspec. Assert no pathspec member appears in
#     the parenthetical, so the NEXT widening reddens HERE and forces the
#     sentence to be rewritten alongside (2026-08-12 re-review).
prose_paren=$(printf '%s' "$src" | grep -o 'behind on NON-code only ([^)]*)' | head -1)
overlap=""
for p in $spec_real; do
  base=${p%%.*}   # requirements.txt -> requirements
  case "$prose_paren" in *"$base"*) overlap="$overlap $p";; esac
done
if [ -n "$prose_paren" ] && [ -z "$overlap" ]; then
  pass "prose bucket sentence names no CODE-pathspec member"
else
  fail "prose bucket sentence names no CODE-pathspec member"
  printf '    paren: %s\n    overlap:%s\n' "${prose_paren:-<none>}" "${overlap:-<none>}"
fi

# The wrapper (test_honest_status_shell.py) requires this exact line as well as
# exit 0 — a harness that exits 0 without reaching its end asserts nothing, and
# the line is what proves it got here.
if [ "$fails" = 0 ]; then echo "ALL PASS"; exit 0; else echo "FAILED: $fails assertion(s)"; exit 1; fi
