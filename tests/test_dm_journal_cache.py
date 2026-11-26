"""code-review r1 finding 1: an unsettled DM made EVERY inbox poll re-read
up to 6 h of journal (no cursor). The map keeps ONE in-process cache: one
bounded full read, then resume-after-cursor reads of only the new lines."""
import os
import sys
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from utils.box_truth_rx import DM_READ_MAX_S, JournalLine  # noqa: E402
from utils.dm_journal_cache import DmJournalCache, get_dm_journal_cache  # noqa: E402

NOW = 1795003600.0


class FakeReader:
    """Stands in for box_truth_rx.read_dm_journal; records each call."""

    def __init__(self, *results):
        self.results = list(results)
        self.calls = []

    def __call__(self, runner, *, since, now, cursor=None):
        self.calls.append({"since": since, "cursor": cursor})
        return self.results.pop(0)


def _a(t, pid=0x51):
    return JournalLine(t, "alloc_err", packet_id=pid, err=39)


def test_second_read_resumes_after_the_cursor():
    r = FakeReader(([_a(NOW - 30)], "c1", None), ([_a(NOW + 5, 0x52)], "c2", None))
    c = DmJournalCache(reader=r)
    lines, why = c.read(None, since=NOW - 60, now=NOW)
    assert why is None and len(lines) == 1
    lines, why = c.read(None, since=NOW - 60, now=NOW + 10)
    assert [x.packet_id for x in lines] == [0x51, 0x52]
    assert r.calls[1]["cursor"] == "c1"


def test_many_polls_cost_one_full_read():
    r = FakeReader(*([([], "c", None)] * 50))
    c = DmJournalCache(reader=r)
    for i in range(50):
        c.read(None, since=NOW - 3 * 3600, now=NOW + i * 10)
    assert sum(1 for k in r.calls if k["cursor"] is None) == 1


def test_an_older_send_than_covered_forces_one_re_read():
    r = FakeReader(([], "c1", None), ([_a(NOW - 3000)], "c2", None), ([], "c3", None))
    c = DmJournalCache(reader=r)
    c.read(None, since=NOW - 60, now=NOW)
    lines, _ = c.read(None, since=NOW - 3600, now=NOW + 1)
    assert r.calls[1]["cursor"] is None and len(lines) == 1
    c.read(None, since=NOW - 3600, now=NOW + 2)
    assert r.calls[2]["cursor"] == "c2"


def test_window_is_capped():
    r = FakeReader(([], "c", None))
    DmJournalCache(reader=r).read(None, since=0, now=NOW)
    assert r.calls[0]["since"] == NOW - DM_READ_MAX_S


def test_failed_resume_says_why_and_the_next_read_starts_over():
    r = FakeReader(([], "c1", None), (None, None, "journal unreadable: x"),
                   ([_a(NOW - 10)], "c2", None))
    c = DmJournalCache(reader=r)
    c.read(None, since=NOW - 60, now=NOW)
    lines, why = c.read(None, since=NOW - 60, now=NOW + 10)
    assert lines is None and "unreadable" in why
    lines, why = c.read(None, since=NOW - 60, now=NOW + 20)
    assert r.calls[2]["cursor"] is None and len(lines) == 1


def test_lines_before_since_are_not_returned_and_old_lines_trimmed():
    r = FakeReader(([_a(NOW - 500, 1), _a(NOW - 10, 2)], "c", None),
                   ([], "c", None))
    c = DmJournalCache(reader=r)
    lines, _ = c.read(None, since=NOW - 60, now=NOW)
    assert [x.packet_id for x in lines] == [2]
    c.read(None, since=NOW - 60, now=NOW + DM_READ_MAX_S)   # NOW-500 aged out
    assert all(x.t >= NOW for x in c._lines)


def test_concurrent_reads_never_interleave():
    r = FakeReader(*([([], "c", None)] * 40))
    c = DmJournalCache(reader=r)
    errs = []

    def go(i):
        try:
            c.read(None, since=NOW - 60, now=NOW + i)
        except Exception as e:     # pragma: no cover - the failure
            errs.append(e)
    ts = [threading.Thread(target=go, args=(i,)) for i in range(40)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert not errs and len(r.calls) == 40


def test_one_cache_per_process():
    assert get_dm_journal_cache() is get_dm_journal_cache()


def test_held_never_reads_and_says_none_without_coverage():
    r = FakeReader(([_a(NOW - 30)], "c1", None))
    c = DmJournalCache(reader=r)
    assert c.held(since=NOW - 60, now=NOW) is None          # never read
    c.read(None, since=NOW - 60, now=NOW)
    assert [x.packet_id for x in c.held(since=NOW - 60, now=NOW + 999)] == [0x51]
    assert c.held(since=NOW - 3600, now=NOW) is None        # before coverage
    assert len(r.calls) == 1
