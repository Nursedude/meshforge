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
  * the read is tri-state: ok / unobservable (journal unreadable, query did
    not run, or the account cannot see system logs) — never "0 heard";
  * one scan per cache window, however often the page polls;
  * /api/inbox carries ``unseen``, and the page SAYS it.
"""
import pathlib
import subprocess
from types import SimpleNamespace

import pytest

from utils import inbox_unseen

PAGE = (pathlib.Path(__file__).parent.parent / "web" / "inbox.html").read_text()


def _recv(ts, frm, pid, msg="Test"):
    return (f"{ts:.6f} box meshtasticd[1]: INFO  | 17:32:02 104520 [Router] "
            f"Received text msg from=0x{frm:x}, id=0x{pid:x}, msg={msg}")


def _pub(ts, pid, frm=0x851A9FE7):
    return (f"{ts:.6f} box meshtasticd[1]: INFO  | 17:32:02 104520 [Router] JSON publish "
            f"message to msh/2/json/LongFast/!6201ce25, 211 bytes: "
            f'{{"channel":1,"from":{frm},"hop_start":3,"hops_away":0,"id":{pid},'
            f'"payload":{{"text":"hi"}},"sender":"!6201ce25","to":4294967295,"type":"text"}}')


def _proc(lines, rc=0, stderr=""):
    return SimpleNamespace(returncode=rc, stdout="\n".join(lines) + "\n", stderr=stderr)


# ── the pure scan ────────────────────────────────────────────────────────────

def test_a_heard_text_never_published_is_unseen():
    lines = [_recv(100.0, 0x851A9FE7, 0x2D04E0AB), _pub(100.1, 0x2D04E0AB),
             _recv(200.0, 0xA2E95BA4, 0x78D74ABB)]
    out = inbox_unseen.scan(lines)
    assert out == {"state": "ok", "heard": 2, "unseen": 1,
                   "last": {"from": "!a2e95ba4", "ts": 200.0}}


def test_every_heard_text_published_is_zero_unseen_with_no_last():
    lines = [_recv(100.0, 0x1, 0x10), _pub(100.1, 0x10)]
    assert inbox_unseen.scan(lines) == {"state": "ok", "heard": 1, "unseen": 0, "last": None}


def test_nothing_heard_is_a_measured_zero():
    assert inbox_unseen.scan([]) == {"state": "ok", "heard": 0, "unseen": 0, "last": None}


def test_last_is_the_newest_unseen_not_the_last_line():
    lines = [_recv(300.0, 0xB, 0x2), _recv(100.0, 0xA, 0x1)]
    assert inbox_unseen.scan(lines)["last"] == {"from": "!0000000b", "ts": 300.0}


def test_a_packet_logged_twice_counts_once():
    lines = [_recv(100.0, 0xA, 0x1), _recv(100.5, 0xA, 0x1)]
    assert inbox_unseen.scan(lines)["heard"] == 1
    assert inbox_unseen.scan(lines)["unseen"] == 1


def test_a_publish_before_its_receive_line_still_pairs():
    lines = [_pub(99.9, 0x10), _recv(100.0, 0x1, 0x10)]
    assert inbox_unseen.scan(lines)["unseen"] == 0


def test_a_nested_payload_id_never_pairs_a_packet():
    # a nodeinfo-style payload carrying its own numeric "id" must not mark
    # that number as a published packet (it would hide a real unseen text)
    nested = ('1.0 box meshtasticd[1]: INFO  | x [Router] JSON publish message to t, 9 bytes: '
              '{"channel":0,"from":1,"id":5,"payload":{"id":16},"type":"x"}')
    lines = [nested, _recv(2.0, 0x1, 0x10)]
    assert inbox_unseen.scan(lines)["unseen"] == 1


def test_a_publish_line_the_log_truncated_still_pairs_by_its_top_level_id():
    # measured 11-17: meshtasticd cuts long log lines (traceroute publishes at
    # char 444). A long text cut the same way must not read as "unseen".
    full = _pub(100.1, 0x10)
    cut = full[:full.index('"payload"') + 20]          # json.loads fails on this
    out = inbox_unseen.scan([cut, _recv(100.0, 0x1, 0x10)])
    assert out["unseen"] == 0 and "unparsed" not in out


def test_a_truncated_line_never_pairs_on_a_nested_id():
    cut = ('1.0 box meshtasticd[1]: INFO  | x [Router] JSON publish message to t, 9 bytes: '
           '{"channel":0,"from":1,"payload":{"id":16,"long')
    out = inbox_unseen.scan([cut, _recv(2.0, 0x1, 0x10)])
    assert out["unseen"] == 1 and out["unparsed"] == 1


def test_an_unparseable_publish_is_counted_not_trusted():
    bad = "1.0 box meshtasticd[1]: INFO  | x [Router] JSON publish message to t, 9 bytes: {not json"
    out = inbox_unseen.scan([bad, _recv(2.0, 0x1, 0x10)])
    assert out["unseen"] == 1 and out["unparsed"] == 1


def test_a_receive_line_with_no_timestamp_still_counts():
    line = _recv(1.0, 0xA, 0x1).split(" ", 1)[1]   # no epoch prefix
    out = inbox_unseen.scan([line])
    assert out["unseen"] == 1 and out["last"] == {"from": "!0000000a", "ts": None}


# ── the tri-state read ───────────────────────────────────────────────────────

@pytest.fixture
def run(monkeypatch):
    calls = []

    def set_result(result):
        def fake(argv, **kw):
            calls.append(argv)
            if isinstance(result, BaseException):
                raise result
            return result
        monkeypatch.setattr(inbox_unseen, "_run", fake)
    inbox_unseen._reset_cache()
    yield set_result, calls
    inbox_unseen._reset_cache()


def test_read_scans_the_meshtasticd_journal_once_per_window(run):
    set_result, calls = run
    set_result(_proc([_recv(100.0, 0xA, 0x1)]))
    a = inbox_unseen.read_unseen(now=1000.0)
    b = inbox_unseen.read_unseen(now=1000.0 + inbox_unseen.CACHE_S - 1)
    assert a == b and a["unseen"] == 1 and a["lookback_h"] == inbox_unseen.LOOKBACK_H
    assert len(calls) == 1
    argv = calls[0]
    assert argv[:3] == ["journalctl", "-u", "meshtasticd"] and "short-unix" in argv
    inbox_unseen.read_unseen(now=1000.0 + inbox_unseen.CACHE_S + 1)
    assert len(calls) == 2


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


def test_an_account_that_cannot_see_system_logs_is_unobservable_not_zero(run):
    # measured shape: rc 0, empty stdout, a hint on stderr — reads as "0 heard"
    # unless the hint is honoured
    set_result, _ = run
    set_result(_proc([], rc=0, stderr="Hint: You are currently not seeing messages "
                                      "from other users and the system."))
    out = inbox_unseen.read_unseen(now=1.0)
    assert out["state"] == "unobservable" and "system logs" in out["error"]


def test_an_unexpected_error_in_the_scan_is_unobservable_never_raised(run, monkeypatch):
    set_result, _ = run
    set_result(_proc([_recv(1.0, 0xA, 0x1)]))
    monkeypatch.setattr(inbox_unseen, "scan", lambda lines: 1 / 0)
    out = inbox_unseen.read_unseen(now=1.0)
    assert out["state"] == "unobservable" and "ZeroDivisionError" in out["error"]


# ── wiring: /api/inbox and the page ─────────────────────────────────────────

def test_read_inbox_carries_unseen(monkeypatch):
    from commands.base import CommandResult
    from utils import inbox_api
    monkeypatch.setattr(inbox_api.messaging, "get_messages",
                        lambda **kw: CommandResult.ok("ok", data={"messages": []}))
    monkeypatch.setattr(inbox_api.messaging, "first_received_id_row", lambda: None)
    sentinel = {"state": "ok", "heard": 3, "unseen": 1, "last": None, "lookback_h": 24}
    monkeypatch.setattr(inbox_unseen, "read_unseen", lambda: sentinel)
    assert inbox_api.read_inbox()["unseen"] == sentinel


def test_read_inbox_survives_an_unseen_reader_that_raises(monkeypatch):
    from commands.base import CommandResult
    from utils import inbox_api
    monkeypatch.setattr(inbox_api.messaging, "get_messages",
                        lambda **kw: CommandResult.ok("ok", data={"messages": []}))
    monkeypatch.setattr(inbox_api.messaging, "first_received_id_row", lambda: None)

    def boom():
        raise RuntimeError("x")
    monkeypatch.setattr(inbox_unseen, "read_unseen", boom)
    out = inbox_api.read_inbox()
    assert out["state"] == "ok" and out["unseen"]["state"] == "unobservable"


def _render_fn():
    i = PAGE.index("function renderUnseen")
    return PAGE[i:PAGE.index("\n  }\n", i)]


def test_page_says_unseen_texts_and_why():
    fn = _render_fn()
    assert "not on this page" in fn
    assert "came in over MQTT" in fn and "no uplink" in fn
    assert "textContent" in fn or "createTextNode" in fn
    assert "innerHTML" not in fn


def test_page_says_when_unreadable_publish_lines_may_inflate_the_count():
    fn = _render_fn()
    assert "u.unparsed" in fn and "may be high" in fn


def test_page_says_when_the_radio_journal_is_unobservable():
    fn = _render_fn()
    assert "unobservable" in fn


def test_page_calls_render_unseen_from_the_poll():
    i = PAGE.index("function load()")
    assert "renderUnseen(" in PAGE[i:PAGE.index("\n  }\n", i)]
