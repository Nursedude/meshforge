#!/usr/bin/env bash
# Fleet-offline monitor — per-box reachability + key-service liveness for the 5
# fleet boxes, using the right "doing its job" signal per role:
#   moc, moc3  : meshtasticd + meshforge-gateway      (gateways)
#   moc1, moc2 : meshtasticd + meshforge-maps         (map/cloud; gateway off by design)
#   bot (.32)  : mesh_bot                             (no local meshtasticd — uses Borg)
#
# Hardened 2026-06-17 (Leg D, the ".32 dark for 33h, silent" fix). Three changes
# over the original fire-once/unwitnessed design:
#   1. WITNESSED delivery  — ntfy push checks HTTP 2xx + a returned message id,
#      retries on failure, and logs a witness line (PUSH-OK id=.. / PUSH-FAILED)
#      to ~/fleet_push_witness.log. A page can no longer vanish unnoticed.
#   2. RE-ALERT cadence    — while a box stays down it re-pages every
#      REALERT_INTERVAL (default 1h), escalating priority, instead of firing once.
#      A 33h outage now produces ~33 nudges, not one (also defeats ntfy's ~12h TTL).
#   3. RICHER state        — state file tracks down_since + last_alert + alert_count
#      so Piece 2 (mini signal / watchdog probe / /fleet) can read live posture.
#   4. PER-BOX TIER (2026-06-19) — the bot (.32) is a non-critical hobby mesh bot
#      on a 426 MB Pi Zero W; its swap-thrash wedge root cause is fixed (desktop
#      + stray meshforge-maps removed 06-17/19, box steady since). So it now
#      pages GENTLY: "default" ntfy priority (no urgent escalation) and a q2hr
#      re-alert instead of 1h. Detection is UNCHANGED (still */5, 3x ~15 min) so
#      a real bot outage is still caught — it is only notified without alarming
#      ("don't need that level of notification", operator 06-19). Production
#      gateways/maps keep full high/urgent + 1h re-alert.
#   5. REPO-TRACKED (2026-07-29) — moved out of the operator home into scripts/
#      after THREE defects of one class surfaced in it in a single day: bot's
#      address was stale, bot's verdict was blindness reported as "confirms
#      DOWN", and moc1 was a hardcoded NAT-front IP. Untracked meant no lint, no
#      tests, and no survival across a box rebuild. Membership + the ssh user now
#      live in ~/.config/meshforge/fleet_offline_boxes.json (operator values,
#      never committed — MF014); this file is portable and gated.
#      ⚠️ That box list is a THIRD copy of fleet membership (fleet_hosts has its
#      own, longer, list). Deliberately NOT unified here: unifying would silently
#      widen who gets paged. Reconcile as its own decision.
# Terminal-independent (runs from cron on the manager box). HB -> ~/fleet_offline_hb.log
# Every path is env-overridable so tests can drive the REAL script with fake
# ssh/curl on PATH rather than re-implementing this logic (which was hardened by
# actual incidents and must not be paraphrased).
export PATH="${MESHFORGE_OFFLINE_PATH:-/usr/local/bin:/usr/bin:/bin}"
FLEETKEY="${MESHFORGE_FLEETKEY:-$HOME/.claude/ssh/id_ed25519}"
BOXCONF="${MESHFORGE_OFFLINE_BOXES:-$HOME/.config/meshforge/fleet_offline_boxes.json}"
LOG="${MESHFORGE_OFFLINE_LOG:-$HOME/fleet_alerts.log}"
WITNESS="${MESHFORGE_OFFLINE_WITNESS:-$HOME/fleet_push_witness.log}"             # delivery receipts / failures
STATE="${MESHFORGE_OFFLINE_STATE:-$HOME/fleet_offline_state.tsv}"              # box \t fail \t alerted \t down_since \t last_alert \t alert_count
HB="${MESHFORGE_OFFLINE_HB:-$HOME/fleet_offline_hb.log}"
ALERT_THRESHOLD="${ALERT_THRESHOLD:-3}"            # ~15 min at a */5 cron cadence
REALERT_INTERVAL="${REALERT_INTERVAL:-3600}"       # re-page an ongoing outage every N s (1h)
QUIET_REALERT_INTERVAL="${QUIET_REALERT_INTERVAL:-${BOT_REALERT_INTERVAL:-7200}}"  # tier=quiet: gentler q2hr re-page
ESCALATE_AFTER="${ESCALATE_AFTER:-4}"              # bump priority to urgent at this alert #
TS=$(date '+%Y-%m-%d %H:%M:%S %Z')
NOW=$(date +%s)
touch "$LOG" "$STATE" "$HB" "$WITNESS"

# Active push via ntfy. Topic from env override (for drills against a throwaway
# topic — see reference_fleet_drill_throwaway_topic) else ~/.config/fleet_push_topic.
NTFY_TOPIC="${MESHFORGE_NTFY_TOPIC:-$(cat "$HOME/.config/fleet_push_topic" 2>/dev/null)}"

