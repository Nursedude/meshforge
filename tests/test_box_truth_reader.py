"""box_truth/v1 pinned reader (NOC read grant session 2, 2026-10-14).

Owed by: S1 (distinct outcomes), S2 (identity on every read, data discarded
on mismatch), S3 (pinned key options), B5c (no ControlMaster), S8 (bounded
read).
"""
import base64
import struct
import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from utils import box_truth as bt  # noqa: E402
from utils import box_truth_app as app  # noqa: E402
from utils import box_truth_reader as br  # noqa: E402


def _hostkey(seed=b"\x22"):
    blob = (struct.pack(">I", 11) + b"ssh-ed25519" + struct.pack(">I", 32) + seed * 32)
    return "ssh-ed25519 " + base64.b64encode(blob).decode(), bt.fingerprint_of_blob(blob)


HOSTKEY_LINE, HOSTKEY_FP = _hostkey()
OTHER_LINE, OTHER_FP = _hostkey(b"\x33")
GOOD = bt.frame_document({"schema": bt.SCHEMA, "sections": {"clock": {"now": 1}}})


def fake_ssh(rc, stdout="", stderr="", hostkey=HOSTKEY_LINE, seen=None,
             timed_out=False, overflow=False):
    def run(argv, deadline):
        if seen is not None:
            seen.append(argv)
        kh = next(a.split("=", 1)[1] for a in argv if a.startswith("UserKnownHostsFile="))
        alias = next(a.split("=", 1)[1] for a in argv if a.startswith("HostKeyAlias="))
        if hostkey:
            Path(kh).write_text(f"{alias} {hostkey}\n")
        return rc, stdout.encode(), stderr.encode(), timed_out, overflow
    return run


def test_ok_read_returns_the_doc_and_the_observed_identity():
    r = br.read_box("moc3", HOSTKEY_FP, runner=fake_ssh(0, GOOD))
    assert r.outcome == "ok" and r.doc["sections"]["clock"] == {"now": 1}
    assert r.hostkey_fp == HOSTKEY_FP


def test_wrong_host_key_discards_the_data():
    r = br.read_box("moc3", HOSTKEY_FP, runner=fake_ssh(0, GOOD, hostkey=OTHER_LINE))
    assert r.outcome == "identity_mismatch" and r.doc is None
    assert OTHER_FP in r.stderr_head


def test_wrong_box_that_refuses_is_still_a_wrong_box():
    r = br.read_box("moc3", HOSTKEY_FP, runner=fake_ssh(
        255, stderr="Permission denied (publickey).", hostkey=OTHER_LINE))
    assert r.outcome == "identity_mismatch"


def test_hashed_known_hosts_entry_is_still_observed():
    """Live drill 2026-10-14: Debian's HashKnownHosts=yes wrote |1|salt|hash
    and every box read identity_mismatch. The fake now writes what ssh wrote."""
    def run(argv, deadline):
        kh = next(a.split("=", 1)[1] for a in argv if a.startswith("UserKnownHostsFile="))
        Path(kh).write_text(f"|1|jIXGaW4RUHofInYd+BeslXb1CgQ=|QLtN6gIy= {HOSTKEY_LINE}\n")
        return 0, GOOD.encode(), b"", False, False
    r = br.read_box("moc3", HOSTKEY_FP, runner=run)
    assert r.outcome == "ok" and r.hostkey_fp == HOSTKEY_FP


def test_unobserved_host_key_cannot_prove_identity():
    r = br.read_box("moc3", HOSTKEY_FP, runner=fake_ssh(0, GOOD, hostkey=None))
    assert r.outcome == "identity_mismatch" and r.doc is None


def test_unpinned_target_is_refused_without_connecting():
    seen = []
    r = br.read_box("moc3", None, runner=fake_ssh(0, GOOD, seen=seen))
    assert r.outcome == "identity_unpinned" and seen == []


@pytest.mark.parametrize("rc,stderr,outcome", [
    (255, "user@moc3: Permission denied (publickey,password).", "refused_grant"),
    (255, "ssh: connect to host moc3 port 22: Connection timed out", "unreachable"),
    (2, "python3: can't open file", "export_broken"),
])
def test_failure_outcomes_are_distinct(rc, stderr, outcome):
    hk = HOSTKEY_LINE if outcome != "unreachable" else None
    r = br.read_box("moc3", HOSTKEY_FP, runner=fake_ssh(rc, stderr=stderr, hostkey=hk))
    assert r.outcome == outcome
    assert r.transport()["outcome"] == outcome and r.transport()["rc"] == rc


def test_refused_cell_names_the_fix():
    r = br.read_box("moc3", HOSTKEY_FP, runner=fake_ssh(
        255, stderr="Permission denied (publickey).", hostkey=HOSTKEY_LINE))
    text = br.describe(r)
    assert "grant refused on moc3 (key or source)" in text
    assert "box_truth_grant.py push moc3" in text


