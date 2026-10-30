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


class TestReadTargets:
    def test_comments_and_blanks_ignored(self, tmp_path):
        mod = _load_spool_script()
        p = tmp_path / "targets"
        p.write_text("# fan-out-unreachable boxes\nkiai\n\nmoc3 noc  # no map\n")
        assert [(t.alias, t.pinned) for t in mod.read_targets(p)] == [
            ("kiai", False), ("moc3", True)]

    def test_absent_file_is_empty(self, tmp_path):
        mod = _load_spool_script()
        assert mod.read_targets(tmp_path / "nope") == []

    def test_hostile_aliases_rejected_loudly(self, tmp_path, capsys):
        """2026-07-21 review: aliases ride into ssh argv (leading '-' parses
        as an ssh option) and shape the spool filename ('/' traverses)."""
        mod = _load_spool_script()
        p = tmp_path / "targets"
        p.write_text("-oProxyCommand=evil\n../escape\nkiai\na/b\n")
        assert [t.alias for t in mod.read_targets(p)] == ["kiai"]
        assert "SKIP invalid target line" in capsys.readouterr().err


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


# ── role-declared expectations now arrive FROM THE BOX (session 3 step 2,
# 2026-10-14): the export's `role` section carries the verdicts; the spool
# only copies them through (tests/test_box_truth_spool_wiring.py). What
# stays here is the COLLECTOR's half: the spool doc's fields reach the
# snapshot unchanged, and an older doc without them reads undecided.
class TestSpoolCarriesTheDeclaration:
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


# --- cron verdicts for map-less boxes (2026-09-07) ----------------------------
# lehua is a `field-node`: no map (fleet_snapshot never publishes its verdicts)
# and no watchdog (probe_cron_verdict_stale, the ONLY emitter of the signal
# class mini's rule matches, never runs there). Its hourly self-healing
# hosts-block cron wrote an honest verdict into a file NOTHING read — a
# producing half shipped without its consuming half (honest_failure_modes #4).

import base64  # noqa: E402

from utils import fleet_truth as ft  # noqa: E402

_CRONTAB = (
    "41 * * * * MESHFORGE_FLEET_DNS=198.51.100.9 /opt/meshforge/scripts/"
    "fleet_hosts_selfheal.sh >/dev/null 2>&1 || /opt/meshforge/scripts/"
    "cron_verdict.sh fleet_hosts_drift FAIL wrapper_crashed\n"
)


def _b64(text):
    return base64.b64encode(text.encode()).decode()


def _section(crontab=_CRONTAB, verdicts=""):
    return {"crontab_b64": _b64(crontab), "verdicts_b64": _b64(verdicts)}


def _verdict(status, ago_s, now, name="fleet_hosts_drift"):
    import datetime
    ts = datetime.datetime.utcfromtimestamp(now - ago_s).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
    return f"{ts} {name} {status} some message\n"


def _awk_reduce(full: str) -> str:
    """Reproduce the remote awk: last TWO lines per name, oldest first.

    A MODEL of the export's `last_two_verdicts` (the shell awk it replaced
    was deleted 2026-10-14). Kept here so both properties the reduction
    must preserve are tested against ONE model of it.
    """
    newest, prior = {}, {}
    for ln in full.strip().split("\n"):
        name = ln.split()[1]
        if name in newest:
            prior[name] = newest[name]
        newest[name] = ln
    out = []
    for name, last in newest.items():
        if name in prior:
            out.append(prior[name])
        out.append(last)
    return "\n".join(out) + "\n"


