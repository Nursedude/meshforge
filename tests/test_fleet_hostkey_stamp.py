"""Tests for scripts/fleet_hostkey_stamp.py — the writer half of the
name→node identity check (2026-09-11).

The script's whole value is what it REFUSES to stamp. A stamper that happily
records whatever answered would manufacture exactly the false confidence the
identity check exists to destroy, so the refusals get the coverage.
"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

_SPEC = importlib.util.spec_from_file_location(
    "fleet_hostkey_stamp",
    Path(__file__).resolve().parent.parent / "scripts" / "fleet_hostkey_stamp.py")
stamp = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(stamp)


def _write_registry(tmp_path, hosts, domain="mf.internal") -> str:
    p = tmp_path / "fleet_naming.json"
    p.write_text(json.dumps({"domain": domain, "hosts": hosts}))
    return str(p)


def _fake_scan(table):
    """table: {(target, port): [(ktype, fp), ...]} -> a scan_typed stand-in."""
    def scan(target, port=22, **_kw):
        pairs = table.get((target, port))
        if not pairs:
            return [], "keyscan empty (rc=1)"
        return pairs, ""
    return scan


def _resolver_to_self(monkeypatch):
    """Resolve every alias to its own FQDN so targets are predictable."""
    monkeypatch.setattr(stamp, "resolve", lambda alias, reg=None, **kw: type(
        "R", (), {"target": f"{alias}.mf.internal"})())


class TestPickFingerprint:
    def test_prefers_ed25519(self):
        pairs = [("ssh-rsa", "SHA256:rsa"), ("ssh-ed25519", "SHA256:ed")]
        assert stamp.pick_fingerprint(pairs) == "SHA256:ed"

    def test_is_deterministic_across_scan_order(self):
        """A re-run must not 'change' a key that never moved — that would
        trip the disagreement refusal against the script's own last run."""
        a = [("ssh-rsa", "SHA256:r"), ("ecdsa-sha2-nistp256", "SHA256:e")]
        assert stamp.pick_fingerprint(a) == stamp.pick_fingerprint(list(reversed(a)))

    def test_unknown_types_fall_back_to_sorted_first(self):
        pairs = [("unknown", "SHA256:b"), ("unknown", "SHA256:a")]
        assert stamp.pick_fingerprint(pairs) == "SHA256:a"


class TestFindCollisions:
    def test_shared_key_is_a_collision_both_ways(self):
        """lehua/trdev, measured 2026-09-11: one front, :22 forwarded to one
        box, so both names scan to the same key."""
        clash = stamp.find_collisions({
            "lehua": [("ssh-ed25519", "SHA256:same")],
            "trdev": [("ssh-ed25519", "SHA256:same")],
            "moc": [("ssh-ed25519", "SHA256:moc")],
        })
        assert clash["lehua"] == ["trdev"]
        assert clash["trdev"] == ["lehua"]
        assert "moc" not in clash

    def test_distinct_keys_are_no_collision(self):
        assert stamp.find_collisions({
            "a": [("ssh-ed25519", "SHA256:a")],
            "b": [("ssh-ed25519", "SHA256:b")],
        }) == {}


