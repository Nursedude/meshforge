"""Tests for scripts/wait_for_ipv6_ll.sh — the rnsd AutoInterface boot gate.

RNS AutoInterface binds IPv6 link-local multicast. Started before the kernel
finishes Duplicate Address Detection, the bind fails [Errno 99] EADDRNOTAVAIL
and rnsd exits 255/EXCEPTION. Measured on moc4 2026-09-10: 1 failure on every
WARM reboot, 0 on a cold power-on (a cold boot is slow enough that DAD
completes first). `network-online.target` does not cover it — that target can
be reached while an address is still `tentative`.

Every test stubs `ip` on PATH, so nothing here depends on the host's real
network state: a test whose verdict depends on un-pinned machine state pins
nothing.
"""
from __future__ import annotations

import subprocess
import time
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent / "scripts" / "wait_for_ipv6_ll.sh"

READY = ("2: eth0    inet6 fe80::617d:8e0:7cf:2b7c/64 scope link noprefixroute "
         "\\       valid_lft forever")
TENTATIVE = ("2: eth0    inet6 fe80::617d:8e0:7cf:2b7c/64 scope link tentative "
             "noprefixroute \\       valid_lft forever")
LO_ONLY = "1: lo    inet6 fe80::1/128 scope link \\       valid_lft forever"


def run(tmp_path, ip_output, budget=2):
    """Run the gate with a stubbed `ip`. Returns (rc, stderr, elapsed)."""
    stub = tmp_path / "ip"
    stub.write_text("#!/bin/sh\ncat <<'OUT'\n" + ip_output + "\nOUT\n", encoding="utf-8")
    stub.chmod(0o755)
    env = {"PATH": f"{tmp_path}:/usr/bin:/bin", "HOME": str(tmp_path)}
    t0 = time.monotonic()
    p = subprocess.run(["sh", str(SCRIPT), str(budget)], capture_output=True,
                       text=True, timeout=60, env=env)
    return p.returncode, p.stderr, time.monotonic() - t0


class TestGate:
    def test_a_ready_link_local_returns_immediately(self, tmp_path):
        rc, err, elapsed = run(tmp_path, READY, budget=10)
        assert rc == 0
        assert elapsed < 2          # must not burn the budget when already ready
        assert "TIMEOUT" not in err

    def test_a_tentative_address_does_not_satisfy_the_gate(self, tmp_path):
        """THE case this exists for. `tentative` means DAD is still running and
        a bind will fail — accepting it would make the gate decorative."""
        rc, err, elapsed = run(tmp_path, TENTATIVE, budget=2)
        assert rc == 0              # fail-open
        assert elapsed >= 2         # it actually waited
        assert "TIMEOUT" in err

    def test_loopback_alone_does_not_satisfy_the_gate(self, tmp_path):
        """lo always has a link-local; accepting it would pass instantly on
        every box and prove nothing."""
        rc, err, elapsed = run(tmp_path, LO_ONLY, budget=2)
        assert rc == 0
        assert elapsed >= 2
        assert "TIMEOUT" in err

    def test_no_addresses_at_all_fails_open_with_a_witness(self, tmp_path):
        rc, err, _ = run(tmp_path, "", budget=1)
        assert rc == 0
        assert "TIMEOUT" in err
        assert "WITNESS" in err     # a swallow that leaves no artifact never happened

    def test_it_never_exits_nonzero(self, tmp_path):
        """Fail-open is the contract: rnsd serves TCP/RNode/Meshtastic too, so a
        box with no IPv6 must still start it. A non-zero exit here would be a
        REGRESSION against a fault that already self-heals via Restart=."""
        for payload in (READY, TENTATIVE, LO_ONLY, ""):
            rc, _, _ = run(tmp_path, payload, budget=1)
            assert rc == 0, f"non-zero exit for payload {payload!r}"

    def test_the_witness_names_the_real_alternative_cause(self, tmp_path):
        """A timeout means either the race or a genuinely dead interface. The
        witness must say so, or a reader chases the wrong one."""
        _, err, _ = run(tmp_path, "", budget=1)
        assert "the problem is the network, not the race" in err


