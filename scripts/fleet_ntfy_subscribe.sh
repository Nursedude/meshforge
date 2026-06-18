#!/usr/bin/env bash
# fleet_ntfy_subscribe.sh — show a scannable QR + link to subscribe a device
# (phone, uConsole, a second handset) to the fleet ntfy alert topic.
#
# WHY: subscribing a device used to mean copying the topic string off one screen
# and emailing it to the phone — error-prone (a 2026-06-14 mismatch left the
# operator on a stale mf-drill-* topic and the alert channel dark for 4 days).
# Scan-once removes the copy step.
#
# The topic is read from the SAME source as fleet_ntfy_push.sh (the SSOT) so it
# can never drift, and is NEVER hardcoded here (operator-specific value — MF014).
# Read-only: this sends nothing.
#
# Usage:  scripts/fleet_ntfy_subscribe.sh
set -uo pipefail

TOPIC="${MESHFORGE_NTFY_TOPIC:-$(cat "$HOME/.config/fleet_push_topic" 2>/dev/null)}"
if [ -z "$TOPIC" ]; then
    echo "No fleet topic configured: \$MESHFORGE_NTFY_TOPIC unset and" >&2
    echo "  $HOME/.config/fleet_push_topic missing. Configure it first." >&2
    exit 1
fi
URL="https://ntfy.sh/$TOPIC"

echo "Fleet ntfy topic : $TOPIC"
echo "Subscribe URL    : $URL"
echo

# Render a scannable QR if we can; degrade gracefully to the URL alone.
if command -v qrencode >/dev/null 2>&1; then
    qrencode -t ANSIUTF8 "$URL"
elif python3 -c "import qrcode" >/dev/null 2>&1; then
    python3 - "$URL" <<'PY'
import sys, qrcode
qr = qrcode.QRCode(border=2)
qr.add_data(sys.argv[1])
qr.make()
qr.print_ascii(invert=True)
PY
else
    echo "(no QR renderer found — install 'qrencode' or python 'qrcode' for a"
    echo " scannable code; the URL above subscribes any device on its own)"
fi

echo
echo "Scan with the phone camera or the ntfy app -> opens the topic -> Subscribe."
echo "After subscribing, confirm with: any fleet page (or a /code-review etc.)."
