"""Watchdog probes — Meshtastic channel-feed observation.

``probe_channel_feed_dark`` (2026-06-04, the .32 dark-feed lesson) and the
json-uplink *instrument* legs it depends on.

Split out of ``watchdog_probes_service`` on 2026-08-07 (MF025: that module
stood at 1,488 of 1,500 lines and the five-state rework needed room —
split the file, never raise the cap). Import via the
``utils.watchdog_probes`` hub, not from here.

⚠️ Why five states (2026-08-07). This probe observes a channel through ONE
instrument: meshtasticd's MQTT-json uplink journal. Before today it could
say only "dark" or "indeterminate", so *having no instrument at all*
(``mqtt.json_enabled=False`` — the normal, deliberate state of every RX-only
box) was reported as a failed observation. Measured that morning: kiai,
moc1, moc4 and moc5 all sat ``indeterminate`` with the reason "mqtt off or
journalctl error" while journalctl was returning rc=1 (**ran, positively no
match**) against healthy 12k–15k-line journals, and three of the four had
``mqtt.enabled=True`` — so the stated reason was not merely vague, it was
wrong, and an operator following it would check MQTT, find it enabled, and
lose the thread. The correlation was exact across all seven boxes:
``mqtt.json_enabled=False`` ⇔ blind.

That is the same defect the 2026-08-05 arc named (``inert`` and
``indeterminate`` are different claims); the tri-state
``_journal_newest_match_status`` was written that day and
``probe_mqtt_root_drift`` converted to it, but this sibling probe was left
on the flat shim. Fixed here.

The trap in the obvious fix: "zero json lines → inert" would ALSO silence a
box whose json uplink genuinely died more than the lookback ago — the
freshness gate only catches a pipeline that died *inside* the window. That
turns this probe's own failure mode ("silence looks like no traffic") on the
instrument itself. So absence is only benign where the box never claimed to
have the instrument: the declaration
(``deployment.json organ_expectations.json_uplink``) is what separates
*off by design* from *died quietly*. Undeclared-and-absent is ``inert``;
declared-and-absent is ``indeterminate`` carrying the full diagnosis as its
reason, which is what ``detector_blind`` escalates when it persists.

Deliberately journal-only: reading ``mqtt.json_enabled`` from the radio
would open a ``TCPInterface`` and steal a PhoneAPI slot (#17 / MF007), which
is exactly what this spine exists to avoid.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from typing import Optional, Tuple

from utils.watchdog_probe_core import (
    Signal,
    _journal_newest_match_status,
    _resolve_main_pid,
    _short_unix_ts,
    note_disposition,
)

#: Journal marker meshtasticd logs for every MQTT-json publish. Its presence
#: is the evidence that this box HAS a json-uplink instrument at all.
_JSON_UPLINK_MARKER = "serialized json message"


def _read_json_uplink_expectation(service_user) -> Tuple[str, str]:
    """Tri-state read of ``organ_expectations.json_uplink`` from deployment.json.

    Returns ``("declared", why)`` when the box states it uplinks MQTT-json,
    ``("undeclared", "")`` when there is no deployment.json or no such
    declaration, or ``("unreadable", "")`` when a file that should be
    readable isn't.

    Mirrors ``watchdog_probes_drift._declared_root_status`` rather than
    ``_read_deployment_declaration``: the latter collapses absent and
    unreadable into ``(None, {})``, and the whole point here is that those
    two are opposite facts — "this box has no json instrument" (inert) vs
    "I could not find out" (indeterminate).

    The watchdog runs as sandboxed root, so the home is derived from the
    service user and READ directly — never escalate (the rns_version_drift
    lesson).
    """
    if not service_user:
        return ("unreadable", "")  # can't resolve the user → can't observe
    try:
        import pwd
        home = pwd.getpwnam(service_user).pw_dir
        path = os.path.join(home, ".config", "meshforge", "deployment.json")
    except (KeyError, OSError, TypeError):
        return ("unreadable", "")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return ("undeclared", "")  # no deployment.json → no statement made
    except (OSError, ValueError, TypeError):
        return ("unreadable", "")
    try:
        organs = data.get("organ_expectations")
        if not isinstance(organs, dict) or "json_uplink" not in organs:
            return ("undeclared", "")
        why = organs["json_uplink"]
        if why is False:
            return ("undeclared", "")  # explicit opt-OUT is still no claim
        return ("declared", why if isinstance(why, str) else "")
    except (AttributeError, TypeError):
        return ("unreadable", "")


def _journal_covers_window(
    unit: str,
    older_than_s: float,
    *,
    journalctl_path: str = "journalctl",
) -> Optional[bool]:
    """Does ``unit``'s journal reach back past ``older_than_s``?

    True when at least one entry exists at or before that point, False when
    the journal demonstrably starts later, None when the query failed.

    ⚠️ Load-bearing for the declared-dark leg (honest_failure_modes #2). A
    box that just booted, or whose journal was rotated/vacuumed, has NO json
    lines in the window for a reason that says nothing about the uplink —
    and on a ``Storage=volatile`` box (which parts of this fleet run, per the
    lxmf_propagation_unused note) every reboot produces exactly that. Firing
    "declared but dark" off a journal too short to hold the answer would be
    a confident claim from an empty channel.

    ⚠️ ``rc`` does NOT discriminate here — journalctl exits 0 with EMPTY
    stdout when nothing is that old (measured 2026-08-07 against both a 6h
    and a 100d ``--until``). The line's presence is the evidence.

    Runs ONLY on the about-to-fire path, never every tick: a per-tick
    journalctl on a 905 MB Pi is not a rounding error.
    """
    try:
        proc = subprocess.run(
            [
                journalctl_path, "-u", unit,
                "--until", f"-{int(older_than_s)}s",
                "-n", "1", "-o", "short-unix", "-q", "--no-pager",
            ],
            capture_output=True, text=True, timeout=10,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None
    if proc.returncode not in (0, 1):
        return None
    return bool(proc.stdout.strip())


def probe_channel_feed_dark(
    *,
    channel_name: str = "meshforge",
    unit: str = "meshtasticd.service",
    dark_after_s: float = 6 * 3600.0,
    lookback: Optional[str] = None,
    journalctl_path: str = "journalctl",
    systemctl_path: str = "systemctl",
    main_pid: Optional[int] = None,
    newest_line_fn=None,
    service_user_fn=None,
    expectation_fn=None,
    coverage_fn=None,
    now: Optional[float] = None,
) -> Optional[Signal]:
    """Fire when a watched channel's decoded-text feed goes silent — or when
    a box that DECLARES a json uplink isn't producing one.

    The .32 dark-feed lesson (2026-06-04 PSK rotation): a consumer missed
    the re-key and its feed went silently dark — heartbeats stayed green
    because *silence looks identical to "no traffic"* unless something
    watches for it. On a normally-busy mesh, hours of zero decoded text on
    the meshforge channel mean a missed PSK re-key, a deaf radio (the moc2
    antenna case — ``channel_utilization=0.0`` tell), or a dead uplink path.

    Watched by NAME, not slot index (2026-06-06 federator false-alarm):
    the ``"channel":N`` field in the json payload is the box-LOCAL slot
    index, and slot layouts legitimately differ across the fleet. Channel
    identity is the NAME (half of the decode gate ``hash(name, psk)``), and
    the json publish-topic journal line carries both the name and the
    payload: ``JSON publish message to msh/2/json/<name>/!<id>, N bytes:
    {...,"type":"text",...}`` — so one pattern matches name + text.

    **Four states** (see the module docstring for why):

    ===================  ==========================================
    ``indeterminate``    meshtasticd MainPID unresolved; journalctl
                         unobservable; declaration unreadable;
                         journal too short to cover the window;
                         unparseable timestamp; or the box DECLARES
                         a json uplink and none is observed (zero
                         lines, or newest line older than
                         ``dark_after_s``) — BLIND, and the reason
                         carries the diagnosis
    ``inert``            journal ran, ZERO json-uplink lines, box
                         declares no json uplink — there is no
                         channel instrument here, by design
    ``clean``            json alive and channel text is fresh
    ``channel_feed_dark``json alive, channel text stale/absent —
                         degraded, the original finding
    ===================  ==========================================

    The undeclared box whose json lines exist but went stale stays
    ``indeterminate`` (unchanged): the instrument stopped, so
    channel-specific dark is genuinely unobservable, and without a
    declaration there is nothing that says it should still be running.
    """
    pid = main_pid if main_pid is not None else _resolve_main_pid(
        unit, systemctl_path=systemctl_path
    )
    if pid is None:
        note_disposition(
            "channel_feed_dark", "indeterminate",
            reason="meshtasticd MainPID unresolved; service_inactive owns that",
        )
        return None

    # Bound the journal scan to the darkness threshold. journalctl -g -r -n 1
    # is cheap on a busy feed (stops at the first newest match) but the
    # NO-MATCH case scans the ENTIRE --since window every tick — and that is
    # exactly a dark or collector feed. moc5 (2026-07-01) is a collector with
    # no json uplink at all, so the observability gate below re-scanned a fixed
    # 24h of meshtasticd journal each tick and pegged a core. The freshness
    # gate (dark_after_s) already discards any json line older than the
    # threshold, so a window longer than dark_after_s is pure wasted CPU.
    # Derive the window from dark_after_s — rounded UP to the next whole hour
    # — so the two can never drift (honest_failure_modes #5) and the scan is
    # bounded to what the decision actually needs. int()+1 guarantees
    # lookback >= dark_after_s (no false dark); the margin is (0, 1h] and is
    # exactly 1h only for whole-hour thresholds (floor semantics).
    if lookback is None:
        lookback = f"{int(dark_after_s // 3600) + 1}h"

    # The default reader records WHETHER the journal answered, alongside the
    # line itself — captured from the one call it already makes, never a
    # second subprocess per tick. An INJECTED newest_line_fn is a test seam
    # that positively returned its answer, so it defaults to "ok".
    read_status = {"status": "ok"}
    if newest_line_fn is None:
        def newest_line_fn(pattern: str) -> Optional[str]:
            st, ln = _journal_newest_match_status(
                unit, pattern, lookback, journalctl_path=journalctl_path
            )
            read_status["status"] = st
            return ln

    ts_now = now if now is not None else time.time()

    any_json = newest_line_fn(_JSON_UPLINK_MARKER)

    # Is the instrument producing? Zero lines and a line older than the
    # darkness threshold are the same fact — no usable json uplink — and
    # they differ only in whether the corpse is still inside the window.
    json_ts = _short_unix_ts(any_json) if any_json is not None else None
    instrument_stale = (
        any_json is not None
        and json_ts is not None
        and (ts_now - json_ts) >= dark_after_s
    )

    if any_json is None or instrument_stale:
        if any_json is None and read_status["status"] != "ok":
            note_disposition(
                "channel_feed_dark", "indeterminate",
                reason="journal unavailable — cannot observe the json uplink",
            )
            return None
        return _judge_absent_instrument(
            channel_name=channel_name,
            unit=unit,
            dark_after_s=dark_after_s,
            lookback=lookback,
            journalctl_path=journalctl_path,
            service_user_fn=service_user_fn,
            expectation_fn=expectation_fn,
            coverage_fn=coverage_fn,
            stale_line_age_s=(ts_now - json_ts) if instrument_stale else None,
        )

    ch_text = newest_line_fn(f'json/{channel_name}/.*"type":"text"')

    if ch_text is None:
        age_desc = f"none within the {lookback} lookback window"
        age_s = None
    else:
        last_ts = _short_unix_ts(ch_text)
        if last_ts is None:
            note_disposition(
                "channel_feed_dark", "indeterminate",
                reason="unparseable journal timestamp on newest channel line",
            )
            return None  # unparseable journal line — indeterminate
        age_s = ts_now - last_ts
        if age_s < dark_after_s:
            note_disposition("channel_feed_dark", "clean")
            return None  # feed is alive
        age_desc = f"last decoded {age_s / 3600.0:.1f}h ago"

    json_age_desc = (
        f"{(ts_now - json_ts) / 3600.0:.1f}h ago" if json_ts is not None
        else "unknown"
    )
    detail = (
        f"No decoded text on Meshtastic channel '{channel_name}' "
        f"({age_desc}) while the json-uplink pipeline is observable (newest "
        f"json line {json_age_desc}). On a normally-busy mesh this is the "
        f"dark-feed tell: missed PSK re-key (decode gate = hash(name,psk)), "
        f"deaf radio (check channel_utilization in DeviceTelemetry), or dead "
        f"uplink path. Verify: journalctl -u meshtasticd | grep "
        f"'json/{channel_name}/' ; then send a test message from another "
        f"fleet box on the '{channel_name}' channel."
    )
    extra: dict = {
        "channel_name": channel_name,
        "dark_after_s": dark_after_s,
        "lookback": lookback,
    }
    if age_s is not None:
        extra["age_s"] = round(age_s, 1)
    return Signal(
        cls="channel_feed_dark",
        subject=f"meshtastic-{channel_name}",
        severity="degraded",
        detail=detail,
        extra=extra,
    )


def _judge_absent_instrument(
    *,
    channel_name: str,
    unit: str,
    dark_after_s: float,
    lookback: str,
    journalctl_path: str,
    service_user_fn,
    expectation_fn,
    coverage_fn,
    stale_line_age_s: Optional[float],
) -> Optional[Signal]:
    """No usable json uplink — decide whether that is by design or a finding.

    Split out so the ``channel_feed_dark`` path above reads as one straight
    line. Emits ``inert`` (no instrument declared) or ``indeterminate``
    (declaration unreadable, journal too short to hold the answer, or the
    box DECLARES an uplink that is absent).

    That last case had its own signal class (``json_uplink_dark``, born
    2026-08-07) for one day. It was removed in the 2026-08-08 subtraction
    audit: its coverage split was identical to ``channel_feed_dark`` on
    every box, so it carried no information this class does not, and it
    watched the instrument BEHIND a detector — a layer up, the exact
    "machinery watching machinery" shape
    ``feedback_my_footprint_is_the_constraint`` warns against. The finding
    is preserved as a REASON on this class's ``indeterminate``: a declared
    uplink that is absent makes this probe blind, and a persistently blind
    coverage class is what ``detector_blind`` exists to escalate. The
    2026-08-07 fix that DID earn its place — undeclared boxes read ``inert``
    rather than a failed observation — is the branch below, and it stays.
    """
    exp_fn = expectation_fn
    if exp_fn is None:
        user_fn = service_user_fn
        if user_fn is None:
            from utils.rns_tree_perms import _read_rnsd_user
            user_fn = _read_rnsd_user

        def exp_fn():
            return _read_json_uplink_expectation(user_fn())

    status, why = exp_fn()

    if status == "unreadable":
        note_disposition(
            "channel_feed_dark", "indeterminate",
            reason=("json-uplink declaration unreadable — never alarm on "
                    "a guess"),
        )
        return None

    if status != "declared":
        # The box never claimed to publish MQTT-json, so there is no channel
        # instrument here to be dark. INERT, not a failed observation — an
        # organ absent by design must never read as an observation that
        # failed, or the real failures have nowhere to stand out
        # (2026-08-05). This is the kiai/moc1/moc4/moc5 shape.
        note_disposition(
            "channel_feed_dark", "inert",
            reason=("no MQTT-json uplink on this box (mqtt.json_enabled "
                    "off) — no channel instrument here"),
        )
        return None

    # Declared. Before calling it dark, prove the journal could have held the
    # evidence: a booted-10-minutes-ago box has no json lines for a reason
    # that says nothing about the uplink.
    cov_fn = coverage_fn
    if cov_fn is None:
        def cov_fn():
            return _journal_covers_window(
                unit, dark_after_s, journalctl_path=journalctl_path)
    covers = cov_fn()
    if covers is None:
        note_disposition(
            "channel_feed_dark", "indeterminate",
            reason=("journal coverage unobservable — cannot judge "
                    "declared uplink"),
        )
        return None
    if not covers:
        note_disposition(
            "channel_feed_dark", "indeterminate",
            reason=("journal does not reach back "
                    f"{dark_after_s / 3600.0:.1f}h — too short to judge"),
        )
        return None

    if stale_line_age_s is None:
        observed = f"no json-uplink line at all within the {lookback} window"
    else:
        observed = (f"newest json-uplink line is "
                    f"{stale_line_age_s / 3600.0:.1f}h old")
    because = f" (declared: {why})" if why else ""
    # DECLARED and absent — this probe is blind, and it says so in its own
    # disposition rather than in a class of its own. A sustained blind here
    # is escalated by ``detector_blind``; the reason carries the whole
    # diagnosis so the read is not a guess (2026-08-05: the witness worked,
    # the READ failed — so the reason must be specific enough to act on).
    note_disposition(
        "channel_feed_dark", "indeterminate",
        reason=(
            f"BLIND: box DECLARES an MQTT-json uplink{because} but none is "
            f"observable ({observed}) though the {unit} journal covers the "
            f"window — the '{channel_name}' canary watches the mesh THROUGH "
            f"this uplink. Likely mqtt.json_enabled off (or reset by a "
            f"zero-config radio join), MQTT module lost its broker, or "
            f"meshtasticd stopped publishing. Verify: journalctl -u {unit} "
            f"--since -1h | grep 'serialized json message'. If this box is "
            f"NOT meant to uplink json, remove organ_expectations.json_uplink "
            f"from deployment.json and this goes inert."
        ),
    )
    return None
