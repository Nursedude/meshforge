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


def _dns(ip):
    """resolve_a stub: authoritative A answer."""
    return lambda f, timeout=3.0: (gfh.ANSWERED, ip)


def _negative(f, timeout=3.0):
    """resolve_a stub: server answered, name/record not in the zone."""
    return (gfh.NEGATIVE, None)


def _unobservable(f, timeout=3.0):
    """resolve_a stub: no server answered at all (blind, not negative)."""
    return (gfh.UNOBSERVABLE, None)


class TestSeedsFromLiveDnsNotTheSnapshot:

    def test_dns_answer_wins_over_ip_fallback(self, registry, monkeypatch):
        """The registry ip_fallback is a snapshot; DNS is the authority.
        Baking the snapshot in would shadow the truth with stale data."""
        monkeypatch.setattr(gfh, "resolve_a", _dns("10.0.0.9"))
        entries, warnings = gfh.build_entries(registry)
        assert all(ip == "10.0.0.9" for ip, _f, _s in entries)
        assert all(src == "dns" for _i, _f, src in entries)
        assert any("fallback_stale" in w for w in warnings), (
            "a DNS/registry mismatch must be surfaced, not silently absorbed")

    def test_falls_back_only_when_dns_answers_negative(self, registry, monkeypatch):
        monkeypatch.setattr(gfh, "resolve_a", _negative)
        entries, warnings = gfh.build_entries(registry)
        assert {src for _i, _f, src in entries} == {"ip_fallback"}
        assert len(warnings) == 2

    def test_unresolvable_with_no_fallback_is_omitted_not_guessed(self, monkeypatch):
        reg = _Registry({"ghost": _Host("ghost", None)})
        monkeypatch.setattr(gfh, "resolve_a", _negative)
        entries, warnings = gfh.build_entries(reg)
        assert entries == []
        assert any("omitted" in w for w in warnings)

    def _name_lines(self, block):
        for line in block.splitlines():
            line = line.split("#", 1)[0].strip()
            if line:
                yield line

    def test_each_entry_carries_the_fqdn_then_the_bare_alias(
            self, registry, monkeypatch):
        """The block exists so fleet names resolve with the router's DNS down.
        FQDN-only half-delivered that: bare names still went to m1, so
        moc4/moc5/kiai stopped resolving bare while their FQDNs were fine
        (2026-08-08). Canonical FQDN first, bare alias second."""
        monkeypatch.setattr(gfh, "resolve_a", _dns("10.0.0.9"))
        entries, _w = gfh.build_entries(registry)
        block = gfh.render_block(entries, local="somethingelse")
        seen = 0
        for line in self._name_lines(block):
            names = line.split()[1:]
            assert len(names) == 2, f"want '<fqdn> <bare>': {line}"
            fqdn, bare = names
            assert fqdn.endswith(".mf.internal"), line
            assert fqdn.split(".", 1)[0] == bare, line
            seen += 1
        assert seen, "no entries rendered — the assertions above proved nothing"

    def test_check_compares_every_name_not_just_the_fqdn(self, registry,
                                                         monkeypatch):
        """--check/--apply compare parse_block() maps. If that map keys only on
        the FQDN, adding bare aliases is INVISIBLE to both: --check reports
        "in sync", --apply says "already current" and declines to write, and
        the change reaches no box while reporting success. Drilled 2026-08-08
        — this is exactly what happened."""
        monkeypatch.setattr(gfh, "resolve_a", _dns("10.0.0.9"))
        entries, _w = gfh.build_entries(registry)
        fqdn_only = "\n".join(
            f"{ip}  {fqdn}" for ip, fqdn, _s in entries) + "\n"
        with_aliases = gfh.render_block(entries, local="somethingelse")
        assert gfh.parse_block(fqdn_only) != gfh.parse_block(with_aliases), (
            "parse_block cannot distinguish a block WITH bare aliases from one "
            "without — so no deploy path could ever apply them")

    def test_local_hostname_never_gets_a_bare_alias(self, registry, monkeypatch):
        """The ONE name this block must not claim. `127.0.1.1 <host>` is
        written by another owner (cloud-init), and getaddrinfo may aggregate
        matches across lines rather than stop at the first — so a second entry
        for our own hostname could contend with loopback self-resolution.
        Relying on file order would be a guess; omitting the name cannot be
        wrong. (This is the surviving half of the original FQDN-only
        rationale; the `search lan` half was measured inert.)"""
        monkeypatch.setattr(gfh, "resolve_a", _dns("10.0.0.9"))
        entries, _w = gfh.build_entries(registry)
        alias = sorted(registry.hosts)[0]
        block = gfh.render_block(entries, local=alias.upper())  # case-insensitive
        for line in self._name_lines(block):
            names = line.split()[1:]
            if names[0] == f"{alias}.mf.internal":
                assert names == [f"{alias}.mf.internal"], (
                    f"emitted a bare alias for THIS box's hostname: {line}")
                break
        else:
            raise AssertionError(f"{alias} not rendered — test proved nothing")