ntfy_push() {  # title priority tags message  -> returns 0 only on confirmed delivery
  local title="$1" prio="$2" tags="$3" msg="$4"
  if [ -z "$NTFY_TOPIC" ]; then
    echo "$TS PUSH-SKIP no topic configured title=\"$title\"" >> "$WITNESS"
    return 1
  fi
  local attempt body rc http id
  for attempt in 1 2 3; do
    # --data-raw (not -d): a message starting with '@' would otherwise be read as a file
    body=$(curl -s --max-time 12 -w '\n%{http_code}' \
      -H "Title: $title" -H "Priority: $prio" -H "Tags: $tags" \
      --data-raw "$msg" "https://ntfy.sh/$NTFY_TOPIC" 2>/dev/null)
    rc=$?
    http=$(printf '%s' "$body" | tail -n1)
    id=$(printf '%s' "$body" | sed -n 's/.*"id"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -n1)
    if [ "$rc" = 0 ] && printf '%s' "$http" | grep -q '^2' && [ -n "$id" ]; then
      echo "$TS PUSH-OK id=$id http=$http attempt=$attempt prio=$prio title=\"$title\"" >> "$WITNESS"
      return 0
    fi
    echo "$TS PUSH-RETRY attempt=$attempt rc=$rc http=${http:-none} title=\"$title\"" >> "$WITNESS"
  done
  echo "$TS PUSH-FAILED after 3 attempts title=\"$title\" msg=\"$msg\"" >> "$WITNESS"
  return 1
}

# Membership, ssh user and per-box paging tier come from BOXCONF — operator
# values stay out of the repo (MF014), and a name can never again be baked into
# logic. Absent/unreadable config is a LOUD refusal, never an empty sweep: an
# empty box list would report "nothing is down" about a fleet it never checked
# (honest_failure_modes #1 — the degraded value overlapping the healthy domain).
if [ ! -r "$BOXCONF" ]; then
  echo "$TS FATAL no box config at $BOXCONF — refusing to run (an empty sweep would look identical to a healthy fleet)" >> "$LOG"
  exit 78
fi
SSH_USER=$(python3 -c 'import json,sys; print((json.load(open(sys.argv[1])).get("ssh_user") or ""))' "$BOXCONF" 2>/dev/null)
mapfile -t BOXES < <(python3 - "$BOXCONF" <<'PYEOF'
import json, sys
doc = json.load(open(sys.argv[1]))
for b in doc.get("boxes") or []:
    name = str(b.get("name") or "").strip()
    host = str(b.get("host") or name).strip()
    svcs = ",".join(str(x) for x in (b.get("services") or []))
    tier = str(b.get("tier") or "critical").strip()
    if name and host:
        print("%s;%s;%s;%s" % (name, host, svcs, tier))
PYEOF
)
if [ -z "$SSH_USER" ] || [ "${#BOXES[@]}" -eq 0 ]; then
  echo "$TS FATAL box config unusable (ssh_user or boxes empty) — refusing to run" >> "$LOG"
  exit 78
fi

# read all state fields for a box into globals (back-compat: old 3-field rows -> 0)
read_state() {  # box
  local line
  line=$(awk -F'\t' -v b="$1" '$1==b{print; f=1; exit} END{if(!f)print ""}' "$STATE")
  g_fail=$(printf '%s' "$line" | cut -f2)
  g_alerted=$(printf '%s' "$line" | cut -f3)
  g_down=$(printf '%s' "$line" | cut -f4)
  g_lastalert=$(printf '%s' "$line" | cut -f5)
  g_count=$(printf '%s' "$line" | cut -f6)
  [ -z "$g_fail" ] && g_fail=0
  [ -z "$g_alerted" ] && g_alerted=0
  [ -z "$g_down" ] && g_down=0
  [ -z "$g_lastalert" ] && g_lastalert=0
  [ -z "$g_count" ] && g_count=0
}

set_state() {  # box fail alerted down_since last_alert alert_count
  grep -vP "^$1\t" "$STATE" > "$STATE.tmp" 2>/dev/null || true
  printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$1" "$2" "$3" "$4" "$5" "$6" >> "$STATE.tmp"
  mv "$STATE.tmp" "$STATE"
}

for entry in "${BOXES[@]}"; do
  IFS=';' read -r name host svcs tier <<< "$entry"
  reason=""
  if ! timeout 15 ssh -i "$FLEETKEY" -o StrictHostKeyChecking=no -o ConnectTimeout=8 \
        -o BatchMode=yes "$SSH_USER@$host" "true" 2>/dev/null; then
    reason="UNREACHABLE (ssh failed)"
  else
    bad=$(timeout 18 ssh -i "$FLEETKEY" -o StrictHostKeyChecking=no -o ConnectTimeout=8 \
          "$SSH_USER@$host" "for s in ${svcs//,/ }; do [ \"\$(systemctl is-active \$s.service 2>/dev/null)\" = active ] || echo \$s; done" 2>/dev/null | tr '\n' ' ')
    [ -n "${bad// /}" ] && reason="service(s) inactive: $bad"
  fi

  read_state "$name"
  # Per-box alerting tier (header note 4): the bot pages gently — "default"
  # priority, no urgent escalation, q2hr re-alert. Everyone else keeps full
  # high/urgent + 1h. Detection (reachability + 3x threshold) is identical.
  if [ "$tier" = quiet ]; then
    # tier comes from BOXCONF. Previously this was `if [ "$name" = bot ]` — a box
    # NAME baked into logic, so the tier could not follow a rename and no other
    # box could ever be quiet. Same defect class as the addresses fixed 07-29.
    box_realert="$QUIET_REALERT_INTERVAL"; alert_prio="default"; alert_tag="warning"; quiet_box=1
  else
    box_realert="$REALERT_INTERVAL"; alert_prio="high"; alert_tag="rotating_light"; quiet_box=0
  fi
  if [ -z "$reason" ]; then
    # healthy
    if [ "$g_alerted" = "1" ]; then
      downmin=$(( (NOW - g_down) / 60 ))
      echo "$TS  FLEET: RECOVERED [$name] healthy again (was down ~${downmin}m)" >> "$LOG"
      ntfy_push "Fleet box RECOVERED: $name" "default" "white_check_mark" \
        "$name healthy again after ~${downmin}m ($TS)" \
        || echo "$TS  FLEET: PUSH-FAILED on RECOVERED [$name] — see witness log" >> "$LOG"
    fi
    set_state "$name" 0 0 0 0 0
  else
    fail=$((g_fail + 1))
    if [ "$fail" -ge "$ALERT_THRESHOLD" ] && [ "$g_alerted" != "1" ]; then
      # first alert for this outage
      echo "$TS  FLEET: ALERT [$name] $reason (failed ${fail}x consecutive)" >> "$LOG"
      # DELIVERY GATES THE STATE (2026-07-29 review). ntfy_push returns 0 only on
      # CONFIRMED delivery (2xx + a message id, after 3 retries). Advancing to
      # alerted=1 regardless meant a first page lost to a brief ntfy.sh dip was
      # never retried: the next tick skips this branch (alerted=1) and the
      # STILL-DOWN branch waits out the full re-alert interval, so a REAL outage
      # produced zero pages for 1 h (2 h on the quiet tier). honest_failure_modes
      # #1 — "push failed" mapped to the valid-looking value "operator notified"
      # — inside the monitor whose 06-17 hardening promised a page can no longer
      # vanish unnoticed.
      if ntfy_push "Fleet box DOWN: $name" "$alert_prio" "$alert_tag" \
           "$name: $reason (failed ${fail}x, ~$((ALERT_THRESHOLD*5))min)"; then
        set_state "$name" "$fail" 1 "$NOW" "$NOW" 1
      else
        echo "$TS  FLEET: PUSH-FAILED on ALERT [$name] — see witness log; keeping alerted=0 so the next run retries the FIRST page" >> "$LOG"
        # Keep alerted=0: the outage is still unannounced, so the next */5 tick
        # must re-enter THIS branch rather than fall into the hourly re-page.
        set_state "$name" "$fail" 0 0 0 0
      fi
    elif [ "$g_alerted" = "1" ] && [ $(( NOW - g_lastalert )) -ge "$box_realert" ]; then
      # ongoing outage -> re-page (escalate), defeats fire-once + ntfy TTL age-off
      count=$((g_count + 1))
      downmin=$(( (NOW - g_down) / 60 ))
      prio="$alert_prio"; [ "$quiet_box" = 0 ] && [ "$count" -ge "$ESCALATE_AFTER" ] && prio="urgent"
      echo "$TS  FLEET: STILL-DOWN [$name] $reason (~${downmin}m, page #$count)" >> "$LOG"
      # Same gate, same reason: stamping last_alert=NOW on an undelivered re-page
      # buys the outage another full silent interval, and advancing $count would
      # inflate the page number past what was actually delivered (and could reach
      # ESCALATE_AFTER without a single page landing).
      if ntfy_push "Fleet box STILL DOWN: $name (~${downmin}m)" "$prio" "$alert_tag" \
           "$name still down ~${downmin}m: $reason (page #$count)"; then
        set_state "$name" "$fail" 1 "$g_down" "$NOW" "$count"
      else
        echo "$TS  FLEET: PUSH-FAILED on STILL-DOWN [$name] — see witness log; last_alert held so the next run retries" >> "$LOG"
        set_state "$name" "$fail" 1 "$g_down" "$g_lastalert" "$g_count"
      fi
    else
      # failing-but-below-threshold, OR down-and-already-paged-not-yet-re-alert-time
      set_state "$name" "$fail" "$g_alerted" "$g_down" "$g_lastalert" "$g_count"
    fi
  fi
done
echo "$TS ran" >> "$HB"; tail -300 "$HB" > "$HB.tmp" 2>/dev/null && mv "$HB.tmp" "$HB"
