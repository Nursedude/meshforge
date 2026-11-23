"""No rpc_key reader or pinner ever quotes config TEXT (WITNESS-TUI r13.3,
Fable r15 #2). ConfigObj's ParseError embeds the offending line ("Invalid
line ('  rpc_key <hex>')") and the strict fallback copied that shape; the
message rode `--check` / `--copy-from` stderr into the installer and the
MeshChatX wrapper's journal. `pin_rpc_key_bytes` logged an invalid-but-
rnsd-used key with %r. Every one of these errors names the LINE NUMBER and
the KIND, never the content.
"""
import logging
import os
import subprocess
import sys
from unittest.mock import patch

import pytest

_ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(_ROOT, "src"))

from utils import rns_config_reader as rcr  # noqa: E402
from utils.rns_config_reader import RNSConfigError, rnsd_rpc_key  # noqa: E402
from utils.rns_config_setup import pin_rpc_key_bytes  # noqa: E402

SECRET = "5ec4e7" + "ab" * 29          # 64 hex
SHAPES = [
    f"[reticulum]\n  rpc_key {SECRET}\n",                 # no '=' → Invalid line
    f"[reticulum]\n  {SECRET}\n",                         # bare value line
    f'[reticulum]\n  rpc_key = "{SECRET}\n',              # unterminated quote
    f"[reticulum]\n  rpc_key = {SECRET}\n  rpc_key = {SECRET}\n",   # duplicate
    f"[reticulum]\n  rpc_key = '''{SECRET}\n",            # open triple quote
    f"[reticulum]\n[[{SECRET}\n",                         # broken section
]
PATHS = ["configobj", "strict"]


def _err(raw: bytes, path: str) -> str:
    flag = rcr.HAS_CONFIGOBJ if path == "configobj" else False
    if path == "configobj" and not flag:
        pytest.skip("RNS ConfigObj not importable here")
    with patch.object(rcr, "HAS_CONFIGOBJ", flag):
        try:
            rnsd_rpc_key(raw)
        except RNSConfigError as e:
            return str(e)
    return ""


@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("text", SHAPES)
def test_a_reader_error_never_quotes_the_line(text, path):
    msg = _err(text.encode(), path)
    assert SECRET not in msg and SECRET[:16] not in msg, msg


def test_the_invalid_line_error_still_says_where(tmp_path):
    msg = _err(SHAPES[0].encode(), "strict")
    assert "line 2" in msg and msg


def _cli(*args):
    return subprocess.run(
        [sys.executable, os.path.join(_ROOT, "scripts", "rns_rpc_key_pin.py"), *map(str, args)],
        capture_output=True, text=True, timeout=60)


@pytest.mark.parametrize("text", SHAPES)
def test_check_and_copy_from_never_print_the_line(tmp_path, text):
    bad = tmp_path / "bad"
    bad.write_text(text)
    good = tmp_path / "good"
    good.write_text("[reticulum]\n  share_instance = Yes\n")
    for r in (_cli("--check", bad), _cli("--copy-from", bad, good),
              _cli("--copy-from", good, bad), _cli(bad)):
        out = r.stdout + r.stderr
        assert SECRET not in out and SECRET[:16] not in out, out


def test_an_invalid_key_rnsd_uses_is_never_logged(caplog):
    raw = f"[reticulum]\n  rpc_key = {SECRET} ; note\n".encode()
    with caplog.at_level(logging.DEBUG):
        pin_rpc_key_bytes(raw)
    assert SECRET not in caplog.text and SECRET[:16] not in caplog.text, caplog.text
