#!/usr/bin/env bash
# harness_audit.sh — executable ground truth for the Claude HARNESS layer
# (2026-07-03). Complements honest_status.sh: that script verifies REPO/FLEET
# truth (CI, suite, SHA convergence); this one verifies the second-brain
# harness a session stands on — hooks, claim gate, calibration ledger, mini,
# cron spine, memory index, the manager deadman. Born from the 07-03 harness
# self-audit (.claude/foundations/harness_map.md is the companion map).
#
# Conventions match honest_status: PASS / FAIL / UNKNOWN per leg;
# UNKNOWN is NEVER a pass. Exit 0 = all PASS; 1 = any FAIL; 2 = UNKNOWN(s)
# but no FAIL. Run on the MANAGER box; legs self-guard elsewhere.
#
# Crontab (manager, daily):
#   35 5 * * * /opt/meshforge/scripts/harness_audit.sh >/dev/null 2>&1; /opt/meshforge/scripts/cron_verdict.sh harness_audit $?
set -uo pipefail

REPO="${MESHFORGE_REPO:-/opt/meshforge}"
MA_REPO="/opt/meshanchor"
# Shared pytest-invocation wrapper (run + classify honestly).
MF_REPO_ROOT="$REPO"
# shellcheck source=lib/pytest_checked.sh
. "$REPO/scripts/lib/pytest_checked.sh"
MEM_DIR="${HOME}/.claude/projects/-opt-meshforge/memory"
NOTES="${HOME}/.claude/plans/gateway-session-notes-$(hostname | tr '[:upper:]' '[:lower:]').md"
VERDICTS="${CRON_VERDICT_LOG:-$HOME/cron_verdicts.log}"
DEADMAN_PEER="${MANAGER_HEARTBEAT_PEER:-moc1}"

pass=0; fail=0; unknown=0
say() { printf '  %-28s %-8s %s\n' "$1" "$2" "$3"; }
P() { say "$1" "PASS" "$2"; pass=$((pass+1)); }
F() { say "$1" "FAIL" "$2"; fail=$((fail+1)); }
U() { say "$1" "UNKNOWN" "$2"; unknown=$((unknown+1)); }

echo "harness_audit — $(hostname) $(date -u +%Y-%m-%dT%H:%M:%SZ)"

# 1. git hooks live (the #29 spine; found dormant once, 2026-06-15)
for r in "$REPO" "$MA_REPO"; do
    [ -d "$r" ] || continue
    hp="$(git -C "$r" config core.hooksPath 2>/dev/null)"
    if [ "$hp" != ".githooks" ]; then F "hooksPath($(basename "$r"))" "got '${hp:-unset}'"; continue; fi
    # The config STRING was the whole check, and it read PASS with the hooks
    # directory gone (§3 drill 2026-09-07) — the dormant spine this leg exists
    # to catch, entered through a second door. The hooks must also EXIST and
    # be executable, or git runs nothing and says nothing.
    _hk_missing=""
    for _hk in pre-commit pre-push; do [ -x "$r/.githooks/$_hk" ] || _hk_missing="$_hk_missing $_hk"; done
    if [ -n "$_hk_missing" ]; then F "hooksPath($(basename "$r"))" ".githooks set but hook absent/not executable:$_hk_missing"
    else P "hooksPath($(basename "$r"))" ".githooks (pre-commit + pre-push executable)"; fi
done

# 2. session hooks wired (Stop->claim_gate is the calibrated-claims enforcer)
SETTINGS="$REPO/.claude/settings.json"
# PARSE the file; never grep it. A substring grep read `PASS wired` on an
# UNPARSEABLE settings file (Claude Code loads NO hooks from one), on a Stop
# entry deleted with the filename surviving in a note key, and on the hook
# moved under a different event (§3 drill 2026-09-07, three plants, all
# silent). The question is "is this command a hook under THIS event", and
# only the JSON can answer it.
hook_wired() {  # $1=settings file  $2=event  $3=command substring → yes|no|badjson|absent|error
    python3 - "$1" "$2" "$3" <<'PY' 2>/dev/null || echo error
import json, sys
p, ev, needle = sys.argv[1:4]
try:
    with open(p, encoding="utf-8") as f:
        d = json.load(f)
except FileNotFoundError:
    print("absent"); raise SystemExit
except (OSError, ValueError):
    print("badjson"); raise SystemExit
hit = False
for grp in ((d.get("hooks") or {}).get(ev) or []):
    for h in (grp.get("hooks") or []) if isinstance(grp, dict) else []:
        if isinstance(h, dict) and needle in str(h.get("command", "")):
            hit = True
print("yes" if hit else "no")
PY
}
judge_hook() {  # $1=label  $2=event  $3=needle  $4=settings file
    case "$(hook_wired "$4" "$2" "$3")" in
        yes)     P "$1" "wired under $2 in $(basename "$4")" ;;
        no)      F "$1" "$3 is not a $2 hook command in $4 (the name elsewhere in the file does not count)" ;;
        badjson) F "$1" "$4 is NOT valid JSON — Claude Code loads no hooks from it" ;;
        absent)  U "$1" "cannot read $4" ;;
        *)       U "$1" "could not parse $4" ;;
    esac
}
judge_hook "Stop->claim_gate" Stop "claim_gate.py" "$SETTINGS"
judge_hook "SessionStart->warmstart" SessionStart "mini_dudeai.warmstart" "$SETTINGS"

