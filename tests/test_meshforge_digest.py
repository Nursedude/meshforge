"""monitoring.meshforge_digest — the manager box's situation digest, moved into
the repo 2026-09-30 from an untracked ~/meshforge_digest.py (it imported repo
mini_dudeai yet nothing versioned or deploy-restarted it; it ran 3 days stale).

Operator-specific values (cloud map URL, expected federation backoffs) are
config now, not code. Pinned here: no config -> the cloud section is inert
(never a gap, never an alarm, never a hardcoded host); expected backoffs come
from config; the digest writes atomically under the operator's home.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))

import monitoring.meshforge_digest as dg  # noqa: E402


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.delenv("MESHFORGE_DIGEST_CLOUD_URL", raising=False)
    with patch.object(dg, "get_real_user_home", return_value=tmp_path):
        yield tmp_path


def _cfg(home, data):
    p = home / ".config" / "meshforge"
    p.mkdir(parents=True)
    (p / "digest.json").write_text(json.dumps(data))


def _fed(peers):
    return {"federation": {"peer_status": peers, "last_sync": None}}


def test_no_config_means_cloud_inert_and_default_federation(home):
    cfg = dg.load_config()
    assert cfg["cloud_url"] == "" and cfg["expected_backoff"] == {}
    assert cfg["federation_url"] == "http://localhost:5000/api/status"
    s = dg.sect_cloudmap(cfg)
    assert s.gap is None and s.posture == "green", (s.gap, s.posture)
    assert "not configured" in s.lines[0]


def test_expected_backoff_comes_from_config(home):
    _cfg(home, {"expected_backoff": {"gw-box": "gateway-only, backoff expected"}})
    cfg = dg.load_config()
    peers = [{"peer_name": "GW-Box-7", "in_backoff": True, "backoff_multiplier": 4},
             {"peer_name": "other-box", "in_backoff": False, "reachable": True}]
    with patch.object(dg, "_fetch_json", return_value=(_fed(peers), None)):
        s = dg.sect_federation(cfg)
    assert s.lines[0] == "peers: 1 ok / 1 expected-backoff / 0 unexpected", s.lines
    assert s.posture != "red"


def test_unexpected_backoff_is_red(home):
    cfg = dg.load_config()                                   # no expectations
    peers = [{"peer_name": "gw-box", "in_backoff": True, "backoff_multiplier": 4}]
    with patch.object(dg, "_fetch_json", return_value=(_fed(peers), None)):
        s = dg.sect_federation(cfg)
    assert s.posture == "red" and "investigate" in " ".join(s.lines)


def test_env_overrides_cloud_url(home, monkeypatch):
    _cfg(home, {"cloud_url": "http://from-config:8808/api/status"})
    monkeypatch.setenv("MESHFORGE_DIGEST_CLOUD_URL", "http://from-env:8808/api/status")
    assert dg.load_config()["cloud_url"] == "http://from-env:8808/api/status"


@pytest.mark.parametrize("body", [
    "{not json",                                         # hand-edit typo
    '["cloud_url"]',                                     # wrong top-level type
    '{"cloud_url": "http://x:8808/api/status", "expected_backoff": ["gw"]}',
    '{"expected_backoff": {"": "matches everyone"}}',     # empty key
])
def test_broken_config_is_a_gap_never_inert(home, body):
    """A config that EXISTS but is unusable must not read like no config:
    that would silently switch the cloud-map check off under a green dot
    (reader pair, 2026-09-30). It must also never kill the cycle."""
    p = home / ".config" / "meshforge"
    p.mkdir(parents=True)
    (p / "digest.json").write_text(body)
    cfg = dg.load_config()
    assert cfg["config_error"], body
    assert cfg["expected_backoff"] == {}
    if not cfg["cloud_url"]:
        s = dg.sect_cloudmap(cfg)
        assert s.gap and "unusable" in s.gap and s.posture != "green", (s.gap, s.posture)
        assert "inert" not in " ".join(s.lines)
    peers = [{"peer_name": "gw-box", "in_backoff": False, "reachable": True}]
    with patch.object(dg, "_fetch_json", return_value=(_fed(peers), None)):
        f = dg.sect_federation(cfg)
    assert f.gap and "NOT applied" in f.gap and f.posture != "green"
    with patch.object(dg, "_fetch_json", return_value=(None, "URLError: offline")):
        text = dg.build_digest(cfg)                      # never raises
    assert "NOMINAL" not in text


def test_green_tldr_does_not_claim_an_unwatched_map(home):
    with patch.object(dg, "sect_federation", return_value=dg.Section("f", "x")), \
         patch.object(dg, "sect_monitors", return_value=dg.Section("m", "x")), \
         patch.object(dg, "sect_mini_dudeai", return_value=dg.Section("d", "x")):
        text = dg.build_digest(dg.load_config())
    tldr = next(l for l in text.splitlines() if l.startswith("## TL;DR"))
    assert "NOMINAL" in tldr and "map fresh" not in tldr, tldr


def test_write_digest_is_atomic_under_the_operator_home(home):
    with patch.object(dg, "_fetch_json", return_value=(None, "URLError: offline")):
        text = dg.write_digest()
    out = home / "situation_digest.md"
    assert out.read_text() == text
    assert not (home / "situation_digest.md.tmp").exists()
    assert "Situation Digest" in text and "GAP" in text          # offline federation said
