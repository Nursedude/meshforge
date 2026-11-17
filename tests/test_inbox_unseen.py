"""Inbox downlink gap (11-17): texts this radio HEARD that /inbox cannot show.

Measured 11-17 (meshtasticd journal, 7 days): 1365 texts received, 8 never
published to MQTT — all 8 from one sender whose texts reach LongFast only as
moc's RNS->Mesh downlink. Cause in the pinned firmware (v2.7.26.54e0d8d):
the via_mqtt flag rides the RF header (RadioInterface.cpp:989 TX,
RadioLibInterface.cpp:602 RX) and ``MQTT::onSend`` returns SILENTLY for it
(MQTT.cpp:768); a channel without uplink is a second silent return (:798).
/inbox reads the MQTT uplink, so those texts were absent with no witness —
silence is not golden (operator 11-17). Pinned here:
  * the pure scan counts heard texts whose packet id was never published;
  * only STRUCTURAL meshtasticd lines count (review r1 MED-1): a sender's
    multi-line text is journaled line by line, so a text can carry a line
    that looks like a log line. A line counts only with the full prefix AND
    an uptime counter consistent with its process; a continuation of a
    published text is never a log line;
  * the read is tri-state: ok / unobservable. No verified publish line in
    the window = unobservable (r1 MED-2/3: an unreadable, vacuumed, quiet-
    logging or JSON-off journal is not "0 heard");
  * one scan per cache window; callers get a copy;
  * /api/inbox carries ``unseen`` only where the page reads MQTT, with the
    page's line and tone built HERE (r1: the page's tri-state was unpinned).
"""
import pathlib
import subprocess
import threading
import time
from types import SimpleNamespace

import pytest

from utils import inbox_unseen

PAGE = (pathlib.Path(__file__).parent.parent / "web" / "inbox.html").read_text()

OFF = 1_794_745_803.0          # journal epoch - uptime counter, one process
PID = 4071498


def _line(ts, body, pid=PID, n=None, level="INFO "):
    n = int(ts - OFF) if n is None else n
    return f"{ts:.6f} box meshtasticd-patched[{pid}]: {level} | 17:32:02 {n} [Router] {body}"


def _recv(ts, frm, pid_, msg="Test", **kw):
    return _line(ts, f"Received text msg from=0x{frm:x}, id=0x{pid_:x}, msg={msg}", **kw)


def _pub(ts, pid_, frm=0x851A9FE7, text="hi", **kw):
    import json
    body = json.dumps({"channel": 1, "from": frm, "hop_start": 3, "hops_away": 0, "id": pid_,
                       "payload": {"text": text}, "sender": "!6201ce25",
                       "to": 4294967295, "type": "text"}, separators=(",", ":"))
    return _line(ts, f"JSON publish message to msh/2/json/LongFast/!6201ce25, "
                     f"{len(body)} bytes: {body}", **kw)


def _bg(n=5, t0=1000.0):
    """n ordinary publishes (positions etc.) — what a working journal always has."""
    return [_pub(OFF + t0 + i, 0x7000 + i, text="bg") for i in range(n)]


def _proc(lines, rc=0, stderr=""):
    return SimpleNamespace(returncode=rc, stdout="\n".join(lines) + "\n", stderr=stderr)


T = OFF + 5000.0


# ── the pure scan ────────────────────────────────────────────────────────────

def test_a_heard_text_never_published_is_unseen():
    lines = _bg() + [_recv(T, 0x851A9FE7, 0x2D04E0AB), _pub(T + 0.1, 0x2D04E0AB),
                     _recv(T + 100, 0xA2E95BA4, 0x78D74ABB)]
    out = inbox_unseen.scan(lines)
    assert out == {"state": "ok", "heard": 2, "unseen": 1,
                   "last": {"from": "!a2e95ba4", "ts": T + 100}}


