"""Spool subsystem enrichment for map-less gateways (2026-07-22).

A gateway-only box (role gateway-only, meshforge-map disabled to avoid the
#17/#75 PhoneAPI contention) has no HTTP truth surface, so mini/radio/services
were accepted-blind on the fleet monitor. This enriches the ssh-spool to carry
those three via NON-HTTP raw reads — exactly the pattern watchdog.json already
uses — so the cells render real state instead of being suppressed.

Contract under test (honest_failure_modes):
- mini_block_from_payload is a shared SSOT transform (like watchdog's); a stale
  mini reads dark, never green-with-old-numbers.
- radio_connection_from_probe is the SAME connected/mode logic the map uses
  (tcp-listen / usb only — never a #17-contending :4403 connect).
- the collector synthesizes status.mini_dudeai + slo.radio + spool_services for
  a map-less box, so the builder's existing cells populate non-DARK.
- the services cell is sourced HONESTLY (ssh_spool.systemctl, not the full SLO
  overall_status which implies more); an enabled-but-inactive unit is FAILED,
  and a box with no enabled units seen is DARK, never green.
"""
import importlib.util
import time
from pathlib import Path

import pytest


# ── shared transforms (SSOT) ────────────────────────────────────────────
class TestMiniBlockFromPayload:
    def _fn(self):
        from utils._map_status_endpoints import mini_block_from_payload
        return mini_block_from_payload

    def test_fresh_payload_ok(self):
        blk = self._fn()({"last_tick_ts": time.time(), "rule_count": 63,
                          "rules": {}}, now=time.time())
        assert blk["installed"] is True and blk["ok"] is True
        assert blk["age_s"] is not None and blk["age_s"] < 5

    def test_stale_payload_reads_dark_not_green(self):
        old = time.time() - 10_000
        blk = self._fn()({"last_tick_ts": old, "rules": {}}, now=time.time())
        assert blk["ok"] is False
        assert "stale" in blk.get("reason", "")

    def test_active_rules_extracted(self):
        payload = {"last_tick_ts": time.time(),
                   "rules": {"r1": {"rule_id": "r1", "subject": "cron",
                                    "currently_active": True, "last_detail": "x"}}}
        blk = self._fn()(payload, now=time.time())
        assert any(a["rule_id"] == "r1" for a in blk["active_rules"])

    def test_non_dict_is_malformed(self):
        blk = self._fn()("nope")
        assert blk["ok"] is False and "malformed" in blk["reason"]


class TestRadioConnectionFromProbe:
    def _fn(self):
        from utils._map_status_endpoints import radio_connection_from_probe
        return radio_connection_from_probe

    def test_tcp_listening_is_connected_tcp(self):
        assert self._fn()(True, False) == (True, "tcp")

    def test_usb_only_is_connected_serial(self):
        assert self._fn()(False, True) == (True, "serial")

    def test_neither_is_disconnected(self):
        assert self._fn()(False, False) == (False, "none")


# ── collector map-less synthesis ─────────────────────────────────────────
def _collector():
    return importlib.import_module("utils.fleet_truth_collector")


