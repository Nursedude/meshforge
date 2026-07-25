"""``scripts/selftest.py`` core-dependency derivation (2026-07-24).

Regression pin for a drift found while running `apt autoremove`: selftest
carried its OWN hardcoded "core dependencies" list, and it had drifted from
``requirements/core.txt`` — the file the installer and CI actually consume —
in BOTH directions. It demanded ``flask`` and ``textual``, neither of which is
imported anywhere in the tree nor declared in any requirements file, while
never checking ``distro``, and it filed ``meshtastic`` (which core.txt DOES
declare) under "optional".

A core-dep check that fails a healthy box on a package MeshForge does not use
is worse than no check: it trains the operator to ignore the result. Same
defect class as the cron-verdict panel (honest_failure_modes #5 — two
consumers of one artifact carrying two constants), and the same cure: derive
from the owner instead of restating it.

These tests fail the moment someone re-hardcodes a dependency here, or adds a
line to core.txt that nothing checks. (They could not have run against the old
code at all — it had no derivation function to test, which is precisely how the
list drifted unnoticed.)
"""

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_selftest():
    """Import scripts/selftest.py by path (not importable as a package)."""
    spec = importlib.util.spec_from_file_location(
        "meshforge_selftest", REPO_ROOT / "scripts" / "selftest.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["meshforge_selftest"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def selftest():
    return _load_selftest()


def _core_txt_packages():
    """Package names declared in requirements/core.txt, parsed independently.

    Deliberately a SECOND implementation — if this and selftest's parser
    agreed only because they share code, the test would prove nothing.
    """
    import re
    text = (REPO_ROOT / "requirements" / "core.txt").read_text(encoding="utf-8")
    out = []
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        out.append(re.split(r"[<>=!~\[;\s]", line, 1)[0].strip().lower())
    return [p for p in out if p]


class TestDerivedFromRequirements:
    def test_core_list_matches_requirements_core_txt(self, selftest):
        """THE pin: the checked set IS the declared set. No more, no less."""
        derived = selftest.core_dependencies()
        assert derived is not None
        assert [pkg for _mod, pkg in derived] == _core_txt_packages()

    def test_declared_core_deps_are_actually_checked(self, selftest):
        """distro was declared for ages and never checked — catch that shape."""
        checked = {pkg for _mod, pkg in selftest.core_dependencies()}
        missing = set(_core_txt_packages()) - checked
        assert not missing, f"declared core dep(s) never checked: {sorted(missing)}"

    def test_no_undeclared_package_is_treated_as_core(self, selftest):
        """The flask/textual defect: failing a box on a package we don't ship."""
        checked = {pkg for _mod, pkg in selftest.core_dependencies()}
        extra = checked - set(_core_txt_packages())
        assert not extra, f"core check demands undeclared package(s): {sorted(extra)}"

    def test_the_specific_stale_entries_are_gone(self, selftest):
        """Named pin — these three are imported NOWHERE in src/."""
        checked = {pkg for _mod, pkg in selftest.core_dependencies()}
        checked |= {p for _m, p in selftest._CORE_FALLBACK}
        for dead in ("flask", "textual", "pygobject"):
            assert dead not in checked

    def test_every_core_module_actually_imports(self, selftest):
        """A declared core dep must be importable in THIS interpreter, or the
        box is genuinely broken — this is the check doing its real job."""
        for module, package in selftest.core_dependencies():
            assert importlib.util.find_spec(module) is not None, (
                f"core dep {package!r} (module {module!r}) not importable")


class TestRuntimeInterpreter:
    """Which interpreter gets checked — the services', not the invoker's.

    Observed 2026-07-24 on a collector box: `python3 scripts/selftest.py`
    reported rich + meshtastic "not installed" and exited 1, while the box was
    healthy — /opt/meshforge/venv had all five, and that venv is what
    meshforge-map's ExecStart execs.
    """

    def test_symlinked_venv_is_not_confused_with_its_base(self, selftest):
        """THE regression pin, and it is not hypothetical — the first cut of
        this fix used os.path.realpath, which collapses a venv's bin/python
        onto /usr/bin/python3.13 and made the checker silently report on the
        invoking interpreter while printing the venv's path."""
        assert selftest.same_interpreter(
            "/opt/meshforge/venv/bin/python", "/usr/bin/python3") is False

    def test_same_path_is_same_interpreter(self, selftest):
        assert selftest.same_interpreter(sys.executable, sys.executable) is True

    def test_relative_and_absolute_forms_agree(self, selftest):
        assert selftest.same_interpreter("/usr/bin/../bin/python3",
                                         "/usr/bin/python3") is True

    def test_runtime_python_reports_provenance(self, selftest):
        py, prov = selftest.runtime_python()
        assert isinstance(py, str) and py
        assert isinstance(prov, str) and prov, "must always say WHICH and WHY"

    def test_runtime_python_prefers_the_venv_when_present(self, selftest):
        py, prov = selftest.runtime_python()
        venv = REPO_ROOT / "venv" / "bin" / "python"
        no_venv = REPO_ROOT / ".no-venv"
        if venv.exists() and not no_venv.exists():
            assert py == str(venv), "venv exists but was not selected"
            assert "venv" in prov.lower()

    def test_unqueryable_interpreter_is_none_not_false(self, selftest, tmp_path):
        """Unobservable != absent. A bogus interpreter must not be scored as a
        missing dependency (which would fail a healthy box)."""
        bogus = str(tmp_path / "no_such_python")
        assert selftest.check_import_in(bogus, "sys", "sys") is None

    def test_cross_interpreter_check_actually_queries_that_python(self, selftest):
        """Positive control: a stdlib module answers True through the
        subprocess path, proving the path works end to end."""
        venv = REPO_ROOT / "venv" / "bin" / "python"
        if not venv.exists():
            pytest.skip("no venv on this box")
        assert not selftest.same_interpreter(str(venv), sys.executable)
        assert selftest.check_import_in(str(venv), "json", "json") is True
        assert selftest.check_import_in(
            str(venv), "definitely_not_a_real_module_xyz", "ghost") is False


class TestParser:
    def _write(self, tmp_path, body):
        r = tmp_path / "requirements"
        r.mkdir(exist_ok=True)
        (r / "core.txt").write_text(body, encoding="utf-8")
        return tmp_path

    def test_strips_versions_extras_and_comments(self, selftest, tmp_path):
        root = self._write(tmp_path, (
            "# leading comment\n\n-r other.txt\n"
            "rich>=13.0.0\npyyaml==6.0  # inline\nsome-pkg[extra]~=1.2\n"))
        assert selftest.core_dependencies(root) == [
            ("rich", "rich"), ("yaml", "pyyaml"), ("some_pkg", "some-pkg")]

    def test_maps_package_name_to_import_name(self, selftest, tmp_path):
        root = self._write(tmp_path, "pyyaml\npyserial\npaho-mqtt\n")
        assert [m for m, _p in selftest.core_dependencies(root)] == [
            "yaml", "serial", "paho.mqtt.client"]

    def test_missing_file_returns_none_not_a_guess(self, selftest, tmp_path):
        """Unobservable != 'no core deps'. None makes the caller SAY so."""
        assert selftest.core_dependencies(tmp_path) is None

    def test_empty_file_returns_none_rather_than_empty_pass(self, selftest, tmp_path):
        """An empty list would render as 'all core deps present' — the exact
        degraded-value-overlap this codebase keeps re-learning."""
        root = self._write(tmp_path, "# only comments\n\n-r other.txt\n")
        assert selftest.core_dependencies(root) is None

    def test_fallback_never_wider_than_the_file_it_replaces(self, selftest):
        """Degrading must narrow, never invent. Every fallback entry has to be
        a real declared core dep, or the degraded path could fail a good box."""
        declared = set(_core_txt_packages())
        for _mod, pkg in selftest._CORE_FALLBACK:
            assert pkg in declared, (
                f"fallback lists {pkg!r}, which core.txt does not declare")