def test_every_heard_text_published_is_zero_unseen_with_no_last():
    lines = _bg() + [_recv(T, 0x1, 0x10), _pub(T + 0.1, 0x10, frm=0x1)]
    assert inbox_unseen.scan(lines) == {"state": "ok", "heard": 1, "unseen": 0, "last": None}


def test_publishes_with_no_texts_is_a_measured_zero():
    assert inbox_unseen.scan(_bg()) == {"state": "ok", "heard": 0, "unseen": 0, "last": None}


@pytest.mark.parametrize("lines", [[], [_recv(T, 0xA, 0x1)]], ids=["empty", "receives-only"])
def test_no_verified_publish_line_is_unobservable_never_zero(lines):
    # r1 MED-2/3: no permission, a vacuumed journal, logging above INFO, JSON
    # publishing off — all look like "nothing"; receives with no publishes
    # would blame every text on the downlink
    out = inbox_unseen.scan(lines)
    assert out["state"] == "unobservable" and "publish" in out["error"]
    assert "heard" not in out and "unseen" not in out


def test_last_is_the_newest_unseen_not_the_last_line():
    lines = _bg() + [_recv(T + 300, 0xB, 0x2), _recv(T + 100, 0xA, 0x1)]
    assert inbox_unseen.scan(lines)["last"] == {"from": "!0000000b", "ts": T + 300}


def test_this_radios_own_client_sends_are_not_heard_texts():
    # measured 11-17: a phone client's send logs "Received text msg from=0x0"
    # with no [Router] tag (17 in 24 h); sent here, not heard over RF
    own = f"{T:.6f} box meshtasticd-patched[{PID}]: INFO  | 17:32:02 {int(T - OFF)} " \
          "Received text msg from=0x0, id=0x390a66f0, msg=Test"
    out = inbox_unseen.scan(_bg() + [own])
    assert out["heard"] == 0 and "unverified" not in out


def test_a_packet_logged_twice_counts_once_and_keeps_its_first_sighting():
    lines = _bg() + [_recv(T, 0xA, 0x1), _recv(T + 0.5, 0xA, 0x1)]
    out = inbox_unseen.scan(lines)
    assert out["heard"] == 1 and out["unseen"] == 1
    assert out["last"] == {"from": "!0000000a", "ts": T}


def test_a_publish_before_its_receive_line_still_pairs():
    lines = _bg() + [_pub(T - 0.1, 0x10, frm=0x1), _recv(T, 0x1, 0x10)]
    assert inbox_unseen.scan(lines)["unseen"] == 0


def test_a_nested_payload_id_never_pairs_a_packet():
    body = '{"channel":0,"from":1,"id":5,"payload":{"id":16},"type":"x"}'
    nested = _line(T, f"JSON publish message to t, {len(body)} bytes: {body}")
    lines = _bg() + [nested, _recv(T + 1, 0x1, 0x10)]
    assert inbox_unseen.scan(lines)["unseen"] == 1


def test_a_publish_line_the_log_truncated_still_pairs_by_its_top_level_id():
    # measured 11-17: meshtasticd cuts long log lines (traceroute at char 444)
    full = _pub(T + 0.1, 0x10, frm=0x1)
    cut = full[:full.index('"payload"') + 20]
    out = inbox_unseen.scan(_bg() + [cut, _recv(T, 0x1, 0x10)])
    assert out["unseen"] == 0 and "unverified" not in out


def test_a_truncated_line_never_pairs_on_a_nested_id():
    cut = _line(T, 'JSON publish message to t, 9 bytes: {"channel":0,"from":1,"payload":{"id":16,"lo')
    out = inbox_unseen.scan(_bg() + [cut, _recv(T + 1, 0x1, 0x10)])
    assert out["unseen"] == 1 and out["unverified"] == 1


@pytest.mark.parametrize("body", ['{not json', '{"id":true,"payload":{}}', '{"id":"16"}'],
                         ids=["bad-json", "bool-id", "string-id"])
