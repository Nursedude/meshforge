#!/usr/bin/env bash
# psk_leak_guard — PreToolUse(Bash) hook that makes it IMPOSSIBLE to run a
# command that would surface a Meshtastic channel PSK (or a channel URL, which
# encodes the PSK) into the session transcript.
#
# Born 2026-07-10: channel PSKs leaked to a session transcript twice via the
# same vector (printing `meshtastic --info` output where a grep swept the
# "psk" field). A memory rule failed to stop it; this hook enforces at the
# harness layer. Read paths go through scripts/mesh_psk_safe.py, which redacts.
#
# Protocol: reads the PreToolUse JSON on stdin, emits a deny decision as JSON
# when the Bash command matches a leak pattern. Fails OPEN on parse errors
# (only ever DENIES on a positive match), fail-CLOSED on detection.

payload="$(cat)"

cmd="$(printf '%s' "$payload" | python3 -c 'import json,sys
try:
    d = json.load(sys.stdin)
    if d.get("tool_name") != "Bash":
        sys.exit(0)
    print(d.get("tool_input", {}).get("command", ""))
except Exception:
    sys.exit(0)' 2>/dev/null)"

[ -z "$cmd" ] && exit 0

# The sanctioned wrapper redacts internally — never block it.
case "$cmd" in
  *mesh_psk_safe.py*) exit 0 ;;
esac

reason=""
# 1. inline 32-byte base64 key literal (a pasted PSK)
if printf '%s' "$cmd" | grep -Eq '[A-Za-z0-9+/]{43}='; then
  reason="command contains an inline 32-byte base64 literal (a PSK). Set keys from a FILE via scripts/mesh_psk_safe.py setpsk."
# 2. channel URL literal (encodes PSKs)
elif printf '%s' "$cmd" | grep -Eq 'meshtastic\.org/e/#'; then
  reason="command contains a Meshtastic channel URL (encodes PSKs). Use scripts/mesh_psk_safe.py; keep URLs out of the transcript."
# 3. raw channel-dumping meshtastic invocations
elif printf '%s' "$cmd" | grep -Eq 'meshtastic\b.*(--info|--export-config|--qr\b)'; then
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
