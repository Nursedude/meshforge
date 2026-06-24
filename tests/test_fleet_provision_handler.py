"""Tests for the Fleet Architecture (fleet_provision) TUI handler + its pure
core. The core takes provision_role as an injected `mod`, so these never touch
real systemd; the handler is driven through a FakeDialog.

Run: python3 -m pytest tests/test_fleet_provision_handler.py -v
"""

import os
import sys
import types

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src', 'launcher_tui'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(__file__))

from handler_test_utils import make_handler_context  # noqa: E402
from handlers import _fleet_provision_core as core    # noqa: E402
from handlers.fleet_provision import FleetProvisionHandler  # noqa: E402

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))


class FakeAction:
    def __init__(self, verb, item, current="x/y", desired="enabled", detail=""):
        self.verb = verb
        self.item = item
        self.current = current
        self.desired = desired
        self.detail = detail


def _stub_mod(role="collector", overrides=None, actions=None):
    """A stand-in for scripts/provision_role.py — no systemd, no files."""
    mod = types.SimpleNamespace()
    mod.DEFAULT_ROLES_FILE = "/dev/null"
    mod.read_role = lambda: role
    mod.read_overrides = lambda: (overrides or {})
    mod.load_roles = lambda path: {"roles": {
        "collector": {}, "full-gateway": {}, "gateway-only": {},
        "primary": {"singleton": True}, "cloud-publisher": {"singleton": True},
    }}
    mod.resolve_role = lambda catalog, r: {"services": {}}
    mod.plan = lambda role_def, ov: (actions if actions is not None else [])
    return mod


@pytest.fixture(scope="module")
def presets_doc():
    return core.load_presets(core.presets_path(REPO))


# ---------------------------------------------------------------------------
# Pure core
# ---------------------------------------------------------------------------
class TestCore:
    def test_load_presets_real_catalog(self, presets_doc):
        assert "full-bridge" in presets_doc["presets"]
        assert "legs" in presets_doc

    def test_gateway_overlay_full_bridge(self, presets_doc):
        overlay = core.gateway_overlay_for("full-bridge", presets_doc)
        assert overlay.get("rns_bridge_enabled") is True
        assert overlay.get("mqtt_bridge.json_enabled") is True
        assert overlay.get("bridge_mode") == "mqtt_bridge"

    def test_gateway_overlay_non_bridge_is_empty(self, presets_doc):
        assert core.gateway_overlay_for("monitor-ingest", presets_doc) == {}
        assert core.gateway_overlay_for("hub-manager", presets_doc) == {}

    def test_preview_preset_filters_to_change_verbs(self, presets_doc):
        mod = _stub_mod(actions=[
            FakeAction("enable", "meshforge-gateway"),
            FakeAction("noop", "rnsd"),
        ])
        prev = core.preview_preset(mod, "full-bridge", presets_doc, {})
        assert prev["role"] == "full-gateway"
        # noop filtered out — only the real change remains
        assert [a.item for a in prev["actions"]] == ["meshforge-gateway"]
        assert prev["gateway_overlay"].get("rns_bridge_enabled") is True

    def test_current_box_reports_drift(self, presets_doc):
        mod = _stub_mod(role="collector", actions=[
            FakeAction("disable", "meshforge-gateway"),
            FakeAction("noop", "meshforge-map"),
        ])
        info = core.current_box(mod)
        assert info["role"] == "collector"
        assert [a.item for a in info["drift"]] == ["meshforge-gateway"]

    def test_current_box_no_role_is_unknown_not_clean(self):
        """No role set → drift must be None (UNKNOWN), never [] (which the UI
        would render as 'no drift')."""
        info = core.current_box(_stub_mod(role=None))
        assert info["drift"] is None


# ---------------------------------------------------------------------------
# Handler (driven through FakeDialog; provision_role stubbed)
# ---------------------------------------------------------------------------
class TestHandler:
    def test_menu_items_exposes_entry(self):
        items = FleetProvisionHandler().menu_items()
        assert items and items[0][0] == "fleet_provision"

    def test_registered_in_get_all_handlers(self):
        from handlers import get_all_handlers
        assert FleetProvisionHandler in get_all_handlers()

    def test_main_menu_renders_current_box(self, monkeypatch, presets_doc):
        monkeypatch.setattr(core, "load_provision_role",
                            lambda *a, **k: _stub_mod(
                                role="full-gateway",
                                actions=[FakeAction("enable", "meshforge-gateway")]))
        monkeypatch.setattr(core, "load_presets", lambda *a, **k: presets_doc)
        ctx = make_handler_context()
        ctx.dialog._menu_returns = ["current", "back"]
        h = FleetProvisionHandler()
        h.set_context(ctx)
        h._main_menu()
        textboxes = [c for c in ctx.dialog.calls if c[0] == "textbox"]
        assert textboxes, "expected a textbox render"
        assert "Declared role" in textboxes[-1][1][1]

    def test_preview_renders_dry_run_and_overlay(self, monkeypatch, presets_doc):
        monkeypatch.setattr(core, "load_provision_role",
                            lambda *a, **k: _stub_mod(
                                role="collector",
                                actions=[FakeAction("enable", "meshforge-gateway")]))
        monkeypatch.setattr(core, "load_presets", lambda *a, **k: presets_doc)
        ctx = make_handler_context()
        # main: pick catalog; catalog: pick full-bridge then back; main: back
        ctx.dialog._menu_returns = ["catalog", "full-bridge", "back", "back"]
        h = FleetProvisionHandler()
        h.set_context(ctx)
        h._main_menu()
        textboxes = [c for c in ctx.dialog.calls if c[0] == "textbox"]
        assert textboxes, "expected a preview textbox"
        body = textboxes[-1][1][1]
        assert "DRY-RUN ONLY" in body
        assert "rns_bridge_enabled" in body