class TestAwkReductionCannotChangeTheAnswer:
    """The export summarises the verdict log before shipping it. It must
    preserve EVERY property the manager-side consumers read — not just the one
    its author thought of.

    Until 2026-09-13 it sent one line per name, pinned only against
    `_parse_cron_verdicts` (last-per-name). That assertion was true and it was
    the wrong parser: the fast-cron confirmation gate reads
    `_prior_verdict_statuses` (SECOND-newest per name), which one line cannot
    express, so every spooled peer's failing hourly cron sat `dark` —
    "unconfirmed first failure" — forever. Both properties are pinned now
    (honest_failure_modes #5)."""

    def test_full_log_and_reduction_parse_identically(self):
        from utils.fleet_snapshot import _parse_cron_verdicts
        now = 1_800_000_000.0
        full = (_verdict("OK", 7200, now)
                + _verdict("FAIL", 3600, now, name="other")
                + _verdict("CONCERN", 1800, now)
                + _verdict("OK", 60, now))
        assert (_parse_cron_verdicts(full, now)
                == _parse_cron_verdicts(_awk_reduce(full), now))

    def test_reduction_preserves_the_previous_verdict(self):
        """THE property the one-line reduction destroyed. Without this the
        confirmation gate can never confirm, and a persistently failing fast
        cron on a spooled box is invisible rather than red."""
        from utils.watchdog_probes_liveness import _prior_verdict_statuses
        now = 1_800_000_000.0
        full = (_verdict("OK", 7200, now)
                + _verdict("FAIL", 3600, now)
                + _verdict("FAIL", 60, now))
        assert _prior_verdict_statuses(full)["fleet_hosts_drift"] == "FAIL"
        assert (_prior_verdict_statuses(_awk_reduce(full))
                == _prior_verdict_statuses(full))


class TestPeerJudgeUsesTheBoxesOwnProbe:

    def test_fresh_ok_verdict_is_healthy(self, tmp_path):
        now = time.time()
        with patch.object(c, "truth_spool_dir", return_value=tmp_path):
            got = c.judge_spooled_schedules(
                "lehua", _section(verdicts=_verdict("OK", 60, now)), now=now)
        assert got["state"] == "healthy", got

    def test_failing_verdict_is_failed(self, tmp_path):
        """The whole point: a FAIL on a map-less, watchdog-less box now
        reaches the NOC instead of dying in a local file."""
        now = time.time()
        # TWO failing runs — what the box's own log holds and what the spool
        # now ships. Before 2026-09-13 this fixture had ONE line and the
        # assertion accepted `dark`, so it passed green while the mechanism
        # was wholly inert: a test that accepts both the working and the
        # broken outcome pins neither.
        sec = _section(verdicts=(_verdict("FAIL", 3600, now)
                                 + _verdict("FAIL", 60, now)))
        with patch.object(c, "truth_spool_dir", return_value=tmp_path):
            # 2-tick debounce: the probe confirms before firing.
            for _ in range(4):
                got = c.judge_spooled_schedules("lehua", sec, now=now)
        assert got["state"] == "failed", got

    def test_single_line_log_cannot_confirm_but_is_never_healthy(self, tmp_path):
        """The degraded case, stated honestly rather than hidden: one line
        genuinely cannot confirm a fast-cron failure. It must read `dark`
        (unobservable), never `healthy` — absence of confirmation is not
        evidence of health."""
        now = time.time()
        sec = _section(verdicts=_verdict("FAIL", 60, now))
        with patch.object(c, "truth_spool_dir", return_value=tmp_path):
            for _ in range(4):
                got = c.judge_spooled_schedules("lehua", sec, now=now)
        assert got["state"] == "dark", got

    def test_unreadable_crontab_is_dark_never_healthy(self, tmp_path):
        with patch.object(c, "truth_spool_dir", return_value=tmp_path):
            got = c.judge_spooled_schedules("lehua", {"crontab_b64": ""})
        assert got["state"] == "dark"

    def test_absent_section_says_nothing(self, tmp_path):
        with patch.object(c, "truth_spool_dir", return_value=tmp_path):
            assert c.judge_spooled_schedules("lehua", None) is None

    def test_no_wired_crons_is_inert_not_dark(self, tmp_path):
        """A box that wires no cron to cron_verdict.sh is absent-by-design.
        `inert` and `indeterminate` are different claims — collapsing them is
        how a real blind spot loses its place to stand out."""
        with patch.object(c, "truth_spool_dir", return_value=tmp_path):
            got = c.judge_spooled_schedules(
                "lehua", _section(crontab="7 * * * * /bin/true\n"))
        assert got["state"] == "inert", got

    def test_peer_judgement_never_pollutes_this_box_disposition(self, tmp_path):
        """THE safety property. Dispositions are per-CLASS for the local tick,
        so judging a peer here must not overwrite the manager's own
        cron_verdict_stale record."""
        # Patch the binding the PROBE actually calls. It does
        # `from watchdog_probe_core import note_disposition`, so patching the
        # core module is a no-op here — the first cut of this test did exactly
        # that and could never fail (caught by drilling it, 2026-09-07).
        from utils import watchdog_probes_liveness as live
        now = time.time()
        seen = []
        with patch.object(live, "note_disposition",
                          side_effect=lambda *a, **k: seen.append(a)):
            with patch.object(c, "truth_spool_dir", return_value=tmp_path):
                c.judge_spooled_schedules(
                    "lehua", _section(verdicts=_verdict("OK", 60, now)), now=now)
        assert seen == [], f"peer judging wrote global dispositions: {seen}"


