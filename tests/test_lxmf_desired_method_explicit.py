"""Bridge matrix F12: every production LXMessage names its delivery method.

No gateway send passed ``desired_method``, so the method was whatever the
LXMF library defaults to at pack time — today ``DIRECT`` (fork
``1.0.1+mf.13``, ``LXMessage.py`` "if self.desired_method == None"). That is
the right method (a link, and a delivery receipt BIC-2 reads as proof), but a
fork merge could change the default and every leg would change with it,
silently. So each construction states DIRECT, and this guard refuses one that
doesn't.

Known blind spot: an alias (``cls = LXMF.LXMessage; cls(...)``) or a
``getattr`` construction is invisible to this walk — none exist in src today.

Lab instruments under ``src/lab/`` are out of scope: they choose their own
methods as part of the experiment.

Identical in MeshForge and MeshAnchor.

Run: python3 -m pytest tests/test_lxmf_desired_method_explicit.py -v
"""

import ast
from pathlib import Path

SRC = Path(__file__).parent.parent / "src"


def _is_lxmessage(node) -> bool:
    return (isinstance(node, ast.Attribute) and node.attr == "LXMessage") or \
        (isinstance(node, ast.Name) and node.id == "LXMessage")


def _constructions():
    """(file, line, call) for each LXMessage(...) and each
    bounded_call(..., LXMF.LXMessage, ...) outside src/lab/."""
    for path in sorted(SRC.rglob("*.py")):
        if "lab" in path.relative_to(SRC).parts[:1]:
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Name) and node.func.id in (
                    "isinstance", "issubclass", "getattr", "hasattr"):
                continue   # a type check names the class; it builds nothing
            passed = list(node.args) + [k.value for k in node.keywords]
            if _is_lxmessage(node.func) or any(_is_lxmessage(a) for a in passed):
                yield path.relative_to(SRC), node.lineno, node


def test_there_are_constructions_to_judge():
    """The guard's own control: zero sites found means the walker is blind."""
    assert len(list(_constructions())) >= 4


def test_every_production_lxmessage_states_direct():
    missing = []
    for rel, line, call in _constructions():
        kw = {k.arg: k.value for k in call.keywords}
        val = kw.get("desired_method")
        # LXMessage.DIRECT, never any other class's DIRECT (RNS has one too)
        if not (isinstance(val, ast.Attribute) and val.attr == "DIRECT"
                and _is_lxmessage(val.value)):
            missing.append(f"{rel}:{line}")
    assert not missing, (
        "LXMessage built without desired_method=...LXMessage.DIRECT "
        f"(bridge matrix F12): {missing}")
