#!/bin/bash
# fleet_up.sh — the mirror of fleet_down.sh: when power is back, watch the
# boxes return and clear each one's "declared off" posture AS it answers, so
# the monitor stops treating them as deliberately dark. Boxes power on by
# themselves when mains returns; this only settles the paperwork.
#
#   scripts/fleet_up.sh              # every declared box, watch up to 30 min
#   scripts/fleet_up.sh moc4         # just one
#   scripts/fleet_up.sh --wait 3600  # watch longer
set -u
REPO="$(cd "$(dirname "$0")/.." && pwd)"
exec python3 "$REPO/scripts/fleet_power.py" resume "$@" --apply