class TestCollectorSynthesizesMapLessSubsystems:
    def _spool_doc(self, **extra):
        doc = {"schema": "truth_spool/v1", "alias": "moc3",
               "fetched_at": time.time(), "slo": None, "status": None,
               "raw_watchdog": {"ok": True, "ts": time.time()},
               "deployment": {"role": "gateway-only"},
               "http_surface_expected": False}
        doc.update(extra)
        return doc

    def test_raw_mini_becomes_status_mini_dudeai(self, monkeypatch):
        c = _collector()
        doc = self._spool_doc(raw_mini={"last_tick_ts": time.time(), "rules": {}})
        monkeypatch.setattr(c, "_read_spool", lambda alias, **k: doc)
        monkeypatch.setattr(c, "_http_get_json", lambda *a, **k: None)
        snap = c._fetch_peer("moc3", is_self=False, port=5000)
        assert snap["resolution_method"] == "ssh_spool"
        assert isinstance(snap["status"].get("mini_dudeai"), dict)
        assert snap["status"]["mini_dudeai"]["installed"] is True

    def test_radio_probe_becomes_slo_radio(self, monkeypatch):
        c = _collector()
        doc = self._spool_doc(radio_probe={"tcp_listening": True, "usb_present": False})
        monkeypatch.setattr(c, "_read_spool", lambda alias, **k: doc)
        monkeypatch.setattr(c, "_http_get_json", lambda *a, **k: None)
        snap = c._fetch_peer("moc3", is_self=False, port=5000)
        assert snap["slo"]["radio"]["connected"] is True

    def test_services_map_attached_for_builder(self, monkeypatch):
        c = _collector()
        doc = self._spool_doc(services={"meshtasticd": {"active": "active",
                                                        "enabled": "enabled"}})
        monkeypatch.setattr(c, "_read_spool", lambda alias, **k: doc)
        monkeypatch.setattr(c, "_http_get_json", lambda *a, **k: None)
        snap = c._fetch_peer("moc3", is_self=False, port=5000)
        assert snap["spool_services"] == {"meshtasticd": {"active": "active",
                                                          "enabled": "enabled"}}


# ── builder services cell + integration ──────────────────────────────────
def _ft():
    return importlib.import_module("utils.fleet_truth")


class TestBuilderServicesFromSpool:
    def _snap(self, services, **extra):
        snap = {"alias": "moc3", "resolution_method": "ssh_spool",
                "status": {"watchdog": {"installed": True, "ok": True,
                                        "ts": time.time(), "age_s": 1.0},
                           "app": {"name": "meshforge", "role": "gateway-only"}},
                "slo": None, "error": None, "answered_at": time.time(),
                "http_surface_expected": False, "spool_services": services}
        snap.update(extra)
        return snap

    def test_all_enabled_active_is_healthy(self):
        ft = _ft()
        box = ft.build_box_truth(self._snap(
            {"meshtasticd": {"active": "active", "enabled": "enabled"},
             "meshforge-gateway.service": {"active": "active", "enabled": "enabled"}}),
            now=time.time(), signal_classes=[])
        cell = box["subsystems"]["services"]
        assert cell["state"] == ft.HEALTHY
        assert not cell.get("accepted_blind")
        assert cell["source"] == "ssh_spool.systemctl"

    def test_enabled_but_inactive_is_failed(self):
        ft = _ft()
        box = ft.build_box_truth(self._snap(
            {"meshtasticd": {"active": "active", "enabled": "enabled"},
             "meshforge-gateway.service": {"active": "inactive", "enabled": "enabled"}}),
            now=time.time(), signal_classes=[])
        cell = box["subsystems"]["services"]
        assert cell["state"] == ft.FAILED
        assert "meshforge-gateway" in cell["reason"]

    def test_no_enabled_units_is_dark_not_green(self):
        ft = _ft()
        box = ft.build_box_truth(self._snap(
            {"meshforge-map.service": {"active": "inactive", "enabled": "disabled"}}),
            now=time.time(), signal_classes=[])
        cell = box["subsystems"]["services"]
        assert cell["state"] == ft.DARK

    def test_mapless_box_with_spool_subsystems_not_accepted_blind(self):
        """The whole point: a gateway-only box with spooled mini/radio/services
        renders them as real state, NOT accepted_blind."""
        ft = _ft()
        snap = self._snap(
            {"meshtasticd": {"active": "active", "enabled": "enabled"}})
        snap["status"]["mini_dudeai"] = {"installed": True, "ok": True,
                                         "ts": time.time(), "age_s": 2.0}
        snap["slo"] = {"radio": {"connected": True, "mode": "tcp"}}
        box = ft.build_box_truth(snap, now=time.time(), signal_classes=[])
        for name in ("mini", "radio", "services"):
            cell = box["subsystems"][name]
            assert cell["state"] == ft.HEALTHY, f"{name} -> {cell}"
            assert not cell.get("accepted_blind"), f"{name} still accepted_blind"