class TestBlindnessIsNeverAnEmptyFleet:

    def test_all_dns_failures_refuse_to_write(self, registry, monkeypatch, tmp_path):
        hosts = tmp_path / "hosts"
        hosts.write_text("127.0.0.1 localhost\n")
        monkeypatch.setattr(gfh, "load_registry", lambda: (registry, []))
        monkeypatch.setattr(gfh, "resolve_a", _negative)
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
        monkeypatch.setattr(gfh, "resolve_a", _dns(ip))
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
        monkeypatch.setattr(gfh, "resolve_a", _dns("10.0.0.9"))
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
        monkeypatch.setattr(gfh, "resolve_a", _dns("10.0.0.9"))
        monkeypatch.setattr(sys, "argv",
                            ["gen", "--apply", "--hosts-file", str(hosts)])
        gfh.main()
        # DHCP moved a box
        monkeypatch.setattr(gfh, "resolve_a", _dns("10.0.0.55"))
        monkeypatch.setattr(sys, "argv",
                            ["gen", "--check", "--hosts-file", str(hosts)])
        assert gfh.main() == gfh.EXIT_DRIFT


class TestResolutionNeverReadsEtcHosts:
    """THE regression this file exists for.

    Live drill 2026-07-25: --check used getaddrinfo, which consults nss
    `files` (/etc/hosts) BEFORE dns — and systemd-resolved answers from
    /etc/hosts too. So the check compared the generated block against
    ITSELF. A deliberately corrupted entry reported "in sync" AND survived
    --apply, because the corrupted file WAS the script's notion of truth.
    A self-confirming detector is worse than none: it reports health it
    cannot observe.
    """

    def test_resolve_a_does_not_use_getaddrinfo(self, monkeypatch):
        """getaddrinfo would read /etc/hosts — the file we are auditing."""
        def boom(*a, **kw):
            raise AssertionError(
                "resolve_a() called getaddrinfo — that reads /etc/hosts and "
                "makes the drift check compare the block against itself")

        monkeypatch.setattr(socket, "getaddrinfo", boom)
        monkeypatch.setattr(gfh, "upstream_servers", lambda: ["192.0.2.53"])
        monkeypatch.setattr(gfh, "_dns_query_a",
                            lambda s, f, t: (True, "192.0.2.99"))
        assert gfh.resolve_a("moc.mf.internal") == (gfh.ANSWERED, "192.0.2.99")

    def test_queries_the_upstream_server_directly(self, monkeypatch):
        seen = {}

        def fake_query(server, fqdn, timeout):
            seen["server"] = server
            seen["fqdn"] = fqdn
            return True, "192.0.2.7"

        monkeypatch.setattr(gfh, "upstream_servers", lambda: ["192.0.2.53"])
        monkeypatch.setattr(gfh, "_dns_query_a", fake_query)
        assert gfh.resolve_a("moc.mf.internal") == (gfh.ANSWERED, "192.0.2.7")
        assert seen == {"server": "192.0.2.53", "fqdn": "moc.mf.internal"}

    def test_unanswered_server_falls_through_to_next(self, monkeypatch):
        calls = []

        def fake_query(server, fqdn, timeout):
            calls.append(server)
            if server == "192.0.2.1":
                return False, None          # no response at all
            return True, "192.0.2.8"

        monkeypatch.setattr(gfh, "upstream_servers",
                            lambda: ["192.0.2.1", "192.0.2.2"])
        monkeypatch.setattr(gfh, "_dns_query_a", fake_query)
        assert gfh.resolve_a("moc.mf.internal") == (gfh.ANSWERED, "192.0.2.8")
        assert calls == ["192.0.2.1", "192.0.2.2"]

    def test_no_reachable_server_is_unobservable_not_a_guess(self, monkeypatch):
        monkeypatch.setattr(gfh, "upstream_servers", lambda: ["192.0.2.1"])
        monkeypatch.setattr(gfh, "_dns_query_a", lambda s, f, t: (False, None))
        assert gfh.resolve_a("moc.mf.internal") == (gfh.UNOBSERVABLE, None)

    def test_answered_with_no_a_record_is_negative(self, monkeypatch):
        """NODATA is a real answer meaning 'no A here' — distinct from
        'server never replied': negative may fall to ip_fallback, blind holds."""
        monkeypatch.setattr(gfh, "upstream_servers", lambda: ["192.0.2.1"])
        monkeypatch.setattr(gfh, "_dns_query_a", lambda s, f, t: (True, None))
        assert gfh.resolve_a("moc.mf.internal") == (gfh.NEGATIVE, None)

    def test_upstream_servers_read_from_the_resolved_dropin(self, monkeypatch,
                                                            tmp_path):
        """MF014: no operator addresses in the repo — discover them."""
        dropin = tmp_path / "resolved.conf.d"
        dropin.mkdir()
        (dropin / "mf-fleet-naming.conf").write_text(
            "[Resolve]\nDNS=192.0.2.53 192.0.2.54\nDomains=~mf.internal\n")
        monkeypatch.setattr(gfh, "RESOLVED_DROPIN_DIR", dropin)
        assert gfh.upstream_servers() == ["192.0.2.53", "192.0.2.54"]

    def test_upstream_servers_dedupes(self, monkeypatch, tmp_path):
        dropin = tmp_path / "resolved.conf.d"
        dropin.mkdir()
        (dropin / "a.conf").write_text("[Resolve]\nDNS=192.0.2.53\n")
        (dropin / "b.conf").write_text("[Resolve]\nDNS=192.0.2.53\n")
        monkeypatch.setattr(gfh, "RESOLVED_DROPIN_DIR", dropin)
        assert gfh.upstream_servers() == ["192.0.2.53"]

    def test_upstream_servers_fall_back_to_classic_resolv_conf(self, monkeypatch,
                                                                tmp_path):
        """A box WITHOUT systemd-resolved must still find the zone server.

        kiai + moc3 (2026-07-26) run a classic /etc/resolv.conf. Before this,
        upstream_servers() returned [] there, every name fell through to
        ip_fallback, and the run refused — so the tool could never be
        deployed on those boxes at all.
        """
        dropin = tmp_path / "resolved.conf.d"          # absent on purpose
        resolv = tmp_path / "resolv.conf"
        resolv.write_text("search lan\nnameserver 192.0.2.53\n"
                          "nameserver 192.0.2.54\n")
        monkeypatch.setattr(gfh, "RESOLVED_DROPIN_DIR", dropin)
        monkeypatch.setattr(gfh, "RESOLV_CONF", resolv)
        monkeypatch.setattr(gfh.subprocess, "run",
                            lambda *a, **k: (_ for _ in ()).throw(OSError()))
        assert gfh.upstream_servers() == ["192.0.2.53", "192.0.2.54"]

    def test_loopback_stub_is_never_an_upstream(self, monkeypatch, tmp_path):
        """127.0.0.53 answers from /etc/hosts — using it would be self-confirming.

        This is the 2026-07-25 drill bug in a new skin: the resolv.conf
        fallback must not smuggle the local stub back in as 'what DNS says'.
        """
        dropin = tmp_path / "resolved.conf.d"
        resolv = tmp_path / "resolv.conf"
        resolv.write_text("nameserver 127.0.0.53\nnameserver 192.0.2.53\n")
        monkeypatch.setattr(gfh, "RESOLVED_DROPIN_DIR", dropin)
        monkeypatch.setattr(gfh, "RESOLV_CONF", resolv)
        monkeypatch.setattr(gfh.subprocess, "run",
                            lambda *a, **k: (_ for _ in ()).throw(OSError()))
        assert gfh.upstream_servers() == ["192.0.2.53"]

    def test_stub_only_resolv_conf_is_blindness_not_a_server(self, monkeypatch,
                                                              tmp_path):
        """Stub-only => no upstream at all => UNKNOWN, never a false 'in sync'."""
        dropin = tmp_path / "resolved.conf.d"
        resolv = tmp_path / "resolv.conf"
        resolv.write_text("nameserver 127.0.0.53\n")
        monkeypatch.setattr(gfh, "RESOLVED_DROPIN_DIR", dropin)
        monkeypatch.setattr(gfh, "RESOLV_CONF", resolv)
        monkeypatch.setattr(gfh.subprocess, "run",
                            lambda *a, **k: (_ for _ in ()).throw(OSError()))
        assert gfh.upstream_servers() == []

    def test_truncated_reply_is_no_answer_not_a_crash(self, monkeypatch):
        """A short or garbage datagram must degrade, never raise."""
        class FakeSock:
            def settimeout(self, _t): pass
            def sendto(self, _d, _a): pass
            def recvfrom(self, _n): return (b"\x00\x01\x02", ("192.0.2.1", 53))
            def close(self): pass

        monkeypatch.setattr(socket, "socket", lambda *a, **kw: FakeSock())
        assert gfh._dns_query_a("192.0.2.1", "moc.mf.internal", 1.0) == (False, None)

    def test_timeout_is_unanswered_not_nxdomain(self, monkeypatch):
        class FakeSock:
            def settimeout(self, _t): pass
            def sendto(self, _d, _a): pass
            def recvfrom(self, _n): raise socket.timeout("timed out")
            def close(self): pass

        monkeypatch.setattr(socket, "socket", lambda *a, **kw: FakeSock())
        answered, ip = gfh._dns_query_a("192.0.2.1", "moc.mf.internal", 1.0)
        assert answered is False and ip is None

