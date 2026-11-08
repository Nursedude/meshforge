"""box_truth/v1 ``rx_heard`` — the hearer's own evidence (INBOX-2 step 2).

Design + pre-registered acceptance: .claude/plans/inbox2_heard_by_design_2026_11_07.md
Byte-identical in MeshForge and MeshAnchor (parity_check).

The fake journal below models the two journalctl facts measured on the fleet
2026-11-08 (systemd 252 + 257): ``--since`` together with any cursor option
is REFUSED, and ``--show-cursor`` prints the cursor even when no entry
followed it. A bad cursor exits 1 ("Failed to seek to cursor").
"""
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from utils import box_truth as bt  # noqa: E402
from utils import box_truth_rx as rx  # noqa: E402

NOW = 1_800_000_000.0
HOST = "box meshtasticd-patched[2743]: "


def rx_line(ts, frm=0x6201CE25, pid=0x1234, text="hello"):
    return (f"{ts:.6f} {HOST}INFO  | 11:23:05 91470 [Router] Received text msg "
            f"from=0x{frm:08x}, id=0x{pid:x}, msg={text}")


def other_line(ts):
    return f"{ts:.6f} {HOST}DEBUG | 11:23:05 91470 [Router] Update DB node 0x1 for variant 4"


class FakeJournal:
    """Entries are (ts, line); cursor i = 'i=<n>' after entry index n."""

    def __init__(self, lines=(), reject_cursor=False, rc=0, raise_exc=None):
        self.lines = list(lines)          # [(ts, text)]
        self.calls = []
        self.reject_cursor = reject_cursor
        self.rc = rc
        self.raise_exc = raise_exc

    def add(self, *lines):
        self.lines.extend(lines)

    def __call__(self, argv, **kw):
        self.calls.append(argv)
        assert kw.get("timeout"), "every subprocess is bounded (MF004)"
        assert kw.get("stdin") == subprocess.DEVNULL
        if self.raise_exc:
            raise self.raise_exc
        assert argv[:2] == ["journalctl", "-u"]
        opts = [a for a in argv if a.startswith(("--since", "--after-cursor",
                                                 "--cursor"))]
        if len(opts) > 1:
            return SimpleNamespace(returncode=1, stdout=b"", stderr=(
                b"Please specify only one of --since=, --cursor=, "
                b"--cursor-file=, and --after-cursor=."))
        if self.rc:
            return SimpleNamespace(returncode=self.rc, stdout=b"", stderr=b"boom")
        if getattr(self, "vanish", False):
            return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")
        start = 0
        for a in opts:
            if a.startswith("--since=@"):
                since = float(a.split("@", 1)[1])
                start = next((i for i, (ts, _) in enumerate(self.lines) if ts >= since),
                             len(self.lines))
            elif a.startswith("--after-cursor="):
                c = a.split("=", 1)[1]
                if self.reject_cursor or not c.startswith("s=x;i="):
                    return SimpleNamespace(returncode=1, stdout=b"",
                                           stderr=b"Failed to seek to cursor: Invalid argument")
                start = int(c.split("i=", 1)[1]) + 1
        out = [t for _, t in self.lines[start:]]
        if self.lines:
            last = max(start, len(self.lines)) - 1
            out.append(f"-- cursor: s=x;i={last}")
        return SimpleNamespace(returncode=0, stdout=("\n".join(out) + "\n").encode()
                               if out else b"", stderr=b"")


def L(ts, **kw):
    return (ts, rx_line(ts, **kw))


def O(ts):
    return (ts, other_line(ts))


@pytest.fixture
def state(tmp_path):
    return tmp_path / "state" / "box_truth" / rx.STATE_BASENAME


def read(state, j, now=NOW, **kw):
    kw.setdefault("identity", "box-a")
    return rx.read_rx_heard(state, j, now=now, **kw)


# ── the contract ────────────────────────────────────────────────────────

def test_rx_heard_is_an_allowlisted_section():
    assert "rx_heard" in bt.SECTIONS
    assert tuple(rx.READ_KINDS) == ("initial", "incremental", "reset", "failed")


