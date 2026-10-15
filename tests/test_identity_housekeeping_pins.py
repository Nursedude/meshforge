"""Pins from the non-author pass on the 10-07 identity arc (2026-10-15):
housekeeping judged a just-listed identity stale on the ledger-wide clock
(finding 3), and `--remove` backed a secret-bearing gateway.json up at umask
mode (finding 4). Drill: scripts/identity_ingress_drill.py."""
import json
import os
import stat
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from gateway import rns_ingress_policy as pol  # noqa: E402
import rns_identity_housekeeping as hk  # noqa: E402


def _cfg(tmp_path, ids):
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    (cfg / "gateway.json").write_text(json.dumps(
        {"rns": {"bridge_source_identities": ids}}), encoding="utf-8")
    return cfg


def test_a_newly_listed_identity_is_too_early_not_stale(tmp_path):
    now = [1_800_000_000.0]
    led = pol.IngressLedger(tmp_path / pol.LEDGER_FILENAME, now_fn=lambda: now[0])
    old, new = "a" * 32, "b" * 32
    led.stamp(policy="enforce", listed=1, identities=[old])
    now[0] += 15 * 86400
    led.note_listed(old)
    led.stamp(policy="enforce", listed=2, identities=[old, new])
    now[0] += 3600
    rows = {r["hash"]: r for r in hk.build_rows(_cfg(tmp_path, [old, new]), led.path, now=now[0])}
    assert rows[old]["verdict"] == "active"
    assert rows[new]["verdict"] == "too early", rows[new]
    use = pol.project_ledger_doc(json.loads(led.path.read_text()), now=now[0])["use"]
    assert new not in use["no_inbound"]


def test_an_identity_listed_a_full_window_without_inbound_is_stale(tmp_path):
    now = [1_800_000_000.0]
    led = pol.IngressLedger(tmp_path / pol.LEDGER_FILENAME, now_fn=lambda: now[0])
    old, new = "a" * 32, "b" * 32
    led.stamp(policy="enforce", listed=1, identities=[old])
    now[0] += 15 * 86400
    led.stamp(policy="enforce", listed=2, identities=[old, new])
    now[0] += 15 * 86400
    rows = {r["hash"]: r for r in hk.build_rows(_cfg(tmp_path, [old, new]), led.path, now=now[0])}
    assert rows[new]["verdict"] == "stale" and rows[old]["verdict"] == "stale"


def test_a_relisted_identity_restarts_its_clock(tmp_path):
    now = [1_800_000_000.0]
    led = pol.IngressLedger(tmp_path / pol.LEDGER_FILENAME, now_fn=lambda: now[0])
    a, b = "a" * 32, "b" * 32
    led.stamp(policy="enforce", listed=2, identities=[a, b])
    now[0] += 20 * 86400
    led.stamp(policy="enforce", listed=1, identities=[a])      # b removed
    led.stamp(policy="enforce", listed=2, identities=[a, b])   # b re-added
    now[0] += 3600
    rows = {r["hash"]: r for r in hk.build_rows(_cfg(tmp_path, [a, b]), led.path, now=now[0])}
    assert rows[b]["verdict"] == "too early", rows[b]


def test_the_remove_backup_keeps_the_original_mode(tmp_path):
    p = tmp_path / "gateway.json"
    victim = "c" * 32
    p.write_text(json.dumps({"rns": {"bridge_source_identities": [victim],
                                     "downlink_psk": "SECRET"}}), encoding="utf-8")
    os.chmod(p, 0o600)
    old = os.umask(0o022)
    try:
        hk.remove(tmp_path, [victim], stamp="T")
    finally:
        os.umask(old)
    bak = tmp_path / "gateway.json.bak-housekeeping-T"
    assert "SECRET" in bak.read_text()
    assert stat.S_IMODE(bak.stat().st_mode) == 0o600, oct(bak.stat().st_mode)
    assert stat.S_IMODE(p.stat().st_mode) == 0o600
    assert not list(tmp_path.glob(".gateway.json.housekeeping-*"))


# ── non-author read of 11388546 (2026-10-15) ─────────────────────────────

def test_a_peer_hash_the_ledger_never_clocked_is_not_watched_not_stale(tmp_path):
    now = [1_800_000_000.0]
    led = pol.IngressLedger(tmp_path / pol.LEDGER_FILENAME, now_fn=lambda: now[0])
    allow, peer = "a" * 32, "c" * 32
    led.stamp(policy="enforce", listed=1, identities=[allow])
    now[0] += 15 * 86400
    led.note_listed(allow)
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    (cfg / "gateway.json").write_text(json.dumps({"rns": {
        "bridge_source_identities": [allow], "peer_gateway_destinations": [peer]}}))
    rows = {r["hash"]: r for r in hk.build_rows(cfg, led.path, now=now[0])}
    assert rows[allow]["verdict"] == "active"
    assert rows[peer]["verdict"] == "not watched", rows[peer]


def test_a_ledger_without_listed_since_reads_too_early_not_stale():
    h = "d" * 32
    doc = {"watch_since": 1_000.0, "listed_ids": [h], "listed_seen": {}}
    use = pol.project_ledger_doc(doc, now=1_000.0 + 30 * 86400)["use"]
    assert use["no_inbound"] == [] and h in use["too_early"]


def test_an_uppercase_listed_hash_is_too_early_not_stale(tmp_path):
    now = [1_800_000_000.0]
    led = pol.IngressLedger(tmp_path / pol.LEDGER_FILENAME, now_fn=lambda: now[0])
    old, new = "a" * 32, "B" * 32
    led.stamp(policy="enforce", listed=1, identities=[old])
    now[0] += 15 * 86400
    led.note_listed(old)
    led.stamp(policy="enforce", listed=2, identities=[old, new])
    now[0] += 3600
    rows = {r["hash"]: r for r in hk.build_rows(_cfg(tmp_path, [old, new]), led.path, now=now[0])}
    assert rows[new.lower()]["verdict"] == "too early", rows[new.lower()]
