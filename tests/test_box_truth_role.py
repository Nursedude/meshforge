"""The box judges its OWN role (NOC read grant session 3, step 2, 2026-10-14).

Until this step the CENTER decided whether a target was supposed to run a
map or a watchdog, by loading MeshForge's provisioner + role catalog against
the target's spooled ``deployment`` section. A MeshAnchor center reading a
MeshForge box then needed MeshForge's catalog on the center. The box is the
one party that holds its own catalog, its own role and its own unit names,
so the export now carries a ``role`` section: the declared role, the
resolved ``services`` map it rests on, and the two verdicts the fleet cell
consumes (``expected.http_surface`` / ``expected.watchdog``). The center
reads verdicts; it never loads a catalog.

Shared with MeshAnchor byte-for-byte (parity BYTE_IDENTICAL), so every
catalog here is PLANTED through ``Sources`` and keyed on the seam's unit
names — nothing below depends on either app's real catalog. The MeshForge
wiring against its real catalog lives in ``test_box_truth_spool_wiring.py``.
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from utils import box_truth as bt  # noqa: E402
from utils import box_truth_app as app  # noqa: E402
from utils import box_truth_collect as bc  # noqa: E402

#: A provisioner with the real one's API: load_roles(path) -> catalog,
#: resolve_role(catalog, name) -> role def (KeyError when unknown).
_PROVISIONER = """
import json
from pathlib import Path

def load_roles(path):
    data = json.loads(Path(path).read_text())
    if not isinstance(data, dict) or "roles" not in data:
        raise ValueError(f"{path}: missing top-level 'roles'")
    return data

def resolve_role(catalog, role):
    return catalog["roles"][role]
