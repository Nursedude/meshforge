#!/usr/bin/env bash
# Behavior test for fleet_sync.sh sync_repo when the pull applies NOTHING.
#
# WHY (2026-09-18): the "no commits to apply" branch skipped the restart
# unconditionally, asserting "any running service is on the same code the
# restart would bring up". That is only true if sync itself is the only thing
# that pulls -- and scripts/fleet_pull.sh pulls BY DESIGN without restarting.
# After a real fleet_pull, 46 units across 10 boxes were behind the newest CODE
# commit and this branch would have reported every one of them `unchanged`
# while exiting 0. Sync was blind to skew it did not itself cause.
#
# Drives the REAL payload (extracted exactly as fleet_sync binds it) with a
# stub systemctl, so the assertion is about behavior, not about source text.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
SYNC="$HERE/../scripts/fleet_sync.sh"
LIB="$HERE/../scripts/lib/code_paths.sh"
fails=0
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT

# --- extract ONLY the sync_repo function ----------------------------------
# NOT the whole REMOTE_SCRIPT body: that payload ends in top-level sync_repo /
# sync_local_* invocations against /opt/meshforge, so sourcing it RUNS A REAL
# DEPLOY on the machine running the tests. Learned the hard way while writing
# this file (no harm: the system paths died early on a missing git_heal stub).
# A test must not be able to deploy.
PAYLOAD="$TMP/sync_repo.sh"
awk '/^sync_repo\(\) \{$/{f=1} f{print} f&&/^\}$/{exit}' "$SYNC" > "$PAYLOAD"
if [ ! -s "$PAYLOAD" ]; then echo "FAIL could not extract sync_repo"; exit 1; fi
# The USER-bus sibling has the SAME branch and the same blindness -- fixing only
# the one the incident arrived through is the 2026-08-09 trap. Extract it too.
UPAYLOAD="$TMP/sync_user_unit.sh"
awk '/^sync_user_unit\(\) \{$/{f=1} f{print} f&&/^\}$/{exit}' "$SYNC" > "$UPAYLOAD"
if [ ! -s "$UPAYLOAD" ]; then echo "FAIL could not extract sync_user_unit"; exit 1; fi
# NOT an early exit: an early exit here short-circuits the BEHAVIOURAL cases,
# so a regression would be proven only by a grep. Counted as an assertion at
# the end instead, after the cases have actually run.
# sync_repo calls git_heal_ownership (prepended separately in the real script).
# Source the REAL lib rather than stub it: a stub that returned the wrong token
# made sync_repo bail early, which silently satisfied the "must not restart"
# case for the wrong reason.
GIT_HEAL_LIB="$HERE/../scripts/lib/git_heal.sh"
if [ -r "$GIT_HEAL_LIB" ]; then . "$GIT_HEAL_LIB"
else echo "FAIL missing $GIT_HEAL_LIB"; exit 1; fi

# --- stub systemctl: MainPID is OUR pid, so /proc/<pid> is real -----------
SB="$TMP/bin"; mkdir -p "$SB"
cat > "$SB/systemctl" <<'STUB'
#!/usr/bin/env bash
case "$*" in
  *"-p MainPID"*)      echo "$STUB_PID" ;;
  *list-unit-files*)   echo "meshforge-test.service enabled" ;;
  *try-restart*)       touch "$STUB_MARKER"; exit 0 ;;
  *is-active*)         exit 0 ;;
  *)                   exit 0 ;;
