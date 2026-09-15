#!/usr/bin/env bash
# honest_status.sh — the skew leg must tell behind-on-CODE from behind-on-PROSE
# (2026-08-12). BEHAVIOURAL, against real git repos — its sibling
# test_honest_status_skew_repos.sh greps the source shape, and a grep cannot
# tell you what `git log -1 -- src` actually returns.
#
# WHY: the leg compared unit-start vs HEAD's commit time unconditionally, so
# every documentation commit marked every active unit "behind" until restart.
# Measured that day: three doc-only commits (.claude/, evals/, scripts/) put
# all nine watchdogs on the list; they were restarted to clear it and the next
# docs: commit would have refilled it. A near-permanently non-empty note
# carries no signal — this file's own standing-noise defect in NOTE's clothing.
#
# THE DANGER this pins, which is worse than the noise: if the code-head cannot
# be resolved (git error, paths never touched, garbage), it must fall back to
# HEAD — the pre-fix behaviour — and NEVER to "no code changes", which would
# silently demote every unit into the benign bucket and read a genuinely stale
# fleet as green. Two cases below plant exactly that.
#
# Non-vacuity is asserted, not assumed: one case reproduces the PRE-fix verdict
# for the same input, so if the fixture ever stops exercising the difference
# this harness fails instead of quietly passing.
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
D="$(mktemp -d)"; trap 'rm -rf "$D"' EXIT
fails=0
ok()   { printf '  %-56s ok\n' "$1"; }
bad()  { printf '  %-56s FAIL — %s\n' "$1" "$2"; fails=$((fails+1)); }

# THE function under test. 2026-09-15: this used to be a VERBATIM COPY of the
# pathspec, i.e. a fourth independent hardcode — so the test validated its own
# declaration and could pass while the real definition said something else.
# It now SOURCES the one definition (scripts/lib/code_paths.sh), which is what
# honest_status.sh interpolates into its remote probe and what fleet_sync.sh
# and fleet_pull.sh call directly. A checker must not re-declare the artifact
# it validates.
. "$HERE/../scripts/lib/code_paths.sh"
hs_codehead() { mf_code_head "$1"; }

mk() {  # $1=repo  $2=path  $3=epoch
  mkdir -p "$(dirname "$1/$2")"; echo x >> "$1/$2"
  git -C "$1" add -A
  GIT_AUTHOR_DATE="@$3 +0000" GIT_COMMITTER_DATE="@$3 +0000" \
    git -C "$1" commit -qm "touch $2"
}
newrepo() { mkdir -p "$1"; git -C "$1" init -q; git -C "$1" config user.email d@e; git -C "$1" config user.name d; }

T_CODE=1786000000
T_DOCS=1786500000

# ── repo A: a code commit, then a docs-only commit on top ────────────
newrepo "$D/a"
mk "$D/a" src/mod.py            "$T_CODE"
mk "$D/a" .claude/foundations/x.md "$T_DOCS"

HEAD_A=$(git -C "$D/a" show -s --format=%ct HEAD)
CODE_A=$(hs_codehead "$D/a")

[ "$HEAD_A" = "$T_DOCS" ] && ok "HEAD is the docs commit" \
  || bad "HEAD is the docs commit" "got $HEAD_A"
[ "$CODE_A" = "$T_CODE" ] && ok "code-head skips the docs commit" \
  || bad "code-head skips the docs commit" "got $CODE_A want $T_CODE"
[ "$CODE_A" != "$HEAD_A" ] && ok "code-head and HEAD actually differ (non-vacuous)" \
  || bad "code-head and HEAD actually differ" "both $CODE_A"

# ── the classification, exactly as the leg writes it ─────────────────
classify() {  # $1=unit start  $2=HEAD  $3=code-head -> B | P | current
  local T="$1" H="$2" HC="$3"
  case "$HC" in ''|*[!0-9]*) HC=$H;; esac
  if   [ "$T" -lt "$HC" ]; then echo B
  elif [ "$T" -lt "$H"  ]; then echo P
  else echo current; fi
}

r=$(classify $((T_CODE - 100)) "$HEAD_A" "$CODE_A")
[ "$r" = B ] && ok "unit older than the code commit -> CODE bucket" \
  || bad "unit older than the code commit -> CODE bucket" "got $r"

r=$(classify $((T_CODE + 100)) "$HEAD_A" "$CODE_A")
[ "$r" = P ] && ok "unit between code and docs commit -> PROSE bucket" \
  || bad "unit between code and docs commit -> PROSE bucket" "got $r"

r=$(classify $((T_DOCS + 100)) "$HEAD_A" "$CODE_A")
[ "$r" = current ] && ok "unit newer than HEAD -> current" \
  || bad "unit newer than HEAD -> current" "got $r"

# THE REGRESSION THIS EXISTS FOR: pre-2026-08-12 the middle case read B.
r_old=$([ $((T_CODE + 100)) -lt "$HEAD_A" ] && echo B || echo current)
[ "$r_old" = B ] && ok "pre-fix behaviour reproduced (that case WAS 'behind')" \
  || bad "pre-fix behaviour reproduced" "planting is vacuous — got $r_old"

