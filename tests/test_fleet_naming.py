"""Tests for the fleet naming resolution SSOT (src/utils/fleet_naming.py,
Arc 2 Phase 0, 2026-07-11).

The module kills the DHCP-reshuffle class (hardcoded IPs going stale) with
a names-first ladder: dns → bare → registry ip_fallback (labeled, never
silent) → honest unresolved. House style = kilo.registry honest-failure
contract: unreadable/invalid registry loads as (None, errors), never as an
empty registry that reads "no names expected, all fine"; IP-shaped alias
KEYS are refused; ip_fallback VALUES must be IPs.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from utils.fleet_naming import (  # noqa: E402
    METHODS, Registry, connect_target, load_registry, resolve, serves_map,
)


def _write(tmp_path, doc) -> str:
    p = tmp_path / "fleet_naming.json"
    p.write_text(doc if isinstance(doc, str) else json.dumps(doc))
    return str(p)


def _resolver(table):
    """Injected resolver: dict name->address; misses return None."""
    return lambda name: table.get(name)


GOOD = {
    "domain": "example.internal",
    "hosts": {
        "box1": {"ip_fallback": "192.0.2.10",
                 "mac": "00:00:5e:00:53:01",
                 "expect_hostkey": "SHA256:aaaa"},
        "box3": None,
    },
}


class TestRegistryHonesty:
    def test_good_registry_loads(self, tmp_path):
        reg, errs = load_registry(_write(tmp_path, GOOD))
        assert errs == []
        assert reg.domain == "example.internal"
        assert reg.hosts["box1"].ip_fallback == "192.0.2.10"
        assert reg.hosts["box3"].ip_fallback is None   # membership shorthand

    def test_missing_file_is_none_plus_errors_never_empty(self, tmp_path):
        reg, errs = load_registry(str(tmp_path / "absent.json"))
        assert reg is None
        assert any("not found" in e for e in errs)

    def test_hosts_null_is_error_not_zero_hosts(self, tmp_path):
        reg, errs = load_registry(_write(tmp_path, {"hosts": None}))
        assert reg is None
        assert any("must be an object" in e for e in errs)

    def test_empty_hosts_object_is_legitimately_valid(self, tmp_path):
        reg, errs = load_registry(_write(tmp_path, {"hosts": {}}))
        assert errs == [] and reg is not None and reg.hosts == {}

    def test_ip_shaped_alias_key_refused(self, tmp_path):
        doc = {"hosts": {"192.0.2.7": {"ip_fallback": "192.0.2.7"}}}
        reg, errs = load_registry(_write(tmp_path, doc))
        assert reg is None
        assert any("looks like an IP" in e for e in errs)

    def test_name_shaped_ip_fallback_refused(self, tmp_path):
        doc = {"hosts": {"box1": {"ip_fallback": "box1.lan"}}}
        reg, errs = load_registry(_write(tmp_path, doc))
        assert reg is None
        assert any("must be an IPv4 literal" in e for e in errs)

    def test_duplicate_json_keys_refused(self, tmp_path):
        doc = '{"hosts": {"a": null}, "hosts": {}}'
        reg, errs = load_registry(_write(tmp_path, doc))
        assert reg is None
        assert any("duplicate" in e for e in errs)

    def test_garbage_file_is_unreadable_error(self, tmp_path):
        reg, errs = load_registry(_write(tmp_path, "{torn"))
        assert reg is None
        assert any("unreadable" in e for e in errs)

    def test_whitespace_alias_key_is_stored_stripped(self, tmp_path):
        """A trailing space in a hand-edited key must not register a host
        no lookup can find (review-caught silent-absence)."""
        doc = {"hosts": {"box1 ": {"ip_fallback": "192.0.2.10"}}}
        reg, errs = load_registry(_write(tmp_path, doc))
        assert errs == []
        assert "box1" in reg.hosts and "box1 " not in reg.hosts

    def test_duplicate_ip_fallback_refused(self, tmp_path):
        """Two hosts claiming one ip_fallback is a copy-paste error — the
        audit's ip→alias map would silently last-win (review-caught)."""
        doc = {"hosts": {"box1": {"ip_fallback": "192.0.2.10"},
                         "box2": {"ip_fallback": "192.0.2.10"}}}
        reg, errs = load_registry(_write(tmp_path, doc))
        assert reg is None
        assert any("duplicate ip_fallback" in e for e in errs)

    def test_declared_nat_front_allows_shared_ip(self, tmp_path):
        """One address, two names is LEGAL when explicitly declared — the
        hap/moc1 case: the NAT device owns the addr that is also the
        fronted box's only reachable address (2026-07-17)."""
        doc = {"hosts": {"box1": {"ip_fallback": "192.0.2.10"},
                         "natdev": {"ip_fallback": "192.0.2.10",
                                    "shares_front_with": "box1"}}}
        reg, errs = load_registry(_write(tmp_path, doc))
        assert errs == []
        assert reg.hosts["natdev"].shares_front_with == "box1"

    def test_declaration_only_pairs_the_named_alias(self, tmp_path):
        # A third host on the same addr is still the copy-paste error.
        doc = {"hosts": {"box1": {"ip_fallback": "192.0.2.10"},
                         "natdev": {"ip_fallback": "192.0.2.10",
                                    "shares_front_with": "box1"},
                         "box9": {"ip_fallback": "192.0.2.10"}}}
        reg, errs = load_registry(_write(tmp_path, doc))
        assert reg is None
        assert any("box9" in e and "duplicate" in e for e in errs)

    def test_dangling_shares_front_with_is_an_error(self, tmp_path):
        doc = {"hosts": {"natdev": {"ip_fallback": "192.0.2.10",
                                    "shares_front_with": "ghost"}}}
        reg, errs = load_registry(_write(tmp_path, doc))
        assert reg is None
        assert any("names no registry host" in e for e in errs)


