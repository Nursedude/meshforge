#!/usr/bin/env python3
"""bot_deaf_check — is the mesh bot ALIVE but DEAF?

Born 2026-09-04. `mesh_bot` was restarted, came up `active`, logged
"Autoresponder Started", and then received NOTHING for 12 minutes while the
operator sent it commands. `Restart=always` never fired because the process
was healthy; every instrument we had said the bot was fine. The only reason
it surfaced is that a human happened to be testing at that moment.

That is the fleet's recurring defect class in its purest form: PRESENCE
reported as FUNCTION. `systemctl is-active` answers "is the process running",
which is not the question. The question is "is the radio feed reaching it",
and the bot already answers that itself -- it logs a line for every message
it hears.

THE HARD PART (honest_failure_modes #2): bot-heard-nothing and
mesh-was-quiet are the SAME observation from the bot's journal alone. A probe
that pages on silence would cry wolf every quiet night, get muted, and be
useless on the night it matters. So silence is only ever a finding when a
SECOND, INDEPENDENT witness says traffic existed that the bot should have
heard. No corroboration => `clean`, never a page. The blindness is surfaced
as its own state, never folded into health.

VERDICTS (tri-state-plus; `inert` and `unobservable` are different claims):
  clean         bot heard traffic recently, OR the mesh was genuinely quiet
  deaf          corroborator saw traffic, bot heard NONE past the threshold
  inert         the unit is absent or not active -- a *different* probe owns
                that ("service down" is not "service deaf"); never pages here
  unobservable  a box was unreachable or a journal unreadable; NEVER a pass

Exit: 0 clean/inert, 1 deaf (past debounce), 2 unobservable.
Wire it through scripts/cron_verdict.sh like the other periodic checks.

CONFIG (operator values stay out of the repo -- MF014). Absent config is a
LOUD refusal, never an empty check that would look identical to a healthy bot:

    ~/.config/meshforge/bot_deaf_check.json
    {
      "bot_host": "<ssh destination running the bot>",
      "bot_unit": "mesh_bot",
      "corroborator_host": "<ssh destination that sees the same channel>",
      "corroborator_unit": "meshtasticd",
      "corroborator_match": "json/meshforge",
      "deaf_after_s": 600,
      "min_corroboration": 2,
      "debounce_ticks": 2
    }

⚠️ `bot_unit` and `corroborator_unit` are read in SYSTEM scope
(`systemctl show <unit>`, no `--user`). A user-scope unit reads `inert` with
"does not exist on <host>", which is true of system scope and misleading
about the box -- resolve a unit's manager before configuring it here; the
split is per-UNIT on this fleet, not per-box. lehua's `mesh_bot` is a system
unit (verified 2026-09-05).

`corroborator_match` is a PROXY for "traffic the bot should have heard" --
it counts what a box on the same logical channel observed. It cannot prove
the bot was in range of any particular packet, so this probe claims only
"traffic existed and the bot logged none", which is what the 12-minute
outage looked like.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
import time
from typing import Optional, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "src"))

from utils.paths import get_real_user_home  # noqa: E402

CLEAN = "clean"
DEAF = "deaf"
INERT = "inert"
UNOBSERVABLE = "unobservable"

# 10 min, not 30: the founding incident was 12 minutes of deafness, and a
# threshold of 1800 would have slept through it (caught by
# TestDeafness::test_the_incident_this_probe_was_written_for -- the test was
# written first and failed, which is the only reason this number is 600).
DEFAULT_DEAF_AFTER_S = 600
DEFAULT_DEBOUNCE = 2
# A single message the bot missed is RF luck, not deafness. Requiring two
# keeps a stray out of the alarm while still catching the founding incident,
# where the corroborator saw exactly two commands go unanswered.
DEFAULT_MIN_CORROBORATION = 2
SSH_TIMEOUT_S = 25
SSH_TRANSPORT_RC = 255       # ssh's own "I could not connect" exit code
JOURNAL_LOOKBACK_S = 6 * 3600        # bounded: never scan a whole journal

# The bot logs one line per message it hears. Matching the bot's OWN record
# keeps this probe independent of anything we wrote (authorial distance: the
# witness we did not author outranks the one we did).
#
# ⚠️ This pattern was WRONG on first writing and is the reason this probe was
# not shipped on 2026-09-04. It matched the literal `ReceivedChannel`, which
# is only ONE of the five branches mesh_bot.py takes after hearing a packet
# (Received DM / ReceivedChannel / Ignoring DM / Ignoring Message /
# bridge-machinery ACK). Measured against lehua's real journal over 14 days:
# `ReceivedChannel` matched 0 of 9 receptions — so the probe would have read
# "heard nothing, ever" and paged DEAF at a perfectly healthy bot the moment
# the corroborator saw traffic. Same class as the 2026-08-05 `@rns/<name>`
# detector: a checker keyed to a name the subject does not emit reads
# healthy-or-broken by luck, never by observation.
#
# The invariant that actually holds: mesh_bot's per-packet logger emits
# `Device:<n> ...` as the message body, while every non-reception line it
# writes begins `System:`. The formatter puts " | " before the body, so
# " | Device:" anchors on receptions only. Measured: 9/9 receptions matched,
# 0 false positives, 0 lines matched this without a reception verb.
#
# Known deliberate exclusion: `System: Ignoring packet missing 'from' field
# on Device:` (mesh_bot.py:2081) IS technically a reception but is logged
# under the System: prefix. It is a malformed-packet warning; a bot hearing
# ONLY malformed packets is fairly called deaf, so excluding it is the safe
# direction. This is a decision, not an oversight.
RECEPTION_PATTERN = r"\| Device:"
RECEPTION_RE = re.compile(RECEPTION_PATTERN)
# `journalctl -o short-unix` leads every line with epoch seconds.
UNIX_TS_RE = re.compile(r"^(\d+(?:\.\d+)?)\s")


def _cfg_path() -> str:
    env = os.environ.get("MESHFORGE_BOT_DEAF_CONFIG")
    if env:
        return env
    return os.path.join(str(get_real_user_home()), ".config", "meshforge",
                        "bot_deaf_check.json")


def _state_path() -> str:
    env = os.environ.get("MESHFORGE_BOT_DEAF_STATE")
    if env:
        return env
    return os.path.join(str(get_real_user_home()), ".local", "state",
                        "meshforge", "bot_deaf_state.json")


def _ssh(host: str, remote_cmd: str) -> Tuple[int, str]:
    """Run one remote command. Returns (rc, stdout). rc<0 means the transport
    itself failed -- the caller MUST map that to unobservable, never to a
    domain answer (honest_failure_modes #1)."""
    argv = [
        "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
        host, remote_cmd,
    ]
    try:
        r = subprocess.run(argv, capture_output=True, text=True,
                           timeout=SSH_TIMEOUT_S)
    except (subprocess.TimeoutExpired, OSError):
        return -1, ""
    # ssh reports its OWN failures (no route, auth refused, DNS) as 255. A
    # bare `return r.returncode` let that fall through as a domain answer, so
    # an unreachable box reported "systemctl returned no LoadState" -- the
    # right VERDICT (unobservable) blaming the wrong THING. A misread
    # instrument is a bug report against the instrument, not carelessness.
    # `systemctl show` and `journalctl` never exit 255, so 255 with nothing on
    # stdout is unambiguously the transport.
    if r.returncode == SSH_TRANSPORT_RC and not r.stdout.strip():
        return -1, ""
    return r.returncode, r.stdout


def parse_unit_uptime(fields: dict, proc_uptime: str) -> Optional[float]:
    """Seconds the unit has been active, from MONOTONIC anchors only.

    Both inputs come from the BOT box, so this never compares clocks across
    machines and never parses a timezone. systemd's
    `ActiveEnterTimestamp` is a locale string ("Fri 2026-09-04 09:21:51 HST")
    -- parsing that would re-introduce exactly the cross-box timezone defect
    just removed from the journal leg. `ActiveEnterTimestampMonotonic` is
    microseconds since the bot box's own boot, and /proc/uptime is seconds
    since that same boot, so the difference is a pure duration.

    Wall clocks are forgeable on this fleet anyway (honest_failure_modes #6:
    RTC-less Pis, fake-hwclock, NTP steps); lehua is a Zero 2W with no RTC.

    Returns None when the value is absent or nonsensical -- the caller must
    treat that as unobservable, never as "no restart happened".
    """
    raw = fields.get("ActiveEnterTimestampMonotonic", "").strip()
    if not raw:
        return None
    try:
        entered_us = float(raw)
        boot_elapsed_s = float(proc_uptime.split()[0])
    except (ValueError, IndexError):
        return None
    # systemd writes 0 for a unit that has never become active.
    if entered_us <= 0:
        return None
    up = boot_elapsed_s - (entered_us / 1_000_000.0)
    # A unit cannot have started before the boot it is measured against; a
    # negative value means the two anchors disagree, which is knowledge we
    # do not have rather than an uptime of zero.
    if up < 0:
        return None
    return up


def unit_state(host: str, unit: str) -> Tuple[str, str, Optional[float]]:
    """(status, detail, uptime_seconds). status is one of ok/inert/unobservable.

    LoadState=not-found means the unit does not exist here -- the DETECTOR is
    pointed at nothing, which reads `inert`, never a failure and never a
    silent pass (the 2026-08-12 'nothing owned them' class).

    uptime_seconds is how long the CURRENT process has been active, and it is
    load-bearing: `journalctl -u <unit>` spans restarts, so receptions logged
    by a PREVIOUS process would otherwise count as this one having heard
    something. Fetched in the SAME round trip -- one ssh, not two.
    """
    rc, out = _ssh(
        host,
        f"systemctl show {unit} -p LoadState -p ActiveState "
        f"-p ActiveEnterTimestampMonotonic; echo '--'; cat /proc/uptime",
    )
    if rc < 0:
        return UNOBSERVABLE, "ssh transport failed", None
    show, _, proc_uptime = out.partition("--")
    fields = dict(
        line.split("=", 1) for line in show.strip().splitlines() if "=" in line
    )
    load = fields.get("LoadState", "")
    active = fields.get("ActiveState", "")
    if not load:
        return UNOBSERVABLE, "systemctl returned no LoadState", None
    if load == "not-found":
        return INERT, f"unit {unit} does not exist on {host}", None
    if active != "active":
        return (INERT,
                f"unit {unit} is {active} (service-down probes own that)",
                None)
    return "ok", f"{unit} active", parse_unit_uptime(fields, proc_uptime)


def parse_reception_age(line: str, now: float) -> Tuple[str, Optional[float]]:
    """(status, age_seconds) from ONE `journalctl -o short-unix` line.

    Split out as a pure function so the epoch parse can be pinned against
    verbatim real journal lines -- the layer the original tests skipped
    entirely, which is how a pattern matching NOTHING passed 19 green tests.

    A line we cannot date is a parse failure (unobservable), never "heard
    nothing" -- collapsing those two is the honest_failure_modes #1 defect
    that gives a degraded read a healthy-looking value.
    """
    m = UNIX_TS_RE.match(line)
    if not m:
        return UNOBSERVABLE, None
    try:
        t = float(m.group(1))
    except ValueError:
        return UNOBSERVABLE, None
    if t <= 0:
        # Epoch-zero is the absent-value sentinel leaking into the
        # measurement domain (2026-09-02: an age of 29,806,174 minutes).
        # Refuse it rather than report a 57-year-old reception.
        return UNOBSERVABLE, None
    # Clock skew between the two boxes can put the stamp in the future; a
    # negative age would read as "just heard it" and mask real deafness.
    return "ok", max(0.0, now - t)


def last_reception_age(host: str, unit: str, now: float) -> Tuple[str, Optional[float]]:
    """(status, age_seconds_since_last_heard_message).

    age is None when nothing was heard inside the lookback window -- that is
    NOT an error and NOT proof of deafness; the caller decides with the
    corroborator."""
    # -o short-unix, not short-iso: short-iso emits a local offset
    # ("2026-09-05T06:16:32-10:00") whose offset this probe would have to
    # parse, and the original code discarded it and fed the naked stamp to
    # time.mktime() -- which interprets it in the PROBE box's timezone, not
    # the BOT box's. Same-TZ today is luck, not correctness, and it is also
    # ambiguous across a DST fold. short-unix is epoch seconds: no timezone,
    # no locale, no parse.
    cmd = (
        f"journalctl -u {unit} --since '-{JOURNAL_LOOKBACK_S} seconds' "
        f"--no-pager -o short-unix 2>/dev/null | "
        f"grep -E -- {shlex.quote(RECEPTION_PATTERN)} | tail -1"
    )
    rc, out = _ssh(host, cmd)
    if rc < 0:
        return UNOBSERVABLE, None
    line = out.strip()
    if not line:
        return "ok", None          # heard nothing in the window
    return parse_reception_age(line, now)


def corroborating_traffic(host: str, unit: str, match: str,
                          window_s: int) -> Tuple[str, Optional[int]]:
    """(status, count) of messages an independent box saw in the window."""
    # shlex.quote, not a hand-rolled quote-strip: the old form silently
    # MUTATED the operator's configured pattern (dropping apostrophes) and
    # then searched for something the config never asked for -- a checker
    # quietly auditing the wrong thing.
    cmd = (
        f"journalctl -u {unit} --since '-{int(window_s)} seconds' --no-pager "
        f"2>/dev/null | grep -c -F -- {shlex.quote(match)}"
    )
    rc, out = _ssh(host, cmd)
    if rc < 0:
        return UNOBSERVABLE, None
    # grep -c exits 1 with a legitimate count of 0; only transport failure is
    # unobservable, so rc is not consulted beyond the -1 sentinel above.
    try:
        return "ok", int(out.strip() or "0")
    except ValueError:
        return UNOBSERVABLE, None


def effective_silence(age: Optional[float], unit_up_s: float,
                      lookback_s: float = JOURNAL_LOOKBACK_S
                      ) -> Tuple[float, bool]:
    """(seconds the CURRENT process has heard nothing, was_it_bounded_by_restart).

    `journalctl -u <unit>` spans restarts, so `age` can point at a reception
    made by a PREVIOUS process. The current process cannot have been silent
    for longer than it has been running, so the honest figure is
    `min(age, uptime)` -- and when nothing was found at all, simply `uptime`.

    This is the 2026-09-04 incident's own shape and the reason the gap
    mattered: lehua's radio died mid-run and the bot was restarted twice that
    day. Without this, a bot restarted seconds ago with no journal history
    reads "heard nothing, ever" and can reach DEAF before it has had any
    chance to hear -- and conversely a pre-restart reception could vouch for
    a process that has heard nothing since.

    The grace period falls out of the arithmetic rather than being a second
    tunable: silence can never exceed uptime, so a unit up for less than the
    threshold can never be called deaf.

    When nothing was found at all, the bound is the LOOKBACK window, not
    infinity -- the journal was only consulted that far back, so claiming
    more silence than we looked for would be asserting what we did not
    observe. It also keeps the returned flag honest: a bot up 24h with an
    empty 6h window is silent because it heard nothing, not because it
    restarted, and the two must not print the same sentence.
    """
    observed = lookback_s if age is None else age
    if unit_up_s < observed:
        return unit_up_s, True
    return observed, False


def decide(unit_status: str, unit_detail: str,
           age_status: str, age: Optional[float],
           corr_status: str, corr: Optional[int],
           deaf_after_s: float,
           min_corroboration: int = DEFAULT_MIN_CORROBORATION,
           unit_up_s: Optional[float] = None) -> Tuple[str, str]:
    """Pure verdict function -- unit-testable without a fleet.

    `unit_up_s` (how long the CURRENT process has been active) is required to
    judge silence honestly, because `journalctl -u <unit>` spans restarts. See
    `effective_silence()`.
    """
    if unit_status == UNOBSERVABLE:
        return UNOBSERVABLE, f"bot box: {unit_detail}"
    if unit_status == INERT:
        return INERT, unit_detail
    if age_status == UNOBSERVABLE:
        return UNOBSERVABLE, "bot journal unreadable or undateable"
    if unit_up_s is None:
        # We cannot tell a deaf bot from one that restarted seconds ago and
        # has not yet had a chance to hear anything. Guessing either way is
        # the defect class: a degraded observation must not be given a
        # healthy-looking (or alarming) value.
        return (UNOBSERVABLE,
                "bot unit uptime unreadable -- cannot tell a deaf bot from a "
                "freshly restarted one, so neither claim is available")

    silence, since_restart = effective_silence(age, unit_up_s)
    if silence <= deaf_after_s:
        if since_restart:
            return (CLEAN,
                    f"bot restarted {int(unit_up_s)}s ago and has not been up "
                    f"long enough to call silent (threshold {int(deaf_after_s)}s)")
        return CLEAN, f"bot heard traffic {int(silence)}s ago"

    # Bot has been silent past the threshold FOR THIS PROCESS. Only a second
    # witness can tell deafness from a quiet mesh.
    if corr_status == UNOBSERVABLE:
        return (UNOBSERVABLE,
                "bot silent, and the corroborating box could not be read -- "
                "cannot tell deafness from a quiet mesh")
    heard = (f"nothing in {int(unit_up_s)}s since it restarted" if since_restart
             else "nothing in the lookback window" if age is None
             else f"{int(age)}s ago")
    if not corr or corr < min_corroboration:
        return (CLEAN,
                f"bot last heard {heard}, and the corroborator saw only "
                f"{corr or 0} message(s) (< {min_corroboration}) -- quiet mesh "
                f"or RF luck, not demonstrable deafness")
    return (DEAF,
            f"bot is ACTIVE but heard {heard}, while the corroborator saw "
            f"{corr} message(s) in the same window -- the feed is not reaching it")


def _load_state(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, ValueError, OSError):
        return {}


def _save_state(path: str, state: dict) -> Optional[str]:
    """Returns None on success, else the error string. A debounce whose saver
    cannot write freezes the streak below the threshold and the probe can NEVER
    fire -- exactly the 2026-09-02 class where three probes sat 'held by
    debounce' for days. So a write failure is REPORTED, not swallowed."""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(state, fh)
        os.replace(tmp, path)
        return None
    except OSError as exc:
        return f"{type(exc).__name__}: {exc}"


def main(argv) -> int:
    cfg_path = _cfg_path()
    try:
        with open(cfg_path, "r", encoding="utf-8") as fh:
            cfg = json.load(fh)
    except FileNotFoundError:
        print(f"FATAL no config at {cfg_path} — refusing to run (a check with "
              f"no target would report 'nothing wrong' about a bot it never "
              f"looked at)", file=sys.stderr)
        return 2
    except (ValueError, OSError) as exc:
        print(f"FATAL config unreadable: {exc}", file=sys.stderr)
        return 2

    bot_host = cfg.get("bot_host")
    corr_host = cfg.get("corroborator_host")
    if not bot_host or not corr_host:
        print("FATAL config needs bot_host and corroborator_host",
              file=sys.stderr)
        return 2
    bot_unit = cfg.get("bot_unit", "mesh_bot")
    corr_unit = cfg.get("corroborator_unit", "meshtasticd")
    corr_match = cfg.get("corroborator_match", "json/meshforge")
    deaf_after_s = float(cfg.get("deaf_after_s", DEFAULT_DEAF_AFTER_S))
    debounce = int(cfg.get("debounce_ticks", DEFAULT_DEBOUNCE))
    min_corr = int(cfg.get("min_corroboration", DEFAULT_MIN_CORROBORATION))

    now = time.time()
    u_status, u_detail, u_up = unit_state(bot_host, bot_unit)
    if u_status == "ok":
        a_status, age = last_reception_age(bot_host, bot_unit, now)
        c_status, corr = corroborating_traffic(
            corr_host, corr_unit, corr_match, int(deaf_after_s))
    else:
        a_status, age, c_status, corr = "ok", None, "ok", 0

    verdict, detail = decide(u_status, u_detail, a_status, age,
                             c_status, corr, deaf_after_s, min_corr,
                             unit_up_s=u_up)

    state = _load_state(_state_path())
    streak = int(state.get("deaf_streak", 0)) + 1 if verdict == DEAF else 0
    save_err = _save_state(_state_path(),
                           {"deaf_streak": streak, "last_verdict": verdict,
                            "last_check_ts": now})

    line = f"bot_deaf_check: {verdict} — {detail}"
    if save_err:
        # Loud, and it changes the claim: without a durable streak the
        # debounce cannot be trusted, so say so rather than print a number
        # that will never grow.
        line += (f" | ⚠️ state NOT saved ({save_err}) — debounce is not "
                 f"durable; treat the streak as unknown")
    elif verdict == DEAF:
        line += f" | streak {streak}/{debounce}"
    print(line)

    if verdict == DEAF and streak >= debounce and not save_err:
        push = os.path.join(_HERE, "fleet_ntfy_push.sh")
        if os.path.exists(push):
            try:
                subprocess.run(
                    [push, f"Mesh bot DEAF: {bot_host}", "high", "warning",
                     detail],
                    timeout=60, check=False)
            except (subprocess.TimeoutExpired, OSError) as exc:
                print(f"bot_deaf_check: page FAILED: {exc}", file=sys.stderr)
    if verdict == DEAF:
        return 1
    if verdict == UNOBSERVABLE:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
