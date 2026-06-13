"""Traffic Pulse Handler — the live "Traffic Heartbeat" view.

A dashboard that shows *what is actually moving through the gateway right
now*, bucketed into five axes — telemetry · RF · DUPs · diagnostics · QA
(delivery quality/reliability). Built on :mod:`monitoring.traffic_pulse`
(read-only aggregator). Mirrors the ``rns_monitor`` live-loop idiom:
``clear_screen()`` + redraw with a non-blocking ``threading.Event().wait``
countdown and ``Ctrl+C`` to exit (never ``time.sleep`` — MF010).

Drills into the existing network topology (``maps_viz``/topology handler)
so the operator can hop from "what's flowing" to "who's connected".
Per-box scope — fleet aggregation is a later phase.
"""
import logging
import socket
import threading

from backend import clear_screen
from handler_protocol import BaseHandler

logger = logging.getLogger(__name__)

# ANSI palette (matches rns_monitor / dashboard handlers).
_GREEN = "\033[0;32m"
_RED = "\033[0;31m"
_YELLOW = "\033[0;33m"
_CYAN = "\033[0;36m"
_DIM = "\033[2m"
_BOLD = "\033[1m"
_RESET = "\033[0m"

# Block status → (colour, glyph, label). "unobservable" is dim, never green —
# we don't dress up a blind spot as healthy.
_STATUS_STYLE = {
    "ok": (_GREEN, "●", "ok"),
    "quiet": (_DIM, "○", "quiet"),
    "stale": (_YELLOW, "◐", "stale"),
    "alert": (_RED, "●", "ALERT"),
    "unobservable": (_DIM, "?", "unobs"),
}


def _badge(status: str) -> str:
    colour, glyph, label = _STATUS_STYLE.get(status, (_DIM, "?", status))
    return f"{colour}{glyph} {label:<6}{_RESET}"


