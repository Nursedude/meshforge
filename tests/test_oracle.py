"""Tests for the dude-AI Mesh Oracle — Phase 0 (read-only intent engine).

Phase 0 is the deterministic core: pure intent functions over a read-only
``NocSnapshot``, ≤237-byte calibrated answers, NO mesh I/O and NO LLM. These
tests pin: each intent's behaviour on fixture state; the honest-failure contract
(unknown/stale never reads as healthy); the ≤237-byte budget + UTF-8-safe
truncation; the cap pinned to the gateway's Meshtastic payload limit; the stale
thresholds matched to /api/status; and THE INVARIANT — the oracle is read-only
(no transmit / mutate / shell-out anywhere in the package).

Coords/ids are SYNTHETIC. Arc: .claude/plans/dudeai_mesh_oracle_arc_2026_06_21.md.
"""
from __future__ import annotations

import json
import pathlib
import re

import pytest

from oracle import (
    MAX_REPLY_BYTES,
    MINI_STALE_S,
    NocSnapshot,
    NodeRec,
    WATCHDOG_STALE_S,
    answer,
    fetch_api_status,
    is_query,
    read_snapshot,
)
from oracle.intents import _truncate

_REPO = pathlib.Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------- #
# Builders
# --------------------------------------------------------------------------- #
def _snap(**kw) -> NocSnapshot:
    base = dict(now=10_000.0, box="testbox", wd_installed=True, wd_ok=True,
                wd_signals=[], mini_installed=True, mini_ok=True, mini_active=[])
    base.update(kw)
    return NocSnapshot(**base)


def _sig(cls, subject, severity="degraded"):
    return {"class": cls, "subject": subject, "severity": severity}


# --------------------------------------------------------------------------- #
# status
# --------------------------------------------------------------------------- #
def test_status_full():
    snap = _snap(directory_total=2731, federation_ok=5, federation_peers=6,
                 wd_signals=[_sig("rules_seed_drift", "mini-dudeai")],
                 mini_active=[{"rule_id": "x", "subject": "y"}])
    out = answer("status", snap)
    assert out.startswith("dude-AI@testbox: ")
    assert "fleet:2731" in out
    assert "fed:5/6" in out
    assert "wd:1 SIG" in out
    assert "mini:1act" in out


def test_status_unknown_fleet_is_not_faked():
    # No directory/federation injected -> honest "?", and fed omitted entirely.
    out = answer("status", _snap())
    assert "fleet:?" in out
    assert "fed:" not in out
    assert "wd:0 OK" in out


def test_status_wd_stale_surfaces():
    out = answer("status", _snap(wd_stale=True, wd_age_s=900.0, wd_ok=False))
    assert "wd:stale" in out
    assert "wd:0 OK" not in out


def test_status_partial_federation_ok_unknown():
    out = answer("status", _snap(federation_peers=4, federation_ok=None))
    assert "fed:?/4" in out


# --------------------------------------------------------------------------- #
# whatsup
# --------------------------------------------------------------------------- #
def test_whatsup_lists_signals_and_active_rules():
    snap = _snap(
        wd_signals=[_sig("host_frozen", "moc3", "wedge"),
                    _sig("nice_to_know", "x", "info")],  # info excluded
        mini_active=[{"rule_id": "federation_backoff", "subject": "moc"}],
    )
    out = answer("whatsup", snap)
    assert "host_frozen(moc3)" in out
    assert "federation_backoff" in out
    assert "nice_to_know" not in out  # info severity not surfaced as "active"


def test_whatsup_all_quiet():
    assert "all quiet" in answer("whatsup", _snap())


def test_whatsup_mini_stale_is_a_blind_note_not_quiet():
    out = answer("whatsup", _snap(mini_stale=True, mini_age_s=600.0, mini_ok=False))
    assert "mini stale" in out
    # blindness must be surfaced, never silently read as "all quiet"
    assert out.strip() != "dude-AI@testbox: all quiet"


def test_whatsup_dedupes():
    snap = _snap(
        wd_signals=[_sig("dup", "n")],
        mini_active=[{"rule_id": "dup(n)", "subject": "n"}],
    )
    out = answer("whatsup", snap)
    assert out.count("dup(n)") == 1


# --------------------------------------------------------------------------- #
# wd
# --------------------------------------------------------------------------- #
def test_wd_not_installed():
    out = answer("wd", _snap(wd_installed=False, wd_ok=None, wd_reason="no_state_file"))
    assert "not installed" in out


def test_wd_stale():
    out = answer("watchdog", _snap(wd_stale=True, wd_age_s=1200.0, wd_ok=False))
    assert "STALE" in out and "daemon may be down" in out