def test_first_read_uses_since_and_never_a_cursor_option(state):
    j = FakeJournal([O(NOW - 7000), L(NOW - 100, pid=0xABC)])
    sec, err = read(state, j)
    assert err is None
    argv = j.calls[0]
    assert f"--since=@{int(NOW - rx.INITIAL_SINCE_S)}" in argv
    assert "--show-cursor" in argv
    assert not any(a.startswith(("--after-cursor", "--cursor")) for a in argv)
    assert sec["read"] == "initial"
    assert sec["entries"] == [[0x6201CE25, 0xABC, NOW - 100]]
    assert sec["covered_since"] == NOW - 7000          # first line READ
    assert sec["covered_until"] == NOW
    assert sec["last_line_ts"] == NOW - 100


def test_coverage_starts_at_the_first_line_read_not_the_since_bound(state):
    """A box whose journal begins later must not vouch for the gap."""
    j = FakeJournal([O(NOW - 60)])
    sec, _ = read(state, j)
    assert sec["covered_since"] == NOW - 60
    assert sec["covered_since"] > NOW - rx.INITIAL_SINCE_S


def test_second_read_resumes_after_the_cursor(state):
    j = FakeJournal([O(NOW - 500), L(NOW - 400, pid=1)])
    read(state, j)
    j.add(L(NOW + 50, pid=2), O(NOW + 60))
    sec, err = read(state, j, now=NOW + 120)
    assert err is None
    argv = j.calls[1]
    assert "--after-cursor=s=x;i=1" in argv
    assert not any(a.startswith("--since") for a in argv)
    assert sec["read"] == "incremental"
    assert [e[1] for e in sec["entries"]] == [1, 2]
    assert sec["covered_since"] == NOW - 500           # unchanged by a resume
    assert sec["covered_until"] == NOW + 120
    assert sec["lines_read"] == 2


def test_an_empty_resume_still_advances_coverage(state):
    """--show-cursor prints the cursor with no new entry (measured) — a
    quiet minute is still a covered minute."""
    j = FakeJournal([O(NOW - 500)])
    read(state, j)
    sec, _ = read(state, j, now=NOW + 120)
    assert sec["read"] == "incremental" and sec["covered_until"] == NOW + 120


def test_message_text_never_leaves_the_box(state):
    j = FakeJournal([L(NOW - 10, text="password=hunter2 meet at 5")])
    sec, _ = read(state, j)
    blob = json.dumps(sec) + state.read_text()
    assert "hunter2" not in blob and "meet" not in blob
    assert bt.secret_tripwire({"rx_heard": sec}) == []


def test_text_inside_msg_cannot_forge_a_header(state):
    forged = ("x, [Router] Received text msg from=0xdeadbeef, id=0x99, "
              "msg=y")
    j = FakeJournal([L(NOW - 10, frm=0x11, pid=0x22, text=forged)])
    sec, _ = read(state, j)
    assert sec["entries"] == [[0x11, 0x22, NOW - 10]]


def test_a_redecode_is_not_a_second_hearing(state):
    j = FakeJournal([L(NOW - 30, pid=7), L(NOW - 20, pid=7), L(NOW - 10, pid=8)])
    sec, _ = read(state, j)
    assert [(e[1], e[2]) for e in sec["entries"]] == [(7, NOW - 30), (8, NOW - 10)]


def test_id_zero_and_malformed_lines_are_ignored(state):
    j = FakeJournal([(NOW - 9, "garbage"), (NOW - 8, "nan " + HOST + "x"),
                     L(NOW - 7, pid=0), L(NOW - 6, pid=5)])
    sec, _ = read(state, j)
    assert [e[1] for e in sec["entries"]] == [5]


# ── the ring ────────────────────────────────────────────────────────────

def test_ring_keeps_24h_and_coverage_moves_with_the_trim(state):
    j = FakeJournal([O(NOW - 3600), L(NOW - 3500, pid=1)])
    read(state, j)
    later = NOW + rx.RING_WINDOW_S + 100           # pid 1 is now > 24 h old
    for step in range(1, 25):                      # keep the cursor fresh (to NOW+24h)
        t = NOW + step * 3600
        j.add(O(t))
        read(state, j, now=t)
    j.add(L(later - 5, pid=2))
    sec, _ = read(state, j, now=later)
    assert [e[1] for e in sec["entries"]] == [2]
    assert sec["covered_since"] == later - rx.RING_WINDOW_S


