"""Tests for the fleet-naming drift audit (scripts/fleet_naming_audit.py,
Arc 2 Phase 0, 2026-07-11).

Hermetic: injected resolver + injected keyscan + fixture operator-config
tree. Honesty pins: reservation coverage reads UNKNOWN (this box cannot
observe the DHCP server), an unreachable host under --verify-identity is
identity UNKNOWN (never a mismatch finding), and only real drift counts
toward the exit code (a pre-namespace baseline of ip_fallback/bare rows is
NOT a failure).
"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

_SCRIPT = (Path(__file__).resolve().parent.parent
           / "scripts" / "fleet_naming_audit.py")
_spec = importlib.util.spec_from_file_location("fleet_naming_audit",
                                               str(_SCRIPT))
audit = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(audit)

from utils.fleet_naming import FleetHost, Registry  # noqa: E402


def _resolver(table):
    return lambda name: table.get(name)


def _reg(**hosts):
    return Registry(domain="example.internal",
                    hosts={a: h for a, h in hosts.items()})


class TestAuditHost:
    def test_healthy_dns_row_no_findings(self):
        reg = _reg(box1=FleetHost("box1", ip_fallback="192.0.2.10"))
        row = audit.audit_host(
            "box1", reg,
            resolver=_resolver({"box1.example.internal": "192.0.2.10"}))
        assert row["method"] == "dns" and row["findings"] == []

    def test_unresolved_is_a_finding(self):
        row = audit.audit_host("ghost", _reg(), resolver=_resolver({}))
        assert row["findings"] == ["unresolved"]

    def test_fallback_stale_when_dns_moved_on(self):
        reg = _reg(box1=FleetHost("box1", ip_fallback="192.0.2.10"))
        row = audit.audit_host(
            "box1", reg,
            resolver=_resolver({"box1.example.internal": "192.0.2.77"}))
        assert any(f.startswith("fallback_stale") for f in row["findings"])

    def test_baseline_ip_fallback_is_not_a_finding(self):
        """Pre-namespace: name doesn't resolve, fallback carries the day —
        the METHOD is reported but it is not drift."""
        reg = _reg(box1=FleetHost("box1", ip_fallback="192.0.2.10"))
        row = audit.audit_host("box1", reg, resolver=_resolver({}))
        assert row["method"] == "ip_fallback" and row["findings"] == []


class TestIdentity:
    REG = staticmethod(lambda: _reg(box1=FleetHost(
        "box1", ip_fallback="192.0.2.10", expect_hostkey="SHA256:good")))

    def test_match_is_ok(self):
        row = audit.audit_host(
            "box1", self.REG(), resolver=_resolver({}),
            verify_identity=True,
            keyscan=lambda t: ("SHA256:good", ""))
        assert row["identity"] == "OK" and row["findings"] == []

    def test_mismatch_is_the_smoking_gun_finding(self):
        row = audit.audit_host(
            "box1", self.REG(), resolver=_resolver({}),
            verify_identity=True,
            keyscan=lambda t: ("SHA256:EVIL", ""))
        assert row["identity"].startswith("MISMATCH")
        assert "identity_mismatch" in row["findings"]

    def test_unreachable_is_unknown_never_a_mismatch(self):
        row = audit.audit_host(
            "box1", self.REG(), resolver=_resolver({}),
            verify_identity=True,
            keyscan=lambda t: (None, "keyscan empty (rc=1)"))
        assert row["identity"].startswith("UNKNOWN")
        assert "identity_mismatch" not in row["findings"]

    def test_undeclared_hostkey_is_labeled_not_failed(self):
        reg = _reg(box1=FleetHost("box1", ip_fallback="192.0.2.10"))
        row = audit.audit_host("box1", reg, resolver=_resolver({}),
                               verify_identity=True,
                               keyscan=lambda t: ("SHA256:x", ""))
        assert row["identity"] == "UNDECLARED"
        assert row["findings"] == []


class TestConfigSweep:
    def _home(self, tmp_path):
        (tmp_path / ".ssh").mkdir()
        (tmp_path / ".ssh" / "config").write_text(
            "Host box1\n"
            "    HostName 192.0.2.10\n"
            "Host other\n"
            "    HostName unrelated.example.com\n"
            "# comment mentioning 192.0.2.10 outside a HostName line\n")
        cfg = tmp_path / ".config" / "meshforge"
        cfg.mkdir(parents=True)
        (cfg / "map_settings.json").write_text(json.dumps({
            "federation_peers": ["192.0.2.10", "203.0.113.9"],
            "aredn_node_ips": ["198.51.100.3"],
            "unrelated_setting": "192.0.2.10",
        }))
        (cfg / "fleet.json").write_text(json.dumps({
            "peers": {"box1": {"ip": "192.0.2.10"}}}))
        return tmp_path

    def test_flags_only_ips_with_names_available(self, tmp_path):
        home = self._home(tmp_path)
        reg = _reg(box1=FleetHost("box1", ip_fallback="192.0.2.10"))
        findings = audit.scan_operator_configs(reg, home=home)
        assert any(".ssh/config" in f and "-> box1" in f for f in findings)
        assert any("map_settings.json" in f for f in findings)
        assert any("fleet.json" in f for f in findings)
        # 203.0.113.9 / 198.51.100.3 have no registry name → not flagged
        assert not any("203.0.113.9" in f for f in findings)
        assert not any("198.51.100.3" in f for f in findings)

    def test_ssh_scan_is_hostname_lines_only(self, tmp_path):
        home = self._home(tmp_path)
        reg = _reg(box1=FleetHost("box1", ip_fallback="192.0.2.10"))
        findings = audit.scan_operator_configs(reg, home=home)
        ssh_hits = [f for f in findings if ".ssh/config" in f]
        assert len(ssh_hits) == 1     # the comment line is not flagged

    def test_map_settings_scan_is_keyed_not_whole_file(self, tmp_path):
        """Only federation_peers/aredn_node_ips are naming surfaces — an
        IP in an unrelated setting must not be flagged."""
        home = self._home(tmp_path)
        reg = _reg(box1=FleetHost("box1", ip_fallback="192.0.2.10"))
        findings = audit.scan_operator_configs(reg, home=home)
        ms_hits = [f for f in findings if "map_settings.json" in f]
        assert len(ms_hits) == 1

    def test_no_registry_scans_nothing(self, tmp_path):
        assert audit.scan_operator_configs(None,
                                           home=self._home(tmp_path)) == []


class TestRunAudit:
    def test_exit_count_and_unknown_reservations(self, tmp_path):
        reg = _reg(box1=FleetHost("box1", ip_fallback="192.0.2.10"))
        doc = audit.run_audit(["box1", "ghost"], reg, [],
                              resolver=_resolver({}), home=tmp_path)
        assert doc["reservation_coverage"].startswith("UNKNOWN")
        assert doc["finding_count"] == 1          # ghost unresolved only
        assert doc["registry_ok"] is True

    def test_broken_registry_is_carried_not_hidden(self, tmp_path):
        doc = audit.run_audit(["box1"], None, ["registry unreadable: x"],
                              resolver=_resolver({"box1": "192.0.2.5"}),
                              home=tmp_path)
        assert doc["registry_ok"] is False
        assert doc["registry_errors"]


class TestEmitters:
    REG = staticmethod(lambda: _reg(
        box1=FleetHost("box1", ip_fallback="192.0.2.10",
                       mac="00:00:5e:00:53:01"),
        box2=FleetHost("box2", ip_fallback="198.51.100.20"),
        box3=FleetHost("box3")))

    def test_dnsmasq_shape(self):
        out = audit.emit_dnsmasq(self.REG())
        assert "domain=example.internal" in out
        assert "local=/example.internal/" in out
        assert "dhcp-host=00:00:5e:00:53:01,box1,192.0.2.10" in out
        assert "address=/box2.example.internal/198.51.100.20" in out
        assert "box3" in out and "dynamic only" in out

    def test_mikrotik_shape(self):
        out = audit.emit_mikrotik(self.REG())
        assert ("/ip dns static add name=box1.example.internal "
                "address=192.0.2.10") in out
        assert ("/ip dhcp-server lease add mac-address=00:00:5e:00:53:01 "
                "address=192.0.2.10") in out

    def test_uci_shape(self):
        out = audit.emit_uci(self.REG())
        assert "uci set dhcp.@dnsmasq[0].domain='example.internal'" in out
        assert "uci set dhcp.@host[-1].mac='00:00:5e:00:53:01'" in out
        assert "uci commit dhcp" in out