# 2c. USER-level hooks — the file this audit never read (§3 drill 2026-09-07).
# Two of the three Bash guards (psk_leak_guard on MeshAnchor sessions, the
# Layer B exit_code_mask_guard everywhere) are wired in ~/.claude/settings.json,
# not the repo's. Legs 2/2b could not see a deleted live hook that the user
# settings still referenced. Every command there that names a
# ~/.claude/hooks/<file> must point at a file that exists and is executable.
USER_SETTINGS="$HOME/.claude/settings.json"
_us="$(python3 - "$USER_SETTINGS" "$HOME" <<'PY' 2>/dev/null || echo error
import json, os, re, sys
p, home = sys.argv[1:3]
try:
    with open(p, encoding="utf-8") as f:
        d = json.load(f)
except FileNotFoundError:
    print("absent"); raise SystemExit
except (OSError, ValueError):
    print("badjson"); raise SystemExit
n = 0; missing = []
for ev, groups in ((d.get("hooks") or {}).items()):
    for grp in groups or []:
        for h in (grp.get("hooks") or []) if isinstance(grp, dict) else []:
            if not isinstance(h, dict):
                continue
            for ref in re.findall(r"\$HOME/\.claude/hooks/[\w.\-]+", str(h.get("command", ""))):
                n += 1
                if not os.access(ref.replace("$HOME", home), os.X_OK):
                    missing.append(f"{ev}:{os.path.basename(ref)}")
print("ok", n, ",".join(missing))
PY
)"
case "$_us" in
    absent)  U "user hooks" "no $USER_SETTINGS — operator-local guards not wired on this box" ;;
    badjson) F "user hooks" "$USER_SETTINGS is NOT valid JSON — Claude Code loads no user-level hooks" ;;
    ok*)
        read -r _ _us_n _us_miss <<<"$_us"
        if [ -n "${_us_miss:-}" ]; then F "user hooks" "wired but the hook file is absent/not executable: $_us_miss"
        elif [ "${_us_n:-0}" -eq 0 ]; then P "user hooks" "no ~/.claude/hooks command wired"
        else P "user hooks" "$_us_n ~/.claude/hooks command(s) wired, all present + executable"; fi ;;
    *)       U "user hooks" "could not parse $USER_SETTINGS" ;;
esac

