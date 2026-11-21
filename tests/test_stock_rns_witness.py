"""scripts/stock_rns_witness.sh — the stock-RNS witness (2026-11-21).

What these pin: the witness is its OWN RNS instance (never transport, never the
fleet's instance ports — MF009), stamp enforcement is an explicit yes|no, and it
REFUSES on a box that carries the fleet's RNS config. Nothing here installs,
starts, or execs systemd-run: every case exits before that (refusal or render).
"""
import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "stock_rns_witness.sh"
FLEET_PORTS = ("37428", "37429")


def _run(*args, home, extra=None):
    env = {"PATH": "/usr/bin:/bin", "HOME": str(home),
           "STOCK_WITNESS_BASE": str(Path(home) / "witness"),
           "STOCK_WITNESS_ENTRY_HOST": "entry.example.invalid"}
    env.update(extra or {})
    return subprocess.run(["/bin/bash", str(SCRIPT), *args], capture_output=True,
                          text=True, timeout=20, env=env)


@pytest.mark.parametrize("enforce", ["yes", "no"])
def test_config_is_its_own_non_transport_instance(tmp_path, enforce):
    r = _run("render-config", enforce, home=tmp_path)
    assert r.returncode == 0, r.stderr
    cfg = r.stdout
    assert "enable_transport = No" in cfg
    assert f"enforce_stamps = {enforce}" in cfg
    for port in FLEET_PORTS:                       # MF009: never the fleet rnsd's ports
        assert f"= {port}" not in cfg, cfg
    assert cfg.count("type = ") == 1               # ONE leg: the entry point
    assert "type = TCPClientInterface" in cfg
    assert "AutoInterface" not in cfg


def test_enforce_value_outside_vocabulary_is_refused(tmp_path):
    r = _run("render-config", "maybe", home=tmp_path)
    assert r.returncode == 2
    assert "enforce must be yes|no" in r.stderr


def test_refuses_on_a_box_with_the_fleet_rns_config(tmp_path):
    """The witness must never share a box with the fleet's RNS (identity and
    ports). Planted ~/.reticulum/config -> refused before anything runs. (On a
    box with /etc/reticulum/config it refuses on that instead — same exit.)"""
    (tmp_path / ".reticulum").mkdir()
    (tmp_path / ".reticulum" / "config").write_text("[reticulum]\n")
    for cmd in ("install", "start"):
        r = _run(cmd, home=tmp_path)
        assert r.returncode == 2, (cmd, r.stdout, r.stderr)
        assert "refusing:" in r.stderr, (cmd, r.stderr)
    assert not (tmp_path / "witness").exists(), "refusal must happen before any write"


def test_status_on_an_uninstalled_box_says_so(tmp_path):
    r = _run("status", home=tmp_path)
    assert "NOT INSTALLED" in r.stdout, r.stdout


def test_entry_host_has_no_default(tmp_path):
    """No guessed entry point: unset host is refused, never defaulted."""
    r = _run("render-config", "no", home=tmp_path, extra={"STOCK_WITNESS_ENTRY_HOST": ""})
    assert r.returncode == 2
    assert "STOCK_WITNESS_ENTRY_HOST is not set" in r.stderr


# ------------------------------------------- 11-21 Run A: the status count lied
# MEASURED: `status` said "paths known: 0" while the table held 5 rows — rns
# 1.6.1 prints `1h (?)`, never "hops", and the old count grepped 'hop'. And a
# passive table is not reachability: `probe` drops the cached path and asks the
# network fresh. These are the REAL 1.6.1 lines from that run.
TABLE_161 = """\
<59f83bb6565288236b0c299d3432d3bc>   1h (?) on TCPInterface[Regional entry point/moc.mf.internal:4242] (dv:0.000/lc:n)
<ef1dcedc4c132296f2821ca14c870043>   2h (?) on TCPInterface[Regional entry point/moc.mf.internal:4242] (dv:0.000/lc:n)
<fdf27722b6b26503b5d5bb766a734923>   2h (?) on TCPInterface[Regional entry point/moc.mf.internal:4242] (dv:0.000/lc:n)
"""


def _stdin(cmd, text, home):
    env = {"PATH": "/usr/bin:/bin", "HOME": str(home),
           "STOCK_WITNESS_BASE": str(Path(home) / "witness"),
           "STOCK_WITNESS_ENTRY_HOST": "entry.example.invalid"}
    return subprocess.run(["/bin/bash", str(SCRIPT), cmd], input=text, capture_output=True,
                          text=True, timeout=20, env=env)


def test_table_count_reads_hash_rows_not_the_word_hop(tmp_path):
    r = _stdin("count-table", TABLE_161, tmp_path)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "3"


