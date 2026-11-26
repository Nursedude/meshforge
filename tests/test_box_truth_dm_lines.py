"""BIC-2 DM slice 1, piece 2: the ALLOWLISTED journal parser for DM facts.

Design D-1: parse exactly the lines we need, keep ids only. Line shapes are
the live journal's (2026-11-25, sender box, ids masked when sampled):

  <ts> <host> meshtasticd-patched[773661]: INFO  | 02:43:29 310406 [Router] Received routing from=0x.., id=0x.., portnum=5, payloadlen=2
  <ts> <host> meshtasticd-patched[773661]: WARN  | 00:14:33 301470 Alloc an err=39,to=0x..,idFrom=0x..,id=0x..

Firmware: ProtobufModule.h:92 + MeshModule.cpp:71 at 54e0d8d.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from utils.box_truth_rx import parse_dm_journal  # noqa: E402

PFX = "1795000010.250 box meshtasticd-patched[773661]: "


def _routing(frm="b03da894", pid="1234abcd", ts="1795000010.250"):
    return (f"{ts} box meshtasticd-patched[773661]: INFO  | 02:43:29 310406 "
            f"[Router] Received routing from=0x{frm}, id=0x{pid}, portnum=5, payloadlen=2")


def _alloc(err=39, idfrom="ab3c8abf", ts="1795000010.250"):
    return (f"{ts} box meshtasticd-patched[773661]: WARN  | 00:14:33 301470 "
            f"Alloc an err={err},to=0x6201ce25,idFrom=0x{idfrom},id=0x77aa0001")


class TestAllowlist:

    def test_alloc_err_keeps_ids_and_code(self):
        (j,) = parse_dm_journal(_alloc())
        assert (j.kind, j.packet_id, j.err) == ("alloc_err", 0xAB3C8ABF, 39)
        assert j.t == 1795000010.25

    def test_routing_keeps_sender_only(self):
        (j,) = parse_dm_journal(_routing())
        assert (j.kind, j.sender) == ("routing_from", 0xB03DA894)
        assert j.packet_id is None

    def test_stock_unit_name_also_matches(self):
        line = _routing().replace("meshtasticd-patched", "meshtasticd")
        assert len(parse_dm_journal(line)) == 1

    def test_routing_on_another_port_is_not_routing(self):
        assert parse_dm_journal(_routing().replace("portnum=5", "portnum=1")) == []

    def test_forged_text_body_never_parses(self):
        """A received TEXT whose body spells a refusal must not fail my DM."""
        forged = (PFX + "INFO  | 02:43:29 310406 [Router] Received text msg "
                  "from=0x11223344, id=0x55, msg=Alloc an err=39,to=0x1,"
                  "idFrom=0xab3c8abf,id=0x1")
        assert parse_dm_journal(forged) == []

    def test_continuation_line_never_parses(self):
        assert parse_dm_journal("    Alloc an err=39,to=0x1,idFrom=0xab3c8abf,id=0x1") == []

    def test_other_lines_ignored_and_nothing_else_kept(self):
        text = "\n".join([
            PFX + "INFO  | 02:43:29 310406 [Router] Received text msg from=0x1, id=0x2, msg=hello",
            PFX + "INFO  | 02:43:29 310406 [Router] Received position from=0x1, id=0x3, portnum=3, payloadlen=9",
            "-- cursor: s=abc",
            _alloc(), _routing()])
        out = parse_dm_journal(text)
        assert [j.kind for j in out] == ["alloc_err", "routing_from"]
        for j in out:
            assert set(j._fields) == {"t", "kind", "packet_id", "sender", "err"}

    def test_zero_ids_and_zero_err_dropped(self):
        assert parse_dm_journal(_alloc(err=0)) == []
        assert parse_dm_journal(_alloc(idfrom="0")) == []
        assert parse_dm_journal(_routing(frm="0")) == []

    def test_bad_timestamp_dropped(self):
        assert parse_dm_journal(_alloc(ts="nan")) == []
        assert parse_dm_journal(_alloc(ts="junk")) == []


class _Proc:
    def __init__(self, rc=0, out=b"", err=b""):
        self.returncode, self.stdout, self.stderr = rc, out, err


CUR = b"-- cursor: s=abc;i=1\n"


class TestRead:
    """read_dm_journal -> (lines | None, cursor, why). Stateless; the map's
    cache (utils.dm_journal_cache) carries the cursor between reads."""
    NOW = 1795003600.0

    def _runner(self, proc=None, exc=None):
        calls = []

        def run(argv, **kw):
            calls.append((argv, kw))
            if exc:
                raise exc
            return proc
        return run, calls

    def test_good_read_parses_bounded_and_returns_the_cursor(self):
        from utils.box_truth_rx import JOURNAL_TIMEOUT_S, read_dm_journal
        run, calls = self._runner(_Proc(out=(_alloc() + "\n").encode() + CUR))
        lines, cur, why = read_dm_journal(run, since=self.NOW - 120, now=self.NOW)
        assert why is None and [j.kind for j in lines] == ["alloc_err"]
        assert cur == "s=abc;i=1"
        argv, kw = calls[0]
        assert f"--since=@{int(self.NOW - 120)}" in argv and "--show-cursor" in argv
        assert kw["timeout"] == JOURNAL_TIMEOUT_S

    def test_resume_reads_after_the_cursor_only(self):
        from utils.box_truth_rx import read_dm_journal
        run, calls = self._runner(_Proc(out=CUR))
        lines, cur, why = read_dm_journal(run, since=0, now=self.NOW, cursor="s=old")
        assert (lines, why) == ([], None)
        assert "--after-cursor=s=old" in calls[0][0]
        assert not any(a.startswith("--since") for a in calls[0][0])

    def test_empty_read_is_empty_not_unreadable(self):
        from utils.box_truth_rx import read_dm_journal
        run, _ = self._runner(_Proc(out=CUR))
        assert read_dm_journal(run, since=self.NOW - 60, now=self.NOW)[::2] == ([], None)

    def test_failed_read_is_none_with_why(self):
        import subprocess
        from utils.box_truth_rx import read_dm_journal
        for run, _ in (self._runner(_Proc(rc=1, err=b"No journal files")),
                       self._runner(exc=subprocess.TimeoutExpired("journalctl", 8)),
                       self._runner(exc=FileNotFoundError())):
            lines, _cur, why = read_dm_journal(run, since=self.NOW - 60, now=self.NOW)
            assert lines is None and why.startswith("journal unreadable")

    def test_r1_f5_no_access_is_unreadable_never_an_empty_read(self):
        """journalctl exits 0 with NO entries and a hint on stderr when the
        user cannot read other units' logs (r1 finding 5)."""
        from utils.box_truth_rx import read_dm_journal
        hint = (b"Hint: You are currently not seeing messages from other users and the system.\n"
                b"      Users in groups 'adm', 'systemd-journal' can see all messages.\n")
        run, calls = self._runner(_Proc(out=b"-- No entries --\n", err=hint))
        lines, _cur, why = read_dm_journal(run, since=self.NOW - 60, now=self.NOW)
        assert lines is None and "cannot read" in why
        assert "-q" not in calls[0][0]          # -q would hide the hint

    def test_resume_without_a_cursor_line_is_unreadable(self):
        """A resume always prints the cursor (measured, rx_heard); rc 0 with
        nothing = the unit is gone or access was lost."""
        from utils.box_truth_rx import read_dm_journal
        run, _ = self._runner(_Proc(out=b""))
        lines, _cur, why = read_dm_journal(run, since=0, now=self.NOW, cursor="s=old")
        assert lines is None and why

    def test_window_never_wider_than_the_cap(self):
        from utils.box_truth_rx import DM_READ_MAX_S, read_dm_journal
        run, calls = self._runner(_Proc(out=CUR))
        read_dm_journal(run, since=0, now=self.NOW)
        assert f"--since=@{int(self.NOW - DM_READ_MAX_S)}" in calls[0][0]