"""

_EXITING_PROVISIONER = """
import sys
print("ERROR: PyYAML required", file=sys.stderr)
sys.exit(2)
"""


def _plant(tmp_path, roles, provisioner=_PROVISIONER):
    prov = tmp_path / "scripts" / "prov.py"
    prov.parent.mkdir(parents=True, exist_ok=True)
    prov.write_text(provisioner)
    cat = tmp_path / "docs" / "roles.json"
    cat.parent.mkdir(parents=True, exist_ok=True)
    cat.write_text(json.dumps({"roles": roles}))
    return prov, cat


def _home(tmp_path, deployment):
    h = tmp_path / "home"
    d = h / ".config" / app.CONFIG_DIRNAME
    d.mkdir(parents=True, exist_ok=True)
    if deployment is not None:
        (d / "deployment.json").write_text(json.dumps(deployment))
    return h


def _src(tmp_path, deployment, roles, provisioner=_PROVISIONER, **kw):
    prov, cat = _plant(tmp_path, roles, provisioner)
    base = dict(home=_home(tmp_path, deployment), env={},
                watchdog_path=tmp_path / "wd.json",
                proc_tcp=(str(tmp_path / "no-proc"),),
                dev_globs=(str(tmp_path / "nodev*"),),
                runner=lambda *a, **k: (_ for _ in ()).throw(OSError("no subprocess")),
                http_get=lambda url: None,
                provisioner=prov, catalog=cat)
    base.update(kw)
    return bc.Sources(**base)


MAPLESS = {"services": {app.HTTP_SURFACE_UNIT: "disabled",
                        app.WATCHDOG_UNIT: "enabled", "rnsd": "enabled"}}
FIELD = {"services": {app.HTTP_SURFACE_UNIT: "absent",
                      app.WATCHDOG_UNIT: "absent"}}
FULL = {"services": {app.HTTP_SURFACE_UNIT: "enabled",
                     app.WATCHDOG_UNIT: "enabled"}}


def test_role_is_an_allowlisted_section():
    assert "role" in bt.SECTIONS


def test_the_box_judges_its_own_role(tmp_path):
    src = _src(tmp_path, {"role": "mapless"}, {"mapless": MAPLESS})
    sections, errors = bc.collect_sections(src)
    role = sections["role"]
    assert role["app"] == app.APP
    assert role["name"] == "mapless"
    assert role["catalog"] == str(src.catalog)
    assert role["services"] == MAPLESS["services"]
    assert role["expected"] == {"http_surface": False, "watchdog": True}
    assert role["why"] is None
    assert "role" not in errors


@pytest.mark.parametrize("roles, want", [
    ({"r": FIELD}, {"http_surface": False, "watchdog": False}),
    ({"r": FULL}, {"http_surface": True, "watchdog": True}),
    ({"r": {"services": {app.HTTP_SURFACE_UNIT: " Disabled "}}},
     {"http_surface": False, "watchdog": None}),
])
def test_declared_states_map_to_verdicts(tmp_path, roles, want):
    """disabled/absent = not expected; anything else declared = expected; a
    unit the role is SILENT about stays None — silence is not a declaration."""
    s, _e = bc.collect_sections(_src(tmp_path, {"role": "r"}, roles))
    assert s["role"]["expected"] == want


@pytest.mark.parametrize("deployment, why_has", [
    (None, "deployment.json"),            # no declaration at all
    ({}, "no role"),
    ({"role": ""}, "no role"),
    ({"role": 7}, "no role"),
    ({"role": "unknown-role"}, "unknown-role"),
    ({"role": "no-services"}, "services"),
])
def test_undecidable_is_none_with_the_reason_never_false(tmp_path, deployment, why_has):
    """THE anti-silence guard, now at the source. False is what stops a dark
    box tainting the fleet verdict, so anything the box cannot decide must
    stay None — and say why, so the center can show it."""
    roles = {"mapless": MAPLESS, "no-services": {"board_tier": "x"}}
    s, errors = bc.collect_sections(_src(tmp_path, deployment, roles))
    role = s["role"]
    assert role["expected"] == {"http_surface": None, "watchdog": None}
    assert role["services"] is None
    assert why_has in role["why"]
    assert "role" not in errors, "an absent or silent declaration is not an error"


def test_a_provisioner_that_exits_at_import_is_an_error_not_a_crash(tmp_path):
    """The real provisioner calls sys.exit(2) when PyYAML is missing.
    SystemExit is not an Exception: unhandled it would kill the whole export
    mid-document. It must become THIS section's error and nothing else's."""
    s, errors = bc.collect_sections(
        _src(tmp_path, {"role": "mapless"}, {"mapless": MAPLESS},
             provisioner=_EXITING_PROVISIONER))
    assert s["role"]["expected"] == {"http_surface": None, "watchdog": None}
    assert "role" in errors and "exit" in errors["role"] and "2" in errors["role"]
    assert isinstance(s["clock"]["now"], int), "the rest of the document survived"


def test_a_missing_provisioner_or_catalog_is_an_error(tmp_path):
    src = _src(tmp_path, {"role": "mapless"}, {"mapless": MAPLESS})
    src.provisioner = tmp_path / "nowhere.py"
    s, errors = bc.collect_sections(src)
    assert s["role"]["expected"] == {"http_surface": None, "watchdog": None}
    assert "role" in errors and "nowhere.py" in errors["role"]

    src = _src(tmp_path, {"role": "mapless"}, {"mapless": MAPLESS})
    src.catalog.unlink()
    s, errors = bc.collect_sections(src)
    assert s["role"]["expected"] == {"http_surface": None, "watchdog": None}
    assert "role" in errors


def test_catalog_values_are_carried_as_strings(tmp_path):
    """YAML may hand back non-strings; the wire carries strings, and a
    non-string state is undecidable for that unit, never 'expected'."""
    roles = {"r": {"services": {app.HTTP_SURFACE_UNIT: True, app.WATCHDOG_UNIT: None}}}
    s, _e = bc.collect_sections(_src(tmp_path, {"role": "r"}, roles))
    assert s["role"]["services"] == {app.HTTP_SURFACE_UNIT: "True",
                                     app.WATCHDOG_UNIT: "None"}
    assert s["role"]["expected"] == {"http_surface": None, "watchdog": None}


def test_role_rides_the_framed_export(tmp_path):
    src = _src(tmp_path, {"role": "mapless"}, {"mapless": MAPLESS})
    doc = bt.parse_framed(bc.run_export("SHA256:" + "A" * 43, src))
    assert doc["sections"]["role"]["expected"] == {"http_surface": False,
                                                   "watchdog": True}


# ── non-author read 2026-10-15 (Opus 5.5) ────────────────────────────────
# The fake above never resolves `inherits`, so it could not see that the real
# provisioner raises KeyError(<PARENT>) for a missing parent — the section
# then said the box's OWN role was "not in the catalog" and reported no error.

_INHERITING_PROVISIONER = _PROVISIONER.replace(
    "def resolve_role(catalog, role):\n    return catalog[\"roles\"][role]",
    "def resolve_role(catalog, role):\n"
    "    roles = catalog[\"roles\"]\n"
    "    if role not in roles:\n"
    "        raise KeyError(role)\n"
    "    node = dict(roles[role])\n"
    "    if node.get(\"inherits\"):\n"
    "        parent = resolve_role(catalog, node[\"inherits\"])\n"
    "        node[\"services\"] = dict(parent.get(\"services\", {}), **node.get(\"services\", {}))\n"
    "    return node")


def test_a_missing_parent_is_a_catalog_error_not_a_missing_role(tmp_path):
    roles = {"child": {"inherits": "ghostparent", "services": FULL["services"]}}
    sec, err = bc._role(_src(tmp_path, {"role": "child"}, roles, _INHERITING_PROVISIONER))
    assert sec["expected"] == {"http_surface": None, "watchdog": None}
    assert "not in the catalog" not in sec["why"], sec["why"]
    assert err and "ghostparent" in err


def test_an_undecidable_unit_names_itself_in_why(tmp_path):
    roles = {"x": {"services": {app.HTTP_SURFACE_UNIT: 1, app.WATCHDOG_UNIT: "enabled"}}}
    sec, err = bc._role(_src(tmp_path, {"role": "x"}, roles))
    assert sec["expected"] == {"http_surface": None, "watchdog": True}
    assert err is None
    assert sec["why"] and app.HTTP_SURFACE_UNIT in sec["why"], sec["why"]
