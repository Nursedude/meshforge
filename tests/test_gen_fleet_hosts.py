"""Guards for the generated /etc/hosts fleet block.

/etc/hosts SHADOWS DNS, so this generator is held to the honest-failure-modes
checklist harder than most code: a wrong or stale block makes a healthy box
look dead, and the 86.x segment is deliberately unreserved DHCP (a reshuffle
already did exactly that to moc5 on 2026-06-24).

The behaviours pinned here are the ones whose failure is SILENT:
  * a blind observer must never be written as an empty fleet
  * the unmanaged parts of /etc/hosts must survive byte-for-byte
  * a DNS answer must win over the registry's ip_fallback snapshot
  * --check must distinguish drift (1) from unobservable (2)
"""

import importlib.util
import socket
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "gen_fleet_hosts.py"
spec = importlib.util.spec_from_file_location("gen_fleet_hosts", SCRIPT)
gfh = importlib.util.module_from_spec(spec)
sys.modules["gen_fleet_hosts"] = gfh
spec.loader.exec_module(gfh)


class _Host:
    def __init__(self, alias, ip_fallback=None):
        self.alias = alias
        self.ip_fallback = ip_fallback


class _Registry:
    def __init__(self, hosts, domain="mf.internal"):
        self.domain = domain
        self.hosts = hosts


@pytest.fixture
def registry():
    # TEST-NET-1 (RFC 5737), never operator LAN addresses — MF014.
    return _Registry({
        "moc": _Host("moc", "192.0.2.38"),
        "moc1": _Host("moc1", "192.0.2.249"),
    })


class TestSeedsFromLiveDnsNotTheSnapshot:

    def test_dns_answer_wins_over_ip_fallback(self, registry, monkeypatch):
        """The registry ip_fallback is a snapshot; DNS is the authority.
        Baking the snapshot in would shadow the truth with stale data."""
        monkeypatch.setattr(gfh, "resolve_a", lambda f, timeout=3.0: "10.0.0.9")
        entries, warnings = gfh.build_entries(registry)
        assert all(ip == "10.0.0.9" for ip, _f, _s in entries)
        assert all(src == "dns" for _i, _f, src in entries)
        assert any("fallback_stale" in w for w in warnings), (
            "a DNS/registry mismatch must be surfaced, not silently absorbed")

    def test_falls_back_only_when_dns_cannot_answer(self, registry, monkeypatch):
        monkeypatch.setattr(gfh, "resolve_a", lambda f, timeout=3.0: None)
        entries, warnings = gfh.build_entries(registry)
        assert {src for _i, _f, src in entries} == {"ip_fallback"}
        assert len(warnings) == 2

    def test_unresolvable_with_no_fallback_is_omitted_not_guessed(self, monkeypatch):
        reg = _Registry({"ghost": _Host("ghost", None)})
        monkeypatch.setattr(gfh, "resolve_a", lambda f, timeout=3.0: None)
        entries, warnings = gfh.build_entries(reg)
        assert entries == []
        assert any("omitted" in w for w in warnings)

    def test_only_fqdns_are_emitted(self, registry, monkeypatch):
        """A bare alias would shadow the box's own hostname entry and the
        `search lan` domain — FQDN only."""
        monkeypatch.setattr(gfh, "resolve_a", lambda f, timeout=3.0: "10.0.0.9")
        entries, _w = gfh.build_entries(registry)
        block = gfh.render_block(entries)
        for line in block.splitlines():
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            names = line.split()[1:]
            assert all(n.endswith(".mf.internal") for n in names), line


class TestBlindnessIsNeverAnEmptyFleet:

    def test_all_dns_failures_refuse_to_write(self, registry, monkeypatch, tmp_path):
        hosts = tmp_path / "hosts"
        hosts.write_text("127.0.0.1 localhost\n")
        monkeypatch.setattr(gfh, "load_registry", lambda: (registry, []))
        monkeypatch.setattr(gfh, "resolve_a", lambda f, timeout=3.0: None)
        monkeypatch.setattr(sys, "argv",
                            ["gen", "--apply", "--hosts-file", str(hosts)])
        assert gfh.main() == gfh.EXIT_UNKNOWN
        assert hosts.read_text() == "127.0.0.1 localhost\n", "must not touch the file"

    def test_unreadable_registry_is_unknown_not_ok(self, monkeypatch, tmp_path):
        hosts = tmp_path / "hosts"
        hosts.write_text("127.0.0.1 localhost\n")
        monkeypatch.setattr(gfh, "load_registry", lambda: (None, ["bad json"]))
        monkeypatch.setattr(sys, "argv",
                            ["gen", "--check", "--hosts-file", str(hosts)])
        assert gfh.main() == gfh.EXIT_UNKNOWN


