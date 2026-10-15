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
            "GRANT_CLI_SEARCH", "HTTP_SURFACE_UNIT", "WATCHDOG_UNIT",
            "ROLE_CATALOG", "PROVISIONER", "app_version", "expect_hostkey_of",
            "HOSTKEY_FIX")

APP_NAMES = ("meshforge", "meshanchor")


def _fold(n):
    """The string a node evaluates to when it is built only from constants —
    `"a" + "b"`, `"".join(["a", "b"])`, `b"..."` — else None. A lexical scan
    of single Constants misses an app name split across pieces (non-author
    review of 3f4c9996, planted: `+`, `.join` and bytes all read GREEN)."""
    if isinstance(n, ast.Constant) and isinstance(n.value, (str, bytes)):
        v = n.value
        return v.decode("utf-8", "replace") if isinstance(v, bytes) else v
    if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Add):
        a, b = _fold(n.left), _fold(n.right)
        return None if a is None or b is None else a + b
    if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr == "join" and len(n.args) == 1
            and isinstance(n.args[0], (ast.List, ast.Tuple))):
        sep = _fold(n.func.value)
        parts = [_fold(e) for e in n.args[0].elts]
        if sep is not None and None not in parts:
            return sep.join(parts)
    return None


def _code_string_literals(path: Path):
    """Strings in CODE — docstrings (a bare string statement) are prose and
    may name both apps; a string the program USES may not."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    prose = {id(n.value) for n in ast.walk(tree)
             if isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)}
    for n in ast.walk(tree):
        if id(n) in prose:
            continue
        s = _fold(n)
        if s is not None:
            yield n.lineno, s


def _app_values():
    """This app's own per-app VALUES and their basenames. One that names no
    app — `mini_dudeai_state.json` — is still wrong for the sibling if a
    shared module hardcodes it, and parity_check cannot see that: both copies
    are byte-identical, and one of them is wrong."""
    vals = set()
    for name in REQUIRED:
        v = getattr(app, name, None)
        for item in (v if isinstance(v, (tuple, list)) else (v,)):
            if isinstance(item, str):
                vals.add(item)
                vals.add(item.rstrip("/").rsplit("/", 1)[-1])
    return {v for v in vals if len(v) > 3}


def test_shared_modules_carry_no_app_name_literal():
    values = _app_values()
    hits = []
    for name in SHARED:
        for line, s in _code_string_literals(ROOT / "src" / "utils" / name):
            if any(a in s.lower() for a in APP_NAMES) or s in values:
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
    # The box judges its own role from ITS catalog through ITS provisioner;
    # the HTTP surface is this app's map, the watchdog is the ONE box organ
    # (MeshForge's) whichever app exports.
    assert app.HTTP_SURFACE_UNIT == f"{app.APP}-map"
    assert app.WATCHDOG_UNIT == "meshforge-watchdog"
    assert app.ROLE_CATALOG == Path(app.INSTALL_DIR) / "docs" / "fleet_roles.yaml"
    assert app.PROVISIONER == Path(app.INSTALL_DIR) / "scripts" / "provision_role.py"


def test_install_dir_matches_this_checkout_on_a_deployed_box():
    # On the fleet the repo IS the install dir; in CI/worktrees it is not,
    # so this only pins the NAME: the checkout's basename is the app.
    if os.path.realpath(ROOT) == app.INSTALL_DIR:
        assert ROOT.name == app.APP
