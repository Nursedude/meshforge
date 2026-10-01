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
import urllib.parse
from pathlib import Path
from unittest.mock import patch

import pytest

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))

import monitoring.meshforge_digest as dg  # noqa: E402


_LIVE_PORTS = (5000, 8808)           # the real map / cloud map — never from a test
_real_urlopen = dg.urllib.request.urlopen


@pytest.fixture(autouse=True)
def _no_live_services(monkeypatch):
    """2026-09-30: moving the federation fetch to a new seam left 5 tests
    stubbing the OLD one, so they silently read this box's live :5000 and
    passed or failed on the fleet's mood. Any request to a live service port
    from this file now fails the test instead."""
    def guarded(req, *a, **k):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        # parsed port, never a substring: ":5000" is inside ":50001" (readers)
        if urllib.parse.urlsplit(url).port in _LIVE_PORTS:
            raise AssertionError(f"test reached a LIVE service: {url}")
        return _real_urlopen(req, *a, **k)
    monkeypatch.setattr(dg.urllib.request, "urlopen", guarded)


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
    with patch.object(dg, "_fetch_once", return_value=(_fed(peers), None, None)):
        s = dg.sect_federation(cfg)
    assert s.lines[0] == "peers: 1 ok / 1 expected-backoff / 0 unexpected", s.lines
    assert s.posture != "red"


def test_unexpected_backoff_is_red(home):
    cfg = dg.load_config()                                   # no expectations
    peers = [{"peer_name": "gw-box", "in_backoff": True, "backoff_multiplier": 4}]
    with patch.object(dg, "_fetch_once", return_value=(_fed(peers), None, None)):
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
    with patch.object(dg, "_fetch_once", return_value=(_fed(peers), None, None)):
        f = dg.sect_federation(cfg)
    assert f.gap and "NOT applied" in f.gap and f.posture != "green"
    with patch.object(dg, "_fetch_once", return_value=(None, "URLError: offline", None)):
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
    with patch.object(dg, "_fetch_once", return_value=(None, "URLError: offline", None)):
        text = dg.write_digest()
    out = home / "situation_digest.md"
    assert out.read_text() == text
    assert not (home / "situation_digest.md.tmp").exists()
    assert "Situation Digest" in text and "GAP" in text          # offline federation said


# ── fleet leg: who is down NOW comes from the monitor's STATE, not its log ──
# 2026-09-30: the section read 🔴 ACTION for a 3-day-old POSTURE-LIFTED line
# (a recovery) because ANY non-empty fleet_alerts.log coloured it red — while
# fleet_offline_state.tsv said all 8 boxes healthy. Append-only history can
# never return to green, so the alarm carried no information.

import json as _json
import re
import time

_WRITER = _ROOT / "scripts" / "fleet_offline_check.sh"
BOXES = ["box-a", "box-b"]
HEALTHY = [("box-a", 0, 0, 0, 0, 0, "healthy"), ("box-b", 0, 0, 0, 0, 0, "healthy")]


def _ts(ago_s, utc=False, zone=None):
    t = time.time() - ago_s
    if utc:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))
    return time.strftime("%Y-%m-%d %H:%M:%S ", time.localtime(t)) + (zone or time.strftime("%Z"))


def _fleet(home, monkeypatch, rows=HEALTHY, boxes=BOXES, ran_ago=60, log=(), hb=None):
    """A writer-shaped home: state TSV, box config, heartbeat, alerts log."""
    for v in ("MESHFORGE_OFFLINE_STATE", "MESHFORGE_OFFLINE_HB", "MESHFORGE_OFFLINE_BOXES",
              "ALERT_THRESHOLD"):
        monkeypatch.delenv(v, raising=False)
    if rows is not None:
        (home / "fleet_offline_state.tsv").write_text(
            "".join("\t".join(map(str, r)) + "\n" for r in rows))
    cfg = home / ".config" / "meshforge"
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / "fleet_offline_boxes.json").unlink(missing_ok=True)
    if boxes is not None:
        (cfg / "fleet_offline_boxes.json").write_text(
            _json.dumps({"ssh_user": "u", "boxes": [{"name": b} for b in boxes]}))
    if hb is None and ran_ago is not None:
        hb = [f"{_ts(ran_ago + 300)} ran", f"{_ts(ran_ago)} ran"]
    if hb is not None:
        (home / "fleet_offline_hb.log").write_text("\n".join(hb) + "\n")
    if log:
        (home / "fleet_alerts.log").write_text("\n".join(log) + "\n")
    return dg.sect_monitors()


