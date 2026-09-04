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
#   6. PATH-AWARE VERDICT (2026-08-11) — a box whose only route is a tunnel
#      through ANOTHER box cannot be judged by that route alone. An ssh failure
#      then observes THE PATH, not the box, and calling it "DOWN" is the same
#      lie note 5 already caught once (bot: blindness reported as "confirms
#      DOWN", 3.5 h of false pages). Proven again 2026-08-10: the T1 restore
#      drill factory-reset the tunnel host, and this monitor paged a box DOWN
#      for 54 min that was never down — it is up 17 days (honest_failure_modes
#      #2, absence of evidence is not evidence of absence).
#      A box may now declare `"via": "<ssh-destination>"` in BOXCONF. When the
#      direct check fails we ask the dependency BEFORE naming a verdict:
#        via UP   -> the box is implicated  -> verdict=down         "DOWN"
#        via DOWN -> we observed the path   -> verdict=unobservable "UNOBSERVABLE"
#      Both still page — suppression would hide a real outage — but the page,
#      the log and the state file all say which claim is being made. The verdict
#      is state field 7 so the mini/watchdog reader words it the same way
#      (honest_failure_modes #5: two consumers, ONE artifact).
#      ⚠️ The `via` probe deliberately does NOT pass -i "$FLEETKEY" or
#      StrictHostKeyChecking=no: a tunnel hop is an ssh-config alias with its own
#      port, user, key and HostKeyAlias. Overriding those would test a path
#      nobody uses and fail-closed into a false "path down".
# Terminal-independent (runs from cron on the manager box). HB -> ~/fleet_offline_hb.log
# Every path is env-overridable so tests can drive the REAL script with fake
# ssh/curl on PATH rather than re-implementing this logic (which was hardened by
# actual incidents and must not be paraphrased).
export PATH="${MESHFORGE_OFFLINE_PATH:-/usr/local/bin:/usr/bin:/bin}"
FLEETKEY="${MESHFORGE_FLEETKEY:-$HOME/.claude/ssh/id_ed25519}"
BOXCONF="${MESHFORGE_OFFLINE_BOXES:-$HOME/.config/meshforge/fleet_offline_boxes.json}"
#   7. DECLARED POSTURE (2026-09-01, DORMANT arc batch 1) — a box that is OFF
#      ON PURPOSE is a fourth state, not a DOWN. Hurricane Lala replayed
#      against this monitor = DOWN x8 hourly for the whole storm, all true,
#      all expected, all noise. The operator now declares dormant/detached
#      boxes in ~/.config/meshforge/fleet_posture.json (SSOT + rules:
#      src/utils/fleet_posture.py — mandatory capped `until`, expiry is the
#      honest default). Effect here, per box:
#        silent (dormant/detached) + unreachable -> "DORMANT [box]" witness
#            line, verdict=<state>, alerted=0, NO page (an open outage is
#            closed by the declaration, logged, never "RECOVERED")
#        silent + REACHABLE  -> POSTURE-DRIFT: declared off but answering —
#            logged + one gentle page per re-alert interval (posture is
#            stale or a battery is being burned); verdict=drift, never DOWN
#        expired / absent file -> exactly today's behaviour
#        UNREADABLE / INVALID file -> loud line, then today's behaviour:
#            a broken declaration must never silence pages (hfm #1)
#      Paths are env-overridable like everything else so tests drive the
#      REAL script (MESHFORGE_FLEET_POSTURE).
POSTURE="${MESHFORGE_FLEET_POSTURE:-$HOME/.config/meshforge/fleet_posture.json}"
LOG="${MESHFORGE_OFFLINE_LOG:-$HOME/fleet_alerts.log}"
WITNESS="${MESHFORGE_OFFLINE_WITNESS:-$HOME/fleet_push_witness.log}"             # delivery receipts / failures
STATE="${MESHFORGE_OFFLINE_STATE:-$HOME/fleet_offline_state.tsv}"              # box \t fail \t alerted \t down_since \t last_alert \t alert_count \t verdict
HB="${MESHFORGE_OFFLINE_HB:-$HOME/fleet_offline_hb.log}"
ALERT_THRESHOLD="${ALERT_THRESHOLD:-3}"            # ~15 min at a */5 cron cadence
REALERT_INTERVAL="${REALERT_INTERVAL:-3600}"       # re-page an ongoing outage every N s (1h)
QUIET_REALERT_INTERVAL="${QUIET_REALERT_INTERVAL:-${BOT_REALERT_INTERVAL:-7200}}"  # tier=quiet: gentler q2hr re-page
ESCALATE_AFTER="${ESCALATE_AFTER:-4}"              # bump priority to urgent at this alert #

