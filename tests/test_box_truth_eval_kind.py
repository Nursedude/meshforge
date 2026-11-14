"""box_truth grant kinds — the EVAL-PF lab key rides the ONE grant writer.

Operator 11-13 ("go with A — one writer"): the eval key is a second, CLOSED
grant kind of box_truth_grants (S11 stays: one writer of the key file). These
pin that the new kind is exact in both directions — what it writes reads
back as kind eval-pf, a near-miss reads as FOREIGN, and NOC grants are
byte-for-byte what they were.
"""

import base64
import os
import stat
from pathlib import Path

import pytest

from utils import box_truth as bt
from utils import box_truth_grants as bg


def _pub(comment, seed=1):
    blob = (len(b"ssh-ed25519").to_bytes(4, "big") + b"ssh-ed25519"
            + (32).to_bytes(4, "big") + bytes([seed]) * 32)
    return f"ssh-ed25519 {base64.b64encode(blob).decode()} {comment}"


WRAP = "/opt/meshforge/scripts/eval_pf_readonly.sh"
EXPORT = "/opt/meshforge/scripts/box_truth_export.py"


def test_eval_line_shape_and_readback():
    key = bt.parse_noc_pubkey(_pub("eval-pf:center-a"), kind=bt.KIND_EVAL)
    line = bt.build_grant_line(key, WRAP, kind=bt.KIND_EVAL)
    assert line == (f'restrict,command="/bin/bash {WRAP} --grant {key.fingerprint} '
                    f'</dev/null" {key.line()}')
    e = bt.parse_authorized_keys_line(line)
    assert e.is_grant and e.kind == bt.KIND_EVAL and not e.foreign
    assert e.grant_fp == key.fingerprint


def test_noc_line_unchanged_and_kind_noc():
    key = bt.parse_noc_pubkey(_pub("noc:center-a"))
    line = bt.build_grant_line(key, EXPORT)
    assert line == (f'restrict,command="/usr/bin/python3 -I {EXPORT} --grant '
                    f'{key.fingerprint} </dev/null" {key.line()}')
    e = bt.parse_authorized_keys_line(line)
    assert e.is_grant and e.kind == bt.KIND_NOC


@pytest.mark.parametrize("comment,kind", [
    ("noc:center-a", bt.KIND_EVAL),       # a NOC key cannot be granted as eval
    ("eval-pf:center-a", bt.KIND_NOC),    # nor the reverse
    ("eval-pf@center-a", bt.KIND_EVAL),   # wrong separator
    ("eval-pf:", bt.KIND_EVAL),
])
def test_comment_bound_to_kind(comment, kind):
    with pytest.raises(bt.KeyRejected):
        bt.parse_noc_pubkey(_pub(comment), kind=kind)


def test_unknown_kind_refused():
    with pytest.raises(bt.KeyRejected):
        bt.parse_noc_pubkey(_pub("noc:x"), kind="shell")
    with pytest.raises(bt.KeyRejected):
        bt.export_command(WRAP, "SHA256:" + "A" * 43, kind="shell")


@pytest.mark.parametrize("path,kind", [
    (EXPORT, bt.KIND_EVAL),                          # the export is not the eval wrapper
    (WRAP, bt.KIND_NOC),                             # nor the reverse
    ("/opt/meshforge/scripts/../eval_pf_readonly.sh.bak", bt.KIND_EVAL),
    ("relative/eval_pf_readonly.sh", bt.KIND_EVAL),
])
def test_command_path_bound_to_kind(path, kind):
    with pytest.raises(bt.KeyRejected):
        bt.export_command(path, "SHA256:" + "A" * 43, kind=kind)


def _eval_line(seed=1, comment="eval-pf:center-a"):
    key = bt.parse_noc_pubkey(_pub(comment, seed), kind=bt.KIND_EVAL)
    return key, bt.build_grant_line(key, WRAP, kind=bt.KIND_EVAL)


@pytest.mark.parametrize("mutate", [
    lambda l: l.replace("restrict,", "restrict,pty,", 1),          # extra option
    lambda l: l.replace("restrict,", "", 1),                       # unrestricted
    lambda l: l.replace("/bin/bash ", "/bin/sh ", 1),              # other interpreter
    lambda l: l.replace(" </dev/null", "", 1),                     # stdin left open
    lambda l: l.replace("eval-pf:center-a", "noc:center-a"),     # comment swapped
])
def test_near_miss_eval_lines_read_as_foreign(mutate):
    _, line = _eval_line()
    e = bt.parse_authorized_keys_line(mutate(line))
    assert not e.is_grant and e.foreign and e.kind is None