class TestSchedulesCellFromSpool:

    def test_states_render(self):
        assert ft._schedules_cell_from_spool({"state": "healthy"})["state"] == ft.HEALTHY
        assert ft._schedules_cell_from_spool(
            {"state": "failed", "reason": "boom"})["state"] == ft.FAILED
        assert ft._schedules_cell_from_spool({"state": "dark"})["state"] == ft.DARK
        assert ft._schedules_cell_from_spool({})["state"] == ft.DARK

    def test_inert_is_absent_not_dark(self):
        c_ = ft._schedules_cell_from_spool({"state": "inert"})
        assert c_["state"] == ft.HEALTHY and c_.get("absent") is True

    def test_source_is_honest_about_where_it_came_from(self):
        c_ = ft._schedules_cell_from_spool({"state": "healthy"})
        assert c_["source"] == "ssh_spool.cron_verdicts"


class TestPeerJudgeRefusesASkewedClock:

    def test_peer_clock_far_ahead_is_dark_with_the_number(self, tmp_path):
        now = time.time()
        with patch.object(c, "truth_spool_dir", return_value=tmp_path):
            got = c.judge_spooled_schedules(
                "lehua", _section(verdicts=_verdict("OK", 60, now)), now=now,
                peer_now=now + 86400, fetched_at=now)
        assert got["state"] == "dark", got
        assert "skew" in got["reason"] and "lehua" in got["reason"]
        assert "+86400s" in got["reason"] or "+1d" in got["reason"]

    def test_peer_clock_far_behind_is_dark_not_failed(self, tmp_path):
        """The moc4 08-27 shape: a verdict log that reads days stale ONLY
        because the writer's clock was behind."""
        now = time.time()
        with patch.object(c, "truth_spool_dir", return_value=tmp_path):
            for _ in range(4):
                got = c.judge_spooled_schedules(
                    "lehua", _section(verdicts=_verdict("OK", 60, now)), now=now,
                    peer_now=now - 8 * 86400, fetched_at=now)
        assert got["state"] == "dark", got

    def test_ssh_latency_sized_skew_is_judged_normally(self, tmp_path):
        """`fetched_at` is stamped after the round trip, so a healthy pair
        differs by the ssh duration — seconds, never minutes."""
        now = time.time()
        with patch.object(c, "truth_spool_dir", return_value=tmp_path):
            got = c.judge_spooled_schedules(
                "lehua", _section(verdicts=_verdict("OK", 60, now)), now=now,
                peer_now=now - 25, fetched_at=now)
        assert got["state"] == "healthy", got

    def test_absent_clock_judges_as_before(self, tmp_path):
        """Half-rolled: an older spool carries no clock. No witness means
        today's judgment, not darkness for the whole fleet."""
        now = time.time()
        with patch.object(c, "truth_spool_dir", return_value=tmp_path):
            got = c.judge_spooled_schedules(
                "lehua", _section(verdicts=_verdict("OK", 60, now)), now=now,
                peer_now=None, fetched_at=now)
        assert got["state"] == "healthy", got

    def test_threshold_is_one_constant(self):
        assert c.PEER_CLOCK_SKEW_DARK_S >= 300


