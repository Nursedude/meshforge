"""A TUI password prompt never echoes the secret (deferred tui-masked-password-input).

Raised in every F8 review round (11-01, rounds 1-5): the MQTT password
prompts used ``inputbox``, which ECHOES what is typed — into the terminal,
tmux scrollback and a screen share. whiptail and dialog both have
``--passwordbox``; the backend had none.

Pinned here: the backend primitive (masked, the secret never in argv, its
spaces kept), and a GATE over every TUI source file — an input call (the
primitive or a wrapper such as ``_ask``) whose title or prompt asks for a
secret fails this test. It reads string literals only: a prompt built from
variables is not seen (a known limit, not a guarantee).
"""
import ast
import os
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src', 'launcher_tui'))
sys.path.insert(0, os.path.dirname(__file__))

from backend import DialogBackend, DialogError  # noqa: E402

TUI = Path(__file__).resolve().parent.parent / "src" / "launcher_tui"


def _backend(result):
    be = DialogBackend.__new__(DialogBackend)
    be.backend = 'whiptail'
    be.width = 78
    be.height = 22
    be.list_height = 14
    be._status_bar = None
    be.calls = []

    def fake_run(args, timeout=None, **kw):
        be.calls.append((args, kw))
        return result

    be._run = fake_run
    return be


def test_passwordbox_is_masked_and_never_puts_a_secret_in_argv():
    be = _backend((0, "s3cret"))
    assert be.passwordbox("MQTT Password", "Enter MQTT password:") == "s3cret"
    args, _ = be.calls[0]
    i = args.index('--passwordbox')
    # text, height, width — and NOTHING after: an init value would ride argv (ps)
    assert len(args) == i + 4, args
    assert '--inputbox' not in args


def test_passwordbox_keeps_the_secrets_own_spaces():
    """A password may begin or end with a space; strip() would change it."""
    be = _backend((0, " pass word "))
    assert be.passwordbox("P", "p:") == " pass word "
    _, kw = be.calls[0]
    assert kw.get("strip") is False


def test_passwordbox_cancel_is_none_and_a_dead_tool_raises():
    assert _backend((1, "")).passwordbox("P", "p:") is None
    with pytest.raises(DialogError):
        _backend((-1, "")).passwordbox("P", "p:")


def test_passwordbox_tool_error_is_not_a_cancel():
    """Review r1 nit: dialog's own failure (255 + a reason) is not the
    operator's Esc (255, empty) — one discriminator, ``_is_tool_error``."""
    assert _backend((255, "")).passwordbox("P", "p:") is None
    with pytest.raises(DialogError):
        _backend((255, "Error: Can't make new window")).passwordbox("P", "p:")


#: what a prompt asking for a SECRET says (r1: PSK / API key / private key too)
SECRET_WORDS = re.compile(r"password|passphrase|passwd|secret|\bpsk\b(?!\s*reporter)|api.?key|private.?key", re.I)  # "PSK Reporter" = the ham spotting service, not a key
#: an input-ish call: the primitive and the wrappers that forward to it
#: (r1: broker's ``_ask`` echoed the setup password past the first gate)
INPUT_CALL = re.compile(r"inputbox|^_?ask|prompt", re.I)
#: reviewed exceptions — (file, prompt substring): why it is not a secret
ALLOWED = {
    ("handlers/fleet_backup.py", "Path to SSH private key"): "asks for a file PATH, not the key",
}


def _strings(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.JoinedStr):
        return [v.value for v in node.values
                if isinstance(v, ast.Constant) and isinstance(v.value, str)]
    if isinstance(node, ast.BinOp):                     # "a" "b" / "a" + "b"
        return _strings(node.left) + _strings(node.right)
    return []


def _prompt_text(call):
    parts = [s for a in call.args[:2] for s in _strings(a)]
    parts += [s for k in call.keywords if k.arg in ("title", "text")
              for s in _strings(k.value)]
    return " ".join(parts)