# ── 2026-07-26 review D2: DNS answers must be validated, not just parsed ─────

class TestDnsAnswerValidation:
    """SERVFAIL/REFUSED parsed as "authoritatively no A record", any QID was
    accepted (and the QID was a fixed repo-public constant), and QR was never
    checked — so one spoofed/stray datagram could poison an /etc/hosts write."""

    @staticmethod
    def _reply(query, rcode=0, qid=None, qr=True, answers=b"", ancount=0):
        import struct as _s
        q_qid = _s.unpack(">H", query[:2])[0]
        qid = q_qid if qid is None else qid
        flags = (0x8000 if qr else 0) | (rcode & 0xF)
        return (_s.pack(">HHHHHH", qid, flags, 1, ancount, 0, 0)
                + query[12:] + answers)

    def _query_with(self, monkeypatch, respond):
        sent = {}

        class FakeSock:
            def settimeout(self, _t): pass
            def sendto(self, d, _a): sent["query"] = d
            def recvfrom(self, _n): return respond(sent["query"]), ("192.0.2.1", 53)
            def close(self): pass

        monkeypatch.setattr(socket, "socket", lambda *a, **kw: FakeSock())
        return gfh._dns_query_a("192.0.2.1", "moc.mf.internal", 1.0)

    def test_servfail_is_not_an_answer(self, monkeypatch):
        """SERVFAIL must fall through to the next server / UNKNOWN — it used to
        parse as 'answered, no A record' and license an ip_fallback write."""
        got = self._query_with(monkeypatch, lambda q: self._reply(q, rcode=2))
        assert got == (False, None)

    def test_refused_is_not_an_answer(self, monkeypatch):
        got = self._query_with(monkeypatch, lambda q: self._reply(q, rcode=5))
        assert got == (False, None)

    def test_qid_mismatch_is_rejected(self, monkeypatch):
        """An off-path spoof (or stray datagram) carries the wrong QID."""
        got = self._query_with(
            monkeypatch,
            lambda q: self._reply(
                q, qid=(int.from_bytes(q[:2], "big") ^ 0xFFFF),
                ancount=1,
                answers=b"\xc0\x0c" + bytes([0, 1, 0, 1]) + b"\x00\x00\x00\x3c"
                        + b"\x00\x04" + bytes([203, 0, 113, 66])))
        assert got == (False, None)

    def test_response_without_qr_bit_is_rejected(self, monkeypatch):
        got = self._query_with(monkeypatch, lambda q: self._reply(q, qr=False))
        assert got == (False, None)

    def test_qid_is_randomized_per_query(self, monkeypatch):
        """The fixed repo-public 0x7A5F QID made off-path poisoning feasible."""
        qids = []

        def respond(q):
            qids.append(int.from_bytes(q[:2], "big"))
            return self._reply(q)

        for _ in range(16):
            self._query_with(monkeypatch, respond)
        assert len(set(qids)) > 1, "QID constant across queries"
        assert qids != [0x7A5F] * len(qids)

    def test_nxdomain_is_answered_negative(self, monkeypatch):
        got = self._query_with(monkeypatch, lambda q: self._reply(q, rcode=3))
        assert got == (True, None)

    def test_label_then_pointer_answer_name_parses(self, monkeypatch):
        """Compression pointer mid-name (label, then pointer) — the old walk
        only understood a pointer at the very start of the name."""
        answers = (b"\x03moc\xc0\x10"                    # label + pointer
                   + bytes([0, 1, 0, 1]) + b"\x00\x00\x00\x3c"
                   + b"\x00\x04" + bytes([203, 0, 113, 77]))
        got = self._query_with(
            monkeypatch, lambda q: self._reply(q, ancount=1, answers=answers))
        assert got == (True, "203.0.113.77")