def test_wd_clear():
    assert "all clear" in answer("wd", _snap())


def test_wd_signals_reports_worst_and_example():
    snap = _snap(wd_signals=[_sig("a", "n1", "degraded"), _sig("b", "n2", "wedge")])
    out = answer("wd", snap)
    assert "wd:2" in out and "wedge" in out and "a:n1" in out


# --------------------------------------------------------------------------- #
# node
# --------------------------------------------------------------------------- #
def test_node_found():
    nodes = {"!a1b2c3d4": NodeRec("!a1b2c3d4", name="ridge",
                                  last_seen_age_s=42.0, snr=-7.5, hops=2)}
    out = answer("node !a1b2c3d4", _snap(nodes=nodes))
    assert "node !a1b2c3d4" in out and "'ridge'" in out
    assert "seen 42s" in out and "snr -7.5" in out and "hops 2" in out


def test_node_unknown_in_populated_directory():
    out = answer("node !deadbeef", _snap(nodes={"!a1b2c3d4": NodeRec("!a1b2c3d4")}))
    assert "unknown" in out


def test_node_no_directory_is_honest():
    out = answer("node !a1b2c3d4", _snap())  # nodes empty
    assert "lookup unavailable" in out


def test_node_by_short_name():
    nodes = {"!a1b2c3d4": NodeRec("!a1b2c3d4", name="Ridge")}
    out = answer("node ridge", _snap(nodes=nodes))
    assert "node !a1b2c3d4" in out


def test_node_missing_arg():
    assert "need an id" in answer("node", _snap())


# --------------------------------------------------------------------------- #
# link
# --------------------------------------------------------------------------- #
def test_link_coords():
    out = answer("link 0.0,0.0 0.1,0.0", _snap())
    assert "link 11.1km" in out      # 0.1deg lat ~ 11.1 km
    assert "fspl" in out and "(est)" in out
    assert "beyond horizon" in out   # both heights 0 -> 0 km horizon


def test_link_with_heights_gives_los():
    out = answer("link 0.0,0.0,1000 0.05,0.0,1000", _snap())
    # ~5.5 km apart, two 1000 m masts -> horizon ~260 km -> LOS ok
    assert "LOS ok" in out


def test_link_node_ids():
    nodes = {
        "!aa": NodeRec("!aa", lat=0.0, lon=0.0, height_m=10.0),
        "!bb": NodeRec("!bb", lat=0.1, lon=0.0, height_m=10.0),
    }
    out = answer("link !aa !bb", _snap(nodes=nodes))
    assert "link 11.1km" in out


def test_link_bad_args():
    assert "need" in answer("link", _snap())
    assert "bad point" in answer("link foo bar", _snap())


def test_link_rejects_out_of_range_coords():
    assert "bad point" in answer("link 999,0 0,0", _snap())


# --------------------------------------------------------------------------- #
# help / parsing / identity
# --------------------------------------------------------------------------- #
def test_help():
    out = answer("help", _snap())
    assert "status" in out and "link" in out and "node" in out


def test_bare_question_mark_is_status():
    assert "fleet:" in answer("?", _snap())


def test_unknown_intent_when_forced():
    # answer() is robust even if called with non-query text directly.
    assert "try:" in answer("flibbertigibbet", _snap())


def test_identity_prefix_with_and_without_box():
    assert answer("help", _snap(box="volcano")).startswith("dude-AI@volcano: ")
    assert answer("help", _snap(box=None)).startswith("dude-AI: ")


def test_is_query():
    for q in ["status", "?", "?status", "whatsup", "sup", "node !x", "wd",
              "watchdog", "link a b", "help", "  STATUS  "]:
        assert is_query(q), q
    for not_q in ["", "   ", "hello there", "the status is fine",
                  "ping", "good morning"]:
        assert not is_query(not_q), not_q


def test_is_query_natural_language_starting_with_a_head_is_not_a_query():
    # QA 2026-07-05: a lone command triggers, but a head that merely LEADS a
    # natural-language sentence must not — matching those consumed 'help me…'
    # off a HAM channel, dropping it from cross-mesh bridging.
    for not_q in ["help me at the pavilion", "sup everyone",
                  "status update: heading out", "help please",
                  "s good to see you"]:
        assert not is_query(not_q), not_q
    # '?'-prefix and arg-heads with args still work
    for q in ["?help me", "node !abc please", "link !a !b now"]:
        assert is_query(q), q


# --------------------------------------------------------------------------- #
# Budget + truncation
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("q", ["status", "whatsup", "wd", "node !a1b2c3d4",
                               "link 0,0 1,1", "help", "?"])
