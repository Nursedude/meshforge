"""In-app remediation wiring for diagnostic views (In-Domain Principle / MF018).

Three TUI diagnostic views used to PRINT `sudo systemctl …` shell instructions
when they found a down service. This pins the cure: they now route the down
service through the proven service-remediation surface
(`service_remediation.offer_service_fix` / `offer_service_fix_chooser`,
profile-gated, applied via `service_check` SSOT) — the fix comes to the operator
where they saw the problem, no shell-escape.

Also pins the latent bug fixed in the same pass: `traffic_inspector` read the
service state with `ServiceStatus.get("active")`, but ServiceStatus is a
dataclass with no `.get()` — the call always raised and the meshtasticd check
silently fell to "could not check" (a degraded check masking real state, the
honest-failure-modes class). The check now uses `bool(status)` and is honest.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

# Match the import shape used by the running TUI (cd into src; relative imports).
SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(SRC / "launcher_tui"))


class _Svc:
    """Minimal ServiceStatus stand-in: truthy iff available (matches the real
    dataclass's __bool__ → .available, and has NO .get())."""

    def __init__(self, available: bool):
        self.available = available

    def __bool__(self) -> bool:
        return self.available


# --------------------------------------------------------------- node_health


def test_latency_probe_offers_fix_for_down_services(monkeypatch):
    import handlers.node_health as nh
    from handlers.node_health import NodeHealthHandler

    h = NodeHealthHandler()
    h.ctx = MagicMock()
    monkeypatch.setattr(nh, "_HAS_LATENCY", True)
    monkeypatch.setattr(nh, "DEFAULT_SERVICES",
                        [("meshtasticd_tcp", "localhost", 4403)])
    monkeypatch.setattr(nh, "probe_tcp",
                        lambda host, port, timeout=2.0: (False, 0.0))
    seen = {}
    monkeypatch.setattr("service_remediation.collect_degraded_services",
                        lambda names: [("meshtasticd", False)])
    monkeypatch.setattr("service_remediation.offer_service_fix_chooser",
                        lambda ctx, deg: seen.update(deg=deg) or True)

    h._service_latency_probe()

    assert seen.get("deg") == [("meshtasticd", False)]
    # Chooser engaged -> the method returns early, no bare wait-for-enter.
    h.ctx.wait_for_enter.assert_not_called()


def test_latency_probe_no_chooser_when_all_up(monkeypatch):
    import handlers.node_health as nh
    from handlers.node_health import NodeHealthHandler

    h = NodeHealthHandler()
    h.ctx = MagicMock()
    monkeypatch.setattr(nh, "_HAS_LATENCY", True)
    monkeypatch.setattr(nh, "DEFAULT_SERVICES",
                        [("meshtasticd_tcp", "localhost", 4403)])
    monkeypatch.setattr(nh, "probe_tcp",
                        lambda host, port, timeout=2.0: (True, 5.0))
    called = {"chooser": False}
    monkeypatch.setattr("service_remediation.offer_service_fix_chooser",
                        lambda ctx, deg: called.update(chooser=True) or True)

    h._service_latency_probe()

    assert called["chooser"] is False  # nothing down -> no offer
    h.ctx.wait_for_enter.assert_called_once()


# ------------------------------------------------------------ traffic_inspector


def _toggle_capture_handler(monkeypatch):
    import handlers.traffic_inspector as ti
    from handlers.traffic_inspector import TrafficInspectorHandler

    h = TrafficInspectorHandler()
    h.ctx = MagicMock()
    # Reach the start path, then the capture-FAILED diag branch.
    monkeypatch.setattr(ti, "start_packet_capture", lambda: False)
    monkeypatch.setattr(ti, "stop_packet_capture", lambda: True)
    monkeypatch.setattr(ti, "is_capture_running", lambda: False)
    return h


def test_toggle_capture_failed_offers_meshtasticd_fix(monkeypatch):
    h = _toggle_capture_handler(monkeypatch)
    monkeypatch.setattr("utils.service_check.check_service",
                        lambda name: _Svc(False))  # meshtasticd DOWN
    offered = {}
    monkeypatch.setattr("service_remediation.offer_service_fix",
                        lambda ctx, name, running: offered.update(
                            name=name, running=running))

    h._toggle_capture()

    assert offered.get("name") == "meshtasticd"
    assert offered.get("running") is False
    # No shell-escape in the diagnostic text.
    shown = " ".join(str(a) for c in h.ctx.dialog.msgbox.call_args_list
                     for a in c[0])
    assert "systemctl" not in shown


def test_toggle_capture_failed_no_offer_when_meshtasticd_up(monkeypatch):
    # The bug fix: meshtasticd healthy must read as healthy (bool(status)),
    # so no fix is offered. Pre-fix, .get() raised -> always "could not check".
    h = _toggle_capture_handler(monkeypatch)
    monkeypatch.setattr("utils.service_check.check_service",
                        lambda name: _Svc(True))  # meshtasticd UP
    offered = {}
    monkeypatch.setattr("service_remediation.offer_service_fix",
                        lambda ctx, name, running: offered.update(name=name))

    h._toggle_capture()

    assert "name" not in offered
    shown = " ".join(str(a) for c in h.ctx.dialog.msgbox.call_args_list
                     for a in c[0])
    assert "meshtasticd is running" in shown


# --------------------------------------------------------------- radio_menu


def test_radio_op_error_offers_fix_when_meshtasticd_down(monkeypatch):
    from handlers.radio_menu import RadioMenuHandler

    h = RadioMenuHandler()
    h.ctx = MagicMock()
    monkeypatch.setattr("service_remediation.collect_degraded_services",
                        lambda names: [("meshtasticd", False)])
    seen = {}
    monkeypatch.setattr("service_remediation.offer_service_fix",
                        lambda ctx, name, running: seen.update(name=name))

    h._handle_radio_op_error(RuntimeError("boom"))

    assert seen.get("name") == "meshtasticd"
    args = " ".join(str(a) for a in h.ctx.dialog.msgbox.call_args[0])
    assert "boom" in args            # the real error surfaced
    assert "systemctl" not in args   # no shell-escape


def test_radio_op_error_no_offer_when_meshtasticd_up(monkeypatch):
    from handlers.radio_menu import RadioMenuHandler

    h = RadioMenuHandler()
    h.ctx = MagicMock()
    monkeypatch.setattr("service_remediation.collect_degraded_services",
                        lambda names: [])  # nothing down
    seen = {}
    monkeypatch.setattr("service_remediation.offer_service_fix",
                        lambda ctx, name, running: seen.update(name=name))

    h._handle_radio_op_error(RuntimeError("boom"))

    assert "name" not in seen  # service healthy -> no presumptuous offer
