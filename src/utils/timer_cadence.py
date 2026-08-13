"""How often does a systemd ``.timer`` fire? — asked of systemd, not guessed.

**Why this exists.** Several probes derive a staleness or lookback bar from a
timer's cadence, and every one of them has so far *hardcoded* the number it
read out of a unit file once (``watchdog_probes_gateway_flow``: "the synth soak
fires hourly (OnCalendar=\\*:07:00)"). That works until the unit changes and the
comment does not — honest_failure_modes #5, two consumers of one artifact
drifting apart, with the artifact here being the schedule itself.

**Why it shells out.** The ``OnCalendar=`` grammar is large (``*-*-* *:00/10:00``,
``08,18:00:00``, ``Mon..Fri``, ``@`` shorthands, timezone suffixes) and a
reimplementation would be a second, worse parser that disagrees with the one
actually scheduling the job. ``systemd-analyze calendar`` IS that scheduler's
parser, so the cadence is read from the authority rather than from my model of
it (calibrated_claims #7 — verify the consumer of record, and rank evidence by
authorial distance). Measured cost 2026-08-13 on a Pi 5: ~8.5 ms per call, and
results are memoised on the spec text, so a long-lived watchdog pays it once
per distinct schedule per process rather than once per tick.

**Tri-state, always.** ``None`` means *the cadence could not be determined* —
``systemd-analyze`` missing, the unit unreadable, a schedule this module does
not recognise, or a spec systemd itself reports as never elapsing. Callers must
fall back to their own flat default on ``None`` and must NEVER read it as
"fires constantly" or "never fires"; a lost cadence has to degrade to the
pre-existing behaviour, loudly (see ``_analyze_missing_warned``), not to a
silently narrower window.

**Conservative by construction.** Where a schedule is ambiguous the answer is
the LARGER interval: for ``08,18:00:00`` the gaps are 10h and 14h and this
returns 14h, and for a unit carrying several ``OnCalendar=`` lines it returns
the widest of them. Every consumer so far uses the cadence to size a lookback
window, and an over-wide window can only make a detector look FURTHER back —
it cannot manufacture a false "nothing wrong here", which the narrow direction
can (2026-08-13: a daily timer judged against a 3h window).
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
from datetime import datetime, timezone
from typing import Dict, List, Optional

logger = logging.getLogger("watchdog")

# Consecutive elapses to ask for. 4 gives 3 gaps, enough to see the long leg of
# an uneven schedule (``08,18:00:00`` needs two gaps before the 14h one shows).
CADENCE_ITERATIONS = 4
_ANALYZE_TIMEOUT_S = 10

# Realtime schedule. Several are allowed per unit; systemd fires on the union.
_ONCALENDAR_RE = re.compile(r"(?mi)^\s*OnCalendar\s*=\s*(\S.*?)\s*$")
# Monotonic REPEAT knobs — these are what make a monotonic timer recurring.
# OnBootSec=/OnStartupSec=/OnActiveSec= are deliberately NOT here: alone they
# fire the unit exactly once per boot, which has no cadence to derive a window
# from, and treating "once ever" as a period would invent a bar out of nothing.
_MONOTONIC_REPEAT_RE = re.compile(
    r"(?mi)^\s*On(?:UnitActive|UnitInactive)Sec\s*=\s*(\S.*?)\s*$")
# Jitter systemd may add on top of any of the above.
_RANDOM_DELAY_RE = re.compile(
    r"(?mi)^\s*RandomizedDelaySec\s*=\s*(\S.*?)\s*$")

# `(in UTC): Thu 2026-08-13 16:30:00 UTC` — the weekday token is skipped rather
# than parsed with %a, which is locale-dependent and would break under a
# non-English LC_TIME while looking like a schedule the unit does not have.
_UTC_LINE_RE = re.compile(
    r"\(in UTC\):\s+\S+\s+(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s+UTC")

# Memo keyed on the SPEC TEXT, so an edited unit file yields a new key and can
# never be answered from a stale entry. Process-lifetime; the watchdog daemon
# is long-lived, a CLI invocation pays one call.
_CADENCE_MEMO: Dict[str, Optional[float]] = {}

# One-shot flag: losing systemd-analyze silently regresses every cadence-aware
# window back to its flat default, which is exactly the blindness these
# consumers exist to close. First failure is loud; the rest are not, or a
# per-tick per-timer warning would drown the journal it is trying to protect.
_analyze_missing_warned = False


def _run_analyze(args: List[str]) -> Optional[str]:
    """``systemd-analyze <args>`` stdout, or None if it cannot be trusted.

    None on: binary absent, timeout, OS error, or a nonzero exit (which is how
    ``systemd-analyze`` rejects a spec it cannot parse — rc 1, verified
    2026-08-13). Never raises into a probe tick.
    """
    global _analyze_missing_warned
    try:
        proc = subprocess.run(
            ["systemd-analyze"] + args,
            capture_output=True, text=True, timeout=_ANALYZE_TIMEOUT_S,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        if not _analyze_missing_warned:
            _analyze_missing_warned = True
            logger.warning(
                "systemd-analyze unavailable (%s: %s) — timer cadence cannot "
                "be derived; every cadence-aware window falls back to its flat "
                "default, so slow timers (daily/weekly) go back to being "
                "unjudgeable. Install the systemd package or check the "
                "service sandbox's exec permissions.",
                type(exc).__name__, exc)
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def _timespan_s(spec: str) -> Optional[float]:
    """A systemd time span (``5min``, ``1h30``, ``90``) in seconds, via
    ``systemd-analyze timespan`` — systemd's own parser, not a second one."""
    out = _run_analyze(["timespan", spec])
    if not out:
        return None
    m = re.search(r"(?m)^\s*[^\s:]*s:\s*(\d+)\s*$", out)
    if not m:
        return None
    try:
        secs = int(m.group(1)) / 1_000_000.0
    except ValueError:
        return None
    return secs if secs > 0 else None


