"""The path out, as mini sees it: the ladder's verdict WITH its localization.

Born 2026-09-06. The WAN ladder had been logging FAIL every ten minutes for
seven hours and mini's brief never mentioned it once — the ladder is a
self-verdicting cron, so ``probe_cron_verdict_stale`` (which judges only crons
wired via a ``cron_verdict.sh <name>`` token in the crontab) treated it as an
orphan and skipped it. The instrument was working perfectly and speaking to
nobody. The operator's rule: *a tool that's silent has no diagnostic meaning
to a user.*

This source reads two files and emits ONE condition, because two lines saying
"the internet is lossy" and "here is where" are one finding, and a brief that
splits them makes the reader do the join at 3am:

  * ``wan_path.json``   — the ladder: which RUNG is losing (utils.wan_path)
  * ``wan_trace.json``  — the trace: WHERE on the path (utils.wan_autotrace)

Observation-only, as mini must be (MF021): both are plain file reads. The
tracing itself is a cron-side job precisely because it shells out to ``ping``
dozens of times, which mini may never do.

Honesty rules this source keeps:
  * A missing ladder file is INERT, not healthy — a box that runs no ladder
    has nothing to say, and says nothing.
  * A ladder file older than its staleness window is its own condition. A
    frozen green verdict must never read as a live one.
  * A trace older than the ladder reading it accompanies is reported as
    HISTORICAL, never presented as the current localization.
"""
from __future__ import annotations

import time
from typing import Any, Dict, Iterable, Optional

from .._util import read_json
from .base import Condition, Source

#: 2.5 cadences of the 10-minute ladder cron — same threshold the fleet page uses.
DEFAULT_STALE_S = 25 * 60


class WanPathSource(Source):
    """Emit the ladder's verdict, carrying the trace's localization when it has one."""

    def __init__(self, ladder_path: str, trace_path: Optional[str] = None,
                 stale_after_s: float = DEFAULT_STALE_S,
                 name: str = "wan_path") -> None:
        self.ladder_path = ladder_path
        self.trace_path = trace_path
        self.stale_after_s = stale_after_s
        self.name = name

    def _trace_note(self, ladder_at: Optional[float], now: float) -> str:
        """The localization line, or an honest account of why there isn't one."""
        if not self.trace_path:
            return ""
        data, err = read_json(self.trace_path)
        if err or not isinstance(data, dict):
            return (" · no path trace yet (wire `wan_path_probe.py --verdict "
                    "--auto-trace` on the ladder cron to get one automatically)")
        summary = data.get("summary")
        if not summary:
            return " · a trace ran but produced no finding: %s" % (data.get("reason") or "?")
        gen = data.get("generated_at")
        if isinstance(gen, (int, float)):
            age_min = max(0.0, (now - float(gen)) / 60.0)
            # A trace older than the ladder sample it is shown beside describes a
            # DIFFERENT moment. Say so rather than let it read as current.
            stale = isinstance(ladder_at, (int, float)) and gen < float(ladder_at) - self.stale_after_s
            return " · %s trace (%.0f min ago): %s" % (
                "HISTORICAL" if stale else "latest", age_min, summary)
        return " · trace (age unknown): %s" % summary

    def collect(self) -> Iterable[Condition]:
        now = time.time()
        data, err = read_json(self.ladder_path)
        if err or not isinstance(data, dict):
            # No ladder on this box = nothing to report. Absent by design is
            # INERT, never a condition and never a clean claim.
            return
        status = data.get("status")
        gen = data.get("generated_at")

        if isinstance(gen, (int, float)):
            age = now - float(gen)
            if age > self.stale_after_s:
                yield Condition(
                    kind="wan_path_stale",
                    subject="wan_path",
                    detail=("the path ladder has not run for %.0f min (threshold %.0f min) — "
                            "its last verdict was %r and is NOT current; check the "
                            "wan_path cron"
                            % (age / 60.0, self.stale_after_s / 60.0, status)),
                    source=self.name,
                    extras={"age_s": age, "last_status": status},
                )
                return

        if status in ("fail", "concern", "unknown"):
            yield Condition(
                kind="wan_path_degraded",
                subject=str(data.get("cause") or "unknown"),
                detail="%s%s" % (data.get("message") or "no message",
                                 self._trace_note(gen, now)),
                source=self.name,
                extras={
                    "status": status,
                    "cause": data.get("cause"),
                    "worst_far_loss_pct": data.get("worst_far_loss_pct"),
                },
            )