def test_every_reply_within_budget(q):
    snap = _snap(directory_total=2731, federation_ok=5, federation_peers=6,
                 wd_signals=[_sig("c", "s")], mini_active=[{"rule_id": "r"}])
    out = answer(q, snap)
    assert len(out.encode("utf-8")) <= MAX_REPLY_BYTES
    assert isinstance(out, str) and out


def test_long_answer_is_truncated_within_budget():
    many = [_sig(f"signal_class_number_{i}", f"subject_node_{i}") for i in range(40)]
    out = answer("whatsup", _snap(wd_signals=many))
    assert len(out.encode("utf-8")) <= MAX_REPLY_BYTES
    assert out.endswith("…")


def test_truncation_is_utf8_safe_on_multibyte():
    text = "dude-AI@box: " + ("café—" * 80)  # multibyte, well over budget
    out = _truncate(text, MAX_REPLY_BYTES)
    assert len(out.encode("utf-8")) <= MAX_REPLY_BYTES
    # round-trips cleanly -> no broken multi-byte sequence at the cut
    assert out.encode("utf-8").decode("utf-8") == out
    assert out.endswith("…")


def test_truncation_noop_when_within_budget():
    assert _truncate("short", MAX_REPLY_BYTES) == "short"


# --------------------------------------------------------------------------- #
# Constant + threshold pins (text-read, no heavy gateway/collector import)
# --------------------------------------------------------------------------- #
def test_max_reply_bytes_pinned_to_canonical_payload_cap():
    # Single-source the Meshtastic payload cap. Read the constant from source
    # text rather than importing gateway.canonical_message (its package __init__
    # pulls RNS/LXMF — too heavy and CI-fragile for a unit test).
    text = (_REPO / "src" / "gateway" / "canonical_message.py").read_text(encoding="utf-8")
    m = re.search(r"^MESHTASTIC_MAX_PAYLOAD\s*=\s*(\d+)", text, re.M)
    assert m, "MESHTASTIC_MAX_PAYLOAD not found"
    assert MAX_REPLY_BYTES == int(m.group(1))


def test_stale_thresholds_match_api_status_readers():
    text = (_REPO / "src" / "utils" / "_map_status_endpoints.py").read_text(encoding="utf-8")
    # WATCHDOG_STALE_S (2026-07-19) and MINI_STALE_S (2026-07-22) both became
    # module-level SSOTs shared with the fleet-truth ssh-spool transforms; each
    # class attr is now an alias with no literal, so pin the module constants.
    wd = re.search(r"^WATCHDOG_STALE_S\s*=\s*([\d.]+)", text, re.M)
    mini = re.search(r"^MINI_STALE_S\s*=\s*([\d.]+)", text, re.M)
    assert wd and mini
    assert WATCHDOG_STALE_S == float(wd.group(1))
    assert MINI_STALE_S == float(mini.group(1))


# --------------------------------------------------------------------------- #
# THE INVARIANT: read-only (autonomy rung 1)
# --------------------------------------------------------------------------- #
def test_oracle_intent_engine_is_pure_no_io():
    """intents.py is the pure brain: zero I/O, zero side effects of any kind."""
    import oracle
    src = (pathlib.Path(oracle.__file__).parent / "intents.py").read_text(encoding="utf-8")
    for tok in ("subprocess", "os.system", "os.popen", "Popen", "systemctl",
                "shell=True", ".write(", "open(", "urlopen", "urllib", "socket",
                "requests", "send_text", "send_to_rns"):
        assert tok not in src, f"intents.py must do no I/O — found {tok!r}"


def test_oracle_never_controls_services_or_mutates():
    """No oracle module shells out, controls services, writes files directly,
    or calls the gateway's mutating send paths. The sanctioned rung-1 I/O is:
    read-only file + HTTP reads (snapshot), a directed reply via the injected
    send_fn, and an append-only audit log via the injected log_fn."""
    import oracle
    pkg = pathlib.Path(oracle.__file__).parent
    forbidden = ("subprocess", "os.system", "os.popen", "Popen", "systemctl",
                 "shell=True", ".write(", "send_text", "send_to_rns")
    offenders = []
    for py in sorted(pkg.glob("*.py")):
        src = py.read_text(encoding="utf-8")
        for tok in forbidden:
            if tok in src:
                offenders.append(f"{py.name}:{tok}")
    assert not offenders, f"read-only invariant violated: {offenders}"


# --------------------------------------------------------------------------- #
# fetch_api_status — read-only /api/status enrichment (degrade to None)
# --------------------------------------------------------------------------- #
def test_fetch_api_status_success():
    got = fetch_api_status(_get=lambda url, timeout: b'{"directory":{"total":5}}')
    assert got == {"directory": {"total": 5}}


