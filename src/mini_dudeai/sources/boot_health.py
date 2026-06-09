"""Detect an unclean / unexpected reboot from stdlib signals only.

mini-dudeai is observation-only (no subprocess), so it cannot run journalctl
to inspect the prior boot. This source classifies the *current* boot as clean
vs unclean using three files it can read directly:

  - /proc/uptime              → how long we've been up (the fresh-boot gate)
  - mini_dudeai_state.json    → last_tick_ts, the wall-clock of mini's last
                                tick BEFORE this boot (the engine advances it
                                every tick)
  - clean-exit marker file    → wall-clock of mini's last *graceful* stop
                                (the engine writes it only on SIGTERM/SIGINT)

The dirty-bit insight: a graceful shutdown writes the clean-exit marker right
after the final tick, so marker >= last_tick - slack. A hard crash never
writes it, so last_tick advances PAST the stale marker. Therefore, right
after a fresh boot:

    clean_exit < last_tick - slack   →   prior shutdown was UNCLEAN (crash)

The verdict is latched to a per-boot assessment file so the emitted condition
stays stable across the whole fresh-boot window (one clean edge_up, not a
per-tick flap). It also surfaces the last line of power_history.log — the last
throttled/voltage reading captured before the cut — so the operator / next
cloud session can immediately see whether a brownout preceded the reset
(under-voltage sticky bit 0x10000) WITHOUT running anything. See memory
project_volcanoai_hard_reset_2026_05_28 for the full forensic method.

Emits kind="unexpected_reboot" (subject = hostname) when the prior shutdown
was unclean and we're still inside the fresh-boot window. Clean reboots,
first-ever runs, and steady state stay silent. Unreadable /proc/uptime →
source_error (so mini can flag going blind on its own boot signal).
"""
from __future__ import annotations

import os
import time
from typing import Iterable

from .._util import atomic_write_json, read_json
from .base import Condition, Source


