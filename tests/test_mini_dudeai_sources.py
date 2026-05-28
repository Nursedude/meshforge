"""Source adapter tests."""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest

from mini_dudeai import FileMtimeSource, HttpJsonSource, JsonFileSource


def test_json_file_emits_one_condition_per_item(tmp_path):
    p = tmp_path / "data.json"
    p.write_text(json.dumps({"signals": [
        {"subject": "alpha", "detail": "one", "extra1": 1},
        {"subject": "beta", "detail": "two", "extra2": 2},
    ]}))
    src = JsonFileSource(
        path=str(p), kind="signal_class",
        extractor=lambda d: d.get("signals", []),
    )
    conds = list(src.collect())
    assert [c.subject for c in conds] == ["alpha", "beta"]
    assert conds[0].extras == {"extra1": 1}
    assert conds[1].extras == {"extra2": 2}


def test_json_file_emits_source_error_on_missing(tmp_path):
    src = JsonFileSource(
        path=str(tmp_path / "nope.json"), kind="x", extractor=lambda d: [],
    )
    conds = list(src.collect())
    assert len(conds) == 1
    assert conds[0].kind == "source_error"
    assert "not found" in conds[0].detail


def test_json_file_emits_source_error_on_extractor_crash(tmp_path):
    p = tmp_path / "data.json"
    p.write_text(json.dumps({"signals": []}))
    src = JsonFileSource(
        path=str(p), kind="x",
        extractor=lambda d: [None][0].nonexistent,  # AttributeError
    )
    conds = list(src.collect())
    assert len(conds) == 1
    assert conds[0].kind == "source_error"


def test_file_mtime_quiet_when_fresh(tmp_path):
    p = tmp_path / "fresh"
    p.write_text("")
    src = FileMtimeSource(path=str(p), max_age_s=3600)
    assert list(src.collect()) == []


def test_file_mtime_emits_when_stale(tmp_path):
    p = tmp_path / "stale"
    p.write_text("")
    # backdate it
    old = 1000.0
    os.utime(str(p), (old, old))
    src = FileMtimeSource(path=str(p), max_age_s=10, kind="my_stale")
    conds = list(src.collect())
    assert len(conds) == 1
    assert conds[0].kind == "my_stale"
    assert conds[0].extras["threshold_seconds"] == 10


def test_file_mtime_source_error_on_missing(tmp_path):
    src = FileMtimeSource(path=str(tmp_path / "nope"), max_age_s=10)
    conds = list(src.collect())
    assert len(conds) == 1
    assert conds[0].kind == "source_error"


def test_http_json_source_error_on_unreachable_url():
    src = HttpJsonSource(
        url="http://127.0.0.1:1/nope",  # almost certainly nothing listening on :1
        kind="x", extractor=lambda d: [], timeout=1.0,
    )
    conds = list(src.collect())
    assert len(conds) == 1
    assert conds[0].kind == "source_error"
    assert "unreachable" in conds[0].detail