class TestSpoolCarriesTheIngressLedger:
    """2026-10-06: the RNS→RF tripwire ledger rides the spool RAW for
    map-less gateways; fleet_truth projects it with the gateway's own
    function. Absent file → no section → the cell reads absent."""

    def test_collector_promotes_spool_ingress(self):
        raw = {"schema": "rns_ingress_ledger/v1", "policy": "enforce",
               "listed": 3, "senders": {}}
        spool = {"schema": c.SPOOL_SCHEMA, "alias": "moc3",
                 "fetched_at": time.time() - 30.0,
                 "slo": None, "status": None, "raw_watchdog": None,
                 "ingress": raw}
        with patch.object(c, "_http_get_json", return_value=None), \
             patch.object(c, "_resolve_peer", return_value=("moc3", "dns")), \
             patch.object(c, "_read_spool", return_value=spool):
            snap = c._fetch_peer("moc3", is_self=False, port=5000)
        assert snap["spool_ingress"] == raw

    def test_direct_success_leaves_spool_ingress_none(self):
        with patch.object(c, "_http_get_json",
                          side_effect=[{"overall_status": "ready"}, {"app": {}}]), \
             patch.object(c, "_resolve_peer", return_value=("moc", "dns")):
            snap = c._fetch_peer("moc", is_self=False, port=5000)
        assert snap["spool_ingress"] is None


# ── box_truth/v1 path (pinned targets, 2026-10-14) ─────────────────────

def _bt_doc(**sections):
    return {"schema": "box_truth/v1", "sections": sections}


class TestSectionsFromDoc:
    """The spool keys come from the box_truth/v1 document (2026-10-14: the
    inline shell string and its delimiter parser are gone)."""

    def test_sections_carried_under_the_spool_keys(self):
        mod = _load_spool_script()
        out = mod.sections_from_doc(_bt_doc(
            slo={"overall_status": "ready"}, status={"app": {"name": "meshforge"}},
            raw_watchdog={"ts": 1.0, "ok": True}))
        assert out["slo"] == {"overall_status": "ready"}
        assert out["status"]["app"]["name"] == "meshforge"
        assert out["raw_watchdog"]["ok"] is True

    def test_garbage_section_is_none_not_guess(self):
        mod = _load_spool_script()
        out = mod.sections_from_doc(_bt_doc(slo="<html>502</html>", status={"app": {}}))
        assert out["slo"] is None and out["status"] == {"app": {}}
        assert out["raw_watchdog"] is None

    def test_no_document_all_none(self):
        mod = _load_spool_script()
        out = mod.sections_from_doc(None)
        assert set(out) == set(mod._SECTION_NAMES) and not any(out.values())

    def test_non_dict_json_is_none(self):
        mod = _load_spool_script()
        assert mod.sections_from_doc(_bt_doc(slo=[1, 2, 3]))["slo"] is None


