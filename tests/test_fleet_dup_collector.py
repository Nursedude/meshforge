"""Tests for the cross-box dedup rollup collector (STEP 4c-iii).

The collector ssh-collects per-box content_id_view state, JOINs it, and
writes the rollup. The discipline under test: a box that is reachable-but-
not-a-gateway (rc 1) is silently skipped, a down/unreachable box (rc 255)
is RECORDED (not hidden, not faked as covered), and a file-present-but-
garbage box surfaces as a coverage gap rather than vanishing.
"""
from __future__ import annotations

import json

from utils import fleet_dup_collector as col


NOW = 2_000_000.0


def _state(host, confirmed, *, ts=NOW, unconfirmable_sent=0):
    cf = [{"content_id": c, "recipient": r, "confirmed": n}
          for (c, r, n) in confirmed]
    return json.dumps({
        "schema": 1, "host": host, "ts": ts,
        "unconfirmable_sent": unconfirmable_sent,
        "content_id_view": {
            "tracked_events": len(cf), "untracked_events": 0,
            "unattributed_events": 0, "tracked_pairs": len(cf),
            "confirmed_pairs": len(cf), "local_duplicate_pairs": 0,
            "local_duplicate_deliveries": 0, "confirmed": cf,
            "confirmed_truncated": False,
        },
    })


class TestParseFleetHosts:
    def test_strips_comments_and_blanks(self):
        txt = "# header\nmoc\n\nmoc3   # gateway\n  \nmoc5\n"
        assert col.parse_fleet_hosts(txt) == ["moc", "moc3", "moc5"]


class TestCollectBoxViews:
    def test_rc0_valid_is_covered(self):
        reader = lambda h: (0, _state(h, [("c", "r", 1)]))
        views, notes = col.collect_box_views(["moc"], reader=reader)
        assert len(views) == 1 and "state" in views[0]
        assert notes == []

    def test_rc1_no_file_is_skipped_quietly(self):
        reader = lambda h: (1, "")
        views, notes = col.collect_box_views(["moc1"], reader=reader)
        assert views == []
        assert notes[0]["host"] == "moc1" and notes[0]["rc"] == 1

    def test_rc255_unreachable_is_recorded_not_faked(self):
        reader = lambda h: (255, "")
        views, notes = col.collect_box_views(["moc3"], reader=reader)
        # NOT a covered box (can't masquerade as healthy)…
        assert views == []
        # …but RECORDED so a down gateway is visible (rc 255 != rc 1).
        assert notes[0]["rc"] == 255

    def test_rc0_garbage_surfaces_as_error_box(self):
        reader = lambda h: (0, "{not json")
        views, notes = col.collect_box_views(["moc"], reader=reader)
        assert views[0]["host"] == "moc" and "error" in views[0]

    def test_local_host_uses_local_reader(self):
        reader = lambda h: (255, "")  # would fail if used for local
        local = lambda: (0, _state("self", [("c", "r", 1)]))
        views, notes = col.collect_box_views(
            ["self", "moc3"], local_host="self",
            reader=reader, local_reader=local)
        hosts = {v["host"] for v in views}
        assert "self" in hosts  # local read succeeded via local_reader
        assert any(n["host"] == "moc3" for n in notes)


class TestBuildFleetDupRollup:
    def test_dup_a_detected_end_to_end(self):
        def reader(h):
            if h in ("moc", "moc3"):
                return (0, _state(h, [("c1:abc", "6b1a0120", 1)]))
            return (1, "")
        rollup = col.build_fleet_dup_rollup(
            hosts=["moc", "moc1", "moc3"], local_host="volcano",
            reader=reader, now=NOW)
        assert rollup["status"] == "ok"
        assert rollup["fleet_duplicate_pairs"] == 1
        assert sorted(rollup["collector"]["covered"]) == ["moc", "moc3"]
        # moc1 (no file) recorded as a quiet note, not a covered box
        assert any(n["host"] == "moc1" and n["rc"] == 1
                   for n in rollup["collector"]["notes"])

    def test_one_gateway_down_is_indeterminate_with_note(self):
        def reader(h):
            if h == "moc":
                return (0, _state(h, [("c1", "r", 1)]))
            if h == "moc3":
                return (255, "")  # the OTHER gateway is unreachable
            return (1, "")
        rollup = col.build_fleet_dup_rollup(
            hosts=["moc", "moc3"], local_host="volcano",
            reader=reader, now=NOW)
        # honest: can't see moc3 -> indeterminate, NOT "0 dups healthy"
        assert rollup["status"] == "indeterminate"
        assert any(n["host"] == "moc3" and n["rc"] == 255
                   for n in rollup["collector"]["notes"])

    def test_collector_meta_attached(self):
        rollup = col.build_fleet_dup_rollup(
            hosts=["moc"], local_host="volcano",
            reader=lambda h: (1, ""), now=NOW)
        assert rollup["collector"]["host"] == "volcano"
        assert rollup["collector"]["attempted"] == ["moc"]


class TestWriteRollup:
    def test_roundtrip(self, tmp_path):
        rollup = {"status": "ok", "fleet_duplicate_pairs": 0}
        target = tmp_path / "fleet_dup_view.json"
        assert col.write_rollup(rollup, path=target) is True
        assert json.loads(target.read_text())["status"] == "ok"

    def test_best_effort_on_bad_path(self, tmp_path):
        blocker = tmp_path / "f"
        blocker.write_text("x")
        bad = blocker / "sub" / "r.json"
        assert col.write_rollup({"a": 1}, path=bad) is False


class TestMain:
    def test_main_writes_and_returns_zero(self, tmp_path, monkeypatch):
        out = tmp_path / "r.json"
        monkeypatch.setattr(col, "build_fleet_dup_rollup",
                            lambda: {"status": "ok",
                                     "fleet_duplicate_pairs": 0,
                                     "covered_hosts": []})
        rc = col.main(["--out", str(out), "--quiet"])
        assert rc == 0
        assert out.exists()