# ONE writer at a time (2026-08-11 frontier review). A total-outage tick —
# every box's ssh timing out, ntfy retrying — can outlast the */5 cadence, and
# two overlapped runs interleave $STATE.tmp, a fixed temp name two writers
# share (honest_failure_modes #8: exclude or merge, never interleave). Wait
# out the earlier run briefly; past that, refuse LOUDLY — exit 75 lands as a
# FAIL in cron_verdict rather than this run silently corrupting state. The
# lock precedes TS/NOW so a waited-out run stamps the time it actually ran.
LOCK_WAIT="${MESHFORGE_OFFLINE_LOCK_WAIT:-240}"
exec 9>>"$STATE.lock"
if ! flock -w "$LOCK_WAIT" 9; then
  echo "$(date '+%Y-%m-%d %H:%M:%S %Z')  FLEET: LOCKED-OUT — a previous run still holds $STATE.lock after ${LOCK_WAIT}s; skipping this tick rather than interleaving state" >> "$LOG"
  exit 75
fi

TS=$(date '+%Y-%m-%d %H:%M:%S %Z')
NOW=$(date +%s)
# Heartbeat mtime read BEFORE the touch below overwrites it — it is the only
# record of when this monitor last ran. An absent/empty heartbeat is a FIRST
# RUN, never a gap since the epoch (the 29,806,174-minute lesson: an absent-
# value sentinel must not leak into the measurement domain).
HB_LAST=""
[ -s "$HB" ] && HB_LAST=$(stat -c %Y "$HB" 2>/dev/null || echo "")
touch "$LOG" "$STATE" "$HB" "$WITNESS"

# --- monitor self-absence witness (honest_failure_modes #2 + #9) -------------
# The heartbeat has been WRITTEN since this script existed and never READ, so a
# window in which the monitor ITSELF was not running left no trace in $LOG: a
# reader could not tell "the fleet was healthy" from "nobody was looking". The
# 2026-09-04 UPS shutdown made that concrete — the manager box is inside the
# blast radius of the maintenance it watches, so 10:10→10:20 HST had no watcher
# at all and the alerts log ran straight through the hole. Absence of evidence
# is not evidence of absence: disclose the blind window, never average it away.
# This is a WITNESS, not a page — the operator reads it beside the outage it
# brackets; paging on our own absence would page from the box that was absent.
CADENCE_S="${MESHFORGE_OFFLINE_CADENCE_S:-300}"
GAP_FACTOR="${MESHFORGE_OFFLINE_GAP_FACTOR:-2}"
if [ -n "$HB_LAST" ]; then
  gap=$(( NOW - HB_LAST ))
  if [ "$gap" -lt 0 ]; then
    # Clock went backward (RTC-less Pi, fake-hwclock restore, NTP step). Never
    # render a forged duration — report that the clock moved, not a measurement.
    echo "$TS  FLEET: MONITOR-GAP UNKNOWN — clock moved backward ${gap#-}s since the last heartbeat; the unobserved window cannot be measured. Fleet state before this run is UNKNOWN, not healthy" >> "$LOG"
  elif [ "$gap" -ge $(( CADENCE_S * GAP_FACTOR )) ]; then
    # A reboot EXPLAINS the gap and is the informative case (the manager was
    # down, so its watching was too). A gap with the box up the whole time is a
    # DIFFERENT finding: cron or this script was not running while it could be.
    # Uptime source is overridable so the drill can PIN it — a test that reads
    # the real /proc/uptime of whatever box runs the suite pins nothing (the
    # 2026-07-28 lesson: two probe tests read the live crontab and gave three
    # different verdicts on three boxes).
    up_s=$(cut -d. -f1 "${MESHFORGE_OFFLINE_UPTIME_FILE:-/proc/uptime}" 2>/dev/null || echo "")
    if [ -n "$up_s" ] && [ "$up_s" -lt "$gap" ]; then
      why="this box REBOOTED (uptime ${up_s}s < gap) — the manager was down, so nothing watched the fleet"
    elif [ -z "$up_s" ]; then
      why="uptime unreadable, so the cause is UNKNOWN — do not assume a reboot"
    else
      why="this box was UP the whole time (uptime ${up_s}s) — cron or this monitor was not running"
    fi
    echo "$TS  FLEET: MONITOR-GAP ${gap}s (cadence ${CADENCE_S}s) — $why. Fleet state during that window is UNKNOWN, not healthy" >> "$LOG"
  fi