class TestResolvePrecedence:
    def _reg(self):
        reg, errs = (Registry(domain="example.internal", hosts={}), [])
        from utils.fleet_naming import FleetHost
        reg.hosts["box1"] = FleetHost(alias="box1",
                                      ip_fallback="192.0.2.10")
        return reg

    def test_dns_hit_keeps_the_name_as_target(self):
        r = resolve("box1", self._reg(),
                    resolver=_resolver({"box1.example.internal": "192.0.2.10"}))
        assert r.method == "dns"
        assert r.target == "box1.example.internal"   # NAME, not the address
        assert r.address == "192.0.2.10"

    def test_dns_miss_falls_to_bare(self):
        r = resolve("box1", self._reg(),
                    resolver=_resolver({"box1": "192.0.2.11"}))
        assert r.method == "bare" and r.target == "box1"
        assert r.address == "192.0.2.11"

    def test_all_miss_uses_labeled_ip_fallback(self):
        r = resolve("box1", self._reg(), resolver=_resolver({}))
        assert r.method == "ip_fallback"
        assert r.target == "192.0.2.10"
        assert "ip_fallback" in (r.error or "")   # drift visible, not silent

    def test_no_fallback_is_honest_unresolved(self):
        reg = self._reg()
        reg.hosts["box1"].ip_fallback = None
        r = resolve("box1", reg, resolver=_resolver({}))
        assert r.method == "unresolved" and r.target is None
        assert "did not resolve" in r.error

    def test_ip_literal_passes_through_untouched(self):
        r = resolve("192.0.2.99", self._reg(),
                    resolver=_resolver({"should": "never-be-asked"}))
        assert r.method == "ip_literal" and r.target == "192.0.2.99"

    def test_standalone_no_registry_degrades_to_bare(self):
        r = resolve("box1", None, resolver=_resolver({"box1": "192.0.2.5"}))
        assert r.method == "bare"
        r2 = resolve("box1", None, resolver=_resolver({}))
        assert r2.method == "unresolved"

    def test_methods_enum_closed(self):
        assert set(METHODS) == {"dns", "bare", "ip_fallback", "ip_literal",
                                "unresolved"}


class TestConnectTarget:
    def test_unresolved_passes_entry_verbatim(self):
        target, method = connect_target("ghost", None, resolver=_resolver({}))
        assert (target, method) == ("ghost", "unresolved")

    def test_dns_returns_the_fqdn_name(self):
        reg = Registry(domain="example.internal", hosts={})
        target, method = connect_target(
            "box1", reg,
            resolver=_resolver({"box1.example.internal": "192.0.2.10"}))
        assert (target, method) == ("box1.example.internal", "dns")

    def test_ip_literal_labeled_as_legacy_debt(self):
        target, method = connect_target("192.0.2.4", None,
                                        resolver=_resolver({}))
        assert (target, method) == ("192.0.2.4", "ip_literal")