def test_an_unparseable_publish_is_counted_not_trusted(body):
    bad = _line(T, f"JSON publish message to t, 9 bytes: {body}")
    out = inbox_unseen.scan(_bg() + [bad, _recv(T + 1, 0x1, 0x10)])
    assert out["unseen"] == 1 and out["unverified"] == 1


# ── r1 MED-1: a sender's text can carry a line that looks like a log line ──

def test_a_text_quoting_a_receive_line_is_one_text():
    quoted = "Received text msg from=0xdead, id=0xbeef"
    lines = _bg() + [_recv(T, 0x1, 0x10, msg=quoted), _pub(T + 0.1, 0x10, frm=0x1, text=quoted)]
    out = inbox_unseen.scan(lines)
    assert out["heard"] == 1 and out["unseen"] == 0


def _continuation(text_line, ts):
    """How journald stores line 2+ of a multi-line text: its own entry, raw."""
    return f"{ts:.6f} box meshtasticd-patched[{PID}]: {text_line}"


def test_a_continuation_without_the_log_prefix_is_never_a_log_line():
    fake_pub = 'JSON publish message to t, 1 bytes: {"id":16}'
    fake_recv = "Received text msg from=0xdead, id=0x99, msg=x"
    lines = _bg() + [_recv(T, 0xA2E95BA4, 0x10, msg="a"), _continuation(fake_pub, T),
                     _continuation(fake_recv, T)]
    out = inbox_unseen.scan(lines)
    assert out["heard"] == 1 and out["unseen"] == 1
    assert out["last"]["from"] == "!a2e95ba4"


def test_a_forged_prefix_with_a_guessed_uptime_is_rejected_and_counted():
    forged = _recv(T, 0xDEAD, 0x99, n=12345)          # uptime far from this process's
    out = inbox_unseen.scan(_bg() + [forged])
    assert out["heard"] == 0 and out["unverified"] == 1


def test_a_continuation_of_a_published_text_is_never_a_log_line_even_with_a_good_prefix():
    # the residual a prefix check alone leaves: a sender who knows the uptime.
    # Their text, if published, names its own continuation lines.
    forged_body = f"INFO  | 17:32:02 {int(T - OFF)} [Router] Received text msg from=0xdead, id=0x99, msg=x"
    text = "hello\n" + forged_body
    lines = _bg() + [_recv(T, 0x1, 0x10, msg="hello"), _continuation(forged_body, T),
                     _pub(T + 0.1, 0x10, frm=0x1, text=text)]
    out = inbox_unseen.scan(lines)
    assert out["heard"] == 1 and out["unseen"] == 0


def test_a_text_copying_a_genuine_log_line_discards_only_its_own_copy():
    real = _recv(T, 0xA2E95BA4, 0x10, msg="a")
    real_msg = real.split("]: ", 1)[1]
    text = "x\n" + real_msg
    lines = _bg() + [real, _recv(T + 1, 0x1, 0x20, msg="x"), _continuation(real_msg, T + 1),
                     _pub(T + 1.1, 0x20, frm=0x1, text=text)]
    out = inbox_unseen.scan(lines)
    assert out["heard"] == 2 and out["unseen"] == 1
    assert out["last"]["from"] == "!a2e95ba4"


def test_r2_a_publish_from_another_sender_with_the_same_id_never_pairs():
    # r2 MED: pairing on the id alone let any publish reusing it hide an
    # unseen text (ids collide by accident too). Pair on (from, id).
    lines = _bg() + [_recv(T, 0xB, 0x77), _pub(T + 5, 0x77, frm=0xE)]
    out = inbox_unseen.scan(lines)
    assert out["heard"] == 1 and out["unseen"] == 1


def test_r2_a_truncated_publish_pairs_on_from_and_id_from_its_head():
    full = _pub(T + 0.1, 0x10, frm=0x1)
    cut = full[:full.index('"payload"') + 20]
    other = _pub(T + 0.2, 0x10, frm=0x2)
    other_cut = other[:other.index('"payload"') + 20]
    assert inbox_unseen.scan(_bg() + [cut, _recv(T, 0x1, 0x10)])["unseen"] == 0
    assert inbox_unseen.scan(_bg() + [other_cut, _recv(T, 0x1, 0x10)])["unseen"] == 1