class TestExportCarriesTheSameArtifacts:
    """The export (pinned path) must carry every property the legacy
    shell string carried — schedules, clock, the ingress ledger — read from
    the same places."""

    def test_export_reads_both_artifacts(self):
        from utils import box_truth_collect as bcol
        src = (Path(bcol.__file__)).read_text()
        assert '["crontab", "-l"]' in src
        assert "cron_verdicts.log" in src and "b64encode" in src

    def test_export_reads_the_peer_clock(self, tmp_path):
        from types import SimpleNamespace
        from utils import box_truth_collect as bcol
        src = bcol.Sources(home=tmp_path, env={}, watchdog_path=tmp_path / "w",
                           proc_tcp=(), dev_globs=(), http_get=lambda u: None,
                           runner=lambda argv, **kw: SimpleNamespace(returncode=0, stdout=b""))
        now = bcol.collect_sections(src)[0]["clock"]["now"]
        assert isinstance(now, int) and abs(now - time.time()) < 5

    def test_export_reads_the_gateways_own_ledger_path(self, monkeypatch):
        """ONE path, two consumers (hfm #5): the export must read exactly
        where the gateway writes."""
        from utils import box_truth_collect as bcol
        from gateway import rns_ingress_policy as rip
        from utils.paths import get_real_user_home
        monkeypatch.delenv("XDG_DATA_HOME", raising=False)
        src = bcol.Sources(home=get_real_user_home(), env={})
        assert Path(rip.default_ledger_path()) == src.ingress_path

    def test_the_REAL_export_reduction_is_what_was_modelled(self):
        """`_awk_reduce` above is a MODEL of the shipped reduction. A model
        that drifts from the artifact keeps the other two tests green while
        the real transport is broken — the 2026-07-25 resolver lesson. So run
        the ACTUAL function the export ships and require it to agree."""
        from utils.box_truth_collect import last_two_verdicts
        now = 1_800_000_000.0
        full = (_verdict("OK", 7200, now)
                + _verdict("FAIL", 3600, now, name="other")
                + _verdict("FAIL", 1800, now)
                + _verdict("FAIL", 60, now))
        # Same content, per name, as the model — order across names is not
        # part of the contract (both consumers key by name); order WITHIN a
        # name is (oldest first), and both parsers are asserted on it.
        from utils.fleet_snapshot import _parse_cron_verdicts
        from utils.watchdog_probes_liveness import _prior_verdict_statuses
        real, model = last_two_verdicts(full), _awk_reduce(full)
        assert (_parse_cron_verdicts(real, now)
                == _parse_cron_verdicts(model, now))
        assert _prior_verdict_statuses(real) == _prior_verdict_statuses(model)
        assert _prior_verdict_statuses(real)["fleet_hosts_drift"] == "FAIL"


class TestSpoolTransportOutcomeReachesTheCell:
    """S1 (security pass 2026-10-14): a fresh spool that recorded a FAILED
    read must say which failure — never fold "grant refused" into "no
    response" (Q11: the fix is named)."""

    def _snap(self, transport):
        spool = {"schema": c.SPOOL_SCHEMA, "alias": "moc3",
                 "fetched_at": time.time() - 30.0,
                 "slo": None, "status": None, "raw_watchdog": None,
                 "transport": transport}
        with patch.object(c, "_http_get_json", return_value=None), \
             patch.object(c, "_resolve_peer", return_value=("moc3", "dns")), \
             patch.object(c, "_read_spool", return_value=spool):
            return c._fetch_peer("moc3", is_self=False, port=5000)

    def test_refused_grant_names_the_fix(self):
        snap = self._snap({"outcome": "refused_grant", "rc": 255,
                           "reason": "grant refused on moc3 (key or source) — fix"})
        assert snap["error"] == "grant refused on moc3 (key or source) — fix"
        assert snap["status"] is None and snap["slo"] is None

    def test_identity_mismatch_is_its_own_reason(self):
        snap = self._snap({"outcome": "identity_mismatch",
                           "reason": "identity mismatch on moc3: x — data discarded"})
        assert snap["error"].startswith("identity mismatch on moc3")

    def test_old_spool_without_transport_reads_as_before(self):
        snap = self._snap(None)
        assert "ssh spool fresh but empty-handed" in snap["error"]