class TestRefusals:
    def test_collision_is_refused_and_stamps_neither(self, tmp_path,
                                                     monkeypatch, capsys):
        reg = _write_registry(tmp_path, {
            "lehua": {"ip_fallback": "192.0.2.10", "shares_front_with": "trdev"},
            "trdev": {"ip_fallback": "192.0.2.10", "shares_front_with": "lehua"},
        })
        _resolver_to_self(monkeypatch)
        monkeypatch.setattr(stamp, "scan_typed", _fake_scan({
            ("lehua.mf.internal", 22): [("ssh-ed25519", "SHA256:trdevkey")],
            ("trdev.mf.internal", 22): [("ssh-ed25519", "SHA256:trdevkey")],
        }))
        rc = stamp.main(["--registry", reg, "--apply"])
        out = capsys.readouterr().out
        assert rc == 1
        assert "shares a host key" in out
        # Nothing written: both stanzas stay unstamped.
        doc = json.loads(Path(reg).read_text())
        assert "expect_hostkey" not in doc["hosts"]["lehua"]
        assert "expect_hostkey" not in doc["hosts"]["trdev"]

    def test_correct_ssh_port_resolves_the_collision(self, tmp_path,
                                                     monkeypatch, capsys):
        """The cure for a collision is ssh_port, not a stamp — with it, each
        alias reaches its own sshd and both stamp cleanly."""
        reg = _write_registry(tmp_path, {
            "lehua": {"ip_fallback": "192.0.2.10", "ssh_port": 2200,
                      "shares_front_with": "trdev"},
            "trdev": {"ip_fallback": "192.0.2.10", "shares_front_with": "lehua"},
        })
        _resolver_to_self(monkeypatch)
        monkeypatch.setattr(stamp, "scan_typed", _fake_scan({
            ("lehua.mf.internal", 2200): [("ssh-ed25519", "SHA256:lehuakey")],
            ("trdev.mf.internal", 22): [("ssh-ed25519", "SHA256:trdevkey")],
        }))
        rc = stamp.main(["--registry", reg, "--apply"])
        assert rc == 0, capsys.readouterr().out
        doc = json.loads(Path(reg).read_text())
        assert doc["hosts"]["lehua"]["expect_hostkey"] == "SHA256:lehuakey"
        assert doc["hosts"]["trdev"]["expect_hostkey"] == "SHA256:trdevkey"

    def test_unreachable_host_is_left_undeclared_never_invented(
            self, tmp_path, monkeypatch, capsys):
        """kiai/alaula: tunnel-only, :22 does not answer. UNKNOWN is not a
        value (honest_failure_modes #2)."""
        reg = _write_registry(tmp_path, {"kiai": {"ip_fallback": "192.0.2.9"}})
        _resolver_to_self(monkeypatch)
        monkeypatch.setattr(stamp, "scan_typed", _fake_scan({}))
        rc = stamp.main(["--registry", reg, "--apply"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "UNKNOWN" in out and "CONCERN" in out
        assert "expect_hostkey" not in json.loads(Path(reg).read_text())["hosts"]["kiai"]

    def test_changed_key_is_refused_not_overwritten(self, tmp_path,
                                                    monkeypatch, capsys):
        """THE one that matters. A key that moved is the node-swap finding;
        silently re-stamping it would launder the detection away."""
        reg = _write_registry(tmp_path, {
            "moc1": {"ip_fallback": "192.0.2.10",
                     "expect_hostkey": "SHA256:original"}})
        _resolver_to_self(monkeypatch)
        monkeypatch.setattr(stamp, "scan_typed", _fake_scan({
            ("moc1.mf.internal", 22): [("ssh-ed25519", "SHA256:DIFFERENT")]}))
        rc = stamp.main(["--registry", reg, "--apply"])
        out = capsys.readouterr().out
        assert rc == 1
        assert "node-swap finding" in out
        doc = json.loads(Path(reg).read_text())
        assert doc["hosts"]["moc1"]["expect_hostkey"] == "SHA256:original"

    def test_restamp_flag_is_the_human_authorising_a_change(
            self, tmp_path, monkeypatch, capsys):
        reg = _write_registry(tmp_path, {
            "moc1": {"ip_fallback": "192.0.2.10",
                     "expect_hostkey": "SHA256:original"}})
        _resolver_to_self(monkeypatch)
        monkeypatch.setattr(stamp, "scan_typed", _fake_scan({
            ("moc1.mf.internal", 22): [("ssh-ed25519", "SHA256:DIFFERENT")]}))
        rc = stamp.main(["--registry", reg, "--apply", "--restamp", "moc1"])
        assert rc == 0, capsys.readouterr().out
        doc = json.loads(Path(reg).read_text())
        assert doc["hosts"]["moc1"]["expect_hostkey"] == "SHA256:DIFFERENT"

    def test_restamp_is_per_alias_not_global(self, tmp_path, monkeypatch,
                                             capsys):
        """Authorising one changed key must not wave through another."""
        reg = _write_registry(tmp_path, {
            "moc1": {"ip_fallback": "192.0.2.10",
                     "expect_hostkey": "SHA256:orig1"},
            "moc2": {"ip_fallback": "192.0.2.11",
                     "expect_hostkey": "SHA256:orig2"}})
        _resolver_to_self(monkeypatch)
        monkeypatch.setattr(stamp, "scan_typed", _fake_scan({
            ("moc1.mf.internal", 22): [("ssh-ed25519", "SHA256:new1")],
            ("moc2.mf.internal", 22): [("ssh-ed25519", "SHA256:new2")]}))
        rc = stamp.main(["--registry", reg, "--apply", "--restamp", "moc1"])
        assert rc == 1
        doc = json.loads(Path(reg).read_text())
        assert doc["hosts"]["moc1"]["expect_hostkey"] == "SHA256:new1"
        assert doc["hosts"]["moc2"]["expect_hostkey"] == "SHA256:orig2"


class TestNoSilentNoOp:
    def test_empty_registry_is_refused(self, tmp_path, capsys):
        reg = _write_registry(tmp_path, {})
        assert stamp.main(["--registry", reg]) == 1
        assert "refusing a silent no-op" in capsys.readouterr().out

    def test_broken_registry_changes_nothing(self, tmp_path, capsys):
        p = tmp_path / "fleet_naming.json"
        p.write_text('{"hosts": null}')
        assert stamp.main(["--registry", str(p)]) == 1
        assert "FAIL" in capsys.readouterr().out

    def test_default_is_report_only(self, tmp_path, monkeypatch, capsys):
        reg = _write_registry(tmp_path, {"moc": {"ip_fallback": "192.0.2.10"}})
        _resolver_to_self(monkeypatch)
        monkeypatch.setattr(stamp, "scan_typed", _fake_scan({
            ("moc.mf.internal", 22): [("ssh-ed25519", "SHA256:mockey")]}))
        rc = stamp.main(["--registry", reg])
        assert rc == 0
        assert "run with --apply" in capsys.readouterr().out
        assert "expect_hostkey" not in json.loads(Path(reg).read_text())["hosts"]["moc"]