class BootHealthSource(Source):
    """Emit an unexpected_reboot Condition after an unclean boot.

    Args:
        state_path: path to mini_dudeai_state.json (read for last_tick_ts).
        clean_exit_path: path to the clean-exit marker the engine writes on
            graceful stop (a single wall-clock float). Missing = never exited
            cleanly = treat as unclean.
        assessment_path: where this source latches its per-boot verdict so the
            condition is stable across the fresh-boot window.
        power_log_path: optional ~/power_history.log; its last line is attached
            as evidence (last throttled/voltage reading before the cut).
        boot_window_s: only assess while uptime is below this (default 900s =
            15m). Outside the window we are in steady state → silent.
        clean_slack_s: how close the clean-exit marker must be to last_tick to
            count the prior shutdown as clean (default 180s = a few ticks +
            shutdown grace).
        uptime_path: override for testing (default /proc/uptime).
        name: source identity.
    """

    def __init__(
        self,
        state_path: str,
        clean_exit_path: str,
        assessment_path: str,
        power_log_path: str | None = None,
        boot_window_s: float = 900.0,
        clean_slack_s: float = 180.0,
        uptime_path: str = "/proc/uptime",
        boot_id_path: str = "/proc/sys/kernel/random/boot_id",
        name: str = "boot_health",
    ) -> None:
        self.state_path = state_path
        self.clean_exit_path = clean_exit_path
        self.assessment_path = assessment_path
        self.power_log_path = power_log_path
        self.boot_window_s = boot_window_s
        self.clean_slack_s = clean_slack_s
        self.uptime_path = uptime_path
        self.boot_id_path = boot_id_path
        self.name = name

    def collect(self) -> Iterable[Condition]:
        uptime = self._read_uptime()
        if uptime is None:
            yield Condition(
                kind="source_error",
                subject="boot",
                detail=f"{self.uptime_path} unreadable — cannot assess boot health",
                source=self.name,
            )
            return
        if uptime > self.boot_window_s:
            return  # steady state — not a fresh boot

        now = time.time()
        boot_time = now - uptime
        verdict = self._assess(boot_time, now)
        if verdict.get("verdict") != "unclean":
            return  # clean / first-run / indeterminate → silent

        power_tail = verdict.get("power_tail")
        yield Condition(
            kind="unexpected_reboot",
            subject=os.uname().nodename,
            detail=(
                f"unclean shutdown: box reset ~{verdict.get('down_gap_s')}s after "
                f"mini's last tick (up {int(uptime)}s, no clean-exit marker). "
                f"Last power reading before cut: {power_tail or 'n/a'}"
            ),
            source=self.name,
            extras=verdict,
        )

    # --- internals ----------------------------------------------------

    def _assess(self, boot_time: float, now: float) -> dict:
        """Compute and latch the per-boot clean/unclean verdict.

        Latch identity is the kernel boot_id (stable per boot, immune to the
        pre-NTP clock steps of this RTC-less fleet) with the old boot_time
        ±120s key as fallback when boot_id is unreadable. The old time-only
        key had two failure modes: a short hard cut whose fake-hwclock error
        was under 120s latched a wrong pre-NTP verdict FOREVER (silently
        missing exactly the short-power-cut class the detector was built
        for), and a step over 120s broke the key and let a definitive
        verdict flip mid-window.

        Verdict mutability: clean/unclean/unknown_first_run compare two
        PRE-BOOT timestamps (last_tick vs clean-exit marker) and are
        clock-robust — once latched they never change. Only "indeterminate"
        (the gate that compares last_tick to the possibly-stale boot_time)
        may be re-assessed and upgraded, using the pre-boot facts STORED at
        first sight — by the next tick the live state file already reflects
        this boot's ticks, so re-reading it could never upgrade.
        """
        boot_id = self._read_boot_id()
        prev, _ = read_json(self.assessment_path)
        same_boot = False
        if isinstance(prev, dict):
            if boot_id and prev.get("boot_id"):
                same_boot = prev.get("boot_id") == boot_id
            else:
                same_boot = abs(prev.get("boot_time_ts", 0) - boot_time) < 120
        if same_boot and prev.get("verdict") != "indeterminate":
            return prev  # definitive verdict for this boot — immutable

        if same_boot:
            # Re-assessing an indeterminate latch: judge the STORED pre-boot
            # facts against the (possibly now NTP-corrected) boot_time.
            last_tick = float(prev.get("last_tick_ts", 0) or 0)
            clean_exit = float(prev.get("clean_exit_ts", 0) or 0)
            power_tail = prev.get("power_tail")
        else:
            state, _ = read_json(self.state_path)
            last_tick = float((state or {}).get("last_tick_ts", 0) or 0)
            clean_exit = self._read_float(self.clean_exit_path)
            power_tail = self._last_power_line()

        if last_tick <= 0:
            verdict = "unknown_first_run"          # mini never ran before
        elif last_tick >= boot_time:
            verdict = "indeterminate"              # clock says mini ticked within this boot
        elif clean_exit >= last_tick - self.clean_slack_s:
            verdict = "clean"                      # graceful shutdown recorded
        else:
            verdict = "unclean"                    # crash: tick advanced past marker

        assessment = {
            "verdict": verdict,
            "boot_id": boot_id,
            "boot_time_ts": int(boot_time),
            "last_tick_ts": last_tick,
            "clean_exit_ts": clean_exit,
            "down_gap_s": int(max(0.0, boot_time - last_tick)) if last_tick > 0 else 0,
            "power_tail": power_tail,
            "assessed_at_ts": int(now),
        }
        if same_boot and verdict == prev.get("verdict"):
            return assessment  # still indeterminate — nothing new to latch
        try:
            atomic_write_json(self.assessment_path, assessment)
        except OSError:
            pass  # latching is best-effort; verdict is still returned
        return assessment

    def _read_boot_id(self) -> str | None:
        try:
            with open(self.boot_id_path) as f:
                return f.read().strip() or None
        except OSError:
            return None

    def _read_uptime(self) -> float | None:
        try:
            with open(self.uptime_path) as f:
                return float(f.read().split()[0])
        except (OSError, ValueError, IndexError):
            return None

    @staticmethod
    def _read_float(path: str) -> float:
        try:
            with open(path) as f:
                return float(f.read().strip() or 0)
        except (OSError, ValueError):
            return 0.0

    def _last_power_line(self) -> str | None:
        if not self.power_log_path:
            return None
        try:
            with open(self.power_log_path) as f:
                lines = f.readlines()
        except OSError:
            return None
        return lines[-1].strip() if lines else None