class TestExportSectionErrorsReachTheCell:
    """Review B 3: a section the box WITHHELD (tripwire) or failed to read
    rendered as 'no data (source absent)' — asserting absence for a source
    that exists. The export's own reason now reaches the cell."""

    def _truth(self, section_errors):
        from utils import fleet_truth as ft
        spool = {"schema": c.SPOOL_SCHEMA, "alias": "moc3",
                 "fetched_at": time.time() - 30.0,
                 "slo": None, "status": None,
                 "raw_watchdog": {"ok": True, "written_at": time.time(),
                                  "host": "moc3", "signals": []},
                 "raw_mini": None,
                 "transport": {"outcome": "ok"},
                 "box_truth": {"section_errors": section_errors}}
        with patch.object(c, "_http_get_json", return_value=None), \
             patch.object(c, "_resolve_peer", return_value=("moc3", "dns")), \
             patch.object(c, "_read_spool", return_value=spool):
            snap = c._fetch_peer("moc3", is_self=False, port=5000)
        return ft.build_box_truth(snap, now=time.time(), signal_classes=[])

    def test_withheld_section_says_withheld(self):
        t = self._truth({"raw_mini": "withheld: credential-shaped content (tripwire)"})
        mini = t["subsystems"]["mini"]
        assert mini["state"] == "dark"
        assert "withheld" in mini["reason"] and "source absent" not in mini["reason"]

    def test_withheld_watchdog_on_a_box_with_no_other_block(self):
        from utils import fleet_truth as ft
        spool = {"schema": c.SPOOL_SCHEMA, "alias": "moc3", "fetched_at": time.time() - 30.0,
                 "slo": None, "status": None, "raw_watchdog": None, "raw_mini": None,
                 "transport": {"outcome": "ok"},
                 "box_truth": {"section_errors": {"raw_watchdog": "withheld: tripwire"}}}
        with patch.object(c, "_http_get_json", return_value=None), \
             patch.object(c, "_resolve_peer", return_value=("moc3", "dns")), \
             patch.object(c, "_read_spool", return_value=spool):
            snap = c._fetch_peer("moc3", is_self=False, port=5000)
        t = ft.build_box_truth(snap, now=time.time(), signal_classes=[])
        assert t["subsystems"]["watchdog"]["state"] == "dark"
        assert "withheld" in t["subsystems"]["watchdog"]["reason"]

    def test_no_section_errors_reads_as_before(self):
        t = self._truth({})
        assert t["subsystems"]["mini"]["reason"] == "no data (source absent)"


class TestPinnedReadExplainsCenterSideFaults:
    """Review B 2/6: a missing or unreadable center key, and an unreadable
    registry, must not render as 'grant refused … push' / 'stamp it'."""

    def test_missing_center_key_is_named(self, tmp_path):
        mod = _load_spool_script()
        from utils.box_truth_center import Target
        _s, transport, _m = mod.fetch_box(Target("moc3", True), home=tmp_path,
                                          expect_of=lambda a: ("SHA256:" + "H" * 43, None))
        assert transport["reason"].startswith("center key")
        assert "missing" in transport["reason"]

    def test_unreadable_registry_is_named(self, tmp_path):
        mod = _load_spool_script()
        from utils.box_truth_center import Target
        key = tmp_path / ".ssh" / "meshforge_noc_ed25519"
        key.parent.mkdir()
        key.write_text("k")
        _s, transport, _m = mod.fetch_box(Target("moc3", True), home=tmp_path,
                                          expect_of=lambda a: (None, "registry unreadable"))
        assert transport["outcome"] == "identity_unpinned"
        assert "registry unreadable" in transport["reason"]


