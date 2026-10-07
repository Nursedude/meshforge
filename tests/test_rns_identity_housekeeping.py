"""scripts/rns_identity_housekeeping.py — report stale identities, remove ONLY
what the operator names (2026-10-07: 14 days, no auto-remove; "a firm
remove doesn't mean that hash, if useful, can't re-announce").
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "rns_identity_housekeeping.py"
_spec = importlib.util.spec_from_file_location("rns_identity_housekeeping", _SCRIPT)
hk = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hk)

DAY = 86400.0
NOW = 100 * DAY
ACTIVE = "1f68b5463f1b0e8bbac685c109bd1760"
STALE = "7cda0fabcf4e714c421813c22897a565"
PEER = "3dfbdb5d24c6de195ae4f3c0f56b5ea5"


@pytest.fixture
def box(tmp_path):
    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir()
    gw = {"rns": {"bridge_source_identities": [ACTIVE, STALE, PEER],
                  "bridge_source_policy": "observe",
                  "default_lxmf_destination": [STALE],
                  "peer_gateway_destinations": [PEER]},
          "other": {"keep": True}}
    (cfg_dir / "gateway.json").write_text(json.dumps(gw, indent=2))
    (cfg_dir / "lxmf_identities.json").write_text(json.dumps(
        {"identities": {ACTIVE: {"name": "moc1 echo", "purpose": "probe"}}}))
    ledger = tmp_path / "ledger.json"
    ledger.write_text(json.dumps({
        "schema": "rns_ingress_ledger/v1", "policy": "observe", "listed": 3,
        "updated_at": NOW - 60, "watch_since": NOW - 20 * DAY,
        "listed_ids": [ACTIVE, STALE, PEER],
        "listed_seen": {ACTIVE: {"last_seen": NOW - 1 * DAY, "seen": 40},
                        PEER: {"last_seen": NOW - 2 * DAY, "seen": 9}},
        "senders": {}}))
    return cfg_dir, ledger


def _rows(box):
    cfg_dir, ledger = box
    return {r["hash"]: r for r in hk.build_rows(cfg_dir, ledger, now=NOW)}


class TestReport:
    def test_verdicts_from_measured_inbound(self, box):
        rows = _rows(box)
        assert rows[ACTIVE]["verdict"] == "active"
        assert rows[PEER]["verdict"] == "active"
        assert rows[STALE]["verdict"] == "stale"

    def test_roles_and_names(self, box):
        rows = _rows(box)
        assert rows[STALE]["roles"] == ["allowlist", "destination"]
        assert rows[PEER]["roles"] == ["allowlist", "peer"]
        assert rows[ACTIVE]["name"] == "moc1 echo"
        assert rows[STALE]["name"].startswith("unknown:")

    def test_destination_only_use_is_flagged_not_hidden(self, box):
        # the measured quantity is INBOUND; a delivery destination may be used
        # outbound — the row must say so rather than call it unused
        assert "destination" in _rows(box)[STALE]["note"]

    def test_too_early_when_window_not_watched(self, box):
        cfg_dir, ledger = box
        doc = json.loads(ledger.read_text())
        doc["watch_since"] = NOW - 3 * DAY
        ledger.write_text(json.dumps(doc))
        assert {r["verdict"] for r in hk.build_rows(cfg_dir, ledger, now=NOW)} == {"too early"}

    def test_no_ledger_is_unknown_not_stale(self, box, tmp_path):
        cfg_dir, _ = box
        rows = hk.build_rows(cfg_dir, tmp_path / "missing.json", now=NOW)
        assert {r["verdict"] for r in rows} == {"unknown (no ledger)"}


class TestRemove:
    def test_removes_named_hash_everywhere_with_backup(self, box):
        cfg_dir, _ = box
        out = hk.remove(cfg_dir, [STALE], stamp="T")
        gw = json.loads((cfg_dir / "gateway.json").read_text())
        assert STALE not in gw["rns"]["bridge_source_identities"]
        assert gw["rns"]["default_lxmf_destination"] == []
        assert gw["other"] == {"keep": True}
        assert (cfg_dir / "gateway.json.bak-housekeeping-T").exists()
        assert out[STALE] == ["bridge_source_identities", "default_lxmf_destination"]

    def test_refuses_short_or_malformed_hashes(self, box):
        cfg_dir, _ = box
        with pytest.raises(ValueError):
            hk.remove(cfg_dir, [STALE[:8]], stamp="T")
        assert STALE in json.loads((cfg_dir / "gateway.json").read_text())["rns"]["bridge_source_identities"]

    def test_refuses_a_hash_that_is_in_no_list(self, box):
        cfg_dir, _ = box
        with pytest.raises(ValueError):
            hk.remove(cfg_dir, ["ab" * 16], stamp="T")
        assert not list(cfg_dir.glob("*.bak-housekeeping-*"))

    def test_main_without_remove_never_writes(self, box, capsys):
        cfg_dir, ledger = box
        before = (cfg_dir / "gateway.json").read_text()
        rc = hk.main(["--config-dir", str(cfg_dir), "--ledger", str(ledger),
                      "--now", str(NOW)])
        assert rc == 0
        assert (cfg_dir / "gateway.json").read_text() == before
        out = capsys.readouterr().out
        assert "stale" in out and STALE[:8] in out and "moc1 echo" in out