def _says(s, text):
    return any(text in l for l in s.lines) or (s.gap is not None and text in s.gap)


def test_old_recovery_in_the_log_no_longer_paints_red(home, monkeypatch):
    """The exact 09-30 shape: healthy state + a 3-day-old outage and recovery."""
    s = _fleet(home, monkeypatch, log=[
        f"{_ts(3 * 86400)}  FLEET: ALERT [box-a] UNREACHABLE (failed 3x consecutive)",
        f"{_ts(3 * 86400 - 600)}  FLEET: POSTURE-LIFTED [box-a] was dormant; watching again"])
    assert s.posture == "green", (s.posture, s.gap, s.lines)
    assert _says(s, "2/2 healthy") and _says(s, "history")


def test_a_paged_down_box_is_red(home, monkeypatch):
    s = _fleet(home, monkeypatch, rows=HEALTHY + [("box-c", 4, 1, 1, 1, 1, "down")],
               boxes=BOXES + ["box-c"])
    assert s.posture == "red" and _says(s, "box-c: DOWN (paged"), s.lines


def test_down_past_threshold_with_the_page_undelivered_is_red(home, monkeypatch):
    """alerted=0 at fail>=threshold = ntfy failed the FIRST page; nobody knows."""
    s = _fleet(home, monkeypatch, rows=HEALTHY + [("box-c", 3, 0, 0, 0, 0, "down")],
               boxes=BOXES + ["box-c"])
    assert s.posture == "red" and _says(s, "NOT delivered"), s.lines


@pytest.mark.parametrize("row,expect", [
    (("box-c", 1, 0, 0, 0, 0, "down"), "not yet paged"),
    (("box-c", 4, 1, 1, 1, 1, "unobservable"), "UNOBSERVABLE (paged)"),
    (("box-c", 3, 0, 0, 0, 0, "unobservable"), "UNOBSERVABLE (failing 3x"),
    (("box-c", 0, 0, 0, 1, 0, "drift"), "declaration is stale"),
    (("box-c", 0, 0, 0, 0, 0, "sleeping"), "unrecognised verdict"),
])
def test_partial_or_unknown_states_are_amber(home, monkeypatch, row, expect):
    s = _fleet(home, monkeypatch, rows=HEALTHY + [row], boxes=BOXES + ["box-c"])
    assert s.posture == "amber" and _says(s, expect), (s.posture, s.lines)


def test_declared_dormant_is_listed_not_alarmed(home, monkeypatch):
    s = _fleet(home, monkeypatch, rows=HEALTHY + [("box-c", 0, 0, 0, 0, 0, "dormant")],
               boxes=BOXES + ["box-c"])
    assert s.posture == "green" and _says(s, "box-c: declared dormant")


def test_six_field_row_reads_down_like_the_writer(home, monkeypatch):
    s = _fleet(home, monkeypatch, rows=HEALTHY + [("box-c", 4, 1, 1, 1, 1)], boxes=BOXES + ["box-c"])
    assert s.posture == "red"


def test_a_malformed_row_cannot_hide_a_red_row(home, monkeypatch):
    rows = HEALTHY + [("box-c", "x", 0, 0, 0, 0, "healthy"), ("box-d", 4, 1, 1, 1, 1, "down")]
    s = _fleet(home, monkeypatch, rows=rows, boxes=BOXES + ["box-c", "box-d"])
    assert s.posture == "red" and _says(s, "malformed state row") and _says(s, "box-d: DOWN"), s.lines


def test_fresh_state_from_a_run_that_refused_is_not_healthy(home, monkeypatch):
    """Both readers, 2026-09-30: the writer TOUCHES the state file before its
    FATAL box-config refusal, so the TSV stays fresh while nothing is judged.
    Only the heartbeat's final `ran` line marks a completed run."""
    s = _fleet(home, monkeypatch, ran_ago=dg.OFFLINE_RUN_STALE_S + 600,
               log=[f"{_ts(30)} FATAL box config unusable (ssh_user or boxes empty) — refusing to run"])
    assert s.posture != "green" and s.gap and "not running to completion" in s.gap, (s.posture, s.gap)
    assert _says(s, "monitor FATAL")


@pytest.mark.parametrize("hb,gap", [
    ([], "no completed run"),
    (["garbage"], "no completed run"),
    ([f"{_ts(-3600)} ran"], "FUTURE"),
    ([f"{_ts(60, zone='XYZ')} ran"], "unparseable run stamp"),
])
def test_unjudgeable_heartbeat_is_a_gap(home, monkeypatch, hb, gap):
    s = _fleet(home, monkeypatch, hb=hb)
    assert s.posture != "green" and s.gap and gap in s.gap, (s.posture, s.gap)


