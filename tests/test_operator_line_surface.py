"""BIC-15 item 2 — the operator line is not done until the APP shows it
(2026-10-15). The gateway runs as its own process, so the line publishes a
status file and the TUI renders it with a freshness check; absent-by-design
(line OFF) reads OFF, never UNKNOWN; a stale or missing report while ON reads
UNKNOWN, never as the last-known state. Also the two pre-acceptance review
fixes: S1 (an ANSWER leaves a witness line) and S2 (the line's help does not
advertise ``node``, which it cannot answer — no directory).
"""
from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from gateway.operator_line import OperatorLine, default_status_path  # noqa: E402
from gateway.operator_line_config import OperatorLineConfig  # noqa: E402
from oracle import NocSnapshot  # noqa: E402
from utils.operator_line_view import STATUS_SCHEMA, render_lines  # noqa: E402

from _fake_rns_identity import make_identity_class  # noqa: E402

OP = "a1" * 16
STRANGER = "cc" * 16


@pytest.fixture(autouse=True)
def _allow_rns_tx_in_this_file():
    from utils import tx_guard
    with tx_guard.allow_rns_egress():
        yield


def _cfg(**kw):
    base = dict(enabled=True, operators=[OP], announce_interval_sec=0,
                cooldown_s=0.0, alert_poll_s=30.0, alert_min_interval_s=0.0)
    base.update(kw)
    return OperatorLineConfig.from_dict(base)


def _snap(signals=None, box="moc"):
    s = NocSnapshot(now=1000.0, box=box)
    s.wd_installed, s.wd_stale, s.wd_ok = True, False, True
    s.wd_signals = signals or []
    return s


def _line(tmp_path, cfg=None, snap=None, rns=None, lxmf=None, wall=None):
    sent = []
    line = OperatorLine(
        cfg or _cfg(), snapshot_fn=lambda: snap or _snap(),
        deliver_fn=lambda d, b: sent.append((d, b)) or True,
        now_fn=lambda: 1000.0, rns_module=rns, lxmf_module=lxmf,
        identity_path=tmp_path / "id", storage_path=tmp_path / "store",
        status_path=tmp_path / "status.json", wall_fn=wall or (lambda: 5000.0))
    return line, sent


# ── the writer ────────────────────────────────────────────────────────────

def test_default_status_path_is_in_the_gateways_writable_data_dir():
    # ProtectHome=read-only + ReadWritePaths ~/.local/share/meshforge (#60).
    p = default_status_path()
    assert p.parent.name == "meshforge" and p.parent.parent.name == "share"


def test_publish_writes_the_status_with_a_wall_stamp(tmp_path):
    line, _ = _line(tmp_path)
    line.publish_status()
    doc = json.loads((tmp_path / "status.json").read_text())
    assert doc["schema"] == STATUS_SCHEMA and doc["written_at"] == 5000.0
    assert doc["running"] is False and doc["operators"] == 1
    assert doc["last_page_at"] is None
    assert (os.stat(tmp_path / "status.json").st_mode & 0o077) == 0


def test_a_page_records_its_wall_time(tmp_path):
    sig = {"class": "service_inactive", "subject": "rnsd", "severity": "degraded"}
    line, sent = _line(tmp_path, snap=_snap([sig]))
    line.alert_tick()
    assert sent, "precondition: the tick paged"
    line.publish_status()
    assert json.loads((tmp_path / "status.json").read_text())["last_page_at"] == 5000.0


def test_an_unwritable_status_is_counted_never_raised(tmp_path):
    line, _ = _line(tmp_path)
    line._status_path = tmp_path / "no" / "such" / "dir" / "s.json"
    (tmp_path / "no").write_text("a file where a dir must be")
    line.publish_status()
    assert line.stats["status_write_errors"] == 1


def test_a_refused_identity_is_published(tmp_path):
    """Acceptance leg 4 must be VISIBLE: a zero-byte identity refuses the
    start, and the pane names why instead of reading a quiet OFF."""
    from unittest.mock import MagicMock
    rns = MagicMock(name="RNS")
    rns.Identity = make_identity_class()
    (tmp_path / "id").write_bytes(b"")
    line, _ = _line(tmp_path, rns=rns, lxmf=MagicMock(name="LXMF"))
    assert line.start() is False
    doc = json.loads((tmp_path / "status.json").read_text())
    assert doc["running"] is False and doc["identity_error"]


