#!/usr/bin/env bash
# setup_apcupsd.sh — configure a box to shut ITSELF down on its own UPS.
#
# SCOPE, deliberately narrow: this is the UNPLANNED half of power handling.
# One box, one USB-attached APC, no fleet coordination, no declaration. It is
# not posture and must not be confused with it:
#
#   planned   (storm coming, operator decides) -> fleet_posture.py declare
#             + an ordered shutdown. The fleet stays quiet because the
#             dormancy was DECLARED.
#   unplanned (grid just dropped)              -> THIS. The box halts itself.
#             The fleet then pages DOWN, and that page is CORRECT: an
#             unexpected outage is exactly what should page.
#
# Why this is not "autonomy rung 3". field_ecomm_and_dutycycle_fleet.md bars
# automatic POSTURE entry from battery metrics ("an instrument on floating ADC
# inputs must never drive power posture"). That bar is about an unreliable
# sensor driving a FLEET-WIDE declaration. A vendor UPS reporting on-battery
# over USB HID is not a floating ADC, and a box halting ITSELF declares
# nothing about anyone else. Different claim, different rung. The fleet-wide
# declaration stays operator-pressed.
#
# Usage:
#   scripts/setup_apcupsd.sh --dry-run     # print what would be written
#   scripts/setup_apcupsd.sh --apply       # install + configure + enable
#   scripts/setup_apcupsd.sh --status      # what is configured here now
#
set -euo pipefail

MODE="${1:---dry-run}"
CONF=/etc/apcupsd/apcupsd.conf
DEFAULTS=/etc/default/apcupsd

# --- Tunables, and the reasoning. Override by env before --apply. -----------
#
# THE SHORT-OUTAGE TRAP this file exists to avoid. Halting on the first
# flicker strands the box: it comes down, the outage ends 90 seconds later,
# the UPS never drains, nothing power-cycles the board, and it sits halted on
# a healthy battery until someone walks to it. A long outage recovers by
# itself (UPS drains -> output cuts -> mains returns -> board boots); a SHORT
# one does not. So the rule is: ride out the blip, halt late.
#
# BATTERYLEVEL is the primary trigger, not MINUTES. On a ~10 W Pi load against
# a 300 W-class Back-UPS, the "minutes remaining" figure is extrapolated from
# a load table that does not extend down this far, and typically reads pinned
# or wildly optimistic. Percent is derived from battery voltage and degrades
# more honestly. MINUTES is therefore DISABLED by default rather than trusted.
#
# TIMEOUT (a fixed seconds-on-battery halt) is the most reliable trigger of
# the three -- but only ONCE REAL RUNTIME HAS BEEN MEASURED on this UPS with
# this load. Until that measurement exists, leaving it at 0 is the honest
# setting: a guessed TIMEOUT is a guessed shutdown.
MF_BATTERYLEVEL="${MF_BATTERYLEVEL:-35}"    # halt at <=35% remaining
MF_MINUTES="${MF_MINUTES:-0}"               # 0 = disabled (untrustworthy at low load)
MF_TIMEOUT="${MF_TIMEOUT:-0}"               # 0 = disabled until runtime is measured
MF_ONBATTERYDELAY="${MF_ONBATTERYDELAY:-30}"  # ride out flickers before reacting

die() { echo "REFUSED: $*" >&2; exit 2; }

# --- The guard: no UPS, no install. ----------------------------------------
# A box with apcupsd installed and no UPS attached is a unit that fails on
# every boot, on a fleet whose monitors page on inactive services. That is an
# instrument that can only ever be wrong, so this refuses rather than creating
# one. Fail-loud, not fail-quiet: the refusal names what it looked for.
detect_apc() {
  local found=""
  if command -v lsusb >/dev/null 2>&1; then
    # 051d = American Power Conversion. Match the vendor id, not the marketing
    # name -- the product string varies across the Back-UPS line.
    lsusb 2>/dev/null | grep -qiE '051d:|american power conversion' && found="lsusb(051d)"
  fi
  [ -z "$found" ] && ls /dev/usb/hiddev* >/dev/null 2>&1 && found="hiddev"
  printf '%s' "$found"
}

APC="$(detect_apc)"