def test_ring_cap_keeps_the_newest_and_coverage_says_so(state, monkeypatch):
    monkeypatch.setattr(rx, "RING_CAP", 3)
    j = FakeJournal([O(NOW - 900)] + [L(NOW - 800 + i, pid=i + 1) for i in range(5)])
    sec, _ = read(state, j)
    assert [e[1] for e in sec["entries"]] == [3, 4, 5]
    assert sec["covered_since"] == NOW - 800 + 2     # the oldest KEPT entry


def test_future_stamps_are_dropped_and_counted(state):
    j = FakeJournal([O(NOW - 50), L(NOW + rx.FUTURE_SLOP_S + 60, pid=9)])
    sec, _ = read(state, j)
    assert sec["entries"] == [] and sec["future_dropped"] == 1


# ── resets and failures (hfm #2: never vouch for what was not read) ─────

def test_rejected_cursor_rereads_from_since_and_restarts_coverage(state):
    j = FakeJournal([O(NOW - 900), L(NOW - 800, pid=1)])
    read(state, j)
    j.reject_cursor = True
    j.add(O(NOW + 30), L(NOW + 40, pid=2))
    sec, err = read(state, j, now=NOW + 60)
    assert err is None
    assert sec["read"] == "reset" and "cursor rejected" in sec["note"]
    assert any(a.startswith("--since=@") for a in j.calls[-1])
    assert [e[1] for e in sec["entries"]] == [1, 2]   # heard stays heard
    assert sec["covered_since"] == NOW - 900          # first line of the RE-read


def test_cursor_older_than_the_first_read_window_is_dropped(state):
    j = FakeJournal([O(NOW - 100)])
    read(state, j)
    t = NOW + rx.INITIAL_SINCE_S + 600
    j.add(O(t - 60))
    sec, _ = read(state, j, now=t)
    assert sec["read"] == "reset" and "older than" in sec["note"]
    assert not any(a.startswith("--after-cursor") for a in j.calls[-1])
    assert sec["covered_since"] == t - 60


def test_clock_went_backward_restarts_coverage(state):
    j = FakeJournal([O(NOW - 100)])
    read(state, j)
    t = NOW - 3 * 3600
    sec, _ = read(state, j, now=t)
    assert sec["read"] == "reset" and "clock went backward" in sec["note"]


@pytest.mark.parametrize("exc", [subprocess.TimeoutExpired(["journalctl"], 8),
                                 FileNotFoundError("journalctl")])
def test_a_failed_read_serves_the_old_ring_and_does_not_advance(state, exc):
    j = FakeJournal([O(NOW - 900), L(NOW - 800, pid=1)])
    read(state, j)
    before = state.read_text()
    j.raise_exc = exc
    sec, err = read(state, j, now=NOW + 120)
    assert sec["read"] == "failed" and err and err.startswith("rx_heard:")
    assert sec["covered_until"] == NOW                # NOT now+120
    assert [e[1] for e in sec["entries"]] == [1]
    assert state.read_text() == before


def test_a_nonzero_exit_without_a_cursor_is_failed_not_empty(state):
    sec, err = read(state, FakeJournal(rc=1))
    assert sec["read"] == "failed" and "boom" in err
    assert sec["covered_since"] is None


def test_no_journal_at_all_is_absent_not_error(state):
    """meshanchor-server measured 2026-11-08: no meshtasticd → rc 0, no
    output, no cursor line."""
    sec, err = read(state, FakeJournal([]))
    assert err is None
    assert sec["entries"] == [] and sec["covered_since"] is None
    assert sec["covered_until"] is None              # nothing vouched for


@pytest.mark.parametrize("raw", ["{not json", '{"v": 99, "entries": []}', "[]",
                                 '{"v": 1, "entries": null}'])
