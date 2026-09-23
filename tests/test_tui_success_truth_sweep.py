"""The success-truth SWEEP: every TUI action, every external dead.

`test_all_tags_dispatch` (test_all_handlers_protocol.py) proves that all 116
actions ROUTE. This proves what they SAY when there is nothing to say it
about: sockets refused, no subprocess, no tool on PATH. A screen rendered
under those conditions must carry a word of uncertainty — or the action
must be declared LOCAL_ONLY in `launcher_tui/action_truth.py` with a why.

What it catches: the EAS Dashboard class (2026-09-22) — "Weather: no alerts"
printed from a fetch that never happened — and any future action that maps
a dead external to a confident screen (honest_failure_modes #1/#2).

What it cannot catch (stated, not hidden): a screen that says one honest
word and three false ones passes; a handler that returns SILENTLY (no
dialog at all) is counted, not failed — silence is ambiguous between
"asked nothing" and "swallowed the failure", and the swallow class is
MF027/hfm #9's job. The vocabulary below is deliberately broad; the test
is a FLOOR, not a proof.

Run with MF_TRUTH_SWEEP_DUMP=<path> to write every action's verdict and
rendered text as JSON — that is how the allowlists get their entries.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[1] / "src"
for p in (str(_SRC), str(_SRC / "launcher_tui"), str(Path(__file__).resolve().parent)):
    if p not in sys.path:
        sys.path.insert(0, p)

from handler_registry import HandlerRegistry  # noqa: E402
from handler_test_utils import FakeDialog, make_handler_context  # noqa: E402
from launcher_tui.action_truth import KNOWN_FALSE_OK, LOCAL_ONLY  # noqa: E402

# Words that make a screen honest when every external is dead. Broad on
# purpose: the sweep is a floor. Anchored on word starts so "error" also
# matches "errors" and "unreachable" matches "Unreachable:".
HONEST = re.compile(
    r"unknown|unreach|not available|unavailable|could ?n.t|cannot|can.t|"
    r"fail|error|not installed|not found|not running|not connected|"
    r"not reachable|not detected|not configured|not supported|not present|"
    r"refus|missing|offline|time[d ]?out|disabled|absent|no data|"
    r"not (?:yet )?(?:started|active|loaded|set)|n/a\b|unable|"
    r"no (?:\w+ ){0,3}(?:found|detected|available|configured|installed)|"
    r"needs admin|requires admin|admin mode|"  # a refusal is honest
    r"❌|✗|⚠",
    re.IGNORECASE,
)

# Dialog kinds that render only navigation / progress, never a claim.
NAVIGATION = {"menu", "infobox", "checklist", "inputbox", "yesno", "editbox"}


def _all_actions():
    from handlers import get_all_handlers
    ctx = make_handler_context()
    reg = HandlerRegistry(ctx)
    for cls in get_all_handlers():
        reg.register(cls())
    out = []
    for section in reg.section_names:
        for tag, _ in reg.get_menu_items(section):
            out.append((section, tag))
    return sorted(out)


ACTIONS = _all_actions()


@pytest.fixture
def dead_externals(no_network, monkeypatch, tmp_path):
    """Sockets refused (no_network), subprocess absent, nothing on PATH,
    HOME pointed at an empty dir so no operator config leaks in."""
    def _absent(*a, **k):
        raise FileNotFoundError("[truth-sweep] no subprocess: every external is dead")
    for name in ("run", "Popen", "call", "check_call", "check_output"):
        monkeypatch.setattr(subprocess, name, _absent)
    monkeypatch.setattr(shutil, "which", lambda *a, **k: None)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("SUDO_USER", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".config"))
    yield


def _render(section: str, tag: str) -> tuple[bool, list, str]:
    """Dispatch one action on a fresh registry; return (routed, kinds, text)."""
    from handlers import get_all_handlers
    dialog = FakeDialog()
    ctx = make_handler_context(dialog=dialog)
    reg = HandlerRegistry(ctx)
    for cls in get_all_handlers():
        reg.register(cls())
    routed = reg.dispatch(section, tag)
    kinds = [c[0] for c in dialog.calls]
    text = "\n".join(
        " ".join(str(part) for part in c[1] if part is not None) for c in dialog.calls
    )
    return routed, kinds, text


def _verdict(kinds: list, text: str) -> str:
    if not kinds:
        return "silent"
    if all(k in NAVIGATION for k in kinds):
        return "navigation"
    if HONEST.search(text):
        return "honest"
    return "false-ok"


_DUMP: dict = {}


@pytest.mark.parametrize("section,tag", ACTIONS, ids=[f"{s}/{t}" for s, t in ACTIONS])
def test_action_tells_the_truth_with_every_external_dead(section, tag, dead_externals):
    routed, kinds, text = _render(section, tag)
    assert routed is True, f"{section}/{tag} did not route"
    verdict = _verdict(kinds, text)
    key = (section, tag)
    _DUMP[f"{section}/{tag}"] = {"verdict": verdict, "kinds": kinds, "text": text[:600]}

    if key in KNOWN_FALSE_OK:
        assert verdict == "false-ok", (
            f"{section}/{tag} now renders '{verdict}' — it tells the truth. "
            f"Remove it from KNOWN_FALSE_OK (the baseline only shrinks).")
        return
    if key in LOCAL_ONLY:
        # A declared local action may say anything; it asked no external.
        return
    assert verdict != "false-ok", (
        f"{section}/{tag} rendered a confident screen with every external dead "
        f"and no word of uncertainty:\n{text[:400]}\n"
        f"Either the screen lies (fix it: say UNKNOWN when the source could not "
        f"be asked) or the action is local-only (declare it in "
        f"launcher_tui/action_truth.py LOCAL_ONLY with a why).")


def test_allowlists_name_only_live_actions():
    """A closed enum's consumers must not outlive its members."""
    live = set(ACTIONS)
    stale = [k for k in list(LOCAL_ONLY) + list(KNOWN_FALSE_OK) if k not in live]
    assert not stale, f"action_truth lists actions that no longer exist: {stale}"


def test_every_local_only_entry_says_why():
    empty = [k for k, why in LOCAL_ONLY.items() if not str(why).strip()]
    assert not empty, f"LOCAL_ONLY entries without a why: {empty}"


@pytest.fixture(scope="session", autouse=True)
def _write_dump():
    yield
    path = os.environ.get("MF_TRUTH_SWEEP_DUMP")
    if path and _DUMP:
        Path(path).write_text(json.dumps(_DUMP, indent=1, sort_keys=True))