fi

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
    # Optional dependency path (note 6). An ssh DESTINATION, resolved by the
    # operator's ssh config — not a fleet box name, because the hop may not be
    # a fleet box at all.
    via = str(b.get("via") or "").strip()
    if name and host:
        print("%s;%s;%s;%s;%s" % (name, host, svcs, tier, via))
PYEOF
)
if [ -z "$SSH_USER" ] || [ "${#BOXES[@]}" -eq 0 ]; then
  echo "$TS FATAL box config unusable (ssh_user or boxes empty) — refusing to run" >> "$LOG"
  exit 78
fi

# Declared posture (note 7). ONE reader — the Python SSOT — so this script and
# every other consumer agree on expiry, cap and clock rules by construction.
# Output: "<status>" on line 1, then "name;state;note" per declared box.
# Any failure of the reader itself degrades to a LOUD line + watch everything.
declare -A POSTURE_STATE POSTURE_NOTE
posture_out=$(MESHFORGE_FLEET_POSTURE="$POSTURE" PYTHONPATH="$(dirname "$0")/../src" python3 - <<'PYPOSTURE'
import sys
try:
    from utils import fleet_posture as fp
    p = fp.read_posture()
    print(p.status + (" " + p.detail if p.detail else ""))
    for name, b in sorted(p.boxes.items()):
        print("%s;%s;%s" % (name, b.state, b.note.replace(";", ",")))
except Exception as exc:
    print("reader-error %s: %s" % (type(exc).__name__, exc))
PYPOSTURE
) || posture_out="reader-error rc=$?"
posture_status=$(printf '%s\n' "$posture_out" | sed -n '1p')
case "$posture_status" in
  declared|undeclared) ;;
  *) echo "$TS  FLEET: POSTURE-UNREADABLE $POSTURE — $posture_status; watching EVERY box (a broken declaration must not silence pages)" >> "$LOG" ;;
esac
if [ "$posture_status" = declared ]; then
  while IFS=';' read -r pname pstate pnote; do
    [ -z "$pname" ] && continue
    POSTURE_STATE["$pname"]="$pstate"; POSTURE_NOTE["$pname"]="$pnote"
  done < <(printf '%s\n' "$posture_out" | sed '1d')
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
  g_verdict=$(printf '%s' "$line" | cut -f7)
  [ -z "$g_fail" ] && g_fail=0
  [ -z "$g_alerted" ] && g_alerted=0
  [ -z "$g_down" ] && g_down=0
  [ -z "$g_lastalert" ] && g_lastalert=0
  [ -z "$g_count" ] && g_count=0
  # Back-compat: rows written before note 6 have 6 fields. "down" is what those
  # rows MEANT, so defaulting to it preserves their claim exactly — it does not
  # invent an observation.
  [ -z "$g_verdict" ] && g_verdict=down
}

set_state() {  # box fail alerted down_since last_alert alert_count verdict
  grep -vP "^$1\t" "$STATE" > "$STATE.tmp" 2>/dev/null || true
  # ${7:?} not ${7:-down}: a caller that forgets the verdict is a BUG, and the
  # old default silently wrote the STRONGER claim — the exact laundering the
  # probe side refuses. Fail loud instead; the aborted run leaves $STATE
  # unswapped and cron_verdict records the nonzero exit (2026-08-11 review).
  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$1" "$2" "$3" "$4" "$5" "$6" "${7:?BUG: set_state called without a verdict}" >> "$STATE.tmp"
  mv "$STATE.tmp" "$STATE"
}