def test_fetch_api_status_bad_json_returns_none():
    assert fetch_api_status(_get=lambda url, timeout: b"{not json") is None


def test_fetch_api_status_empty_body_returns_none():
    assert fetch_api_status(_get=lambda url, timeout: None) is None


def test_fetch_api_status_non_dict_returns_none():
    assert fetch_api_status(_get=lambda url, timeout: b"[1,2,3]") is None


def test_fetch_api_status_exception_returns_none():
    def boom(url, timeout):
        raise OSError("connection refused")
    assert fetch_api_status(_get=boom) is None


def test_read_snapshot_uses_fetched_status(tmp_path):
    status = {"directory": {"total": 9},
              "federation": {"peer_status": [{"ok": True}, {"ok": False}]}}
    fetched = fetch_api_status(_get=lambda u, t: json.dumps(status).encode())
    snap = read_snapshot(home=tmp_path, watchdog_path=tmp_path / "x.json",
                         status=fetched, now=10_000.0)
    assert snap.directory_total == 9
    assert snap.federation_peers == 2 and snap.federation_ok == 1
    assert "fleet:9" in answer("status", snap) and "fed:1/2" in answer("status", snap)


# --------------------------------------------------------------------------- #
# read_snapshot (the only I/O) — honest-failure contract
# --------------------------------------------------------------------------- #
def test_read_snapshot_missing_files(tmp_path):
    snap = read_snapshot(home=tmp_path, watchdog_path=tmp_path / "nope.json",
                         now=10_000.0)
    assert snap.wd_installed is False
    assert snap.mini_installed is False
    assert snap.box is None  # nothing to derive it from


def test_read_snapshot_reads_files_and_derives_box(tmp_path):
    (tmp_path / "wd.json").write_text(json.dumps(
        {"host": "boxA", "ts": 9_995.0, "ok": True, "probe_count": 7,
         "signals": [_sig("c", "s")]}))
    (tmp_path / "mini_dudeai_state.json").write_text(json.dumps(
        {"host": "boxA", "last_tick_ts": 9_990.0, "error_count": 0,
         "rules": {"r1": {"rule_id": "r1", "subject": "x",
                          "currently_active": True, "last_detail": "d"}}}))
    snap = read_snapshot(home=tmp_path, watchdog_path=tmp_path / "wd.json",
                         now=10_000.0)
    assert snap.wd_installed and snap.wd_ok and len(snap.wd_signals) == 1
    assert snap.mini_installed and snap.mini_ok
    assert len(snap.mini_active) == 1 and snap.mini_active[0]["rule_id"] == "r1"
    assert snap.box == "boxA"


def test_read_snapshot_marks_stale(tmp_path):
    (tmp_path / "wd.json").write_text(json.dumps(
        {"host": "b", "ts": 9_000.0, "ok": True, "signals": []}))  # 1000s old
    snap = read_snapshot(home=tmp_path, watchdog_path=tmp_path / "wd.json",
                         now=10_000.0)
    assert snap.wd_stale is True
    assert snap.wd_ok is False  # stale forces not-ok (never read as healthy)


def test_read_snapshot_malformed_is_installed_not_ok(tmp_path):
    (tmp_path / "wd.json").write_text("{not json")
    snap = read_snapshot(home=tmp_path, watchdog_path=tmp_path / "wd.json",
                         now=10_000.0)
    assert snap.wd_installed is True and snap.wd_ok is False


def test_read_snapshot_status_injection(tmp_path):
    status = {"directory": {"total": 2731},
              "federation": {"peer_status": [{"ok": True}, {"ok": True},
                                             {"ok": False}]}}
    snap = read_snapshot(home=tmp_path, watchdog_path=tmp_path / "nope.json",
                         status=status, now=10_000.0)
    assert snap.directory_total == 2731
    assert snap.federation_peers == 3 and snap.federation_ok == 2


def test_oracle_log_path_under_data_dir_not_bare_home():
    """#60: the audit log must live under the sandbox-writable DATA dir
    (~/.local/share/meshforge), NOT bare ~/. The gateway runs with
    ProtectHome=read-only + .local/share/meshforge in ReadWritePaths, so a
    write to ~/mesh_oracle_log.jsonl silently fails (append_jsonl swallows the
    OSError) and the oracle's continuity log goes dark on every fleet gateway."""
    from oracle import oracle_log_path
    from utils.paths import MeshForgePaths, get_real_user_home
    p = oracle_log_path()
    assert p.name == "mesh_oracle_log.jsonl"
    assert p.parent == MeshForgePaths.get_data_dir()
    assert p.parent != get_real_user_home()  # NOT the read-only bare home