# 2b. the RUNNING hook == the REVIEWED hook (2026-09-07)
# Legs 1-2 check hooks are WIRED. Nothing checked that the file a wired hook
# POINTS AT still matches its repo-tracked source — so the reviewed artifact
# and the running artifact could diverge silently, and did: psk_leak_guard.sh
# in ~/.claude/hooks/ was 2 MONTHS behind the repo copy (2650B Jul 10 vs
# 4972B Jul 11). The live one was missing the honest SCOPE section AND the
# fail-open witness added precisely so "a harness JSON-shape change can't
# silently disable the guard". A security guard was running an older, weaker
# version while its improvements sat reviewed and undeployed.
# A hook present in both places but DIFFERENT is the failure; absent from the
# repo is not this leg's business (an operator-local hook is legitimate).
hook_drift=0; hook_checked=0
for repo_hook in "$REPO"/.claude/hooks/*.sh; do
    [ -f "$repo_hook" ] || continue
    live_hook="$HOME/.claude/hooks/$(basename "$repo_hook")"
    [ -f "$live_hook" ] || continue          # not deployed here — not drift
    hook_checked=$((hook_checked+1))
    if ! cmp -s "$repo_hook" "$live_hook"; then
        hook_drift=$((hook_drift+1))
        F "hook drift($(basename "$repo_hook"))" \
          "live copy differs from the repo-tracked source — the reviewed hook is NOT the running one"
    fi
done
if [ "$hook_checked" -eq 0 ]; then
    U "hook repo==live" "no repo hook is deployed to ~/.claude/hooks — nothing compared"
elif [ "$hook_drift" -eq 0 ]; then
    P "hook repo==live" "$hook_checked hook(s) match their repo source"
fi

# 3. mini fresh + seed coverage test
MINI_STATE="$HOME/mini_dudeai_state.json"
if [ -r "$MINI_STATE" ]; then
    # Three absent/odd shapes get their own words (§3 drill 2026-09-07): a
    # missing key used to read as `now - 0` (the epoch-sentinel tell, 56 years
    # stale), and a tick in the FUTURE — a stepped clock on an RTC-less Pi —
    # read as "state unparseable", which sends the reader to the JSON instead
    # of the clock.
    age="$(python3 - "$MINI_STATE" <<'PY' 2>/dev/null || echo error
import json, sys, time
try:
    d = json.load(open(sys.argv[1]))
except Exception:
    print("unparseable"); raise SystemExit
if not isinstance(d, dict) or "last_tick_ts" not in d:
    print("nokey"); raise SystemExit
try:
    print(int(time.time() - float(d["last_tick_ts"])))
except (TypeError, ValueError):
    print("badvalue")
PY
)"
    case "$age" in
        unparseable) U "mini fresh" "state file is not valid JSON" ;;
        nokey)       U "mini fresh" "state has no last_tick_ts key — freshness unobservable (never 'now - 0')" ;;
        badvalue)    U "mini fresh" "last_tick_ts is not a number" ;;
        error)       U "mini fresh" "could not read state" ;;
        -*)          U "mini fresh" "last tick ${age#-}s in the FUTURE — a clock stepped, not a fresh tick" ;;
        *) if [ "$age" -lt 300 ]; then P "mini fresh" "last tick ${age}s ago"
           else F "mini fresh" "last tick ${age}s ago (stale)"; fi ;;
    esac
else
    U "mini fresh" "no state file (not a mini box?)"
fi
# Was a tail-piped conditional. `set -o pipefail` above meant it did propagate
# pytest's status (it was not the vacuous gate it first looked like), but it
# still trusted the exit code — the signal measured to flap 0 on a failed run
# (2026-07-28). Classify instead, and keep the third state: this audit already
# has a U() for "could not tell", and an unclassifiable run belongs there
# rather than in P.
_ha_cwd="$PWD"; cd "$REPO" 2>/dev/null || true
mf_pytest_checked tests/test_mini_dudeai_honest_failure_modes.py -k "SeedCovers" -q
_ha_rc=$?
cd "$_ha_cwd" 2>/dev/null || true
rm -f "$MF_PYTEST_LOG"
case "$_ha_rc" in
    0) P "seed coverage" "$MF_PYTEST_WHY" ;;
    1) F "seed coverage" "$MF_PYTEST_WHY" ;;
    *) U "seed coverage" "$MF_PYTEST_WHY" ;;
esac

# 4. calibration ledger + daily reverify freshness (<26h)
LEDGER="$HOME/calibration_ledger.jsonl"
# Count PARSEABLE events. `-s` + `wc -l` read 4 KB of random bytes as
# "21 events" (§3 drill 2026-09-07); a corrupted ledger is not a healthy one.
if [ -s "$LEDGER" ]; then
    _lc="$(python3 - "$LEDGER" <<'PY' 2>/dev/null || echo error
import json, sys
good = bad = 0
with open(sys.argv[1], encoding="utf-8", errors="replace") as f:
    for line in f:
        if not line.strip():
            continue
        try:
            json.loads(line); good += 1
        except ValueError:
            bad += 1
print(good, bad)
PY
)"
    if [ "$_lc" = error ]; then U "calibration ledger" "could not read $LEDGER"
    else
        read -r _lg _lb <<<"$_lc"
        if [ "${_lb:-0}" -gt 0 ]; then F "calibration ledger" "$_lb unparseable line(s) beside $_lg events — corrupted, not healthy"
        elif [ "${_lg:-0}" -eq 0 ]; then U "calibration ledger" "no parseable event in $LEDGER"
        else P "calibration ledger" "$_lg events"; fi
    fi
else
    U "calibration ledger" "absent/empty at $LEDGER"
fi
check_verdict_fresh() {  # $1=name $2=max_age_s
    # Select by the structural NAME field, never a whole-line grep: verdict
    # messages now embed raw job output (evidence capture), and a failing
    # cron's excerpt can contain a sibling cron's name — harness_audit's own
    # captured FAIL output literally names `calibration_reverify`, so a
    # whole-line grep would select the wrong row (review 2026-07-31, finding 7).
    line="$(awk -v n="$1" '$2 == n' "$VERDICTS" 2>/dev/null | tail -1)"
    [ -n "$line" ] || { U "$1 verdict" "never reported"; return; }
    ts="$(date -d "$(printf '%s' "$line" | awk '{print $1}')" +%s 2>/dev/null || echo 0)"
    age=$(( $(date +%s) - ts ))
    status="$(printf '%s' "$line" | awk '{print $3}')"
    # A verdict stamped in the FUTURE read "PASS OK, -86382s ago" (§3 drill
    # 2026-09-07). A negative age is a stepped clock (honest_failure_modes #6),
    # and a verdict whose age cannot be known cannot be called fresh.
    if [ "$age" -lt 0 ]; then U "$1 verdict" "stamped ${age#-}s in the FUTURE — a clock stepped; freshness unobservable"
    elif [ "$age" -gt "$2" ]; then F "$1 verdict" "stale ${age}s (>${2}s)"
    elif [ "$status" = "OK" ]; then P "$1 verdict" "OK, ${age}s ago"
    else F "$1 verdict" "$status, ${age}s ago"; fi
}
check_verdict_fresh "calibration_reverify" 93600

# 5. cron freshness watcher healthy. This leg had exactly ONE outcome from
# birth to 2026-09-07 (§3 drill): it judged status only, with no age window,
# and its writer stamped OK unconditionally — the log held zero non-OK
# cron_freshness lines, ever. The watcher for silent crons could not detect
# its own silence. Now: the same age + status rule as calibration_reverify
# (hourly cron → 3h window), and the writer stamps FAIL when anything is
# stale (fixed the same day in the operator-local watcher script).
check_verdict_fresh "cron_freshness" 10800

# 6. memory hot index under the load limit (24576 B; warn at 75%)
if [ -r "$MEM_DIR/MEMORY.md" ]; then
    sz="$(wc -c < "$MEM_DIR/MEMORY.md")"
    if [ "$sz" -ge 24576 ]; then F "memory index" "${sz}B >= 24576B limit"
    elif [ "$sz" -ge 18432 ]; then P "memory index" "${sz}B (>=75% — demote soon)"
    else P "memory index" "${sz}B"; fi
else
    U "memory index" "no MEMORY.md"
fi

# 7. memory git remote private (leaked-secret guard; self-guards offline)
if command -v gh >/dev/null 2>&1 && [ -d "$MEM_DIR/.git" ]; then
    url="$(git -C "$MEM_DIR" remote get-url origin 2>/dev/null | sed -E 's#(git@github.com:|https://github.com/)##; s/\.git$//')"
    vis="$(gh repo view "$url" --json visibility --jq .visibility 2>/dev/null)"
    case "$vis" in
        PRIVATE) P "memory repo" "PRIVATE" ;;
        "")      U "memory repo" "visibility unreadable (offline?)" ;;
        *)       F "memory repo" "visibility=$vis — SECRETS EXPOSED" ;;
    esac
else
    U "memory repo" "gh or .git absent"
fi

# 8. manager deadman spine (heartbeat cron here + deadman cron on peer)
# Comment lines are skipped on both sides: a commented-out crontab entry read
# `PASS wired` (§3 drill 2026-09-07) — a parked cron is exactly the dormant
# spine this leg exists to catch.
crontab -l 2>/dev/null | grep -v '^[[:space:]]*#' | grep -q "manager_heartbeat.sh" \
    && P "heartbeat cron (local)" "wired" \
    || F "heartbeat cron (local)" "missing from crontab (or commented out)"
peer_line="$(timeout 25 ssh -o ConnectTimeout=8 -o BatchMode=yes "$DEADMAN_PEER" "crontab -l 2>/dev/null | grep -v '^[[:space:]]*#' | grep -c manager_deadman.sh" 2>/dev/null)"
if [ "${peer_line:-}" = "" ]; then U "deadman cron ($DEADMAN_PEER)" "peer unreachable"
elif [ "$peer_line" -ge 1 ]; then P "deadman cron ($DEADMAN_PEER)" "wired"
else F "deadman cron ($DEADMAN_PEER)" "missing from peer crontab"; fi

# 9. session-notes size (rotation convention: archive when large)
if [ -r "$NOTES" ]; then
    nsz="$(wc -c < "$NOTES")"
    if [ "$nsz" -gt 81920 ]; then F "session notes" "${nsz}B >80KB — rotate to archive"
    else P "session notes" "${nsz}B"; fi
else
    U "session notes" "absent"
fi

echo
echo "--> ${pass} PASS, ${fail} FAIL, ${unknown} UNKNOWN"
[ "$fail" -gt 0 ] && exit 1
[ "$unknown" -gt 0 ] && exit 2
exit 0