def test_missing_heartbeat_file_is_a_gap(home, monkeypatch):
    s = _fleet(home, monkeypatch, ran_ago=None)
    assert s.posture != "green" and "not found" in s.gap


@pytest.mark.parametrize("rows,gap", [(None, "UNKNOWN"), ([], "no judged box")])
def test_missing_or_empty_state_is_a_gap(home, monkeypatch, rows, gap):
    s = _fleet(home, monkeypatch, rows=rows)
    assert s.posture != "green" and s.gap and gap in s.gap, (s.posture, s.gap)


def test_coverage_against_the_writers_box_list(home, monkeypatch):
    # configured but never judged -> amber
    s = _fleet(home, monkeypatch, boxes=BOXES + ["box-new"])
    assert s.posture == "amber" and _says(s, "never judged (no state row): box-new")
    # a stale row for a box the monitor no longer watches -> ignored, never red forever
    s = _fleet(home, monkeypatch, rows=HEALTHY + [("box-gone", 9, 1, 1, 1, 9, "down")])
    assert s.posture == "green" and _says(s, "stale row(s), ignored: box-gone"), s.lines
    # unreadable box list -> coverage not checked, said
    s = _fleet(home, monkeypatch, boxes=None)
    assert s.posture == "amber" and _says(s, "coverage NOT checked")


def test_state_path_honours_the_writers_env_override(home, monkeypatch, tmp_path_factory):
    _fleet(home, monkeypatch)
    other = tmp_path_factory.mktemp("elsewhere") / "state.tsv"
    other.write_text("box-a\t4\t1\t1\t1\t1\tdown\nbox-b\t0\t0\t0\t0\t0\thealthy\n")
    monkeypatch.setenv("MESHFORGE_OFFLINE_STATE", str(other))
    assert dg.sect_monitors().posture == "red"


@pytest.mark.parametrize("kind_line", [
    "FLEET: MONITOR-GAP 1200s (cadence 300s) — cron missed",
    "FLEET: POSTURE-UNREADABLE x — watching EVERY box",
    "FLEET: VIACONF-SUSPECT [box-a] direct ssh OK but declared dependency 'hop' did not answer",
    "FLEET: PUSH-FAILED on RECOVERED [box-a] — see witness log",
])
def test_monitor_blindness_kinds_are_amber_for_a_day(home, monkeypatch, kind_line):
    s = _fleet(home, monkeypatch, log=[f"{_ts(2 * 3600)}  {kind_line}"])
    assert s.posture == "amber" and _says(s, "in 24h"), s.lines
    s = _fleet(home, monkeypatch, log=[f"{_ts(dg.LOG_ATTENTION_WINDOW_S + 600)}  {kind_line}"])
    assert s.posture == "green", s.lines


def test_cron_freshness_window_edges(home, monkeypatch):
    w = dg.CRON_FRESHNESS_WINDOW_S
    s = _fleet(home, monkeypatch, log=[f"{_ts(w - 60, utc=True)} CRON-FRESHNESS STALE:", "box-a/some_cron: silent"])
    assert s.posture == "amber" and _says(s, "cron freshness"), s.lines
    s = _fleet(home, monkeypatch, log=[f"{_ts(w + 600, utc=True)} CRON-FRESHNESS STALE:"])
    assert s.posture == "green", s.lines


def test_future_dated_log_lines_are_said_not_trusted(home, monkeypatch):
    s = _fleet(home, monkeypatch, log=[f"{_ts(-7200, utc=True)} CRON-FRESHNESS STALE:"])
    assert s.posture == "amber" and _says(s, "in the FUTURE") and not _says(s, "cron freshness flagged")


@pytest.mark.skipif(not _WRITER.exists(), reason="writer script not in this tree")
def test_alert_threshold_default_matches_the_writer():
    m = re.search(r'ALERT_THRESHOLD="\$\{ALERT_THRESHOLD:-(\d+)\}"', _WRITER.read_text())
    assert m and int(m.group(1)) == dg.OFFLINE_ALERT_THRESHOLD_DEFAULT