# ── S1 / S2 ───────────────────────────────────────────────────────────────

def test_an_answer_leaves_a_witness_line(tmp_path, caplog):
    line, _ = _line(tmp_path)
    with caplog.at_level(logging.INFO, logger="gateway.operator_line"):
        reply = line.respond(OP, "status", signature_validated=True)
    rec = [r.getMessage() for r in caplog.records if "ANSWERED" in r.getMessage()]
    assert len(rec) == 1
    assert OP[:8] in rec[0] and "intent=status" in rec[0]
    assert f"bytes={len(reply.encode())}" in rec[0]
    assert OP not in rec[0]                      # a prefix, never the whole hash


def test_the_lines_help_does_not_advertise_node(tmp_path):
    line, _ = _line(tmp_path)
    for q in ("help", "", "bogus"):
        reply = line.respond(OP, q, signature_validated=True)
        assert "node" not in reply and "status" in reply, (q, reply)


# ── the reader ────────────────────────────────────────────────────────────

def _doc(**kw):
    d = {"schema": STATUS_SCHEMA, "written_at": 1000.0, "running": True,
         "destination": "0123456789abcdef0123456789abcdef", "operators": 1,
         "closed": False, "alerts_enabled": True, "watchdog_blind": False,
         "active_alerts": 0, "config_errors": [], "identity_error": None,
         "last_page_at": None, "alert_poll_s": 30.0,
         "stats": {"answered": 2, "strangers": 1, "unverified": 3, "pages": 0,
                   "deliver_failed": 0, "status_write_errors": 0}}
    d.update(kw)
    return d


def test_off_reads_off_with_the_knob():
    text = "\n".join(render_lines(False, None, now=1000.0))
    assert "OFF" in text and "operator_line" in text and "UNKNOWN" not in text


def test_on_without_a_report_is_unknown():
    text = "\n".join(render_lines(True, None, now=1000.0))
    assert "UNKNOWN" in text and "RUNNING" not in text


def test_a_stale_report_is_unknown_with_its_age():
    text = "\n".join(render_lines(True, _doc(written_at=1000.0), now=1000.0 + 600))
    assert "UNKNOWN" in text and "600" in text
    assert "RUNNING" not in text      # never the last-known state as current


def test_a_future_stamped_report_is_unknown():
    assert "UNKNOWN" in "\n".join(render_lines(True, _doc(written_at=9999.0), now=1000.0))


def test_a_fresh_report_shows_address_count_and_refusals():
    text = "\n".join(render_lines(True, _doc(last_page_at=940.0), now=1010.0))
    assert "RUNNING" in text and "0123456789abcdef0123456789abcdef" in text
    assert "operators: 1" in text
    assert "strangers 1" in text and "unverified 3" in text and "answered 2" in text
    assert "70s ago" in text                       # last page
    assert "revoke" in text.lower()


def test_a_refused_identity_reads_not_running_with_the_reason():
    text = "\n".join(render_lines(True, _doc(running=False, identity_error="zero-byte id"),
                                  now=1000.0))
    assert "NOT RUNNING" in text and "zero-byte id" in text


def test_a_closed_line_says_it_answers_nobody():
    text = "\n".join(render_lines(True, _doc(operators=0, closed=True), now=1000.0))
    assert "answers nobody" in text


def test_a_wrong_schema_is_unknown_not_parsed():
    assert "UNKNOWN" in "\n".join(render_lines(True, _doc(schema="x/v0"), now=1000.0))


# ── the TUI wiring ────────────────────────────────────────────────────────

def test_gateway_menu_offers_the_operator_line_pane():
    src = (Path(__file__).resolve().parent.parent / "src" / "launcher_tui" / "handlers"
           / "gateway.py").read_text()
    assert '("operator_line",' in src and "def _show_operator_line" in src