class TestResolveALoopSemantics:
    def test_answered_empty_from_first_server_does_not_stop_the_loop(
            self, monkeypatch):
        """07-26 review D2c: only a positive A answer may stop the loop — an
        answered-empty from server 1 must not mask the A server 2 holds."""
        def fake_query(server, fqdn, timeout):
            if server == "192.0.2.1":
                return True, None          # answered, no A
            return True, "192.0.2.9"

        monkeypatch.setattr(gfh, "upstream_servers",
                            lambda: ["192.0.2.1", "192.0.2.2"])
        monkeypatch.setattr(gfh, "_dns_query_a", fake_query)
        assert gfh.resolve_a("moc.mf.internal") == (gfh.ANSWERED, "192.0.2.9")

    def test_all_servers_empty_is_negative(self, monkeypatch):
        monkeypatch.setattr(gfh, "upstream_servers",
                            lambda: ["192.0.2.1", "192.0.2.2"])
        monkeypatch.setattr(gfh, "_dns_query_a", lambda s, f, t: (True, None))
        assert gfh.resolve_a("moc.mf.internal") == (gfh.NEGATIVE, None)


# ── 2026-07-26 review D3: per-name unobservable HOLDS, never snapshots ───────

class TestHoldOnPerNameUnobservable:
    """One lost UDP datagram used to substitute the registry's ip_fallback
    snapshot for a GOOD current hosts entry — and --apply then overwrote it:
    the exact moc5-reshuffle class the module header names as its reason to
    exist, self-inflicted."""

    def test_unobservable_with_current_entry_holds_it(self, registry, monkeypatch):
        monkeypatch.setattr(gfh, "resolve_a", _unobservable)
        current = {"moc.mf.internal": "10.0.0.42", "moc1.mf.internal": "10.0.0.43"}
        entries, warnings = gfh.build_entries(registry, current)
        by_fqdn = {f: (ip, src) for ip, f, src in entries}
        assert by_fqdn["moc.mf.internal"] == ("10.0.0.42", "held")
        assert by_fqdn["moc1.mf.internal"] == ("10.0.0.43", "held")
        assert any("HELD" in w for w in warnings), "hold needs a printed witness"

    def test_unobservable_without_current_entry_falls_to_ip_fallback(
            self, registry, monkeypatch):
        monkeypatch.setattr(gfh, "resolve_a", _unobservable)
        entries, _w = gfh.build_entries(registry, current={})
        assert {src for _i, _f, src in entries} == {"ip_fallback"}

    def test_negative_falls_to_ip_fallback_even_with_current_entry(
            self, registry, monkeypatch):
        """NXDOMAIN means the zone dropped the name — a registry decision, not
        an observation gap. The snapshot may take over."""
        monkeypatch.setattr(gfh, "resolve_a", _negative)
        current = {"moc.mf.internal": "10.0.0.42"}
        entries, _w = gfh.build_entries(registry, current)
        by_fqdn = {f: (ip, src) for ip, f, src in entries}
        assert by_fqdn["moc.mf.internal"] == ("192.0.2.38", "ip_fallback")

    def test_apply_holds_good_entry_on_per_name_timeout(self, registry,
                                                        monkeypatch, tmp_path):
        """End-to-end: apply a good block, then lose ONE name's datagram —
        --apply must keep the good current entry, not regress it to the
        registry snapshot."""
        hosts = tmp_path / "hosts"
        hosts.write_text("127.0.0.1 localhost\n")
        monkeypatch.setattr(gfh, "load_registry", lambda: (registry, []))
        monkeypatch.setattr(gfh, "resolve_a", _dns("10.0.0.9"))
        monkeypatch.setattr(sys, "argv",
                            ["gen", "--apply", "--hosts-file", str(hosts)])
        assert gfh.main() == gfh.EXIT_OK

        def flaky(fqdn, timeout=3.0):
            if fqdn.startswith("moc1."):
                return (gfh.UNOBSERVABLE, None)     # one lost datagram
            return (gfh.ANSWERED, "10.0.0.9")

        monkeypatch.setattr(gfh, "resolve_a", flaky)
        assert gfh.main() == gfh.EXIT_OK
        have = gfh.parse_block(gfh.current_block(hosts))
        assert have["moc1.mf.internal"] == "10.0.0.9", (
            "per-name unobservable must HOLD the current entry, not overwrite "
            "it with the registry snapshot")

    def test_check_does_not_report_drift_on_one_lost_datagram(
            self, registry, monkeypatch, tmp_path):
        hosts = tmp_path / "hosts"
        hosts.write_text("127.0.0.1 localhost\n")
        monkeypatch.setattr(gfh, "load_registry", lambda: (registry, []))
        monkeypatch.setattr(gfh, "resolve_a", _dns("10.0.0.9"))
        monkeypatch.setattr(sys, "argv",
                            ["gen", "--apply", "--hosts-file", str(hosts)])
        gfh.main()

        def flaky(fqdn, timeout=3.0):
            if fqdn.startswith("moc1."):
                return (gfh.UNOBSERVABLE, None)
            return (gfh.ANSWERED, "10.0.0.9")

        monkeypatch.setattr(gfh, "resolve_a", flaky)
        monkeypatch.setattr(sys, "argv",
                            ["gen", "--check", "--hosts-file", str(hosts)])
        assert gfh.main() == gfh.EXIT_OK