@pytest.mark.skipif(not _WRITER.exists(), reason="writer script not in this tree")
def test_every_verdict_the_writer_can_store_is_one_the_digest_knows():
    """Closed enum, closed consumer (honest_failure_modes #7): a new verdict in
    fleet_offline_check.sh must fail HERE, not render as 'unrecognised' live.
    Reads CODE lines only — a first draft matched `verdict=unobservable` in a
    header COMMENT and passed off it."""
    code_lines = [l for l in _WRITER.read_text().splitlines() if not l.lstrip().startswith("#")]
    code = "\n".join(code_lines)
    stored = re.findall(r'set_state "\$name"(?: \S+){5} (\S+)', code)
    calls = [l for l in code_lines if re.search(r'\bset_state\s+"', l)]
    assert stored and len(stored) == len(calls), (
        f"parsed {len(stored)} set_state calls but {len(calls)} lines call it — "
        "a call of a new shape would escape this check")
    literal = {v for v in stored if not v.startswith('"$')}
    dynamic = {v for v in stored if v.startswith('"$')}
    assert dynamic == {'"$verdict"', '"$pstate"'}, dynamic
    verdict_assigns = re.findall(r'(?<![\w.])verdict=(\S+)', code)
    assert verdict_assigns and all(re.fullmatch(r'"?[a-z]+"?', v) for v in verdict_assigns), (
        f"$verdict is assigned a non-literal {verdict_assigns} — enumerate it here")
    verdict_vals = {v.strip('"') for v in verdict_assigns}
    assert '[ "$pstate" = dormant ] || [ "$pstate" = detached ]' in code
    writable = literal | verdict_vals | {"dormant", "detached", "down"}  # +6-field back-compat
    assert writable <= set(dg.OFFLINE_VERDICTS), writable - set(dg.OFFLINE_VERDICTS)


# ── deploy/boot race: a service still starting is not a fault ──────────────
# 2026-09-30: fleet_sync restarted the map, then the digest 1s later; the
# digest's first tick read :5000 "Connection refused" and painted the TL;DR
# red for 15 min. The map binds ~2s after start, then answers 503
# {"state": "warming"} for 10-30s. The digest now settles through exactly
# those two shapes, and nothing else.

import http.server
import socket
import threading


def _free_port():
    with socket.socket() as so:
        so.bind(("127.0.0.1", 0))
        return so.getsockname()[1]


class _Scripted(http.server.BaseHTTPRequestHandler):
    script = []          # list of (status, body) served in order; last repeats
    hits = 0

    def do_GET(self):
        cls = type(self)
        status, body = cls.script[min(cls.hits, len(cls.script) - 1)]
        cls.hits += 1
        raw = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *a):
        pass


@pytest.fixture
def scripted_server():
    """A REAL local HTTP server that can start late: (url, start(script, delay))."""
    port = _free_port()
    servers = []

    def start(script, delay=0.0):
        handler = type("H", (_Scripted,), {"script": script, "hits": 0})

        def run():
            time.sleep(delay)
            srv = http.server.HTTPServer(("127.0.0.1", port), handler)
            servers.append(srv)
            srv.serve_forever(poll_interval=0.05)
        threading.Thread(target=run, daemon=True).start()
        return handler

    yield f"http://127.0.0.1:{port}/api/status", start
    for srv in servers:
        srv.shutdown()
        srv.server_close()


WARMING = (503, {"error": "service_warming", "state": "warming", "retry_after_s": 10})
STATUS_OK = (200, {"federation": {"peer_status": [], "last_sync": None}})


def test_refused_then_warming_then_ok_settles_green(scripted_server):
    """The real consumer path: not bound yet → 503 warming → 200."""
    url, start = scripted_server
    h = start([WARMING, WARMING, STATUS_OK], delay=0.4)       # refused for 0.4s first
    data, err, note = dg._fetch_json_settling(url, settle_s=10, step_s=0.1)
    assert err is None and data["federation"] is not None, err
    assert h.hits == 3
    assert note and "answered after" in note and "refused" in note, note


def test_settle_gives_up_and_says_why(scripted_server):
    url, start = scripted_server
    start([WARMING])                                          # warms forever
    data, err, note = dg._fetch_json_settling(url, settle_s=0.5, step_s=0.1)
    assert data is None and "still " in err and "warming after" in err, err


def test_refused_with_nothing_ever_listening_gives_up(scripted_server):
    url, _ = scripted_server
    data, err, note = dg._fetch_json_settling(url, settle_s=0.3, step_s=0.1)
    assert data is None and "still refused after" in err, err


@pytest.mark.parametrize("resp", [
    (503, {"error": "overloaded"}),          # a 503 the map did NOT declare as warming
    (500, {"error": "boom"}),
])
def test_other_errors_are_never_retried(scripted_server, resp):
    url, start = scripted_server
    h = start([resp, STATUS_OK])
    time.sleep(0.2)
    data, err, note = dg._fetch_json_settling(url, settle_s=10, step_s=0.1)
    assert data is None and h.hits == 1, (err, h.hits)