for entry in "${BOXES[@]}"; do
  IFS=';' read -r name host svcs tier via <<< "$entry"
  reason=""
  verdict="down"
  if ! timeout 15 ssh -i "$FLEETKEY" -o StrictHostKeyChecking=no -o ConnectTimeout=8 \
        -o BatchMode=yes "$SSH_USER@$host" "true" 2>/dev/null; then
    # An ssh failure observes ONE PATH, not a box state (note 6). Only a box
    # that declares its dependency can have that distinction drawn for it;
    # everyone else keeps the historical verdict, unchanged.
    if [ -n "$via" ]; then
      if timeout 15 ssh -o BatchMode=yes -o ConnectTimeout=8 "$via" true 2>/dev/null; then
        reason="UNREACHABLE (ssh failed; dependency path via $via is UP, so the box itself is implicated)"
      else
        # We reached neither. The one thing actually OBSERVED is the broken
        # path — so that is the only thing this page is allowed to claim.
        reason="UNOBSERVABLE (dependency path via $via is DOWN; box not reached and NOT observed down — state UNKNOWN)"
        verdict="unobservable"
      fi
    else
      reason="UNREACHABLE (ssh failed)"
    fi
  else
    bad=$(timeout 18 ssh -i "$FLEETKEY" -o StrictHostKeyChecking=no -o ConnectTimeout=8 \
          "$SSH_USER@$host" "for s in ${svcs//,/ }; do [ \"\$(systemctl is-active \$s.service 2>/dev/null)\" = active ] || echo \$s; done" 2>/dev/null | tr '\n' ' ')
    [ -n "${bad// /}" ] && reason="service(s) inactive: $bad"
    # VIA SELF-CHECK (2026-08-11 frontier review). A broken via DECLARATION —
    # typo'd alias, ssh config missing on this box — is indistinguishable from
    # "path down" at outage time, so every REAL outage of this box would page
    # the demoted UNOBSERVABLE claim forever, unwitnessed (the 08-05 "detector
    # keyed to the wrong name reads healthy" class). The one free cross-check:
    # a box whose ONLY route transits the hop was just reached directly, so
    # the path through the hop is PROVEN functional — a failing via probe on
    # this same tick can only indict the declaration (or a flake; the line
    # self-clears by not recurring). Witness, not verdict: nothing pages here.
    if [ -n "$via" ]; then
      if ! timeout 15 ssh -o BatchMode=yes -o ConnectTimeout=8 "$via" true 2>/dev/null; then
        echo "$TS  FLEET: VIACONF-SUSPECT [$name] direct ssh OK but declared dependency '$via' did not answer — if this recurs, the via entry in $BOXCONF (or this box's ssh config for '$via') is broken, and the NEXT real outage of $name will be misreported UNOBSERVABLE instead of DOWN" >> "$LOG"
      fi
    fi
  fi

  read_state "$name"
  # DECLARED POSTURE (note 7) — evaluated AFTER the probe, never instead of it:
  # a silent box that ANSWERS is the finding (posture drift), so we must look.
  pstate="${POSTURE_STATE[$name]:-active}"
  if [ "$pstate" = dormant ] || [ "$pstate" = detached ]; then
    pnote="${POSTURE_NOTE[$name]:-declared $pstate}"
    if [ -z "$reason" ]; then
      echo "$TS  FLEET: POSTURE-DRIFT [$name] $pnote — but it ANSWERED (ssh ok, services up): the declaration is stale or the box is burning power it was meant to save" >> "$LOG"
      if [ "$g_verdict" != drift ] || [ $(( NOW - g_lastalert )) -ge "$REALERT_INTERVAL" ]; then
        ntfy_push "Fleet posture DRIFT: $name" "default" "warning" \
          "$name answered while $pnote — clear or renew the declaration ($TS)" \
          || echo "$TS  FLEET: PUSH-FAILED on POSTURE-DRIFT [$name] — see witness log" >> "$LOG"
        set_state "$name" 0 0 0 "$NOW" 0 drift
      else
        set_state "$name" 0 0 0 "$g_lastalert" 0 drift
      fi
    else
      if [ "$g_alerted" = "1" ]; then
        echo "$TS  FLEET: DORMANT [$name] $pnote — supersedes the open outage (was paged as $g_verdict); no RECOVERED page, nothing recovered" >> "$LOG"
      else
        echo "$TS  FLEET: DORMANT [$name] $pnote — not paged" >> "$LOG"
      fi
      set_state "$name" 0 0 0 0 0 "$pstate"
    fi
    continue
  fi
  # A box coming OUT of a declared-silent verdict is watched again from a
  # clean row: its old fail/alert counters belonged to a different claim.
  if [ "$g_verdict" = dormant ] || [ "$g_verdict" = detached ] || [ "$g_verdict" = drift ]; then
    echo "$TS  FLEET: POSTURE-LIFTED [$name] was $g_verdict; watching again" >> "$LOG"
    g_fail=0; g_alerted=0; g_down=0; g_lastalert=0; g_count=0
  fi
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
  # The page must make the SAME claim the verdict does. An unobservable box is
  # not asserted down anywhere the operator reads: title, log line, recovery.
  if [ "$verdict" = unobservable ]; then
    page_first="Fleet box UNOBSERVABLE"; page_still="Fleet box STILL UNOBSERVABLE"
  else
    page_first="Fleet box DOWN"; page_still="Fleet box STILL DOWN"
  fi
  if [ -z "$reason" ]; then
    # healthy
    if [ "$g_alerted" = "1" ]; then
      downmin=$(( (NOW - g_down) / 60 ))
      # Recovery describes what ENDED. A box we could never see did not "come
      # back up" — its path came back; saying otherwise would retroactively
      # assert the outage we just refused to claim.
      if [ "$g_verdict" = unobservable ]; then
        was="reachable again (path restored; was unobservable ~${downmin}m)"
      else
        was="healthy again (was down ~${downmin}m)"
      fi
      echo "$TS  FLEET: RECOVERED [$name] $was" >> "$LOG"
      ntfy_push "Fleet box RECOVERED: $name" "default" "white_check_mark" \
        "$name $was ($TS)" \
        || echo "$TS  FLEET: PUSH-FAILED on RECOVERED [$name] — see witness log" >> "$LOG"
    fi
    set_state "$name" 0 0 0 0 0 down
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
      if ntfy_push "$page_first: $name" "$alert_prio" "$alert_tag" \
           "$name: $reason (failed ${fail}x, ~$((ALERT_THRESHOLD*5))min)"; then
        set_state "$name" "$fail" 1 "$NOW" "$NOW" 1 "$verdict"
      else
        echo "$TS  FLEET: PUSH-FAILED on ALERT [$name] — see witness log; keeping alerted=0 so the next run retries the FIRST page" >> "$LOG"
        # Keep alerted=0: the outage is still unannounced, so the next */5 tick
        # must re-enter THIS branch rather than fall into the hourly re-page.
        set_state "$name" "$fail" 0 0 0 0 "$verdict"
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
      # "still down" is a claim; on the unobservable verdict it is the WRONG one,
      # and a re-page repeats it every interval for as long as the path is dark.
      if [ "$verdict" = unobservable ]; then
        still_body="$name still unobservable ~${downmin}m: $reason (page #$count)"
      else
        still_body="$name still down ~${downmin}m: $reason (page #$count)"
      fi
      if ntfy_push "$page_still: $name (~${downmin}m)" "$prio" "$alert_tag" \
           "$still_body"; then
        set_state "$name" "$fail" 1 "$g_down" "$NOW" "$count" "$verdict"
      else
        echo "$TS  FLEET: PUSH-FAILED on STILL-DOWN [$name] — see witness log; last_alert held so the next run retries" >> "$LOG"
        set_state "$name" "$fail" 1 "$g_down" "$g_lastalert" "$g_count" "$verdict"
      fi
    else
      # failing-but-below-threshold, OR down-and-already-paged-not-yet-re-alert-time
      # $verdict, not $g_verdict: a path that came back mid-outage must promote
      # the row to a real "down", and one that just broke must demote it.
      set_state "$name" "$fail" "$g_alerted" "$g_down" "$g_lastalert" "$g_count" "$verdict"
    fi
  fi
done
echo "$TS ran" >> "$HB"; tail -300 "$HB" > "$HB.tmp" 2>/dev/null && mv "$HB.tmp" "$HB"