esac
STUB
chmod +x "$SB/systemctl"
# sync_repo restarts via `sudo -n systemctl`, and sudo resets PATH (secure_path),
# so the stub above would be bypassed and the REAL systemctl asked to restart a
# unit that does not exist. Stub sudo to drop its flags and exec in place.
cat > "$SB/sudo" <<'STUB'
#!/usr/bin/env bash
while [ $# -gt 0 ]; do case "$1" in -n|-A|-H|-E|-S) shift ;; *) break ;; esac; done
exec "$@"
STUB
chmod +x "$SB/sudo"
export PATH="$SB:$PATH"

# --- a repo that is genuinely AT HEAD, with a datable code commit ---------
mk_repo() {   # $1 = committer date for the src/ commit
    rm -rf "$TMP/origin" "$TMP/repo"
    git init -q --bare "$TMP/origin"
    git init -q "$TMP/repo"
    cd "$TMP/repo" || return 1
    git config user.email t@t; git config user.name t
    git symbolic-ref HEAD refs/heads/main
    mkdir -p src; echo "x=1" > src/x.py; git add src/x.py
    GIT_AUTHOR_DATE="$1" GIT_COMMITTER_DATE="$1" git commit -q -m "code"
    git remote add origin "$TMP/origin"
    git push -q origin main 2>/dev/null
    git branch --set-upstream-to=origin/main main >/dev/null 2>&1
    cd - >/dev/null || return 1
}

run_case() {  # $1=label $2=date $3=expect_restart(yes|no)
    mk_repo "$2" || { echo "FAIL $1 repo setup"; fails=$((fails+1)); return; }
    export STUB_MARKER="$TMP/restarted.$1"; rm -f "$STUB_MARKER"
    export STUB_PID=$$
    local head out
    head="$(git -C "$TMP/repo" rev-parse HEAD)"
    out="$(cd "$TMP/repo" && . "$LIB" && . "$PAYLOAD" && \
           sync_repo test "$TMP/repo" meshforge-test "$head" 2>&1)"
    if [ "$3" = "yes" ]; then
        if [ -f "$STUB_MARKER" ]; then echo "ok   $1 restarted despite no commits"
        else echo "FAIL $1 expected a restart, got: $out"; fails=$((fails+1)); fi
    else
        if [ -f "$STUB_MARKER" ]; then
            echo "FAIL $1 restarted a CURRENT process: $out"; fails=$((fails+1))
        elif ! printf %s "$out" | grep -q unchanged; then
            # "no marker" is ALSO true when sync_repo died before deciding, so
            # require the decision itself to appear. Without this the case
            # passes for the wrong reason (it did, mid-authoring).
            echo "FAIL $1 never reached the decision: $out"; fails=$((fails+1))
        else echo "ok   $1 no restart, decision reached (process is current)"; fi
    fi
}

# Process start time is ~now (our own /proc entry).
# Code commit an hour OLD  -> process is current    -> must NOT restart.
run_case current "$(date -d '-1 hour' 2>/dev/null || date -v-1H)" no
# Code commit an hour AHEAD -> process predates code -> MUST restart, even
# though the pull applied nothing. This is the regression this file exists for.
run_case behind  "$(date -d '+1 hour' 2>/dev/null || date -v+1H)" yes

run_user_case() {  # $1=label $2=date $3=expect_restart(yes|no)
    mk_repo "$2" || { echo "FAIL $1 repo setup"; fails=$((fails+1)); return; }
    export STUB_MARKER="$TMP/restarted.$1"; rm -f "$STUB_MARKER"
    export STUB_PID=$$
    local head out
    head="$(git -C "$TMP/repo" rev-parse HEAD)"
    # pre_head == HEAD is the "pull applied nothing" case, same as sync_repo.
    out="$(cd "$TMP/repo" && . "$LIB" && . "$UPAYLOAD" && \
           sync_user_unit test "$TMP/repo" meshforge-test "$head" 2>&1)"
    if [ "$3" = "yes" ]; then
        if [ -f "$STUB_MARKER" ]; then echo "ok   $1 user unit restarted despite no commits"
        else echo "FAIL $1 expected a user restart, got: $out"; fails=$((fails+1)); fi
    else
        if [ -f "$STUB_MARKER" ]; then
            echo "FAIL $1 restarted a CURRENT user process: $out"; fails=$((fails+1))
        elif ! printf %s "$out" | grep -q unchanged; then
            echo "FAIL $1 never reached the decision: $out"; fails=$((fails+1))
        else echo "ok   $1 no restart, decision reached (user process is current)"; fi
    fi
}

run_user_case user-current "$(date -d '-1 hour' 2>/dev/null || date -v-1H)" no
# The one that would have shipped half-fixed: a USER unit older than its code,
# with nothing pulled. It must restart -- and must NOT be swallowed by the
# docs_only test that sits after this branch and diffs a sha against itself.
run_user_case user-behind  "$(date -d '+1 hour' 2>/dev/null || date -v+1H)" yes

if grep -q 'mf_code_head' "$PAYLOAD" && grep -q 'mf_code_head' "$UPAYLOAD"; then
    echo "ok   both sync_repo and sync_user_unit consult mf_code_head"
else
    echo "FAIL a sync path does not consult mf_code_head"; fails=$((fails+1))
fi

# mf_code_head must be REACHABLE from the payload on the remote box, or the
# branch above dies with command-not-found on all nine at once.
if grep -q 'MF_CODE_PATHS_SRC' "$SYNC" && grep -q '\$MF_CODE_PATHS_SRC' "$SYNC"; then
    echo "ok   lib/code_paths.sh is shipped into the remote payload"
else
    echo "FAIL remote payload does not ship lib/code_paths.sh"; fails=$((fails+1))
fi

# "ALL PASS" is the sentinel tests/test_honest_status_shell.py asserts on --
# exit 0 alone is NOT enough there, deliberately, so a harness cannot pass by
# saying nothing. (This file failed that check on its first run.)
if [ "$fails" -eq 0 ]; then echo "ALL PASS"; exit 0; fi
echo "SOME FAILED ($fails assertion(s))"
exit 1