def test_a_timeout_is_never_retried(monkeypatch):
    calls = []

    def fake(url, timeout=8):
        calls.append(1)
        return None, "URLError: timed out", "timeout"
    monkeypatch.setattr(dg, "_fetch_once", fake)
    data, err, _ = dg._fetch_json_settling("http://x", settle_s=10, step_s=0.01)
    assert data is None and len(calls) == 1


def test_shutdown_cuts_the_settle_short(monkeypatch):
    monkeypatch.setattr(dg, "_fetch_once", lambda url, timeout=8: (None, "refused", "refused"))
    monkeypatch.setattr(dg._stop, "wait", lambda s: True)          # stop requested
    t = time.monotonic()
    data, err, _ = dg._fetch_json_settling("http://x", settle_s=60, step_s=5)
    assert data is None and time.monotonic() - t < 1


def test_first_try_success_leaves_no_note_even_if_slow(monkeypatch):
    """A slow but successful first fetch is not a settle (and must not crash)."""
    def slow(url, timeout=8):
        time.sleep(1.1)
        return {"federation": {}}, None, None
    monkeypatch.setattr(dg, "_fetch_once", slow)
    data, err, note = dg._fetch_json_settling("http://x")
    assert data is not None and note is None


def test_federation_section_settles_through_a_restart_to_green(home, scripted_server, monkeypatch):
    url, start = scripted_server
    start([WARMING, STATUS_OK], delay=0.3)
    monkeypatch.setattr(dg, "SETTLE_STEP_S", 0.1)
    s = dg.sect_federation({"federation_url": url, "expected_backoff": {}, "config_error": None})
    assert s.posture == "green" and s.gap is None, (s.posture, s.gap)
    assert any("a restart or a crash" in l and "NRestarts" in l for l in s.lines), s.lines


def test_fetch_once_classifies_real_socket_failures():
    """The classification itself, on REAL sockets (a mutant calling every
    error 'refused' survived the stubbed tests): nothing listening → refused;
    listening but never answering → a timeout, which is NOT starting."""
    port = _free_port()                                      # nothing bound
    _, err, starting = dg._fetch_once(f"http://127.0.0.1:{port}/api/status", timeout=2)
    assert starting == "refused", err
    with socket.socket() as so:                              # bound, never accepts
        so.bind(("127.0.0.1", 0))
        so.listen(1)
        p = so.getsockname()[1]
        _, err, starting = dg._fetch_once(f"http://127.0.0.1:{p}/api/status", timeout=0.5)
    assert starting == "timeout" and "timed out" in err, err


def test_timeout_after_a_starting_phase_is_still_starting(monkeypatch):
    """Warming clears BEFORE the map prewarms its status caches, so the first
    post-warming read can time out (reader P). After an observed starting
    phase that is still starting; without one it is a wedge (tested above)."""
    seq = iter([(None, "refused", "refused"), (None, "HTTPError: 503", "warming"),
                (None, "URLError: timed out", "timeout"), ({"federation": {}}, None, None)])
    monkeypatch.setattr(dg, "_fetch_once", lambda url, timeout=8: next(seq))
    data, err, note = dg._fetch_json_settling("http://x", settle_s=10, step_s=0.01)
    assert data is not None and "refused → warming → timeout" in note, note


def test_a_failure_after_a_starting_phase_keeps_the_witness(monkeypatch):
    seq = iter([(None, "refused", "refused"), (None, "HTTPError: 500", None)])
    monkeypatch.setattr(dg, "_fetch_once", lambda url, timeout=8: next(seq))
    data, err, note = dg._fetch_json_settling("http://x", settle_s=10, step_s=0.01)
    assert data is None and "500" in err and "after refused at tick start" in err, err


def test_a_stop_mid_cycle_never_overwrites_the_last_digest(home, monkeypatch):
    out = home / "situation_digest.md"
    out.write_text("LAST COMPLETE DIGEST")
    monkeypatch.setattr(dg, "build_digest", lambda cfg=None: "half-observed")
    monkeypatch.setattr(dg._stop, "is_set", lambda: True)
    dg.write_digest()
    assert out.read_text() == "LAST COMPLETE DIGEST"


def test_fence_matches_the_port_not_a_substring(scripted_server):
    """A test server on 5000x must not trip the live-port fence."""
    url = "http://127.0.0.1:50001/api/status"
    assert urllib.parse.urlsplit(url).port not in _LIVE_PORTS
    assert urllib.parse.urlsplit("http://localhost:5000/x").port in _LIVE_PORTS
