#!/usr/bin/env bash
# psk_leak_guard — PreToolUse(Bash) hook that blocks the known commands that
# would surface a Meshtastic channel PSK (or a channel URL, which encodes the
# PSK) into the session transcript.
#
# Born 2026-07-10: channel PSKs leaked to a session transcript twice via the
# same vector (printing `meshtastic --info` output where a grep swept the
# "psk" field). A memory rule failed to stop it; this hook enforces at the
# harness layer. Read paths go through scripts/mesh_psk_safe.py, which redacts.
#
# SCOPE (be honest — this closes vectors, not the whole class): it inspects the
# Bash command STRING only. It does NOT see the Read tool, a `bash script.sh`
# whose body dumps keys, a `python3 -` heredoc using the meshtastic library, or
# runtime shell-variable indirection. Those remain the operator's discipline;
# this hook is defense-in-depth for the historically-observed shell vectors.
#
# Protocol: reads the PreToolUse JSON on stdin, emits a deny decision as JSON
# when the Bash command matches a leak pattern. Fail-CLOSED on a positive match.
# Fails OPEN on a payload it cannot parse — but LEAVES A WITNESS (a marker file a
# probe/operator can see) so a harness JSON-shape change can't silently and
# permanently disable the guard (honest_failure_modes point 9).

payload="$(cat)"

# Extract the Bash command. Exit 0 = "not a Bash call, nothing to guard".
# Exit 3 = "could not parse the payload" — a witnessed fail-open, not silence.
cmd="$(printf '%s' "$payload" | python3 -c 'import json,sys
try:
    d = json.load(sys.stdin)
except Exception:
    sys.exit(3)
if d.get("tool_name") != "Bash":
    sys.exit(0)
print(d.get("tool_input", {}).get("command", ""))' 2>/dev/null)"
parse_rc=$?

if [ "$parse_rc" = 3 ]; then
  # Fail OPEN (allow) but WITNESS the blind spot so it is observable.
  witness="${CLAUDE_PROJECT_DIR:-$HOME}/.claude/psk_leak_guard_failopen.log"
  { printf '%s parse-failed payload_len=%s\n' \
      "$(date -u +%FT%TZ 2>/dev/null || echo unknown-time)" "${#payload}" \
      >> "$witness"; } 2>/dev/null || true
  exit 0
fi

[ -z "$cmd" ] && exit 0

# Exempt ONLY a bare, un-chained invocation of the sanctioned wrapper (it
# redacts internally). Anchored to the START of the command (optional
# sudo/python3 prefix) and forbidding any chain/pipe/command-substitution
# operator, so `wrapper && meshtastic --info` or `echo mesh_psk_safe.py;
# meshtastic --info` are NOT exempt and still face the deny scan below.
# (Fixed 2026-07-11: the old `*mesh_psk_safe.py*` substring glob exempted any
# command merely containing the string — a one-semicolon bypass.)
if [[ "$cmd" =~ ^[[:space:]]*(sudo[[:space:]]+)?((python3?|/usr/bin/env[[:space:]]+python3?)[[:space:]]+)?[^[:space:]]*mesh_psk_safe\.py([[:space:]]|$) ]] \
   && [[ ! "$cmd" =~ [\;\&\|\`] ]] && [[ "$cmd" != *'$('* ]]; then
  exit 0
fi

reason=""
# 1. inline base64 PSK literal — 32-byte (43 b64 + '=') OR 16-byte (22 b64 +
#    '=='). Bounded by non-base64 on both sides so a long continuous base64 run
#    (ssh public key, a base64-encoded file transfer) does NOT false-match a
#    43-char window inside it (fixed 2026-07-11: was an unanchored {43}= that
#    both false-denied ssh keys and MISSED 16-byte AES-128 keys entirely).
if printf '%s' "$cmd" | grep -Eq '(^|[^A-Za-z0-9+/])([A-Za-z0-9+/]{43}=|[A-Za-z0-9+/]{22}==)([^A-Za-z0-9+/=]|$)'; then
  reason="command contains an inline base64 key literal (a PSK). Set keys from a FILE via scripts/mesh_psk_safe.py setpsk."
# 2. channel URL literal (encodes PSKs)
elif printf '%s' "$cmd" | grep -Eq 'meshtastic\.org/e/#'; then
  reason="command contains a Meshtastic channel URL (encodes PSKs). Use scripts/mesh_psk_safe.py; keep URLs out of the transcript."
# 3. raw channel-dumping meshtastic invocations — anchored to meshtastic at a
#    COMMAND position (start, or after ;|&) so a `grep 'meshtastic --info' …`
#    maintenance sweep is not false-denied (fixed 2026-07-11: was match-anywhere).
elif printf '%s' "$cmd" | grep -Eq '(^|[;&|])[[:space:]]*(sudo[[:space:]]+)?meshtastic[[:space:]].*(--info|--export-config|--qr)\b'; then
  reason="raw 'meshtastic --info/--export-config/--qr' can print PSKs. Use: scripts/mesh_psk_safe.py info <host>  (psk + URLs auto-redacted)."
# 4. --seturl with an inline URL
elif printf '%s' "$cmd" | grep -Eq 'seturl.*http'; then
  reason="--seturl with an inline URL puts a PSK-bearing URL in the transcript. Apply channel URLs from a file via the wrapper."
# 5. touching the raw key store
elif printf '%s' "$cmd" | grep -Eq 'channels\.proto'; then
  reason="channels.proto is raw key material. Read channels via scripts/mesh_psk_safe.py info <host> instead."
fi

if [ -n "$reason" ]; then
  printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":%s}}\n' \
    "$(printf '%s' "PSK-leak guard: $reason" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))')"
  exit 0
fi

exit 0