class TestOnlyTheManagedBlockIsTouched:

    def _apply(self, registry, monkeypatch, hosts, ip="10.0.0.9"):
        monkeypatch.setattr(gfh, "load_registry", lambda: (registry, []))
        monkeypatch.setattr(gfh, "resolve_a", lambda f, timeout=3.0: ip)
        monkeypatch.setattr(sys, "argv",
                            ["gen", "--apply", "--hosts-file", str(hosts)])
        return gfh.main()

    def test_existing_content_survives(self, registry, monkeypatch, tmp_path):
        hosts = tmp_path / "hosts"
        original = ("127.0.0.1\tlocalhost\n"
                    "127.0.1.1\tVolcanoAI\n"
                    "192.168.1.5\tprinter.lan\n")
        hosts.write_text(original)
        assert self._apply(registry, monkeypatch, hosts) == gfh.EXIT_OK

        text = hosts.read_text()
        for line in original.splitlines():
            assert line in text, f"lost pre-existing line: {line!r}"
        assert gfh.BEGIN in text and gfh.END in text

    def test_reapply_is_idempotent_and_makes_one_block(self, registry,
                                                       monkeypatch, tmp_path):
        hosts = tmp_path / "hosts"
        hosts.write_text("127.0.0.1 localhost\n")
        self._apply(registry, monkeypatch, hosts)
        first = hosts.read_text()
        assert self._apply(registry, monkeypatch, hosts) == gfh.EXIT_OK
        assert hosts.read_text() == first
        assert hosts.read_text().count(gfh.BEGIN) == 1

    def test_changed_dns_rewrites_in_place_without_duplicating(
            self, registry, monkeypatch, tmp_path):
        hosts = tmp_path / "hosts"
        hosts.write_text("127.0.0.1 localhost\n")
        self._apply(registry, monkeypatch, hosts, ip="10.0.0.9")
        self._apply(registry, monkeypatch, hosts, ip="10.0.0.77")
        text = hosts.read_text()
        assert text.count(gfh.BEGIN) == 1
        assert "10.0.0.77" in text and "10.0.0.9" not in text
        assert "127.0.0.1 localhost" in text

    def test_torn_block_is_repaired_not_duplicated(self, registry,
                                                   monkeypatch, tmp_path):
        """A block whose END marker was lost must be rewritten, not appended to."""
        hosts = tmp_path / "hosts"
        hosts.write_text(f"127.0.0.1 localhost\n{gfh.BEGIN}\n10.0.0.1 old.mf.internal\n")
        self._apply(registry, monkeypatch, hosts)
        text = hosts.read_text()
        assert text.count(gfh.BEGIN) == 1
        assert text.count(gfh.END) == 1
        assert "old.mf.internal" not in text


class TestCheckModeExitCodes:

    def test_in_sync_is_zero(self, registry, monkeypatch, tmp_path):
        hosts = tmp_path / "hosts"
        hosts.write_text("127.0.0.1 localhost\n")
        monkeypatch.setattr(gfh, "load_registry", lambda: (registry, []))
        monkeypatch.setattr(gfh, "resolve_a", lambda f, timeout=3.0: "10.0.0.9")
        monkeypatch.setattr(sys, "argv",
                            ["gen", "--apply", "--hosts-file", str(hosts)])
        gfh.main()
        monkeypatch.setattr(sys, "argv",
                            ["gen", "--check", "--hosts-file", str(hosts)])
        assert gfh.main() == gfh.EXIT_OK

    def test_drift_is_one(self, registry, monkeypatch, tmp_path):
        hosts = tmp_path / "hosts"
        hosts.write_text("127.0.0.1 localhost\n")
        monkeypatch.setattr(gfh, "load_registry", lambda: (registry, []))
        monkeypatch.setattr(gfh, "resolve_a", lambda f, timeout=3.0: "10.0.0.9")
        monkeypatch.setattr(sys, "argv",
                            ["gen", "--apply", "--hosts-file", str(hosts)])
        gfh.main()
        # DHCP moved a box
        monkeypatch.setattr(gfh, "resolve_a", lambda f, timeout=3.0: "10.0.0.55")
        monkeypatch.setattr(sys, "argv",
                            ["gen", "--check", "--hosts-file", str(hosts)])
        assert gfh.main() == gfh.EXIT_DRIFT


class TestResolveUsesIpv4Only:

    def test_resolve_a_does_not_ask_for_aaaa(self, monkeypatch):
        """Asking AF_UNSPEC here would pay the exact 75ms WAN round trip
        this whole script exists to remove."""
        seen = {}

        def fake_getaddrinfo(host, port, family=0, *a, **kw):
            seen["family"] = family
            return [(socket.AF_INET, None, None, "", ("1.2.3.4", 0))]

        monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
        assert gfh.resolve_a("moc.mf.internal") == "1.2.3.4"
        assert seen["family"] == socket.AF_INET