def _offenders(root):
    """(rel:line, prompt) for every echoing input call that asks for a secret
    under ``root``, plus the number of files scanned (a vacuous scan is a
    finding, never a pass)."""
    found, scanned = [], 0
    for path in sorted(root.rglob("*.py")):
        scanned += 1
        rel = path.relative_to(root).as_posix()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), filename=str(path))):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            name = node.func.attr
            if name == "passwordbox" or not INPUT_CALL.search(name):
                continue
            text = _prompt_text(node)
            if not SECRET_WORDS.search(text):
                continue
            if any(rel == f and sub in text for f, sub in ALLOWED):
                continue
            found.append((f"{rel}:{node.lineno}", text[:60]))
    return found, scanned


def test_no_tui_prompt_echoes_a_secret():
    """THE GATE: an echoing prompt for a secret, anywhere in the TUI — the
    primitive or a wrapper, positional or keyword."""
    found, scanned = _offenders(TUI)
    assert scanned > 50, f"scanned only {scanned} files — wrong root?"
    assert not found, f"echoing secret prompts (use dialog.passwordbox): {found}"


def test_the_gate_catches_every_planted_shape(tmp_path):
    """A gate that never failed is not evidence: run the REAL scan over a
    planted tree carrying each shape r1 found past the first gate."""
    (tmp_path / "h.py").write_text(
        'a = self.ctx.dialog.inputbox("MQTT Password", "Enter MQTT password:")\n'
        'b = self._ask("MQTT Password", "Password for broker authentication:\\n\\n" "x")\n'
        'c = self.ctx.dialog.inputbox(title="Custom PSK", text="Enter a key")\n'
        'd = self.ctx.dialog.inputbox("Key", "Paste your API key:")\n'
        'ok1 = self.ctx.dialog.passwordbox("MQTT Password", "Enter MQTT password:")\n'
        'ok2 = self.ctx.dialog.msgbox("Password", "Password: set")\n'
        'ok3 = self.ctx.dialog.inputbox("PSK Reporter", "Your callsign:")\n')
    found, scanned = _offenders(tmp_path)
    assert scanned == 1
    assert [f.split(":")[1] for f, _ in found] == ["1", "2", "3", "4"], found


def test_the_allowlist_is_live():
    """Every reviewed exception still matches a real prompt (a stale entry
    would silently exempt whatever later lands under that text)."""
    for (f, sub), why in ALLOWED.items():
        assert sub in (TUI / f).read_text(encoding="utf-8"), (f, sub, why)


# ---- review r2 (Fable, 11-24) ----------------------------------------------
from backend import blank_secret  # noqa: E402


@pytest.mark.parametrize("typed,want", [(None, None), ("", ""), ("   ", ""), ("\t", ""),
                                        (" a b ", " a b "), ("pw", "pw")])
def test_blank_secret_is_the_one_blank_rule(typed, want):
    assert blank_secret(typed) == want


#: every "blank = none" masked prompt (r2: 5 of 6 had their own copy, unpinned)
BLANK_IS_NONE_SITES = {"handlers/_mqtt_config_form.py": 2, "handlers/broker.py": 2,
                       "handlers/gateway.py": 1, "handlers/meshtasticd_mqtt.py": 1}


@pytest.mark.parametrize("rel,n", sorted(BLANK_IS_NONE_SITES.items()))
def test_each_blank_is_none_prompt_uses_the_one_rule(rel, n):
    src = (TUI / rel).read_text(encoding="utf-8")
    assert src.count("blank_secret(") == n, rel
    assert "not typed.strip()" not in src and "not password.strip()" not in src, rel


def test_dialog_shows_stars_like_whiptail():
    """dialog's passwordbox echoes NOTHING without --insecure (no feedback
    a key landed); whiptail shows '*' per key (measured r2 on a pty)."""
    be = _backend((0, "x"))
    be.backend = 'dialog'
    be.passwordbox("P", "p:")
    args, _ = be.calls[0]
    assert args[0] == '--insecure' and args.index('--passwordbox') > 0, args
    wt = _backend((0, "x"))
    wt.passwordbox("P", "p:")
    assert '--insecure' not in wt.calls[0][0]