class TestDropIn:
    CONF = (Path(__file__).parent.parent / "templates" / "systemd"
            / "rnsd.service.d" / "10-wait-for-ipv6-ll.conf")

    def test_the_drop_in_ships(self):
        assert self.CONF.exists()

    def test_no_percent_signs_in_execstartpre(self):
        """A percent is a systemd unit specifier, expanded before the shell sees
        the line. That rule cost the federator an outage on 2026-05-30."""
        for line in self.CONF.read_text().splitlines():
            if line.startswith("ExecStartPre="):
                assert "%" not in line, line

    def test_start_timeout_exceeds_the_gate_budget(self):
        """Otherwise systemd kills the gate mid-wait and the fail-open exit 0
        never fires — the failure mode the map drop-in hit in 2026-05-30."""
        text = self.CONF.read_text()
        budget = None
        timeout_s = None
        for line in text.splitlines():
            if line.startswith("ExecStartPre="):
                budget = int(line.strip().split()[-1])
            if line.startswith("TimeoutStartSec="):
                timeout_s = int(line.split("=", 1)[1].strip())
        assert budget is not None and timeout_s is not None
        assert timeout_s > budget, f"TimeoutStartSec={timeout_s} must exceed gate budget {budget}"

    def test_it_points_at_the_shipped_script(self):
        assert "wait_for_ipv6_ll.sh" in self.CONF.read_text()


class TestDropInIsDeployed:
    """The drop-in was hand-deployed from 2026-09-10 and one box (the manager)
    never got it; it crashed on its 2026-09-26 reboot. Both installers now
    carry it. These run the REAL update.sh block, with its /etc paths pointed
    at a sandbox, rather than grepping for the file name alone."""

    REPO = Path(__file__).parent.parent
    TMPL = REPO / "templates" / "systemd" / "rnsd.service.d" / "10-wait-for-ipv6-ll.conf"
    START = "# Deploy the rnsd IPv6-DAD wait drop-in."

    def _block(self) -> str:
        text = (self.REPO / "scripts" / "update.sh").read_text()
        i = text.index(self.START)
        j = text.index("\nfi\n", i) + len("\nfi\n")
        return text[i:j]

    def _run(self, etc: Path, install_dir: Path) -> subprocess.CompletedProcess:
        block = self._block().replace("/etc/systemd/system", str(etc))
        script = (f'GREEN=""; NC=""; SVC_UPDATED=false; INSTALL_DIR="{install_dir}"\n'
                  f'{block}\necho "SVC_UPDATED=$SVC_UPDATED"\n')
        return subprocess.run(["bash", "-c", script], capture_output=True,
                              text=True, timeout=30)

    def test_installs_the_template_byte_for_byte_then_is_idempotent(self, tmp_path):
        etc = tmp_path / "etc"
        etc.mkdir()
        (etc / "rnsd.service").write_text("[Service]\n")
        first = self._run(etc, self.REPO)
        dst = etc / "rnsd.service.d" / "10-wait-for-ipv6-ll.conf"
        assert first.returncode == 0, first.stderr
        assert dst.read_bytes() == self.TMPL.read_bytes()
        assert "SVC_UPDATED=true" in first.stdout
        second = self._run(etc, self.REPO)
        assert "already current" in second.stdout
        assert "SVC_UPDATED=false" in second.stdout

    def test_follows_a_checkout_outside_opt_meshforge(self, tmp_path):
        etc = tmp_path / "etc"
        etc.mkdir()
        (etc / "rnsd.service").write_text("[Service]\n")
        alt = tmp_path / "alt"
        (alt / "templates" / "systemd" / "rnsd.service.d").mkdir(parents=True)
        (alt / "templates" / "systemd" / "rnsd.service.d" / self.TMPL.name).write_bytes(
            self.TMPL.read_bytes())
        self._run(etc, alt)
        body = (etc / "rnsd.service.d" / self.TMPL.name).read_text()
        assert f"ExecStartPre={alt}/scripts/wait_for_ipv6_ll.sh" in body
        assert "/opt/meshforge/" not in body.split("ExecStartPre=")[1].splitlines()[0]

    def test_a_box_without_system_rnsd_gets_nothing(self, tmp_path):
        etc = tmp_path / "etc"
        etc.mkdir()
        self._run(etc, self.REPO)
        assert not (etc / "rnsd.service.d").exists()

    def test_fresh_install_path_carries_it_too(self):
        text = (self.REPO / "scripts" / "install_noc.sh").read_text()
        assert "rnsd.service.d/10-wait-for-ipv6-ll.conf" in text
        # through the dry-run-aware writer, never a raw redirection
        assert "| mf_write_stdin /etc/systemd/system/rnsd.service.d/10-wait-for-ipv6-ll.conf" in text