def test_pinned_read_carries_all_three_options_and_no_control_master(tmp_path):
    seen = []
    br.read_box("moc3", HOSTKEY_FP, key_path=tmp_path / "k",
                runner=fake_ssh(0, GOOD, seen=seen))
    argv = seen[0]
    joined = " ".join(argv)
    for opt in ("IdentitiesOnly=yes", "IdentityAgent=none", "ControlMaster=no",
                "ControlPath=none", "StrictHostKeyChecking=accept-new",
                "GlobalKnownHostsFile=/dev/null", "HostKeyAlias=moc3",
                "BatchMode=yes"):
        assert opt in joined, opt
    assert argv[argv.index("-i") + 1] == str(tmp_path / "k")
    assert argv[-2:] == ["moc3", br.REMOTE_COMMAND]


def test_unpinned_legacy_read_has_no_identity_options_but_still_no_master(tmp_path):
    seen = []
    br.read_box("moc3", HOSTKEY_FP, runner=fake_ssh(0, GOOD, seen=seen))
    assert "-i" not in seen[0] and "ControlMaster=no" in seen[0]


def test_option_injecting_alias_is_refused():
    with pytest.raises(ValueError):
        br.ssh_argv("-oProxyCommand=x", Path("/tmp/kh"))


def test_overflow_and_timeout():
    r = br.read_box("moc3", HOSTKEY_FP, runner=fake_ssh(None, overflow=True))
    assert r.outcome == "export_broken"
    r = br.read_box("moc3", HOSTKEY_FP, runner=fake_ssh(None, timed_out=True, hostkey=None))
    assert r.outcome == "unreachable"


def test_bounded_run_caps_stdout_for_real(monkeypatch):
    monkeypatch.setattr(br, "MAX_BODY_BYTES", 1000)
    rc, out, _err, timed_out, overflow = br._bounded_run(
        ["/bin/sh", "-c", "yes x | head -c 100000"], 10)
    assert overflow and len(out) <= 1000


def test_bounded_run_deadline_for_real():
    rc, _o, _e, timed_out, _ov = br._bounded_run(["/bin/sh", "-c", "sleep 5"], 0.3)
    assert timed_out


def test_noc_key_never_under_claude_dir(tmp_path):
    p = br.noc_key_path(tmp_path)
    assert p == tmp_path / ".ssh" / app.NOC_KEY_BASENAME
    assert ".claude" not in p.parts


def test_refusal_reason_reaches_the_cell():
    """Review B: a refused export used to read as a generic failure. The
    export's own reason is carried into describe()."""
    doc = bt.frame_document({"schema": bt.SCHEMA, "refused": "witness",
                             "reason": "read witness unwritable at /x — fix ownership/mode"})
    r = br.read_box("moc3", HOSTKEY_FP, runner=fake_ssh(0, doc))
    assert r.outcome == "export_broken"
    assert br.describe(r) == "export on moc3 refused: read witness unwritable at /x — fix ownership/mode"
    busy = bt.frame_document({"schema": bt.SCHEMA, "refused": "busy", "reason": "lock held"})
    r = br.read_box("moc3", HOSTKEY_FP, runner=fake_ssh(0, busy))
    assert r.outcome == "rate_limited" and "lock held" in br.describe(r)


def test_n1_identity_learn_presents_no_credentials():
    """Review A N1: the 'learn the host key' connection authenticated with the
    operator's agent on a not-yet-verified box. It now offers nothing — the
    host key is recorded before user auth (verified by A on a real sshd)."""
    seen = []

    def run(argv, deadline):
        seen.append(argv)
        kh = next(a.split("=", 1)[1] for a in argv if a.startswith("UserKnownHostsFile="))
        Path(kh).write_text(f"moc3 {HOSTKEY_LINE}\n")
        return (255 if len(seen) == 1 else 0), b"", b"", False, False
    r = br.exec_verified("moc3", HOSTKEY_FP, "true", runner=run)
    assert r.outcome == "ok"
    learn = " ".join(seen[0])
    for opt in ("PubkeyAuthentication=no", "PasswordAuthentication=no",
                "KbdInteractiveAuthentication=no", "GSSAPIAuthentication=no",
                "IdentityAgent=none"):
        assert opt in learn, opt
    for argv in seen:
        j = " ".join(argv)
        for opt in ("ForwardAgent=no", "ForwardX11=no", "ClearAllForwardings=yes",
                    "PermitLocalCommand=no"):
            assert opt in j, opt
    assert "PubkeyAuthentication=no" not in " ".join(seen[1])


def test_b2_center_key_problem_is_named_not_grant_refused():
    r = br.ReadResult("moc3", "refused_grant", pinned=True,
                      stderr_head=f"center key /h/.ssh/{app.NOC_KEY_BASENAME} missing — "
                                  "box_truth_grant.py center-key --create")
    assert br.describe(r).startswith(f"center key /h/.ssh/{app.NOC_KEY_BASENAME} missing")
