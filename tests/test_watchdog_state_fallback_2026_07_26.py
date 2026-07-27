"""Tests for the 2026-07-26 watchdog state-fallback review fixes.

The 2376e20c fix gave the debounce streaks an in-process fallback, but the
loaders consulted it only when the DISK READ failed. A broken state dir
(the #60 sandbox class: ro-remount, ENOSPC, perms flip) usually leaves the
STALE file readable while every write fails — so the stale disk value won
every tick and the debounce stayed silenced, the exact defect the fix
claimed to close (drill-proven surviving, 2026-07-26). These tests pin:

  1. the in-process value outranks a READABLE stale file — within one
     process the memory copy is always at-least-as-new (it is written
     unconditionally at the top of every save), and the file exists only
     to survive a restart;
  2. ``probe_memory_cap_engaged`` state gets the same fallback at all —
     without it an unwritable path made every tick ``fresh``, so ONE
     historical kill re-paged forever and the ceiling streak never
     reached its threshold;
  3. the disk path resets ``streak`` on a boot_id mismatch, agreeing with
     the fallback path (a carried streak would debounce against samples
     from a different boot);
  4. an unexpected raise inside either host probe becomes an
     ``indeterminate`` disposition instead of killing the whole runner
     tick's dispatch;
  5. state-write failures follow the #63 witness pattern: ERROR on first
     failure, counted while failing, INFO with the count on recovery.
"""

import json
import logging

import pytest

from utils import watchdog_probe_core as core
from utils import watchdog_probes_host as host
from utils.watchdog_probe_core import (
    _load_parity_streak,
    _save_parity_streak,
    collect_dispositions,
    reset_dispositions,
)
from utils.watchdog_probes_host import (
    _load_cap_state,
    _load_hist_state,
    _save_cap_state,
    probe_host_memory_pressure,
    probe_memory_cap_engaged,
)


@pytest.fixture(autouse=True)
def _clean_module_state():
    """Per-test isolation for every module-level fallback/witness dict."""
    def _clear():
        reset_dispositions()
        core._streak_mem_fallback.clear()
        host._MEM_FALLBACK.clear()
        host._WRITE_ERRORS.clear()
        for name in ("_CAP_MEM_FALLBACK", "_CAP_WRITE_ERRORS"):
            d = getattr(host, name, None)
            if d is not None:
                d.clear()
        for name in ("_streak_write_warned", "_streak_write_errors"):
            d = getattr(core, name, None)
            if d is not None:
                d.clear()
        warned = getattr(host, "_cap_state_write_warned", None)
        if warned is not None:
            warned.clear()
    _clear()
    yield
    _clear()


def _blocked_path(tmp_path, name="state.json"):
    """A path whose parent is a FILE: reads and writes both fail with
    NotADirectoryError (an OSError), uid-independently — the readable-parent
    analogue of a broken state dir, without chmod (which root ignores)."""
    blocker = tmp_path / "blocked"
    blocker.write_text("x")
    return str(blocker / name), blocker


# ─────────────────────────────────────────────────────────────────────
# FIX 1 — in-process value outranks a READABLE stale file
# ─────────────────────────────────────────────────────────────────────

class TestStreakFallbackOutranksStaleDisk:
    def test_in_process_streak_wins_over_readable_stale_file(self, tmp_path):
        """The ro-remount shape: writes fail, the OLD file stays readable.

        The memory copy is written unconditionally at the top of every save,
        so within one process it is always at-least-as-new than the disk —
        a readable-but-stale file must never outvote it.
        """
        sp = str(tmp_path / "streak.json")
        with open(sp, "w", encoding="utf-8") as fh:
            json.dump({"streak": 1}, fh)          # stale survivor on disk
        core._streak_mem_fallback[sp] = 4         # what this process last wrote
        assert _load_parity_streak(sp) == 4

    def test_streak_survives_a_full_failed_write_cycle(self, tmp_path):
        """save(ok) → save(fails, mem updated) → load must see the new value."""
        sp, blocker = _blocked_path(tmp_path)
        _save_parity_streak(sp, 2)                # disk write fails, mem holds 2
        assert _load_parity_streak(sp) == 2
        _save_parity_streak(sp, 3)
        assert _load_parity_streak(sp) == 3

    def test_disk_still_read_at_process_start(self, tmp_path):
        """No in-process entry (a fresh process) → the file is the truth."""
        sp = str(tmp_path / "streak.json")
        with open(sp, "w", encoding="utf-8") as fh:
            json.dump({"streak": 7}, fh)
        assert _load_parity_streak(sp) == 7

    def test_hist_state_in_process_wins_over_readable_stale_file(self, tmp_path):
        p = tmp_path / "hist.json"
        p.write_text(json.dumps(
            {"streak": 0, "boot_id": "boot-1", "hist": []}))   # stale survivor
        host._MEM_FALLBACK[str(p)] = {
            "streak": 2, "boot_id": "boot-1", "hist": [[10.0, 500.0]],
        }
        st = _load_hist_state(str(p), "boot-1")
        assert st["streak"] == 2
        assert st["hist"] == [[10.0, 500.0]]

    def test_hist_state_fallback_boot_mismatch_resets(self, tmp_path):
        p = tmp_path / "hist.json"
        host._MEM_FALLBACK[str(p)] = {
            "streak": 5, "boot_id": "old-boot", "hist": [[10.0, 500.0]],
        }
        st = _load_hist_state(str(p), "new-boot")
        assert st == {"streak": 0, "boot_id": "new-boot", "hist": []}


