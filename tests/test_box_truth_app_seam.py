"""The per-app seam of the NOC read grant (session 3, 2026-10-14).

The five ``utils/box_truth*.py`` modules are shared with MeshAnchor
byte-for-byte (``scripts/parity_check.py`` BYTE_IDENTICAL). Everything that
must differ per app — install dir, config dir name, unit glob, the center's
key name, where this app's watchdog / mini / ingress state lives — lives in
ONE per-app module, ``utils/box_truth_app.py``, whose NAMES both repos share
and whose VALUES differ. A shared module that grows an app-name literal is a
module that can no longer be byte-identical: this test fails first.
"""
import ast
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from utils import box_truth_app as app  # noqa: E402

SHARED = ("box_truth.py", "box_truth_reader.py", "box_truth_grants.py",
          "box_truth_center.py", "box_truth_collect.py")

#: The contract: every name a shared module (or a shared test) reads.
REQUIRED = ("APP", "INSTALL_DIR", "CONFIG_DIRNAME", "WATCHDOG_PATH",
            "UNIT_GLOB", "MINI_STATE_RELPATH", "NOC_KEY_BASENAME",
            "GRANT_CLI_SEARCH", "app_version")

APP_NAMES = ("meshforge", "meshanchor")


def _code_string_literals(path: Path):
    """String constants in CODE — docstrings (a bare string statement) are
    prose and may name both apps; a literal the program USES may not."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    prose = {id(n.value) for n in ast.walk(tree)
             if isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)}
    for n in ast.walk(tree):
        if (isinstance(n, ast.Constant) and isinstance(n.value, str)
                and id(n) not in prose):
            yield n.lineno, n.value


def test_shared_modules_carry_no_app_name_literal():
    hits = []
    for name in SHARED:
        for line, s in _code_string_literals(ROOT / "src" / "utils" / name):
            if any(a in s.lower() for a in APP_NAMES):
                hits.append(f"{name}:{line}: {s!r}")
    assert not hits, ("app-specific literal in a byte-identical module — move "
                      "it to utils/box_truth_app.py:\n  " + "\n  ".join(hits))


def test_app_module_exports_the_contract():
    missing = [n for n in REQUIRED if not hasattr(app, n)]
    assert not missing, f"utils/box_truth_app.py lacks {missing}"


def test_app_module_values_are_this_app():
    assert app.APP in APP_NAMES
    assert app.INSTALL_DIR == f"/opt/{app.APP}"
    assert app.CONFIG_DIRNAME == app.APP
    # MeshAnchor has a bare `meshanchor.service` (the orchestrator), so its
    # glob is `meshanchor*.service`; MeshForge has none. Pin the family only.
    assert app.UNIT_GLOB.startswith(app.APP) and app.UNIT_GLOB.endswith(".service")
    assert app.NOC_KEY_BASENAME == f"{app.APP}_noc_ed25519"
    # A center pushes to a box running EITHER app (or both): it tries its
    # own app's grant CLI first, then the sibling's — never a third dir.
    assert app.GRANT_CLI_SEARCH[0] == app.INSTALL_DIR
    assert sorted(app.GRANT_CLI_SEARCH) == ["/opt/meshanchor", "/opt/meshforge"]
    assert isinstance(app.app_version(), str) and app.app_version()


def test_install_dir_matches_this_checkout_on_a_deployed_box():
    # On the fleet the repo IS the install dir; in CI/worktrees it is not,
    # so this only pins the NAME: the checkout's basename is the app.
    if os.path.realpath(ROOT) == app.INSTALL_DIR:
        assert ROOT.name == app.APP
