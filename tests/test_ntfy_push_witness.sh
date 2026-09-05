#!/usr/bin/env bash
# Behavior tests for scripts/fleet_ntfy_push.sh — the send WITNESS (2026-09-05).
#
# Why these exist: before the witness, a failed page and a successful page left
# identical traces (none), because the script was
# `curl ... >/dev/null 2>&1 || true; exit 0`. The operator found out by noticing
# silence. These pin the distinctions that make the witness worth having:
#   - a send that FAILS is recorded as failed, even though the script still
#     exits 0 (paging stays advisory — the exit code is NOT the evidence)
#   - HTTP 500 with curl exit 0 is a FAILURE, not a success
#   - "no topic" is INERT, a different claim from both ok and failed
#   - a drill topic writes to a DIFFERENT state file, so a throwaway success
#     can never vouch for the real channel
#   - --check reports, and its exit code separates healthy/failing/unobservable
#
# curl is stubbed on PATH — no network.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
SCRIPT="$HERE/../scripts/fleet_ntfy_push.sh"
fails=0

ok()   { echo "PASS: $1"; }
bad()  { echo "FAIL: $1"; fails=$((fails+1)); }
check(){ if [ "$2" = "$3" ]; then ok "$1"; else bad "$1 (want '$3', got '$2')"; fi; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
SB="$TMP/stubs"; mkdir -p "$SB"

# curl stub: emits $STUB_HTTP as the -w body, exits $STUB_RC.
cat >"$SB/curl" <<'EOF'
#!/bin/bash
printf '%s' "${STUB_HTTP-200}"
exit "${STUB_RC:-0}"
EOF
chmod +x "$SB/curl"

field() {  # field <state file> <key>
    python3 -c "
import json,sys
try: print(json.load(open(sys.argv[1])).get(sys.argv[2], ''))
except Exception: print('<unreadable>')
" "$1" "$2"
}

send() {  # send <state> <http> <rc>
    STUB_HTTP="$2" STUB_RC="$3" PATH="$SB:$PATH" \
    MESHFORGE_NTFY_PUSH_STATE="$1" MESHFORGE_NTFY_TOPIC=faketopic \
        bash "$SCRIPT" "drill title" high warning "body" >/dev/null 2>&1
    echo $?
}

# ── 1. a successful publish is recorded, script still exits 0 ───────────────
S="$TMP/ok.json"
check "successful send exits 0" "$(send "$S" 200 0)" "0"
check "  recorded ok"           "$(field "$S" last_status)" "ok"
check "  http code kept"        "$(field "$S" last_http_code)" "200"
check "  ok counter"            "$(field "$S" sends_ok)" "1"
check "  no consecutive fails"  "$(field "$S" consecutive_failures)" "0"

# ── 2. curl ERROR: exit code stays 0, witness says failed ──────────────────
S="$TMP/curlfail.json"
check "curl failure still exits 0 (advisory)" "$(send "$S" '' 7)" "0"
check "  recorded failed"       "$(field "$S" last_status)" "failed"
check "  failure counted"       "$(field "$S" sends_failed)" "1"
check "  consecutive=1"         "$(field "$S" consecutive_failures)" "1"

# ── 3. THE TRAP: curl exits 0 but the server said 500 ──────────────────────
# curl's exit code is 0 for a 500, a 403, a 404. Trusting it alone would have
# recorded a rejected publish as a successful one.
S="$TMP/http500.json"
send "$S" 500 0 >/dev/null
check "HTTP 500 with curl rc=0 is FAILED" "$(field "$S" last_status)" "failed"
check "  error names the code" \
    "$(field "$S" last_error | grep -c 500)" "1"
S="$TMP/http403.json"; send "$S" 403 0 >/dev/null
check "HTTP 403 is FAILED"      "$(field "$S" last_status)" "failed"
S="$TMP/httpempty.json"; send "$S" '' 0 >/dev/null
check "empty HTTP code is FAILED" "$(field "$S" last_status)" "failed"

# ── 4. consecutive failures accumulate, then RESET on success ──────────────
S="$TMP/streak.json"
send "$S" 500 0 >/dev/null; send "$S" 500 0 >/dev/null; send "$S" 500 0 >/dev/null
check "3 failures accumulate"   "$(field "$S" consecutive_failures)" "3"
send "$S" 200 0 >/dev/null
check "success resets the streak" "$(field "$S" consecutive_failures)" "0"
check "  totals both kept: ok"  "$(field "$S" sends_ok)" "1"
check "  totals both kept: fail" "$(field "$S" sends_failed)" "3"

# ── 5. no topic is INERT, not success and not failure ──────────────────────
S="$TMP/notopic.json"
HOME="$TMP/emptyhome" PATH="$SB:$PATH" MESHFORGE_NTFY_PUSH_STATE="$S" \
    bash "$SCRIPT" t high "" body >/dev/null 2>&1
check "no topic exits 0"        "$?" "0"
check "  recorded no_topic"     "$(field "$S" last_status)" "no_topic"
check "  not counted as a send" "$(field "$S" sends_ok)" ""

# ── 6. a DRILL topic must not write the real state file ────────────────────
# Otherwise a successful send to a throwaway topic would be recorded as
# evidence that the operator's real channel works.
DH="$TMP/drillhome"; mkdir -p "$DH/.config" "$DH/.local/state/meshforge"
echo realtopic > "$DH/.config/fleet_push_topic"
STUB_HTTP=200 STUB_RC=0 PATH="$SB:$PATH" HOME="$DH" MESHFORGE_NTFY_TOPIC=throwaway \
    bash "$SCRIPT" t high "" body >/dev/null 2>&1
if [ -f "$DH/.local/state/meshforge/ntfy_push_state.json" ]; then
    bad "drill send polluted the REAL state file"
else
    ok "drill send left the real state file untouched"
fi
if [ -f "$DH/.local/state/meshforge/ntfy_push_state.drill.json" ]; then
    ok "drill send wrote the drill state file"
else
    bad "drill send wrote no drill state file"
fi

# ── 7. --check: the READER (a witness with no reader is writer-with-no-reader)
# Sets RC and OUT in the PARENT shell. Calling this inside $( ) would run it
# in a subshell and OUT would never come back — which is exactly how the first
# draft of this harness died with "OUT: unbound variable".
RC=0; OUT=""
runcheck() {  # runcheck <state>
    OUT="$(MESHFORGE_NTFY_PUSH_STATE="$1" bash "$SCRIPT" --check 2>&1)"
    RC=$?
}
S="$TMP/chk_ok.json"; send "$S" 200 0 >/dev/null
runcheck "$S"; check "--check healthy exits 0" "$RC" "0"
case "$OUT" in *"accepted"*) ok "  says accepted" ;; *) bad "  wording: $OUT" ;; esac
case "$OUT" in *"not this one"*|*"phone"*) ok "  disclaims phone delivery" ;;
               *) bad "  must not claim delivery: $OUT" ;; esac