# ─────────────────────────────────────────────────────────────────────
# FIX 3 — disk path must reset streak on boot mismatch (fallback path does)
# ─────────────────────────────────────────────────────────────────────

class TestHistStateBootMismatchOnDisk:
    def test_disk_boot_mismatch_resets_streak_and_hist(self, tmp_path):
        p = tmp_path / "hist.json"
        p.write_text(json.dumps(
            {"streak": 5, "boot_id": "old-boot", "hist": [[10.0, 500.0]]}))
        st = _load_hist_state(str(p), "new-boot")
        assert st["hist"] == []
        assert st["streak"] == 0    # a streak from another boot is meaningless

    def test_disk_boot_match_keeps_streak(self, tmp_path):
        p = tmp_path / "hist.json"
        p.write_text(json.dumps(
            {"streak": 3, "boot_id": "boot-1", "hist": [[10.0, 500.0]]}))
        st = _load_hist_state(str(p), "boot-1")
        assert st["streak"] == 3
        assert st["hist"] == [[10.0, 500.0]]


# ─────────────────────────────────────────────────────────────────────
# FIX 2 — cap state: in-process fallback so an unwritable path cannot
# re-fire one historical kill forever nor silence the ceiling leg
# ─────────────────────────────────────────────────────────────────────

def _cap_tree(tmp_path, *, oom_kill, max_hits):
    """A one-cgroup tree carrying a finite MemoryMax + events counters."""
    root = tmp_path / "cgroup"
    cg = root / "user.slice"
    cg.mkdir(parents=True, exist_ok=True)
    (cg / "memory.max").write_text("8589934592\n")
    (cg / "memory.current").write_text("1073741824\n")
    (cg / "memory.events").write_text(
        f"low 0\nhigh 0\nmax {max_hits}\noom 0\noom_kill {oom_kill}\n"
        f"oom_group_kill 0\n"
    )
    return str(root)


class TestCapStateFallback:
    def test_unwritable_state_kill_counted_once_ceiling_reaches_threshold(
            self, tmp_path):
        """3 ticks against an unwritable state path: the historical kill pages
        exactly once, and the rising-``max`` streak still reaches its 2-tick
        ceiling threshold in-process."""
        state_path, _ = _blocked_path(tmp_path, "cap_state.json")
        bootid = tmp_path / "bootid"
        bootid.write_text("11111111-2222-3333-4444-555555555555\n")

        def tick(oom_kill, max_hits):
            reset_dispositions()
            return probe_memory_cap_engaged(
                cgroup_root=_cap_tree(tmp_path, oom_kill=oom_kill,
                                      max_hits=max_hits),
                state_path=state_path,
                boot_id_path=str(bootid),
            )

        # Tick 1: first look of this boot — the cumulative kill IS the news.
        sigs = tick(oom_kill=5, max_hits=10)
        assert [s.severity for s in sigs] == ["wedge"]

        # Tick 2: no new kill; max rose. The kill must NOT re-fire, and the
        # ceiling streak (1 from tick 1's rise counted after the kill, +1 now)
        # reaches the 2-tick threshold — both require the in-process baseline.
        sigs = tick(oom_kill=5, max_hits=11)
        assert all(s.severity != "wedge" for s in sigs)
        assert [s.severity for s in sigs] == ["degraded"]

        # Tick 3: still no new kill, still at the ceiling.
        sigs = tick(oom_kill=5, max_hits=12)
        assert all(s.severity != "wedge" for s in sigs)
        assert [s.severity for s in sigs] == ["degraded"]

    def test_new_kill_still_fires_with_unwritable_state(self, tmp_path):
        """The fallback must not eat a REAL new kill."""
        state_path, _ = _blocked_path(tmp_path, "cap_state.json")
        bootid = tmp_path / "bootid"
        bootid.write_text("11111111-2222-3333-4444-555555555555\n")
        probe_memory_cap_engaged(
            cgroup_root=_cap_tree(tmp_path, oom_kill=0, max_hits=0),
            state_path=state_path, boot_id_path=str(bootid))
        sigs = probe_memory_cap_engaged(
            cgroup_root=_cap_tree(tmp_path, oom_kill=1, max_hits=1),
            state_path=state_path, boot_id_path=str(bootid))
        assert [s.severity for s in sigs] == ["wedge"]
        assert sigs[0].extra["new_kills"] == 1

    def test_cap_fallback_boot_mismatch_resets_fresh(self, tmp_path):
        state_path = str(tmp_path / "cap_state.json")
        host._CAP_MEM_FALLBACK[state_path] = {
            "boot_id": "old-boot",
            "cgroups": {"user.slice": {"oom": 5, "max": 10, "streak": 1}},
        }
        st = _load_cap_state(state_path, "new-boot")
        assert st == {"boot_id": "new-boot", "cgroups": {}, "fresh": True}

    def test_cap_disk_still_read_at_process_start(self, tmp_path):
        state_path = str(tmp_path / "cap_state.json")
        with open(state_path, "w", encoding="utf-8") as fh:
            json.dump({"boot_id": "boot-1",
                       "cgroups": {"a": {"oom": 2, "max": 3, "streak": 0}}}, fh)
        st = _load_cap_state(state_path, "boot-1")
        assert st["fresh"] is False
        assert st["cgroups"]["a"]["oom"] == 2


