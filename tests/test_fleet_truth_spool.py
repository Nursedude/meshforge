"""Tests for the ssh-spool fallback (fleet-truth arc, 2026-07-19).

Contract: the collector consults the spool ONLY when the direct HTTP fan-out
failed, and only while FRESH — a stale/absent/garbage spool reads dark, never
last-known-healthy. A map-less box's raw watchdog.json is shaped through the
SAME transform + staleness threshold the status handler uses (one constant,
two consumers). The spool writer parses delimited remote output defensively:
garbage in any section → None for that section, never a guess.
"""
import importlib.util
import json
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from utils import fleet_truth_collector as c  # noqa: E402
from utils._map_status_endpoints import watchdog_block_from_payload  # noqa: E402


def _load_spool_script():
    path = Path(__file__).resolve().parent.parent / "scripts" / "fleet_truth_spool.py"
    spec = importlib.util.spec_from_file_location("fleet_truth_spool", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestParseSections:
    def test_all_three_sections(self):
        mod = _load_spool_script()
        blob = ("__TRUTH_SLO__\n" + json.dumps({"overall_status": "ready"}) +
                "\n__TRUTH_STATUS__\n" + json.dumps({"app": {"name": "meshforge"}}) +
                "\n__TRUTH_RAWWD__\n" + json.dumps({"ts": 1.0, "ok": True}))
        out = mod.parse_sections(blob)
        assert out["slo"] == {"overall_status": "ready"}
        assert out["status"]["app"]["name"] == "meshforge"
        assert out["raw_watchdog"]["ok"] is True

    def test_garbage_section_is_none_not_guess(self):
        mod = _load_spool_script()
        blob = ("__TRUTH_SLO__\n<html>502 bad gateway</html>\n"
                "__TRUTH_STATUS__\n" + json.dumps({"app": {}}) +
                "\n__TRUTH_RAWWD__\n")
        out = mod.parse_sections(blob)
        assert out["slo"] is None
        assert out["status"] == {"app": {}}
        assert out["raw_watchdog"] is None

    def test_empty_blob_all_none(self):
        mod = _load_spool_script()
        out = mod.parse_sections("")
        assert out == {"slo": None, "status": None, "raw_watchdog": None}

    def test_non_dict_json_is_none(self):
        mod = _load_spool_script()
        out = mod.parse_sections("__TRUTH_SLO__\n[1,2,3]\n")
        assert out["slo"] is None


class TestReadTargets:
    def test_comments_and_blanks_ignored(self, tmp_path):
        mod = _load_spool_script()
        p = tmp_path / "targets"
        p.write_text("# fan-out-unreachable boxes\nkiai\n\nmoc3  # no map\n")
        assert mod.read_targets(p) == ["kiai", "moc3"]

    def test_absent_file_is_empty(self, tmp_path):
        mod = _load_spool_script()
        assert mod.read_targets(tmp_path / "nope") == []

    def test_hostile_aliases_rejected_loudly(self, tmp_path, capsys):
        """2026-07-21 review: aliases ride into ssh argv (leading '-' parses
        as an ssh option) and shape the spool filename ('/' traverses)."""
        mod = _load_spool_script()
        p = tmp_path / "targets"
        p.write_text("-oProxyCommand=evil\n../escape\nkiai\na/b\n")
        assert mod.read_targets(p) == ["kiai"]
        assert "SKIP invalid alias" in capsys.readouterr().err


def _write_spool(tmp_path, alias, *, age_s=10.0, **sections):
    doc = {"schema": c.SPOOL_SCHEMA, "alias": alias,
           "fetched_at": time.time() - age_s,
           "slo": None, "status": None, "raw_watchdog": None}
    doc.update(sections)
    d = tmp_path / "truth_spool"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{alias}.json").write_text(json.dumps(doc))
    return d


class TestReadSpool:
    def test_fresh_spool_returned(self, tmp_path, monkeypatch):
        d = _write_spool(tmp_path, "kiai", age_s=10.0, slo={"overall_status": "ready"})
        monkeypatch.setattr(c, "truth_spool_dir", lambda: d)
        doc = c._read_spool("kiai")
        assert doc is not None and doc["slo"]["overall_status"] == "ready"

    def test_stale_spool_is_none_never_last_known_healthy(self, tmp_path, monkeypatch):
        d = _write_spool(tmp_path, "kiai", age_s=c.SPOOL_STALE_S + 5,
                         slo={"overall_status": "ready"})
        monkeypatch.setattr(c, "truth_spool_dir", lambda: d)
        assert c._read_spool("kiai") is None

    def test_future_stamped_spool_is_stale_not_fresh(self, tmp_path, monkeypatch):
        """honest_failure_modes #6 (RTC-less fleet): a writer stamp AHEAD of
        the reader's clock is a clock fault, never evidence of freshness."""
        d = _write_spool(tmp_path, "kiai", age_s=-3600.0,
                         slo={"overall_status": "ready"})
        monkeypatch.setattr(c, "truth_spool_dir", lambda: d)
        assert c._read_spool("kiai") is None

    def test_wrong_schema_is_none(self, tmp_path, monkeypatch):
        d = tmp_path / "truth_spool"
        d.mkdir()
        (d / "kiai.json").write_text(json.dumps({"schema": "other/v9",
                                                 "fetched_at": time.time()}))
        monkeypatch.setattr(c, "truth_spool_dir", lambda: d)
        assert c._read_spool("kiai") is None

    def test_absent_and_garbage_are_none(self, tmp_path, monkeypatch):
        d = tmp_path / "truth_spool"
        d.mkdir()
        (d / "moc3.json").write_text("{torn")
        monkeypatch.setattr(c, "truth_spool_dir", lambda: d)
        assert c._read_spool("kiai") is None
        assert c._read_spool("moc3") is None


class TestFetchPeerSpoolFallback:
    def test_direct_fail_fresh_spool_answers(self):
        spool = {"schema": c.SPOOL_SCHEMA, "alias": "kiai",
                 "fetched_at": time.time() - 30.0,
                 "slo": {"overall_status": "ready"},
                 "status": {"app": {"name": "meshforge"}}, "raw_watchdog": None}
        with patch.object(c, "_http_get_json", return_value=None), \
             patch.object(c, "_resolve_peer", return_value=("kiai", "dns")), \
             patch.object(c, "_read_spool", return_value=spool):
            snap = c._fetch_peer("kiai", is_self=False, port=5000)
        assert snap["resolution_method"] == "ssh_spool"
        assert snap["slo"] == {"overall_status": "ready"}
        assert snap["error"] is None
        # observation age runs from the spool fetch, not from now
        assert snap["answered_at"] == pytest.approx(spool["fetched_at"])

    def test_direct_success_never_consults_spool(self):
        with patch.object(c, "_http_get_json",
                          side_effect=[{"overall_status": "ready"}, {"app": {}}]), \
             patch.object(c, "_resolve_peer", return_value=("moc", "dns")), \
             patch.object(c, "_read_spool") as spool:
            snap = c._fetch_peer("moc", is_self=False, port=5000)
        spool.assert_not_called()
        assert snap["resolution_method"] == "dns"

    def test_mapless_box_raw_watchdog_synthesized_via_shared_transform(self):
        raw = {"ts": time.time() - 20.0, "ok": True, "probe_count": 7,
               "signals": [], "coverage": {"service_inactive": {"disp": "clean"}}}
        spool = {"schema": c.SPOOL_SCHEMA, "alias": "moc3",
                 "fetched_at": time.time() - 30.0,
                 "slo": None, "status": None, "raw_watchdog": raw}
        with patch.object(c, "_http_get_json", return_value=None), \
             patch.object(c, "_resolve_peer", return_value=("moc3", "dns")), \
             patch.object(c, "_read_spool", return_value=spool):
            snap = c._fetch_peer("moc3", is_self=False, port=5000)
        wd = snap["status"]["watchdog"]
        assert wd["installed"] is True and wd["ok"] is True
        assert wd["coverage"] == {"service_inactive": {"disp": "clean"}}

    def test_stale_raw_watchdog_reads_stale_through_shared_threshold(self):
        from utils._map_status_endpoints import WATCHDOG_STALE_S
        raw = {"ts": time.time() - (WATCHDOG_STALE_S + 60), "ok": True, "signals": []}
        spool = {"schema": c.SPOOL_SCHEMA, "alias": "moc3",
                 "fetched_at": time.time() - 30.0,
                 "slo": None, "status": None, "raw_watchdog": raw}
        with patch.object(c, "_http_get_json", return_value=None), \
             patch.object(c, "_resolve_peer", return_value=("moc3", "dns")), \
             patch.object(c, "_read_spool", return_value=spool):
            snap = c._fetch_peer("moc3", is_self=False, port=5000)
        wd = snap["status"]["watchdog"]
        assert wd["ok"] is False and "stale" in wd["reason"]
        # and the fleet-truth classifier maps that to DARK, not healthy/failed
        from utils import fleet_truth as ft
        assert ft.classify_block(wd, source="t")["state"] == ft.DARK

    def test_empty_handed_spool_stays_dark_with_honest_error(self):
        spool = {"schema": c.SPOOL_SCHEMA, "alias": "kiai",
                 "fetched_at": time.time() - 30.0,
                 "slo": None, "status": None, "raw_watchdog": None}
        with patch.object(c, "_http_get_json", return_value=None), \
             patch.object(c, "_resolve_peer", return_value=("kiai", "dns")), \
             patch.object(c, "_read_spool", return_value=spool):
            snap = c._fetch_peer("kiai", is_self=False, port=5000)
        assert snap["status"] is None and snap["slo"] is None
        assert "empty-handed" in snap["error"]
        assert snap["answered_at"] is None


class TestWatchdogBlockFromPayload:
    def test_fresh_payload_ok(self):
        b = watchdog_block_from_payload({"ts": time.time() - 5, "ok": True,
                                         "signals": []})
        assert b["ok"] is True and "reason" not in b

    def test_stale_payload_not_ok(self):
        b = watchdog_block_from_payload({"ts": time.time() - 9999, "ok": True,
                                         "signals": []})
        assert b["ok"] is False and "stale" in b["reason"]

    def test_non_dict_is_malformed_never_healthy(self):
        b = watchdog_block_from_payload(None)  # type: ignore[arg-type]
        assert b["ok"] is False


# ── role-declared HTTP-surface expectation (2026-07-20) ──────────────────
# A gateway-only box declares `meshforge-map: disabled` (too heavy for a ~1GB
# board), which removes /api/status AND /fleet/slo — the only windows onto its
# mini + services cells. The spool resolves that declaration into a decided
# boolean so the NOC can accept the blind spot instead of darkening the whole
# fleet verdict forever. `None` (undecidable) must never collapse to False.
class TestHttpSurfaceExpected:
    def test_gateway_only_role_declares_no_http_surface(self):
        mod = _load_spool_script()
        assert mod.http_surface_expected({"role": "gateway-only"}) is False

    def test_a_map_running_role_expects_the_surface(self):
        mod = _load_spool_script()
        # Any catalog role that does NOT disable/absent meshforge-map.
        assert mod.http_surface_expected({"role": "full-noc"}) in (True, None)

    @pytest.mark.parametrize("deploy", [
        None, {}, {"role": ""}, {"role": 123},
        {"role": "no-such-role-in-the-catalog"}, "not-a-dict",
    ])
    def test_undecidable_is_none_never_false(self, deploy):
        """THE anti-silence guard, at the source. False is what stops a dark
        box tainting the verdict, so an absent/unknown/unreadable declaration
        must stay None — guessing here would quiet a genuinely broken box."""
        mod = _load_spool_script()
        assert mod.http_surface_expected(deploy) is None

    def test_unloadable_catalog_is_none(self):
        mod = _load_spool_script()
        with patch.object(mod, "Path", side_effect=RuntimeError("boom")):
            assert mod.http_surface_expected({"role": "gateway-only"}) is None


class TestSpoolCarriesTheDeclaration:
    def test_deployment_section_is_parsed(self):
        mod = _load_spool_script()
        raw = ("__TRUTH_SLO__\n\n__TRUTH_STATUS__\n\n__TRUTH_RAWWD__\n\n"
               '__TRUTH_DEPLOY__\n{"role": "gateway-only"}\n')
        out = mod.parse_sections(raw)
        assert out["deployment"] == {"role": "gateway-only"}

    def test_collector_promotes_role_and_expectation(self, tmp_path):
        """End-to-end through the collector: a map-less box's role reaches the
        snapshot in the SAME shape an HTTP box reports it, so the builder needs
        no special case."""
        doc = {"schema": c.SPOOL_SCHEMA, "alias": "moc3", "fetched_at": time.time(),
               "slo": None, "status": None,
               "raw_watchdog": {"ok": True, "written_at": time.time(),
                                "host": "moc3", "signals": []},
               "deployment": {"role": "gateway-only"},
               "http_surface_expected": False}
        (tmp_path / "moc3.json").write_text(json.dumps(doc))
        with patch.object(c, "truth_spool_dir", return_value=tmp_path), \
             patch.object(c, "_http_get_json", return_value=None):
            snap = c._fetch_peer("moc3", is_self=False, port=5000)
        assert snap["resolution_method"] == "ssh_spool"
        assert snap["http_surface_expected"] is False
        assert snap["status"]["app"]["role"] == "gateway-only"

    def test_collector_defaults_expectation_to_none(self, tmp_path):
        """A spool doc written by an OLDER writer carries no expectation field;
        it must read undecided (and keep tainting), not False."""
        doc = {"schema": c.SPOOL_SCHEMA, "alias": "moc3", "fetched_at": time.time(),
               "slo": None, "status": None,
               "raw_watchdog": {"ok": True, "written_at": time.time(),
                                "host": "moc3", "signals": []}}
        (tmp_path / "moc3.json").write_text(json.dumps(doc))
        with patch.object(c, "truth_spool_dir", return_value=tmp_path), \
             patch.object(c, "_http_get_json", return_value=None):
            snap = c._fetch_peer("moc3", is_self=False, port=5000)
        assert snap["http_surface_expected"] is None


class TestWatchdogExpected:
    """The watchdog exemption is judged from the box's DECLARED role, exactly
    like the HTTP surface — and shares one helper with it so the two cannot
    drift apart in what counts as "not expected"."""

    def test_field_node_expects_no_watchdog(self):
        mod = _load_spool_script()
        assert mod.watchdog_expected({"role": "field-node"}) is False

    def test_a_role_that_runs_one_expects_it(self):
        mod = _load_spool_script()
        assert mod.watchdog_expected({"role": "gateway-only"}) is True
        assert mod.watchdog_expected({"role": "primary"}) is True

    def test_map_and_watchdog_are_judged_INDEPENDENTLY(self):
        """gateway-only runs no map but DOES run a watchdog. Collapsing the
        two would silence the one core signal a map-less gateway still owes
        us (moc3's watchdog arrives as a raw file over the spool)."""
        mod = _load_spool_script()
        assert mod.http_surface_expected({"role": "gateway-only"}) is False
        assert mod.watchdog_expected({"role": "gateway-only"}) is True

    def test_undecidable_stays_none_and_never_false(self):
        """None is load-bearing: False stops the box tainting the verdict, so
        guessing it would quiet a genuinely broken box."""
        mod = _load_spool_script()
        for raw in ({}, {"role": ""}, {"role": "no-such-role"}, None, "nope"):
            assert mod.watchdog_expected(raw) is None, raw