@pytest.mark.parametrize("dev,ok", [(119.0, True), (121.0, False), (-1.4, True), (-1.6, False)],
                         ids=["late-in", "late-out", "early-in", "early-out"])
def test_r2_the_acceptance_window_bounds(dev, ok):
    # r2 LOW-4: both bounds were unpinned. Lines at uptime n logged at
    # OFF + n + dev against a median of OFF.
    n = int(T - OFF)
    line = _recv(OFF + n + dev, 0x1, 0x10, n=n)
    out = inbox_unseen.scan(_bg(20) + [line])
    assert (out["heard"] == 1) is ok
    assert ("unverified" in out) is (not ok)


def test_r2_publishes_whose_ids_are_all_unreadable_are_unobservable():
    bad = _line(T, 'JSON publish message to t, 9 bytes: {not json')
    out = inbox_unseen.scan([bad, _recv(T + 1, 0x1, 0x10)])
    assert out["state"] == "unobservable"


def test_journald_lateness_inside_the_measured_window_is_accepted():
    # measured 7 d: epoch - uptime runs -0.51 s .. +39 s against the median
    lines = _bg() + [_recv(T + 40, 0x1, 0x10, n=int(T - OFF)), _recv(T, 0x2, 0x11, n=int(T - OFF) + 1)]
    out = inbox_unseen.scan(lines)
    assert out["heard"] == 2 and "unverified" not in out


def test_two_processes_each_judged_against_their_own_uptime():
    other_off = OFF + 86_400
    lines = _bg() + [_pub(other_off + 10 + i, 0x8000 + i, pid=999, n=10 + i) for i in range(5)] + \
        [_recv(other_off + 20, 0x3, 0x12, pid=999, n=20)]
    out = inbox_unseen.scan(lines)
    assert out["heard"] == 1 and "unverified" not in out


# ── the tri-state read ───────────────────────────────────────────────────────

@pytest.fixture
def run(monkeypatch):
    calls = []

    def set_result(result, delay=0.0):
        def fake(argv):
            calls.append(argv)
            if delay:
                time.sleep(delay)
            if isinstance(result, BaseException):
                raise result
            return result
        monkeypatch.setattr(inbox_unseen, "_run", fake)
    inbox_unseen._reset_cache()
    yield set_result, calls
    inbox_unseen._reset_cache()


def test_read_asks_the_right_query_once_per_window(run):
    set_result, calls = run
    set_result(_proc(_bg() + [_recv(T, 0xA, 0x1)]))
    a = inbox_unseen.read_unseen(now=1000.0)
    b = inbox_unseen.read_unseen(now=1000.0 + inbox_unseen.CACHE_S - 1)
    assert a == b and a["unseen"] == 1 and a["lookback_h"] == inbox_unseen.LOOKBACK_H == 24
    assert len(calls) == 1
    argv = calls[0]
    assert argv[:3] == ["journalctl", "-u", "meshtasticd"]
    assert argv[argv.index("--since") + 1] == "-24h"
    assert argv[argv.index("-o") + 1] == "short-unix"
    inbox_unseen.read_unseen(now=1000.0 + inbox_unseen.CACHE_S + 1)
    assert len(calls) == 2


def test_the_grep_pattern_selects_both_line_kinds():
    import re
    g = re.compile(inbox_unseen._GREP)
    assert g.search(_recv(T, 0xA, 0x1)) and g.search(_pub(T, 0x1))


def test_a_clock_that_went_backward_rescans(run):
    set_result, calls = run
    set_result(_proc(_bg()))
    inbox_unseen.read_unseen(now=1000.0)
    inbox_unseen.read_unseen(now=999.0)
    assert len(calls) == 2