case "$MODE" in
  --status)
    echo "apcupsd installed : $(dpkg -l apcupsd 2>/dev/null | awk '/^ii/{print $3; f=1} END{if(!f) print "no"}')"
    echo "APC USB detected  : ${APC:-NO}"
    if command -v apcaccess >/dev/null 2>&1; then
      echo "--- apcaccess ---"; apcaccess status 2>&1 | grep -E 'STATUS|BCHARGE|TIMELEFT|LOADPCT|MODEL' || true
    fi
    exit 0
    ;;
  --dry-run|--apply) : ;;
  *) die "unknown mode '$MODE' (expected --dry-run | --apply | --status)" ;;
esac

# The guard is asymmetric ON PURPOSE, and the asymmetry was found by running it:
# a --dry-run that refuses without hardware makes this file un-reviewable until
# the UPS is physically plugged in -- which is precisely when review is useless.
# A dry run writes nothing and installs nothing, so it PREVIEWS and says loudly
# that it is unverified. Only --apply, which creates a real unit, refuses.
if [ -z "$APC" ] && [ "$MODE" = "--apply" ]; then
  die "no APC UPS found on USB (looked for lsusb vendor 051d and /dev/usb/hiddev*).
         This script configures a box to halt on ITS OWN UPS; with no UPS attached
         it would install a unit that fails on every boot, on a fleet that pages on
         inactive services. Plug the UPS in first, then re-run.
         Check what is attached with:  lsusb | grep -i 051d"
fi

read -r -d '' CONF_BODY <<EOF || true
## MeshForge-managed apcupsd config. Regenerate: scripts/setup_apcupsd.sh --apply
UPSNAME ups
UPSCABLE usb
UPSTYPE usb
DEVICE
LOCKFILE /var/lock
SCRIPTDIR /etc/apcupsd
PWRFAILDIR /etc/apcupsd
NOLOGONDIR /etc

## Halt-late policy -- see the header of setup_apcupsd.sh for why.
ONBATTERYDELAY $MF_ONBATTERYDELAY
BATTERYLEVEL $MF_BATTERYLEVEL
MINUTES $MF_MINUTES
TIMEOUT $MF_TIMEOUT

ANNOY 300
ANNOYDELAY 60
NOLOGON disable
KILLDELAY 0

NETSERVER off
UPSCLASS standalone
UPSMODE disable

STATTIME 0
LOGSTATS off
DATATIME 0
EOF

if [ "$MODE" = "--dry-run" ]; then
  echo "=== DRY RUN — nothing written ==="
  if [ -z "$APC" ]; then
    echo "APC detected     : NO — this is a PREVIEW ONLY."
    echo "                   Nothing here has been checked against a real UPS."
    echo "                   --apply will REFUSE until one is attached."
  else
    echo "APC detected via : $APC"
  fi
  echo "would install    : apcupsd"
  echo "would write      : $CONF"
  echo "would write      : $DEFAULTS  (ISCONFIGURED=yes)"
  echo "would enable     : apcupsd.service"
  echo
  echo "--- $CONF ---"
  printf '%s\n' "$CONF_BODY"
  exit 0
fi

# --- apply ------------------------------------------------------------------
[ "$(id -u)" -eq 0 ] || die "--apply needs root (re-run with sudo)"

if ! dpkg -l apcupsd 2>/dev/null | grep -q '^ii'; then
  echo "installing apcupsd..."
  DEBIAN_FRONTEND=noninteractive apt-get install -y apcupsd
fi

# Back up any existing config before overwriting -- namespaced by timestamp so
# a second run on the same day cannot destroy the first run's copy.
if [ -f "$CONF" ]; then
  bk="$CONF.bak-$(date +%Y%m%dT%H%M%S)"
  cp -n "$CONF" "$bk" && echo "backed up existing config -> $bk"
fi

printf '%s\n' "$CONF_BODY" > "$CONF"
echo "ISCONFIGURED=yes" > "$DEFAULTS"

systemctl enable apcupsd.service
systemctl restart apcupsd.service
sleep 3

echo "--- verification (apcaccess) ---"
if apcaccess status 2>&1 | grep -E 'STATUS|BCHARGE|TIMELEFT|LOADPCT|MODEL'; then
  echo
  echo "CONFIGURED. Two things are still UNPROVEN and need a real drill:"
  echo "  1. pull mains, confirm the box halts at BATTERYLEVEL $MF_BATTERYLEVEL%"
  echo "  2. confirm the UPS cuts output when flat, and the board boots on mains return"
  echo "Until both are OBSERVED, recovery on this box is BELIEVED, not verified."
else
  die "apcupsd started but apcaccess returned nothing -- the daemon is not talking
         to the UPS. Do NOT leave this box in this state: it looks configured and
         protects nothing. Check: journalctl -u apcupsd -n 50"
fi