S="$TMP/chk_fail.json"
for _ in 1 2 3; do send "$S" 500 0 >/dev/null; done
runcheck "$S"; check "--check failing exits 1" "$RC" "1"

S="$TMP/chk_under.json"; send "$S" 500 0 >/dev/null
runcheck "$S"; check "--check single failure is under threshold (exit 0)" "$RC" "0"
case "$OUT" in *CONCERN*) ok "  reported as CONCERN" ;; *) bad "  wording: $OUT" ;; esac

S="$TMP/chk_missing.json"
runcheck "$S"; check "--check with no state yet exits 0" "$RC" "0"
case "$OUT" in *"no sends recorded"*) ok "  says nothing sent yet" ;;
               *) bad "  wording: $OUT" ;; esac

S="$TMP/chk_corrupt.json"; printf '{not json' > "$S"
runcheck "$S"; check "--check on corrupt state is UNOBSERVABLE (exit 2)" "$RC" "2"

S="$TMP/chk_inert.json"
HOME="$TMP/emptyhome" PATH="$SB:$PATH" MESHFORGE_NTFY_PUSH_STATE="$S" \
    bash "$SCRIPT" t high "" body >/dev/null 2>&1
runcheck "$S"; check "--check inert exits 0" "$RC" "0"
case "$OUT" in *INERT*) ok "  reported as INERT" ;; *) bad "  wording: $OUT" ;; esac

# ── 8. a corrupt prior state is rebuilt, but flagged — never silently reset ─
S="$TMP/recover.json"; printf '{truncated' > "$S"
send "$S" 200 0 >/dev/null
check "corrupt state rebuilt"   "$(field "$S" last_status)" "ok"
check "  and flagged, not silent" "$(field "$S" prior_state_unreadable)" "True"

echo "----"
if [ "$fails" -eq 0 ]; then echo "ALL PASS"; else echo "$fails FAILURE(S)"; fi
exit $((fails > 0))