# ── FAIL-SAFE: a repo that never touched code must fall back to HEAD ─
newrepo "$D/b"
mk "$D/b" docs/only.md "$T_DOCS"
HEAD_B=$(git -C "$D/b" show -s --format=%ct HEAD)
CODE_B=$(hs_codehead "$D/b")
[ -z "$CODE_B" ] && ok "repo with no code paths yields an EMPTY code-head" \
  || bad "repo with no code paths yields an empty code-head" "got '$CODE_B'"
r=$(classify $((T_DOCS - 100)) "$HEAD_B" "$CODE_B")
[ "$r" = B ] && ok "empty code-head falls back to HEAD (fail-SAFE, not benign)" \
  || bad "empty code-head falls back to HEAD" "got $r — units would be demoted"

# ── FAIL-SAFE: a non-numeric code-head must not demote either ────────
r=$(classify $((T_DOCS - 100)) "$HEAD_B" "garbage")
[ "$r" = B ] && ok "non-numeric code-head falls back to HEAD" \
  || bad "non-numeric code-head falls back to HEAD" "got $r"

# ── scripts/ and templates/ are NOT in the code-head (2026-09-15) ────
# ⚠️ READ THIS BEFORE FLIPPING IT BACK. This pair of assertions has now been
# reversed twice, because BOTH directions are defensible and each has a
# measurement behind it:
#
#   2026-05-11  scripts/ was IN. Tuning scripts/cloud/push_snapshot.sh
#               "triggered fleet-wide map restarts for no benefit" — the map
#               does not import it. Excluded.
#   2026-08-12  scripts/ went back IN: nomadnet-silence-watch is Type=simple
#               with ExecStart=scripts/nomadnet_silence_watch.py, and
#               MeshAnchor keeps its systemd unit files under scripts/, so
#               "exec'd fresh per invocation" was false on both repos.
#   2026-09-15  OUT again (operator call). A session widened the shared
#               constant to the union, measured the result, and found it
#               re-created the 05-11 churn on the manager box while fixing
#               nothing on the remote leg — whose classifier had kept the
#               narrow list the whole time, so the two legs now DISAGREED.
#               templates/ settles cleanly on its own: fleet_sync restarts
#               with `try-restart`, never `daemon-reload`, so a changed unit
#               file cannot take effect through the restart it would trigger.
#
# ⚠️ THE GAP THIS LEAVES, accepted knowingly and NOT to be closed by widening:
# a commit touching only scripts/nomadnet_silence_watch.py does not move the
# code-head, so that daemon can keep running pre-fix code. Real instance:
# 366b435d (2026-09-02, the cure for a LATCHING alarm) touched only that file
# and its test; the code-head there resolved to a src/ commit 7.8 hours
# earlier. The fix is PER-UNIT ExecStart attribution, not a wider constant —
# see scripts/lib/code_paths.sh.
mk "$D/a" scripts/thing.sh $((T_DOCS + 1000))
HEAD_A2=$(git -C "$D/a" show -s --format=%ct HEAD)
CODE_A2=$(hs_codehead "$D/a")
[ "$CODE_A2" != $((T_DOCS + 1000)) ] && ok "a scripts/ commit does NOT move the code-head (05-11 churn)" \
  || bad "a scripts/ commit does NOT move the code-head" "got $CODE_A2 — the union is back"
r=$(classify $((T_CODE + 100)) "$HEAD_A2" "$CODE_A2")
[ "$r" = P ] && ok "scripts/ commit lands a stale unit in PROSE, not CODE" \
  || bad "scripts/ commit lands a stale unit in PROSE" "got $r"

# ── templates/ is NOT code: try-restart cannot apply a unit-file change ──
mk "$D/a" templates/systemd/u.service $((T_DOCS + 2000))
CODE_A3=$(hs_codehead "$D/a")
[ "$CODE_A3" != $((T_DOCS + 2000)) ] && ok "a templates/ commit does NOT move the code-head" \
  || bad "a templates/ commit does NOT move the code-head" "got $CODE_A3"

# ── top-level requirements.txt IS code: the meshforge-maps shape ─────
# git pathspec `requirements` matches only the DIRECTORY; meshforge-maps
# pins its deps in a top-level requirements.txt and has no requirements/
# dir, so before 2026-08-12 its dep-floor bumps never moved the code-head.
newrepo "$D/c"
mk "$D/c" src/app.py "$T_CODE"
mk "$D/c" requirements.txt $((T_DOCS + 3000))
CODE_C=$(hs_codehead "$D/c")
[ "$CODE_C" = $((T_DOCS + 3000)) ] && ok "a top-level requirements.txt commit DOES move the code-head" \
  || bad "a top-level requirements.txt commit DOES move the code-head" "got $CODE_C — a maps dep bump would demote to prose"

if [ "$fails" = 0 ]; then echo "ALL PASS"; exit 0; else echo "FAILED: $fails"; exit 1; fi