# ── 2026-07-26 review D2d/D2e: durable write + torn-repair preserves ─────────

class TestWriteDurabilityAndTornRepair:
    def test_write_block_fsyncs_before_replace(self, registry, monkeypatch,
                                               tmp_path):
        """/etc/hosts is load-bearing: a power loss between write and rename
        must never publish an unsynced (possibly empty) file."""
        hosts = tmp_path / "hosts"
        hosts.write_text("127.0.0.1 localhost\n")
        calls = []
        real_fsync = gfh.os.fsync
        monkeypatch.setattr(gfh.os, "fsync",
                            lambda fd: (calls.append(fd), real_fsync(fd))[1])
        monkeypatch.setattr(gfh, "load_registry", lambda: (registry, []))
        monkeypatch.setattr(gfh, "resolve_a", _dns("10.0.0.9"))
        monkeypatch.setattr(sys, "argv",
                            ["gen", "--apply", "--hosts-file", str(hosts)])
        assert gfh.main() == gfh.EXIT_OK
        assert calls, "temp file was never fsynced before os.replace"

    def test_torn_repair_preserves_operator_lines_after_the_tear(
            self, registry, monkeypatch, tmp_path):
        """The old torn-path treated EVERYTHING after BEGIN as managed, so a
        repair silently DELETED operator lines below the tear."""
        hosts = tmp_path / "hosts"
        hosts.write_text(
            "127.0.0.1 localhost\n"
            f"{gfh.BEGIN}\n"
            "# DO NOT EDIT BY HAND. Regenerate with:\n"
            "10.0.0.1     old.mf.internal\n"
            "192.0.2.200  printer.lan\n"          # operator line after the tear
            "192.0.2.201  nas.lan\n")
        monkeypatch.setattr(gfh, "load_registry", lambda: (registry, []))
        monkeypatch.setattr(gfh, "resolve_a", _dns("10.0.0.9"))
        monkeypatch.setattr(sys, "argv",
                            ["gen", "--apply", "--hosts-file", str(hosts)])
        assert gfh.main() == gfh.EXIT_OK
        text = hosts.read_text()
        assert "printer.lan" in text, "torn repair deleted an operator line"
        assert "nas.lan" in text, "torn repair deleted an operator line"
        assert "old.mf.internal" not in text
        assert text.count(gfh.BEGIN) == 1 and text.count(gfh.END) == 1