def test_corrupt_state_restarts_the_ring_and_says_so(state, raw):
    state.parent.mkdir(parents=True)
    state.write_text(raw)
    sec, err = read(state, FakeJournal([O(NOW - 10)]))
    assert err is None and sec["read"] == "reset" and "restarted" in sec["note"]
    assert json.loads(state.read_text())["v"] == rx.STATE_VERSION


def test_malformed_state_rows_are_dropped_not_raised(state):
    state.parent.mkdir(parents=True)
    state.write_text(json.dumps({"v": 1, "machine": "box-a", "cursor": "bad cursor!", "entries": [
        [1, 2, NOW - 5], [True, 2, 1], [1, 0, 1], [1, 2**32, 1], ["a", 1, 1],
        [1, 3, float("nan")], [1, 4], 7]}).replace("NaN", "1e400"))
    sec, _ = read(state, FakeJournal([O(NOW - 10)]))
    assert [e[1] for e in sec["entries"]] == [2]
    assert sec["read"] == "initial"                   # bad cursor discarded


def test_unwritable_state_is_an_error_but_the_section_is_served(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    sec, err = read(blocker / "sub" / rx.STATE_BASENAME, FakeJournal([L(NOW - 5, pid=3)]))
    assert err and "unwritable" in err
    assert [e[1] for e in sec["entries"]] == [3]


def test_state_lives_beside_the_read_witness(tmp_path):
    w = tmp_path / "box_truth" / "reads.jsonl"
    assert rx.state_path_for(w) == tmp_path / "box_truth" / "rx_heard.json"


def test_state_file_is_private(state):
    read(state, FakeJournal([O(NOW - 10)]))
    assert (state.stat().st_mode & 0o777) == 0o600


# ── wired into the export (the consumer path, not just the function) ────

def test_collect_sections_serves_rx_heard_from_the_box_state_dir(tmp_path):
    from utils import box_truth_collect as bc
    import time
    t = time.time()
    j = FakeJournal([O(t - 60), L(t - 50, pid=0x77)])

    def runner(argv, **kw):
        if argv[:1] == ["journalctl"]:
            return j(argv, **kw)
        return SimpleNamespace(returncode=1, stdout=b"", stderr=b"")
    src = bc.Sources(home=tmp_path, env={}, watchdog_path=tmp_path / "wd",
                     proc_tcp=(), dev_globs=(), http_get=lambda u: None,
                     runner=runner)
    sections, errors = bc.collect_sections(src)
    assert "rx_heard" not in errors, errors.get("rx_heard")
    assert sections["rx_heard"]["entries"][0][1] == 0x77
    assert src.rx_state_path == tmp_path / ".local/state/box_truth/rx_heard.json"
    assert src.rx_state_path.exists()


def test_the_export_serves_rx_heard_through_the_frame(tmp_path):
    from utils import box_truth_collect as bc
    j = FakeJournal([O(9e8), L(9e8 + 1, pid=0x78)])

    def runner(argv, **kw):
        if argv[:1] == ["journalctl"]:
            return j(argv, **kw)
        return SimpleNamespace(returncode=1, stdout=b"", stderr=b"")
    src = bc.Sources(home=tmp_path, env={}, watchdog_path=tmp_path / "wd",
                     proc_tcp=(), dev_globs=(), http_get=lambda u: None,
                     runner=runner)
    doc = bt.parse_framed(bc.run_export(None, src, now=9e8 + 5))
    assert doc["sections"]["rx_heard"]["entries"][0][1] == 0x78
    assert doc["tripwire"] == []


# ── non-author review 2026-11-08 (Fable, by running) ────────────────────

def test_a_unit_that_vanishes_from_the_journal_view_is_failed_not_covered(state):
    """F1: rc 0 with NO output and NO cursor line on a RESUME means journalctl
    saw zero entries for the unit (grant user dropped from adm, unit renamed)
    — measured: an unknown unit prints nothing, a quiet known unit still
    prints its cursor. Coverage must not advance over it."""
    j = FakeJournal([O(NOW - 600)])
    read(state, j)
    j.vanish = True
    for k in range(1, 4):
        sec, err = read(state, j, now=NOW + 120 * k)
        assert sec["read"] == "failed"
        assert sec["covered_until"] == NOW
        assert err and "no entries" in err


def test_a_state_file_from_another_machine_is_not_this_boxs_coverage(state):
    """F2: journalctl ACCEPTS another machine's cursor (it seeks by the
    cursor's realtime — measured lehua with moc4's cursor, rc 0). A cloned
    rx_heard.json must restart the ring, and its hearings are not ours."""
    j = FakeJournal([O(NOW - 5000), L(NOW - 4000, pid=0xAAAA)])
    read(state, j, identity="box-a")
    jb = FakeJournal([O(NOW - 3000), L(NOW + 10, pid=0xBBBB)])   # box B's OWN journal
    sec, err = read(state, jb, now=NOW + 60, identity="box-b")
    assert err is None
    assert sec["read"] == "reset" and "another machine" in sec["note"]
    assert [e[1] for e in sec["entries"]] == [0xBBBB]
    assert sec["covered_since"] == NOW - 3000
    assert not any(a.startswith("--after-cursor") for a in jb.calls[-1])


def test_an_unknown_identity_never_matches_a_stored_one(state):
    j = FakeJournal([O(NOW - 50)])
    read(state, j, identity="box-a")
    sec, _ = read(state, j, now=NOW + 60, identity=None)
    assert sec["read"] == "reset"


def test_the_boxs_own_api_sends_are_not_hearings(state):
    """F3: the [Router] anchor is load-bearing — lehua's real journal carries
    `[ServerAPI] Received text msg from=0x0, ...` for its OWN client sends."""
    api = (f"{NOW - 5:.6f} {HOST}INFO  | 01:25:57 89619 [ServerAPI] Received text msg "
           f"from=0x0, id=0xa2ea44f6, msg=hi")
    bare = (f"{NOW - 4:.6f} {HOST}INFO  | 01:25:57 89619 Received text msg "
            f"from=0x6201ce25, id=0xa2ea44f7, msg=hi")
    j = FakeJournal([(NOW - 5, api), (NOW - 4, bare), L(NOW - 3, frm=0, pid=0x9)])
    sec, _ = read(state, j)
    assert sec["entries"] == []


def test_a_reset_with_no_cursor_line_forgets_the_rejected_cursor(state):
    j = FakeJournal([O(NOW - 50)])
    read(state, j)
    j.reject_cursor = True
    j.lines = []                                     # journal now empty for the unit
    read(state, j, now=NOW + 60)
    assert json.loads(state.read_text())["cursor"] is None


def test_a_first_read_failure_runs_journalctl_once_and_claims_no_reset(state):
    j = FakeJournal(rc=1)
    sec, _ = read(state, j)
    assert len(j.calls) == 1 and sec["note"] is None


def test_a_double_failure_says_nothing_restarted(state):
    j = FakeJournal([O(NOW - 50)])
    read(state, j)
    j.reject_cursor = True
    j.rc = 1                                          # the --since re-read fails too
    sec, err = read(state, j, now=NOW + 60)
    assert sec["read"] == "failed" and err
    assert "coverage restarted" not in sec["note"] and "nothing restarted" in sec["note"]
    assert json.loads(state.read_text())["cursor"] == "s=x;i=0"


def test_two_unknown_identities_are_not_a_match(state):
    """None == None must not vouch: a box that cannot read its machine id
    inherits nothing, even from a state written while it also could not."""
    state.parent.mkdir(parents=True)
    state.write_text(json.dumps({"v": 1, "machine": None, "cursor": "s=x;i=0",
                                 "covered_since": NOW - 100, "covered_until": NOW - 60,
                                 "last_line_ts": NOW - 60, "entries": [[1, 2, NOW - 90]]}))
    sec, _ = read(state, FakeJournal([O(NOW - 30)]), identity=None)
    assert sec["read"] == "reset" and sec["entries"] == []


def test_machine_identity_reads_a_real_id_or_none(tmp_path):
    p = tmp_path / "machine-id"
    p.write_text("0123456789abcdef0123456789abcdef\n")
    assert rx.machine_identity(p) == "0123456789abcdef0123456789abcdef"
    p.write_text("uninitialized\n")
    assert rx.machine_identity(p) is None
    assert rx.machine_identity(tmp_path / "absent") is None