def _calendar_max_gap_s(spec: str) -> Optional[float]:
    """Largest gap between consecutive elapses of an ``OnCalendar=`` spec.

    None when systemd cannot parse the spec, reports it as never elapsing, or
    yields fewer than two elapses to measure a gap between.
    """
    out = _run_analyze(
        ["calendar", f"--iterations={CADENCE_ITERATIONS}", spec])
    if not out:
        return None
    stamps: List[float] = []
    for m in _UTC_LINE_RE.finditer(out):
        try:
            dt = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
        stamps.append(dt.replace(tzinfo=timezone.utc).timestamp())
    if len(stamps) < 2:
        return None                       # "never", or nothing parseable
    stamps.sort()
    gaps = [b - a for a, b in zip(stamps, stamps[1:]) if b > a]
    return max(gaps) if gaps else None


def cadence_from_spec_text(text: str) -> Optional[float]:
    """Expected max seconds between firings, from a ``.timer`` unit's TEXT.

    Split from the file reader so the schedule logic is testable without a
    filesystem and so the memo key is the schedule itself.
    """
    if text in _CADENCE_MEMO:
        return _CADENCE_MEMO[text]
    result = _cadence_from_spec_text_uncached(text)
    _CADENCE_MEMO[text] = result
    return result


def _cadence_from_spec_text_uncached(text: str) -> Optional[float]:
    base: Optional[float] = None
    calendars = [s for s in _ONCALENDAR_RE.findall(text) if s]
    if calendars:
        # An empty ``OnCalendar=`` RESETS the list in systemd; a unit using
        # that idiom and then setting nothing recurring has no cadence.
        gaps = [g for g in (_calendar_max_gap_s(c) for c in calendars)
                if g is not None]
        if gaps:
            base = max(gaps)              # widest — see the module docstring
    if base is None:
        repeats = [s for s in _MONOTONIC_REPEAT_RE.findall(text) if s]
        spans = [v for v in (_timespan_s(r) for r in repeats) if v is not None]
        if spans:
            base = max(spans)
    if base is None:
        return None
    for jitter in _RANDOM_DELAY_RE.findall(text):
        j = _timespan_s(jitter)
        if j is not None:
            base += j                     # a firing can land this much later
            break
    return base


def timer_cadence_s(timer_path: str) -> Optional[float]:
    """Expected max seconds between firings of the ``.timer`` at ``timer_path``.

    ``None`` when the file cannot be read or carries no recurring schedule —
    the caller must fall back to its own default, never to a guess.
    """
    try:
        with open(timer_path, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError:
        return None
    return cadence_from_spec_text(text)


def timer_cadences(wants_dirs: List[str]) -> Dict[str, Optional[float]]:
    """``{timer unit name -> cadence seconds or None}`` over enrollment dirs.

    Mirrors ``user_units.enabled_user_timers``' scan so the two agree on WHICH
    timers exist; the first directory carrying a name wins, matching that
    function's home-beats-preset precedence.
    """
    out: Dict[str, Optional[float]] = {}
    for wants in wants_dirs:
        try:
            names = [n for n in os.listdir(wants) if n.endswith(".timer")]
        except OSError:
            continue
        for name in sorted(names):
            if name not in out:
                out[name] = timer_cadence_s(os.path.join(wants, name))
    return out