def test_swapped_key_under_eval_command_is_not_a_grant():
    k1, line = _eval_line(1)
    k2 = bt.parse_noc_pubkey(_pub("eval-pf:center-a", 2), kind=bt.KIND_EVAL)
    swapped = line.replace(k1.blob_b64, k2.blob_b64)
    e = bt.parse_authorized_keys_line(swapped)
    assert not e.is_grant and e.foreign


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "home"
    (h / ".ssh").mkdir(parents=True, mode=0o700)
    os.chmod(h / ".ssh", 0o700)
    wrap = tmp_path / "eval_pf_readonly.sh"
    wrap.write_text("#!/bin/bash\n")
    wrap.chmod(wrap.stat().st_mode | stat.S_IXUSR)
    return h, str(wrap)


def test_plan_apply_list_revoke_eval_grant(home, tmp_path):
    home, wrap = home
    key, _ = _eval_line()
    env = {"XDG_STATE_HOME": str(tmp_path / "state")}
    plan = bg.plan_add(home, key.line(), center="center-a", export_path=wrap,
                       shell="/bin/bash", sshd={}, kind=bt.KIND_EVAL)
    assert plan.problems == [], plan.problems
    bg.apply_add(home, plan, key.fingerprint[-8:], env=env)
    rows = bg.list_grants(home, env=env)
    assert [(r["fingerprint"], r["kind"], r["foreign"]) for r in rows] == \
        [(key.fingerprint, bt.KIND_EVAL, False)]
    assert rows[0]["export"] == wrap
    rp = bg.plan_revoke(home, key.fingerprint)
    assert rp.problems == []
    assert bg.apply_revoke(home, rp, key.fingerprint[-8:], env=env) == 1
    assert bg.list_grants(home, env=env) == []


def test_plan_add_eval_refuses_a_noc_center_comment(home):
    home, wrap = home
    key = bt.parse_noc_pubkey(_pub("eval-pf:other"), kind=bt.KIND_EVAL)
    plan = bg.plan_add(home, key.line(), center="center-a", export_path=wrap,
                       shell="/bin/bash", sshd={}, kind=bt.KIND_EVAL)
    assert any("expected eval-pf:center-a" in p for p in plan.problems)


def test_one_grant_per_fingerprint_across_kinds(home, tmp_path):
    """S5: a key already granted as eval cannot ALSO be added (any kind)."""
    home, wrap = home
    key, _ = _eval_line()
    env = {"XDG_STATE_HOME": str(tmp_path / "state")}
    plan = bg.plan_add(home, key.line(), center="center-a", export_path=wrap,
                       shell="/bin/bash", sshd={}, kind=bt.KIND_EVAL)
    bg.apply_add(home, plan, key.fingerprint[-8:], env=env)
    again = bg.plan_add(home, key.line(), center="center-a", export_path=wrap,
                        shell="/bin/bash", sshd={}, kind=bt.KIND_EVAL)
    assert any("already granted" in p for p in again.problems)


def test_preflight_checks_the_interpreter_OF_THE_KIND(tmp_path, monkeypatch):
    """Fable 11-14 mutant M8 (preflight always checked python) survived: pin
    that each kind's own interpreter is the one measured."""
    wrapper = tmp_path / "w.sh"
    wrapper.write_text("#!/bin/bash\n")
    wrapper.chmod(0o755)
    monkeypatch.setattr(bt, "BASH", str(tmp_path / "no-bash"))
    monkeypatch.setattr(bt, "PYTHON", str(tmp_path / "no-python"))
    ev, _ = bg.preflight(tmp_path, str(wrapper), shell="/bin/bash", kind=bt.KIND_EVAL)
    noc, _ = bg.preflight(tmp_path, str(wrapper), shell="/bin/bash", kind=bt.KIND_NOC)
    assert any("no-bash not executable" in p for p in ev), ev
    assert not any("no-python" in p for p in ev), ev
    assert any("no-python not executable" in p for p in noc), noc
    assert not any("no-bash" in p for p in noc), noc