@pytest.mark.parametrize("raw,verdict", [
    ("Path to <7dce12ce67e4e32d89e6f2b01d238d33> requested   ⢄ ⢂ ⢁ \r"
     "Path found, destination <7dce12ce67e4e32d89e6f2b01d238d33> is 1 hop away via <2bee> on X",
     "FOUND 1"),                                                   # singular — the regex missed it
    ("Path to <57a7…> requested\rPath found, destination <57a7f1400c1015d8907ff177daf89081> "
     "is 2 hops away via <2bee> on X", "FOUND 2"),
    ("Path to <57a7…> requested   ⢄ ⢂\rPath not found", "NOT_FOUND"),
    ("", "UNKNOWN"),                                               # no verdict line is never 'not found'
    ("Traceback (most recent call last):\n  ...", "UNKNOWN"),
])
def test_rnpath_verdicts(tmp_path, raw, verdict):
    r = _stdin("classify", raw, tmp_path)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == verdict, (raw, r.stdout)


def test_probe_survives_not_found_and_reports_every_identity(tmp_path):
    """MEASURED 11-21 n=2 Run B: probe printed its header and then NOTHING —
    rnpath exits non-zero on 'Path not found', and set -e + pipefail killed the
    script at the first miss. The arm that matters most went silent. Stubs only
    (a fake rnpath / systemctl in tmp) — nothing real is executed by PATH."""
    base = tmp_path / "witness"
    vbin = base / "venv" / "bin"
    vbin.mkdir(parents=True)
    (base / "rns").mkdir()
    (base / "rns" / "config").write_text("[stamps]\n  enforce_stamps = yes\n")
    rnpath = vbin / "rnpath"
    rnpath.write_text("#!/bin/sh\n"
                      "for a in \"$@\"; do last=$a; done\n"
                      "case \"$1\" in -d) exit 0;; esac\n"
                      "case \"$last\" in\n"
                      "  aaaa*) printf 'Path found, destination <%s> is 2 hops away via <x> on y\\n' \"$last\"; exit 0;;\n"
                      "  *) printf 'Path to <%s> requested\\rPath not found\\n' \"$last\"; exit 1;;\n"
                      "esac\n")
    py = vbin / "python"
    py.write_text("#!/bin/sh\necho rns=1.6.1 lxmf=1.3.1 nomadnet=1.5.1\n")
    stub = tmp_path / "stubbin"
    stub.mkdir()
    (stub / "systemctl").write_text("#!/bin/sh\nexit 0\n")      # 'is-active' -> active
    for f in (rnpath, py, stub / "systemctl"):
        f.chmod(0o755)
    env = {"PATH": f"{stub}:/usr/bin:/bin", "HOME": str(tmp_path),
           "STOCK_WITNESS_BASE": str(base), "STOCK_WITNESS_ENTRY_HOST": "entry.example.invalid"}
    hit, miss1, miss2 = "a" * 32, "b" * 32, "c" * 32
    r = subprocess.run(["/bin/bash", str(SCRIPT), "probe", f"one={miss1}", f"two={hit}", f"three={miss2}"],
                       capture_output=True, text=True, timeout=30, env=env)
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert r.stdout.count("NOT_FOUND") == 2, r.stdout
    assert "FOUND 2" in r.stdout, r.stdout
    assert "found: 1/3" in r.stdout, r.stdout


# ------------------------------- 11-21 root cause: the drop is SILENT in the log
# MEASURED 13:08–13:20 (meshforge-lab): under enforce_stamps = yes rns 1.6.1
# drops unstamped path responses via filtered_announce() with every log line
# commented out — only the per-interface Filtered ↓ bytes saw it. `status` grepped
# Interface|Status|Traffic and threw that line away. REAL 1.6.1 `rnstatus -F` text.
RNSTATUS_F_161 = """\

 Shared Instance[rns/default]
    Status    : Up
    Serving   : 0 programs
    Rate      : 1.00 Gbps, MTU 262144
    Traffic   : ↑0 B        0 bps
                ↓747 B      0 bps
    Filtered  : ↑0 B  0 bps
                ↓0 B  0 bps

 TCPInterface[Regional entry point/moc.mf.internal:4242]
    Status    : Up
    Mode      : Full
    Rate      : 10.00 Mbps, MTU 16384
    Traffic   : ↑1.25 KB    0 bps
                ↓254 B      0 bps
    Filtered  : ↑0 B  0 bps
                ↓2.66 KB  0 bps
"""


def test_leg_stats_reports_the_entry_legs_filtered_rx_not_the_shared_instance(tmp_path):
    r = _stdin("leg-stats", RNSTATUS_F_161, tmp_path)
    assert r.returncode == 0, r.stderr
    assert "rx accepted: 254 B" in r.stdout, r.stdout
    assert "rx filtered: 2.66 KB" in r.stdout, r.stdout     # the leg's, not the shared instance's 0 B


@pytest.mark.parametrize("raw", [
    "",                                                      # rnstatus gave nothing
    RNSTATUS_F_161.split(" TCPInterface")[0],                # no entry leg at all
    "\n".join(l for l in RNSTATUS_F_161.splitlines() if "Filtered" not in l),  # run without -F
])
def test_leg_stats_missing_line_is_unknown_never_zero(tmp_path, raw):
    r = _stdin("leg-stats", raw, tmp_path)
    assert r.returncode == 0, r.stderr
    assert "UNKNOWN" in r.stdout, r.stdout
    assert "filtered: 0" not in r.stdout, r.stdout
