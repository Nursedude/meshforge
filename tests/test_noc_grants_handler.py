"""NOC read grants in the TUI (session 3 step 5, 2026-10-15).

The second acting carve-out (fleet_posture.py doctrine, Q8/Q17): ONE CLI
(scripts/box_truth_grant.py), plan first, the CLI's own typed confirm token
(a fingerprint's last 8 / the target's name), logged. The handler never
writes a key file itself; under sudo it runs the CLI as the operator (F7:
nothing root-owned in the operator's ~/.ssh). Pinned with a fake dialog and
the subprocess seams replaced by recorders — the CLI's behaviour is pinned
by its own tests.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "launcher_tui"))

from handlers import noc_grants as ng  # noqa: E402

FP1 = "SHA256:" + "A" * 43
FP2 = "SHA256:" + "B" * 43
LIST_OUT = (f"{FP1}  noc:center1             last read 2026-10-15 07:00:01 from 10.0.0.9  "
            f"[/home/u/.ssh/authorized_keys:3 from=10.0.0.9]\n"
            f"{FP2}  stray                    last read never from -  [/home/u/.ssh/authorized_keys:7]"
            "  ⚠ FOREIGN — mentions the export but is NOT a restricted grant; it may be "
            "UNRESTRICTED (revoke --fp … --force)\n")


def test_parse_grants_reads_fingerprint_comment_and_foreign():
    rows = ng.parse_grants(LIST_OUT)
    assert [(r.fingerprint, r.comment, r.foreign) for r in rows] == [
        (FP1, "noc:center1", False), (FP2, "stray", True)]
    assert ng.parse_grants("no NOC read grants on this box\n") == []


def test_revoke_commands_carry_the_tools_own_token():
    plan = ng.revoke_cmd(FP1, foreign=False)
    assert plan[-3:] == ["revoke", "--fp", FP1]
    apply = ng.revoke_cmd(FP1, foreign=False, confirm=FP1[-8:])
    assert apply[-2:] == ["--confirm", FP1[-8:]]
    assert "--force" in ng.revoke_cmd(FP2, foreign=True)


@pytest.mark.parametrize("alias,ok", [("moc3", True), ("box-1.lan", True),
                                       ("moc3;rm", False), ("-x", False), ("", False),
                                       ("a b", False), ("$(id)", False)])
def test_target_aliases_are_validated_before_any_command(alias, ok):
    assert ng.valid_alias(alias) is ok


def test_plain_root_refuses():
    run, why = ng.as_operator(["x"], euid=0, sudo_user="")
    assert run is None and "root" in why


def test_sudo_drops_to_the_operator():
    run, _ = ng.as_operator(["x"], euid=0, sudo_user="op")
    assert run[:5] == ["sudo", "-n", "-u", "op", "-H"]


class _Dialog:
    def __init__(self, menus=(), inputs=()):
        self.menus, self.inputs, self.shown = list(menus), list(inputs), []

    def menu(self, title, text, choices, **k):
        return self.menus.pop(0) if self.menus else None

    def inputbox(self, title, text, init="", **k):
        return self.inputs.pop(0) if self.inputs else None

    def msgbox(self, title, text, **k):
        self.shown.append(text)


class _Ctx:
    def __init__(self, dialog):
        self.dialog = dialog
        self.reported = []

    def report_action(self, ok, *a, **k):
        self.reported.append(ok)

    def safe_call(self, name, fn, *a, **k):
        return fn(*a, **k)

    def wait_for_enter(self, *a, **k):
        pass


def _handler(dialog, outputs):
    h = ng.NocGrantsHandler()
    h.ctx = _Ctx(dialog)
    h.ran = []

    def capture(cmd, timeout=60):
        h.ran.append(cmd)
        return outputs.pop(0) if outputs else (0, "")
    h._capture = capture
    return h


def test_revoke_needs_the_exact_last_8_and_runs_apply_once():
    plan_out = (1, f"revoke {FP1}\nnothing written — re-run with --confirm {FP1[-8:]}")
    # wrong token: plan shown, apply never runs
    h = _handler(_Dialog(menus=[FP1], inputs=["yes"]), [(0, LIST_OUT), plan_out])
    h._revoke_flow()
    assert not any("--confirm" in c for c in h.ran)
    assert len(h.ran) == 2
    # right token: apply runs exactly once with it
    h = _handler(_Dialog(menus=[FP1], inputs=[FP1[-8:]]),
                 [(0, LIST_OUT), plan_out, (0, "revoked")])
    h._revoke_flow()
    applied = [c for c in h.ran if "--confirm" in c]
    assert len(applied) == 1 and applied[0][-2:] == ["--confirm", FP1[-8:]]
    assert h.ctx.reported == [True]


def test_a_refused_plan_never_asks_for_confirmation():
    h = _handler(_Dialog(menus=[FP1], inputs=[FP1[-8:]]),
                 [(0, LIST_OUT), (1, "REFUSED: not one of ours")])
    h._revoke_flow()
    assert not any("--confirm" in c for c in h.ran)


def test_push_needs_the_target_name_typed():
    h = _handler(_Dialog(inputs=["moc3", "moc"]), [])
    h._push_flow("push")
    assert h.ran == []                               # typed "moc" != "moc3"
    h = _handler(_Dialog(inputs=["moc3", "moc3"]), [(0, "ok")])
    h._push_flow("push")
    assert h.ran and h.ran[0][-4:] == ["push", "moc3", "--confirm", "moc3"]


def test_push_refuses_an_invalid_alias_before_running_anything():
    h = _handler(_Dialog(inputs=["moc3;id", "moc3;id"]), [])
    h._push_flow("push")
    assert h.ran == []


# ── step-8 non-author pass (2026-10-15) ──────────────────────────────────

def test_sudo_as_root_is_plain_root():
    # `sudo su` / `sudo -i` then the TUI: SUDO_USER=root would run the CLI on /root
    run, why = ng.as_operator(["x"], euid=0, sudo_user="root")
    assert run is None and "root" in why


def test_a_comment_spelling_foreign_is_not_the_foreign_marker():
    out = f"{FP1}  noc:FOREIGN               last read never from -  [/h/.ssh/k:3]\n"
    assert ng.parse_grants(out)[0].foreign is False


def test_an_alias_with_a_trailing_newline_is_refused():
    assert ng.valid_alias("moc3\n") is False