class TrafficPulseHandler(BaseHandler):
    """TUI handler for the live traffic-flow heartbeat."""

    handler_id = "traffic_pulse"
    menu_section = "dashboard"

    def menu_items(self):
        return [
            ("traffic_pulse",
             "Traffic Heartbeat   Live flow pulse (telem/RF/dups/QA)", None),
        ]

    def execute(self, action):
        if action == "traffic_pulse":
            self.ctx.safe_call("Traffic Heartbeat", self._menu)

    # ── menu ──────────────────────────────────────────────────────────
    def _menu(self):
        while True:
            choice = self.ctx.dialog.menu(
                "Traffic Heartbeat",
                "The heartbeat of real traffic flow (this box):",
                [
                    ("live", "Live Heartbeat (auto-refresh)"),
                    ("snapshot", "Single Snapshot"),
                    ("topology", "Open Network Topology (drill-in)"),
                    ("back", "Back"),
                ],
            )
            if choice is None or choice == "back":
                break
            if choice == "live":
                self._start_live()
            elif choice == "snapshot":
                self.ctx.safe_call("Traffic Snapshot", self._render_once)
            elif choice == "topology":
                # Reuse the whole topology handler — no new graph code.
                if not (self.ctx.registry
                        and self.ctx.registry.dispatch("maps_viz", "topology")):
                    self.ctx.dialog.msgbox(
                        "Topology Unavailable",
                        "Network topology view is not registered.")

    def _start_live(self):
        choice = self.ctx.dialog.menu(
            "Refresh Rate",
            "Select auto-refresh interval:",
            [("5", "5 seconds (default)"), ("3", "3 seconds (fast)"),
             ("10", "10 seconds"), ("30", "30 seconds (low overhead)")],
        )
        if choice is None:
            return
        try:
            interval = int(choice)
        except (ValueError, TypeError):
            interval = 5
        self._pulse_loop(interval)

    # ── data ──────────────────────────────────────────────────────────
    @staticmethod
    def _snapshot():
        # Deferred import: a problem in the aggregator must not break handler
        # registration (first-party, imported lazily per the package idiom).
        from monitoring.traffic_pulse import pulse_snapshot
        return pulse_snapshot()

    # ── live loop ─────────────────────────────────────────────────────
    def _pulse_loop(self, interval: int):
        last_good = None
        try:
            while True:
                try:
                    snap = self._snapshot()
                    last_good = snap
                    err = None
                except Exception as exc:  # contract says it won't, but be safe
                    snap, err = last_good, exc
                    logger.debug("traffic_pulse snapshot failed: %s", exc)

                clear_screen()
                self._render(snap, interval, err)

                for remaining in range(interval, 0, -1):
                    print(f"\r{_DIM}Ctrl+C to exit | next refresh in "
                          f"{remaining}s{_RESET}  ", end="", flush=True)
                    for _ in range(10):
                        threading.Event().wait(0.1)
        except KeyboardInterrupt:
            pass
        print()
        self.ctx.wait_for_enter()

    def _render_once(self):
        clear_screen()
        try:
            snap = self._snapshot()
            self._render(snap, None, None)
        except Exception as exc:
            print(f"{_RED}Failed to read traffic pulse: {exc}{_RESET}")
        self.ctx.wait_for_enter()

    # ── rendering ─────────────────────────────────────────────────────
    def _render(self, snap, interval, err):
        host = socket.gethostname()
        print(f"{_BOLD}=== Traffic Heartbeat — {host} ==={_RESET}", end="")
        if interval is not None:
            print(f"          {_DIM}refresh: {interval}s{_RESET}")
        else:
            print()

        if snap is None:
            print(f"\n{_RED}No pulse data available yet.{_RESET}")
            return

        meta = snap.get("meta", {})
        win_m = int(meta.get("window_s", 0) // 60)
        print(f"{_DIM}the heartbeat of real traffic flow · window {win_m}m · "
              f"delivery:{meta.get('delivery_source')} "
              f"status:{meta.get('status_source')} · scope:{meta.get('scope')}"
              f"{_RESET}")
        if err is not None:
            print(f"{_YELLOW}(showing last good sample — refresh failed){_RESET}")
        print()

        self._line("TELEMETRY", snap.get("telemetry", {}))
        self._line("RF", snap.get("rf", {}))
        self._line("DUPS", snap.get("dups", {}))
        self._line("DIAG", snap.get("diag", {}))
        self._qa_panel(snap.get("qa", {}))

        # Surface active traffic-relevant diagnostics underneath.
        diag = snap.get("diag", {})
        rel = [s for s in diag.get("signals", []) if s.get("traffic_relevant")]
        if rel:
            print(f"\n{_BOLD}Active traffic signals{_RESET}")
            for s in rel:
                sev = s.get("severity", "info")
                colour = _RED if sev in ("wedge", "degraded") else _YELLOW
                print(f"  {colour}{s.get('cls')}{_RESET} [{sev}] "
                      f"{_DIM}{str(s.get('detail') or '')[:60]}{_RESET}")

    def _line(self, label: str, block: dict):
        status = block.get("status", "unobservable")
        detail = block.get("detail", "")
        print(f"{label:<11}{_badge(status)} {_DIM}{detail[:60]}{_RESET}")

    def _qa_panel(self, qa: dict):
        status = qa.get("status", "unobservable")
        verdict = qa.get("verdict") or qa.get("detail") or ""
        print(f"{'QA':<11}{_badge(status)} {verdict[:60]}")
        if qa.get("status") == "unobservable":
            return
        conf = qa.get("confirmation", {})
        c_status = conf.get("status", "unobservable")
        if c_status in ("ok", "alert") and "rate" in conf:
            c_txt = (f"{conf.get('confirmed')}/{conf.get('terminal')} "
                     f"({conf.get('rate', 0):.0%}) "
                     f"[{','.join(conf.get('confirmable', []))}]")
        else:
            c_txt = f"{c_status} ({conf.get('reason', '')})"
        print(f"{'':<13}{_DIM}confirmation:{_RESET} {c_txt}")
        print(f"{'':<13}{_DIM}drops:{_RESET} failed {qa.get('failed_drops', 0)} · "
              f"benign {qa.get('benign_drops', 0)} · circuit {qa.get('circuit_open', 0)}")
        q = qa.get("queue", {})
        if q.get("status") == "ok":
            print(f"{'':<13}{_DIM}queue:{_RESET} backlog {q.get('backlog', 0)} · "
                  f"dead-letter {q.get('dead_letter', 0)}")
