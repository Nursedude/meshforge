"""scripts/fanout_headroom.py — the preflight + abort check for running more
than one agent (or heavy local job) on the manager box. Fixtures are a fake
/proc + /sys + user-slice cgroup tree, so the verdict is pinned against
MEASURED numbers: today's healthy box (2026-10-20) and the 2026-07-21 freeze
(MemAvailable 650 MB, PSI 7.4, load 4.8, 66 °C).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = _ROOT / "scripts" / "fanout_headroom.py"
MB = 1024 * 1024


def _tree(tmp_path, *, total_kb=16603376, avail_kb=13159760, swap_total_kb=2097136,
          swap_free_kb=1748544, load1=0.54, cpus=4, psi_some10=0.0, psi_full60=0.0,
          temp_mc=43900, slice_max=8 * 1024 * MB, anon=978 * MB, shmem=567 * MB,
          kernel=938 * MB, file_=5999 * MB, procs=("bash", "claude"), skip=()):
    root = tmp_path / "root"
    files = {
        "proc/meminfo": f"MemTotal: {total_kb} kB\nMemAvailable: {avail_kb} kB\n"
                        f"SwapTotal: {swap_total_kb} kB\nSwapFree: {swap_free_kb} kB\n",
        "proc/loadavg": f"{load1} 0.43 0.68 2/429 2816623\n",
        "proc/cpuinfo": "".join(f"processor\t: {i}\n\n" for i in range(cpus)),
        "proc/pressure/memory": f"some avg10={psi_some10:.2f} avg60=0.00 avg300=0.00 total=1\n"
                                f"full avg10=0.00 avg60={psi_full60:.2f} avg300=0.00 total=1\n",
        "sys/class/thermal/thermal_zone0/temp": f"{temp_mc}\n",
        "cgroup/memory.max": f"{slice_max}\n",
        "cgroup/memory.stat": f"anon {anon}\nfile {file_}\nkernel {kernel}\nshmem {shmem}\n",
    }
    for i, comm in enumerate(procs, start=100):
        files[f"proc/{i}/comm"] = comm + "\n"
    for rel, body in files.items():
        if rel in skip:
            continue
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    return root


def _run(root):
    r = subprocess.run([sys.executable, str(SCRIPT), "--root", str(root),
                        "--cgroup", str(root / "cgroup")],
                       capture_output=True, text=True, timeout=30)
    return r.returncode, r.stdout


def test_todays_measured_box_is_go(tmp_path):
    rc, out = _run(_tree(tmp_path))
    assert rc == 0 and out.startswith("GO"), out


def test_the_0721_freeze_numbers_are_abort(tmp_path):
    rc, out = _run(_tree(tmp_path, avail_kb=665984, psi_some10=7.07, load1=4.82,
                         temp_mc=66400))
    assert rc == 3 and out.startswith("ABORT"), out
    assert "MemAvailable" in out and "pressure" in out, out


def test_a_full_user_slice_holds_even_with_a_roomy_host(tmp_path):
    """The binding limit is the 8 GB slice cap, not the 16 GB box: real use
    (anon+shmem+kernel) near the cap means the next heavy job is OOM-killed
    inside the slice — possibly this session, or mini/lxmd beside it."""
    rc, out = _run(_tree(tmp_path, anon=4000 * MB))   # ~2.6 GB headroom: below GO, above ABORT
    assert rc == 1 and out.startswith("HOLD"), out
    assert "user slice" in out, out


def test_page_cache_in_the_slice_is_not_counted_as_used(tmp_path):
    """memory.current includes reclaimable cache (6 GB of it today): counting
    it would read 'slice full' on a box with 5.7 GB of real headroom."""
    rc, out = _run(_tree(tmp_path, file_=7000 * MB))
    assert rc == 0, out


def test_ollama_running_holds(tmp_path):
    """Ollama may hold up to 8 GB of its own; 8 + 8 > 16 (the 07-21 trigger)."""
    rc, out = _run(_tree(tmp_path, procs=("bash", "ollama")))
    assert rc == 1 and "ollama" in out, out


def test_high_cpu_load_per_core_holds(tmp_path):
    rc, out = _run(_tree(tmp_path, load1=2.6))
    assert rc == 1 and "load" in out, out


@pytest.mark.parametrize("missing", ["proc/pressure/memory", "cgroup/memory.stat",
                                     "proc/meminfo", "sys/class/thermal/thermal_zone0/temp"])
def test_an_unreadable_input_is_unknown_never_go(tmp_path, missing):
    rc, out = _run(_tree(tmp_path, skip=(missing,)))
    assert rc == 2 and out.startswith("UNKNOWN"), out


def test_an_uncapped_slice_is_unknown_not_go(tmp_path):
    """memory.max 'max' = the blast-radius cap is gone: the OOM that should
    kill one process in the slice can freeze the box instead."""
    root = _tree(tmp_path)
    (root / "cgroup" / "memory.max").write_text("max\n")
    rc, out = _run(root)
    assert rc == 2 and "cap" in out, out