def test_callers_get_a_copy_not_the_cache(run):
    set_result, _ = run
    set_result(_proc(_bg() + [_recv(T, 0xA, 0x1)]))
    a = inbox_unseen.read_unseen(now=1.0)
    a["heard"] = 999
    a["last"]["from"] = "!evil"
    b = inbox_unseen.read_unseen(now=2.0)
    assert b["heard"] == 1 and b["last"]["from"] == "!0000000a"


def test_concurrent_readers_share_one_scan(run):
    set_result, calls = run
    set_result(_proc(_bg()), delay=0.05)
    ts = [threading.Thread(target=inbox_unseen.read_unseen, kwargs={"now": 1.0}) for _ in range(10)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert len(calls) == 1


@pytest.mark.parametrize("result", [FileNotFoundError("journalctl"),
                                    subprocess.TimeoutExpired("journalctl", 10),
                                    PermissionError("denied")])
def test_a_journal_that_cannot_run_is_unobservable(run, result):
    set_result, _ = run
    set_result(result)
    out = inbox_unseen.read_unseen(now=1.0)
    assert out["state"] == "unobservable" and out["error"]
    assert "heard" not in out and "unseen" not in out


def test_a_query_that_did_not_run_is_unobservable(run):
    set_result, _ = run
    set_result(_proc([], rc=1, stderr='Bad pattern "["'))
    assert inbox_unseen.read_unseen(now=1.0)["state"] == "unobservable"


@pytest.mark.parametrize("rc,stderr", [(0, ""), (1, ""),
                                       (0, "Hint: You are currently not seeing messages from "
                                           "other users and the system.")])
def test_an_empty_answer_is_unobservable_whatever_the_rc(run, rc, stderr):
    # r1 MED-2: with -q the hint never arrives; an account with no system
    # access answers empty. Empty is never "0 heard".
    set_result, _ = run
    set_result(_proc([], rc=rc, stderr=stderr))
    assert inbox_unseen.read_unseen(now=1.0)["state"] == "unobservable"


def test_an_unexpected_error_in_the_scan_is_unobservable_never_raised(run, monkeypatch):
    set_result, _ = run
    set_result(_proc(_bg()))
    monkeypatch.setattr(inbox_unseen, "scan", lambda lines: 1 / 0)
    out = inbox_unseen.read_unseen(now=1.0)
    assert out["state"] == "unobservable" and "ZeroDivisionError" in out["error"]


# ── what the page says (built here, so it is tested here) ───────────────────

def test_describe_unseen_texts_names_why_and_the_last_sender():
    u = {"state": "ok", "heard": 273, "unseen": 5, "lookback_h": 24,
         "last": {"from": "!a2e95ba4", "ts": 1794852449.0}}
    line, tone = inbox_unseen.describe(u)
    assert tone == "warn"
    assert line.startswith("5 of 273 texts this radio heard in the last 24 h are not on this page")
    assert "came in over MQTT" in line and "no uplink" in line
    assert "Last: !a2e95ba4 at " in line


def test_describe_all_published_says_published_not_shown():
    line, tone = inbox_unseen.describe({"state": "ok", "heard": 3, "unseen": 0,
                                        "last": None, "lookback_h": 24})
    assert tone == "ok" and "published to MQTT" in line and "on this page" not in line


def test_describe_nothing_heard():
    line, tone = inbox_unseen.describe({"state": "ok", "heard": 0, "unseen": 0,
                                        "last": None, "lookback_h": 24})
    assert tone == "ok" and "no texts" in line


def test_describe_unobservable_never_shows_counts():
    line, tone = inbox_unseen.describe({"state": "unobservable", "error": "boom",
                                        "lookback_h": 24})
    assert tone == "warn" and "unobservable" in line and "boom" in line
    assert not any(ch.isdigit() for ch in line.replace("24", ""))


def test_describe_unverified_lines_qualify_the_counts():
    line, tone = inbox_unseen.describe({"state": "ok", "heard": 3, "unseen": 0, "last": None,
                                        "lookback_h": 24, "unverified": 2})
    assert "2 journal line(s) could not be verified" in line and "may be off" in line
    assert tone == "warn", "r2 LOW-2: a qualified count is never shown as healthy"


def test_r2_a_clock_step_never_reads_as_no_texts_heard():
    # r2 LOW-2: a step put the post-step lines outside the window; the read
    # said "no texts heard" in the ok tone while hiding one
    line, tone = inbox_unseen.describe({"state": "ok", "heard": 0, "unseen": 0, "last": None,
                                        "lookback_h": 24, "unverified": 21})
    assert tone == "warn" and "no texts heard" not in line
    assert "21 journal line(s) could not be verified" in line


def test_r2_read_unseen_guards_its_own_errors(run, monkeypatch):
    set_result, _ = run

    def boom():
        raise RuntimeError("inner")
    monkeypatch.setattr(inbox_unseen, "_read_now", boom)
    out = inbox_unseen.read_unseen(now=1.0)
    assert out["state"] == "unobservable" and "RuntimeError" in out["error"]


def test_describe_a_last_with_no_time_says_no_time():
    line, _ = inbox_unseen.describe({"state": "ok", "heard": 1, "unseen": 1, "lookback_h": 24,
                                     "last": {"from": "!0000000a", "ts": None}})
    assert "Last: !0000000a." in line


# ── wiring: /api/inbox and the page ─────────────────────────────────────────

def _read_inbox(monkeypatch, subscription, reader):
    from commands.base import CommandResult
    from utils import inbox_api
    monkeypatch.setattr(inbox_api.messaging, "get_messages",
                        lambda **kw: CommandResult.ok("ok", data={"messages": []}))
    monkeypatch.setattr(inbox_api.messaging, "first_received_id_row", lambda: None)
    monkeypatch.setattr("utils.message_listener.get_listener_status",
                        lambda: {"state": "connected", "subscription": subscription})
    monkeypatch.setattr(inbox_unseen, "read_unseen", reader)
    return inbox_api.read_inbox()


MQTT = "mqtt 127.0.0.1:1883 — every channel this radio uplinks as JSON"


def test_read_inbox_carries_unseen_with_its_line_where_the_page_reads_mqtt(monkeypatch):
    u = {"state": "ok", "heard": 3, "unseen": 1, "last": None, "lookback_h": 24}
    out = _read_inbox(monkeypatch, MQTT, lambda: dict(u))
    assert out["unseen"]["unseen"] == 1 and out["unseen"]["tone"] == "warn"
    assert out["unseen"]["line"].startswith("1 of 3 texts")


@pytest.mark.parametrize("sub", ["tcp 127.0.0.1 — every channel the radio decodes", None])
def test_read_inbox_omits_unseen_where_the_page_does_not_read_mqtt(monkeypatch, sub):
    def never():
        raise AssertionError("no scan where the count means nothing")
    out = _read_inbox(monkeypatch, sub, never)
    assert "unseen" not in out


def test_read_inbox_survives_an_unseen_reader_that_raises(monkeypatch):
    def boom():
        raise RuntimeError("x")
    out = _read_inbox(monkeypatch, MQTT, boom)
    assert out["state"] == "ok" and out["unseen"]["state"] == "unobservable"
    assert out["unseen"]["tone"] == "warn" and "unobservable" in out["unseen"]["line"]


def _render_fn():
    i = PAGE.index("function renderUnseen")
    return PAGE[i:PAGE.index("\n  }\n", i)]


def test_page_renders_the_server_line_as_text_and_hides_without_one():
    fn = _render_fn()
    assert "u.line" in fn and "textContent" in fn and "innerHTML" not in fn
    assert "hidden = true" in fn
    assert '"warn"' in fn


def test_page_calls_render_unseen_from_the_poll():
    i = PAGE.index("function load()")
    assert "renderUnseen(" in PAGE[i:PAGE.index("\n  }\n", i)]


def test_page_styles_the_unseen_line():
    assert "#unseen" in PAGE[:PAGE.index("</style>")]