# ─────────────────────────────────────────────────────────────────────
# FIX 6 — a raising probe body becomes indeterminate, not a dead tick
# ─────────────────────────────────────────────────────────────────────

class TestProbeCrashGuard:
    def test_host_memory_probe_crash_is_indeterminate(self, monkeypatch):
        def boom(*a, **k):
            raise RuntimeError("synthetic probe crash")
        monkeypatch.setattr(host, "_read_meminfo", boom)
        assert probe_host_memory_pressure() is None
        disp = collect_dispositions()["host_memory_pressure"]
        assert disp["disp"] == "indeterminate"
        assert "probe crashed" in disp["reason"]

    def test_cap_probe_crash_is_indeterminate_empty(self, monkeypatch):
        def boom(*a, **k):
            raise RuntimeError("synthetic probe crash")
        monkeypatch.setattr(host, "_capped_cgroups", boom)
        assert probe_memory_cap_engaged() == []
        disp = collect_dispositions()["memory_cap_engaged"]
        assert disp["disp"] == "indeterminate"
        assert "probe crashed" in disp["reason"]


# ─────────────────────────────────────────────────────────────────────
# FIX 2/4 — write-failure witness: ERROR first, counted, INFO on recovery
# ─────────────────────────────────────────────────────────────────────

class TestWriteWitnessRecovery:
    def test_cap_state_error_then_recovery_info(self, tmp_path, caplog):
        state_path, blocker = _blocked_path(tmp_path, "cap_state.json")
        state = {"boot_id": "b1",
                 "cgroups": {"user.slice": {"oom": 0, "max": 0, "streak": 0}}}

        with caplog.at_level(logging.DEBUG, logger="watchdog"):
            _save_cap_state(state_path, state)
            _save_cap_state(state_path, state)
        errors = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert len(errors) == 1                   # first failure only
        assert host._CAP_WRITE_ERRORS[state_path] == 2

        caplog.clear()
        blocker.unlink()                          # path becomes writable
        with caplog.at_level(logging.INFO, logger="watchdog"):
            _save_cap_state(state_path, state)
        infos = [r for r in caplog.records if r.levelno == logging.INFO]
        assert any("2" in r.getMessage() for r in infos)   # count reported
        assert host._CAP_WRITE_ERRORS[state_path] == 0     # counter reset
        with open(state_path, "r", encoding="utf-8") as fh:
            assert json.load(fh)["boot_id"] == "b1"

    def test_parity_streak_error_then_recovery_info(self, tmp_path, caplog):
        sp, blocker = _blocked_path(tmp_path, "streak.json")

        with caplog.at_level(logging.DEBUG, logger="watchdog"):
            _save_parity_streak(sp, 1)
            _save_parity_streak(sp, 2)
        errors = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert len(errors) == 1

        caplog.clear()
        blocker.unlink()
        with caplog.at_level(logging.INFO, logger="watchdog"):
            _save_parity_streak(sp, 3)
        assert any(r.levelno == logging.INFO for r in caplog.records)
        assert _load_parity_streak(sp) == 3
