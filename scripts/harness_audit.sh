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
# Env-overridable like REPO, so the sandboxed test can point it at nothing
# instead of auditing whatever MeshAnchor checkout the running box carries
# (review 2026-09-07: ambient state in the closing control).
MA_REPO="${MESHANCHOR_REPO:-/opt/meshanchor}"
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
except (PermissionError, IsADirectoryError, OSError):
    print("unreadable"); raise SystemExit   # bytes never read — say so, not "bad JSON"
except ValueError:
    print("badjson"); raise SystemExit
if not isinstance(d, dict):
    print("notobject"); raise SystemExit
hit = False
for grp in ((d.get("hooks") or {}).get(ev) or []):
    for h in (grp.get("hooks") or []) if isinstance(grp, dict) else []:
        # command must be a STRING — Claude Code runs nothing else.
        if isinstance(h, dict) and isinstance(h.get("command"), str) and needle in h["command"]:
            hit = True
print("yes" if hit else "no")
PY
}
judge_hook() {  # $1=label  $2=event  $3=needle  $4=settings file
    case "$(hook_wired "$4" "$2" "$3")" in
        yes)       P "$1" "wired under $2 in $(basename "$4")" ;;
        no)        F "$1" "$3 is not a $2 hook command in $4 (the name elsewhere in the file does not count)" ;;
        badjson)   F "$1" "$4 is NOT valid JSON — Claude Code loads no hooks from it" ;;
        notobject) F "$1" "$4 parses but is not a JSON object — Claude Code loads no hooks from it" ;;
        absent)    U "$1" "cannot read $4" ;;
        unreadable) U "$1" "$4 exists but could not be read (permissions?) — wiring unobservable" ;;
        *)         U "$1" "could not parse $4" ;;
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
# One walker for BOTH settings files (review 2026-09-07: the first version
# knew only the literal `$HOME/.claude/hooks/<file>` spelling, required X_OK
# — bash runs a 644 file fine — accepted a directory, and checked nothing
# on the repo side, where psk_leak_guard is wired). Every script path a hook
# command names — `$CLAUDE_PROJECT_DIR/…`, `$HOME/…`, `${HOME}/…`, `~/…` —
# must resolve to an existing, readable FILE.
hook_files_ok() {  # $1=settings file  $2=repo root  $3=home → absent|unreadable|badjson|notobject|ok <n> <missing,...>
    python3 - "$1" "$2" "$3" <<'PY' 2>/dev/null || echo error
import json, os, re, sys
p, repo, home = sys.argv[1:4]
try:
    with open(p, encoding="utf-8") as f:
        d = json.load(f)
except FileNotFoundError:
    print("absent"); raise SystemExit
except (PermissionError, IsADirectoryError, OSError):
    print("unreadable"); raise SystemExit
except ValueError:
    print("badjson"); raise SystemExit
if not isinstance(d, dict):
    print("notobject"); raise SystemExit
# Bare, braced, and braced-with-default (`${VAR:-.}`) spellings all resolve.
TOK = re.compile(r"(\$CLAUDE_PROJECT_DIR|\$\{CLAUDE_PROJECT_DIR(?::-[^}]*)?\}|\$HOME|\$\{HOME(?::-[^}]*)?\}|~)(/[\w./\-]+\.(?:sh|py))")
n = 0; missing = []
for ev, groups in ((d.get("hooks") or {}).items()):
    for grp in groups or []:
        for h in (grp.get("hooks") or []) if isinstance(grp, dict) else []:
            if not isinstance(h, dict) or not isinstance(h.get("command"), str):
                continue
            for base, rel in TOK.findall(h["command"]):
                n += 1
                root = repo if "PROJECT" in base else home
                path = root + rel
                if not (os.path.isfile(path) and os.access(path, os.R_OK)):
                    missing.append(f"{ev}:{os.path.basename(rel)}")
print("ok", n, ",".join(missing))
PY
}
judge_hook_files() {  # $1=label  $2=settings file  $3=absent-wording(U)
    _hf="$(hook_files_ok "$2" "$REPO" "$HOME")"
    case "$_hf" in
        absent)     U "$1" "$3" ;;
        unreadable) U "$1" "$2 exists but could not be read — wiring unobservable" ;;
        badjson)    F "$1" "$2 is NOT valid JSON — Claude Code loads no hooks from it" ;;
        notobject)  F "$1" "$2 parses but is not a JSON object — Claude Code loads no hooks from it" ;;
        ok*)
            read -r _ _hf_n _hf_miss <<<"$_hf"
            if [ -n "${_hf_miss:-}" ]; then F "$1" "wired but the hook file is absent/unreadable: $_hf_miss"
            elif [ "${_hf_n:-0}" -eq 0 ]; then P "$1" "no script-path hook command wired"
            else P "$1" "$_hf_n script-path hook command(s) wired, all present + readable"; fi ;;
        *)          U "$1" "could not parse $2" ;;
    esac
}
judge_hook_files "hook files(repo)" "$SETTINGS" "cannot read $SETTINGS"
judge_hook_files "user hooks" "$HOME/.claude/settings.json" "no ~/.claude/settings.json — operator-local guards not wired on this box"

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
        # The ledger's own appender deliberately leaves ONE isolated malformed
        # line after a torn (power-loss) write and every reader skips it
        # (history._repair_torn_tail / load_events). A few such lines are the
        # writer's contract, not corruption; they are DISCLOSED, never hidden.
        # Garbage is when there is nothing parseable, or more than three malformed
        # lines AND more than 1 in 10 (review 2026-09-07).
        if [ "${_lg:-0}" -eq 0 ]; then F "calibration ledger" "0 parseable events, $_lb unparseable line(s) — corrupted, not healthy"
        elif [ "${_lb:-0}" -gt 3 ] && [ $((_lb * 10)) -gt "$_lg" ]; then F "calibration ledger" "$_lb unparseable line(s) beside $_lg events — more than the torn-tail contract explains"
        elif [ "${_lb:-0}" -gt 0 ]; then P "calibration ledger" "$_lg events ($_lb torn/malformed line(s) skipped — the appender's torn-tail contract)"
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
    _tsf="$(printf '%s' "$line" | awk '{print $1}')"
    # An unparseable timestamp is its own state — `|| echo 0` made it read
    # "FAIL stale 17xxxxxxxxs", the now-0 sentinel this file removed from the
    # mini leg the same day (review 2026-09-07).
    if ! ts="$(date -d "$_tsf" +%s 2>/dev/null)"; then
        U "$1 verdict" "latest line's timestamp is unparseable ('${_tsf:0:24}') — a torn/NUL-prefixed line? freshness unobservable"; return
    fi
    age=$(( $(date +%s) - ts ))
    status="$(printf '%s' "$line" | awk '{print $3}')"
    msg="$(printf '%s' "$line" | cut -d' ' -f4- | cut -c1-80)"
    # A verdict stamped in the FUTURE read "PASS OK, -86382s ago" (§3 drill
    # 2026-09-07). A negative age is a stepped clock (honest_failure_modes #6),
    # and a verdict whose age cannot be known cannot be called fresh. The
    # writer's own FAIL outranks staleness — an old FAIL is still a FAIL, and
    # the status must not be hidden behind "stale".
    if [ "$age" -lt 0 ]; then U "$1 verdict" "stamped ${age#-}s in the FUTURE — a clock stepped; freshness unobservable"
    elif [ "$status" != "OK" ]; then F "$1 verdict" "$status ${msg:+($msg) }${age}s ago"
    elif [ "$age" -gt "$2" ]; then F "$1 verdict" "stale ${age}s (>${2}s) — last OK${msg:+: $msg}"
    else P "$1 verdict" "OK${msg:+ ($msg)}, ${age}s ago"; fi
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
#
# ⚠️ 2026-09-09: for months this leg reported ONLY the file's total size, and
# that number reaches no reader. warmstart.handoff_block lifts exactly ONE
# section (the first "## ... START HERE") and caps it at HANDOFF_MAX_CHARS —
# so total file size has NEVER had any bearing on what the next session
# actually sees. The day this was found, the leg FAILED at 115,914B, took a
# mini escalation and a cron FAIL, and was "cured" by rotating 81KB out of
# the file: the handoff the next session receives was byte-for-byte
# unchanged. An honest gate, correctly measuring the wrong quantity.
#
# Both numbers are now reported. File size still FAILS (it drives the
# rotation convention, which keeps the note navigable for a human and for
# grep — a real end). The LIFTED-section size is reported beside it because
# that is the one a reader is actually subject to. Deliberately NOT a second
# failure condition: the truncation now states its own magnitude inline
# (warmstart.py), so an over-long section is disclosed to its reader rather
# than silently eaten, and adding a gate here would be one more instrument
# watching an instrument.
#
# The cap is READ from warmstart.py, never restated here: two consumers of
# one constant must share it or they drift (honest_failure_modes #5, the
# 24,000-vs-24,576 precedent).
if [ -r "$NOTES" ]; then
    nsz="$(wc -c < "$NOTES")"
    cap="$(grep -oE '^HANDOFF_MAX_CHARS = [0-9]+' \
             "$REPO/src/mini_dudeai/warmstart.py" 2>/dev/null \
           | grep -oE '[0-9]+$')"
    if [ -n "$cap" ]; then
        # The section warmstart would lift: first "## ...START HERE", else
        # the first "## " heading; ends at the next "## ".
        # wc -m (CHARACTERS), never wc -c: HANDOFF_MAX_CHARS is a character
        # cap and this text is full of em-dashes and emoji, so bytes overstate
        # it (measured on the live note: 5327B vs 5301 chars). Comparing a
        # byte count against a character cap is the same units-mismatch class
        # this leg was rewritten to stop committing. Within ~2 of Python's
        # len() on the stripped section (trailing newline).
        sec="$(awk '
            /^## / { if (started) exit; if (toupper($0) ~ /START HERE/) { started=1 } }
            started { print; next }
            ' "$NOTES" | wc -m)"
        [ "${sec:-0}" -le 1 ] && sec="$(awk '/^## /{n++} n==1{print}' "$NOTES" | wc -m)"
        if [ "${sec:-0}" -gt "$cap" ]; then
            pctshown=$(( sec > 0 ? cap * 100 / sec : 0 ))
            legible="lifted section ${sec} chars vs ${cap}-char cap — next session sees ~${pctshown}% inline (disclosed, not silent)"
        else
            legible="lifted section ${sec} chars fits the ${cap}-char cap — reaches the next session whole"
        fi
    else
        legible="lifted-section size UNKNOWN — could not read HANDOFF_MAX_CHARS from warmstart.py"
    fi
    if [ "$nsz" -gt 81920 ]; then F "session notes" "${nsz}B >80KB — rotate to archive; $legible"
    else P "session notes" "${nsz}B; $legible"; fi
else
    U "session notes" "absent"
fi

echo
echo "--> ${pass} PASS, ${fail} FAIL, ${unknown} UNKNOWN"
[ "$fail" -gt 0 ] && exit 1
[ "$unknown" -gt 0 ] && exit 2
exit 0