# === derived organ posture: role / serves_map (2026-09-02) ==================
#
# The federation collector polled every fleet box on :5000, so a gateway-only
# or field-node box (meshforge-map disabled/absent BY DESIGN) produced a
# permanent federation_peer_unhealthy that each box hand-suppressed. The cure
# is a role stamp the poller can read. The whole value of it is the TRI-STATE:
# only an explicit False stops a poll; UNKNOWN keeps watching, because trading
# a noisy false alarm for a silent real one is the wrong direction (hfm #2).


class TestServesMapTriState:
    def test_true_false_and_unknown_are_three_distinct_answers(self, tmp_path):
        reg, errs = load_registry(_write(tmp_path, {"hosts": {
            "mapbox": {"serves_map": True, "role": "full-gateway"},
            "gwonly": {"serves_map": False, "role": "gateway-only"},
            "nostamp": {"role": "collector"},
        }}))
        assert errs == []
        assert serves_map("mapbox", reg) is True
        assert serves_map("gwonly", reg) is False
        # present but unstamped -> UNKNOWN, and UNKNOWN IS NOT False
        assert serves_map("nostamp", reg) is None

    def test_unknown_host_is_none_not_false(self, tmp_path):
        reg, errs = load_registry(_write(tmp_path, {"hosts": {"a": None}}))
        assert errs == []
        assert serves_map("never-heard-of-it", reg) is None

    def test_empty_entry_and_empty_name_are_none(self, tmp_path):
        reg, _ = load_registry(_write(tmp_path, {"hosts": {"a": None}}))
        assert serves_map("a", reg) is None
        assert serves_map("", reg) is None

    def test_lookup_tolerates_resolved_fqdn_and_ip_fallback(self, tmp_path):
        reg, errs = load_registry(_write(tmp_path, {
            "domain": "mf.internal",
            "hosts": {"gwonly": {"serves_map": False,
                                 "ip_fallback": "10.0.0.9"}},
        }))
        assert errs == []
        # the federation peer list holds resolved names and ip_fallbacks, so
        # the lookup must find the host by either or the filter silently
        # matches nothing and the fix is inert.
        assert serves_map("gwonly.mf.internal", reg) is False
        assert serves_map("10.0.0.9", reg) is False

    def test_no_registry_is_unknown_never_false(self, tmp_path):
        # A missing/broken registry must not read as "nothing serves a map",
        # which would stop federation polling the whole fleet at once.
        assert serves_map("anything", Registry(domain=None, hosts={})) is None


class TestServesMapValidation:
    @pytest.mark.parametrize("bad", ["false", "no", 0, 1, [], {}])
    def test_non_boolean_serves_map_is_rejected(self, tmp_path, bad):
        # "false" is TRUTHY and 0 is falsy — either would silently decide
        # whether a peer is polled. Reject what the author cannot have meant
        # (hfm #3) instead of coercing it.
        reg, errs = load_registry(_write(tmp_path, {"hosts": {
            "x": {"serves_map": bad}}}))
        assert errs, f"serves_map={bad!r} was accepted"
        assert any("serves_map" in e for e in errs)

    def test_absent_serves_map_is_legal_and_means_unknown(self, tmp_path):
        reg, errs = load_registry(_write(tmp_path, {"hosts": {
            "x": {"role": "collector"}}}))
        assert errs == []
        assert reg.hosts["x"].serves_map is None

    def test_role_must_be_a_string(self, tmp_path):
        _reg, errs = load_registry(_write(tmp_path, {"hosts": {
            "x": {"role": 7}}}))
        assert any("role" in e for e in errs)

    def test_role_derived_at_must_be_a_timestamp(self, tmp_path):
        _reg, errs = load_registry(_write(tmp_path, {"hosts": {
            "x": {"role_derived_at": "yesterday"}}}))
        assert any("role_derived_at" in e for e in errs)

    def test_stamp_round_trips(self, tmp_path):
        reg, errs = load_registry(_write(tmp_path, {"hosts": {
            "gwonly": {"role": "gateway-only", "serves_map": False,
                       "role_derived_at": 1788387115}}}))
        assert errs == []
        h = reg.hosts["gwonly"]
        assert (h.role, h.serves_map) == ("gateway-only", False)
        assert h.role_derived_at == 1788387115.0