class TestUnflaggedTargetsRefuseLoudly:
    """2026-10-14, every target pinned → the legacy interactive-key read was
    DELETED. A target line without ``noc`` is not read at all — no
    interactive ssh, no export call — and its spool row says why and what
    to do, so ``/fleet`` shows it DARK with the fix rather than silently
    absent (a box that drops out of the pane is the 07-20 class). Writing a
    row rather than skipping is deliberate: a skipped target leaves a STALE
    spool file that reads "unreachable" for the wrong reason."""

    def _run(self, tmp_path, targets_text):
        mod = _load_spool_script()
        home = tmp_path / "home"
        (home / ".config" / "meshforge").mkdir(parents=True)
        (home / ".config" / "meshforge" / "truth_spool_targets").write_text(targets_text)
        (home / ".ssh").mkdir(mode=0o700)
        mod.br.noc_key_path(home).write_text("scratch")   # a readable center key
        calls = []

        def read(alias, expect, *, key_path=None, **_kw):
            calls.append((alias, key_path is not None))
            return mod.br.ReadResult(alias=alias, outcome="unreachable",
                                     pinned=key_path is not None, stderr_head="timeout")
        with patch.object(mod, "get_real_user_home", return_value=home), \
             patch.object(mod, "truth_spool_dir", return_value=tmp_path / "spool"), \
             patch.object(mod, "_expect_of", return_value=("SHA256:" + "A" * 43, None)), \
             patch.object(mod.br, "read_box", side_effect=read):
            assert mod.main() == 0
        return mod, calls, tmp_path / "spool"

    def test_unflagged_target_is_never_read_and_names_push(self, tmp_path):
        mod, calls, spool = self._run(tmp_path, "kiai\nmoc3 noc  # gateway\n")
        assert calls == [("moc3", True)]            # only the pinned target was read
        kiai = json.loads((spool / "kiai.json").read_text())
        t = kiai["transport"]
        assert t["outcome"] == "refused_grant" and t["pinned"] is False
        assert "box_truth_grant.py push kiai --confirm kiai" in t["reason"]
        assert all(kiai[k] is None for k in mod._SECTION_NAMES)
        moc3 = json.loads((spool / "moc3.json").read_text())
        assert moc3["transport"]["outcome"] == "unreachable"

    def test_tick_line_names_the_grant_not_a_pin(self, tmp_path, capsys):
        """2026-10-15: the tick line said ``[pinned]``/``[unpinned]`` for the
        targets-file GRANT flag while ``identity_unpinned`` and the registry
        ``expect_hostkey`` use "pinned" for the HOST KEY — same word, two
        meanings, misread live in the step-7 drill. The line and the
        refusal reason name the grant."""
        mod, _calls, spool = self._run(tmp_path, "kiai\nmoc3 noc\n")
        out = capsys.readouterr().out
        assert "kiai [no grant] refused_grant" in out
        assert "moc3 [granted] unreachable" in out
        assert "pinned" not in out
        reason = json.loads((spool / "kiai.json").read_text())["transport"]["reason"]
        assert "not pinned" not in reason and "no grant flag" in reason

    def test_legacy_read_path_is_gone(self):
        mod = _load_spool_script()
        for name in ("_REMOTE_CMD", "fetch_box_legacy", "parse_sections",
                     "_SECTIONS", "_SECTION_KEYS", "SSH_TIMEOUT_S"):
            assert not hasattr(mod, name), f"{name} still present"
        src = Path(mod.__file__).read_text(encoding="utf-8")
        assert "subprocess" not in src and "__TRUTH_" not in src


# ── delta read 2026-10-15: MF parity of MA's F1/F5 spool-leg checks ───────

def test_mf_reads_no_spool_doc_that_names_another_box(tmp_path, monkeypatch):
    import json as _json
    import time as _time
    import utils.fleet_truth_collector as ftc
    monkeypatch.setattr(ftc, "truth_spool_dir", lambda: tmp_path)
    (tmp_path / "moc3.json").write_text(_json.dumps({
        "schema": ftc.SPOOL_SCHEMA, "alias": "kiai", "fetched_at": _time.time(),
        "status": {"x": 1}}))
    assert ftc._read_spool("moc3") is None


def test_mf_never_reads_a_spool_for_an_invalid_alias(tmp_path, monkeypatch):
    import json as _json
    import time as _time
    import utils.fleet_truth_collector as ftc
    spool = tmp_path / "spool"
    spool.mkdir()
    monkeypatch.setattr(ftc, "truth_spool_dir", lambda: spool)
    (tmp_path / "x.json").write_text(_json.dumps({     # planted OUTSIDE the spool dir
        "schema": ftc.SPOOL_SCHEMA, "alias": "../x", "fetched_at": _time.time()}))
    assert ftc._read_spool("../x") is None
