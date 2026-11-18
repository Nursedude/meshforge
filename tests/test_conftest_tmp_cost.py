"""CI-1 (2026-11-18): no per-test autouse fixture may pay pytest's basetemp scan.

pytest's ``mktemp`` — called by ``tmp_path_factory.mktemp`` AND by every
``tmp_path`` / ``tmpdir`` request — picks the next number by SCANNING every
entry of the shared basetemp root (``make_numbered_dir`` → ``find_prefixed``).
An AUTOUSE fixture that pays it for every test adds an entry per test and
rescans all earlier ones, so the suite's cost becomes quadratic in its size.
Measured 11-18 (cProfile diff, ``plans/ci_suite_cost_2026_11_18.md``):
``test_rf.py`` 12.8 ms/test alone vs 110.5 ms/test after a 300-file prefix,
+98.6 ms of it in ``mktemp`` from two autouse fixtures; basetemp 27,780 entries.

Paid three times (09-01 delivery counters, 09-10 ``_absent_posture_path``,
11-18 broker store + node cache) before it became this gate. The gate follows
the FIXTURE GRAPH across EVERY ``conftest.py`` under tests/ (review 11-18:
a body-only check missed a helper fixture, a ``tmp_path`` request, and the
other conftests). Module/class/package/session scope is allowed — it pays once
per scope, not per test. A test MODULE's own autouse fixture taking ``tmp_path``
pays only for that module's tests (the same as those tests requesting it), so
it is out of this gate's scope on purpose.
"""
from __future__ import annotations

import ast
import glob
import os

from tests import conftest

_TESTS = os.path.dirname(os.path.abspath(__file__))
_PER_TEST_TMP_ARGS = frozenset({"tmp_path", "tmpdir"})
_FACTORY_ARGS = frozenset({"tmp_path_factory", "tmpdir_factory"})


def _fixture_kwargs(dec):
    """kwargs of a ``@pytest.fixture`` / ``@fixture`` decorator, or None."""
    target = dec.func if isinstance(dec, ast.Call) else dec
    name = (target.attr if isinstance(target, ast.Attribute)
            else target.id if isinstance(target, ast.Name) else None)
    if name != "fixture":
        return None
    return {kw.arg: kw.value for kw in dec.keywords} if isinstance(dec, ast.Call) else {}


def _fixtures(sources):
    """{name: {"autouse", "per_test", "args", "direct"}} over conftest sources."""
    out = {}
    for src in sources:
        for node in ast.walk(ast.parse(src)):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list:
                kw = _fixture_kwargs(dec)
                if kw is None:
                    continue
                auto = kw.get("autouse")
                scope = kw.get("scope")
                scope_v = scope.value if isinstance(scope, ast.Constant) else "function"
                args = {a.arg for a in node.args.args + node.args.kwonlyargs}
                out[node.name] = {
                    "autouse": isinstance(auto, ast.Constant) and auto.value is True,
                    "per_test": scope_v == "function",
                    "args": args,
                    # taking a factory per test can only mean using it per test
                    # (directly or through a plain helper): the scan is paid
                    "direct": bool(args & (_PER_TEST_TMP_ARGS | _FACTORY_ARGS)),
                }
    return out


def _per_test_scan_payers(fixtures):
    """Function-scoped fixtures that pay the scan, directly or via a dependency."""
    memo = {}

    def pays(name, stack=()):
        if name in memo:
            return memo[name]
        f = fixtures.get(name)
        if f is None or not f["per_test"] or name in stack:
            return False
        memo[name] = f["direct"] or any(pays(a, stack + (name,)) for a in f["args"])
        return memo[name]
    return {n for n in fixtures if pays(n)}


def _offenders(sources):
    fx = _fixtures(sources)
    payers = _per_test_scan_payers(fx)
    return sorted(n for n in payers if fx[n]["autouse"])


def _conftest_sources():
    paths = sorted(glob.glob(os.path.join(_TESTS, "**", "conftest.py"), recursive=True))
    assert paths, "found no conftest.py — the gate would pass on nothing"
    return paths, [open(p, encoding="utf-8").read() for p in paths]


def test_no_per_test_autouse_fixture_pays_the_basetemp_scan():
    paths, sources = _conftest_sources()
    bad = _offenders(sources)
    assert not bad, (
        f"autouse fixture(s) {bad} pay pytest's basetemp scan for EVERY test "
        "(tmp_path / tmpdir / tmp_path_factory, directly or via a fixture) — "
        "quadratic suite cost (CI-1). Request `_per_test_isolation_dir` instead "
        f"(scanned: {[os.path.relpath(p, _TESTS) for p in paths]}).")


def test_the_gate_catches_every_shape_it_exists_for():
    """A gate that never failed is not evidence: plant each shape (review 11-18
    #1/#2/#4), across two conftest sources (#3)."""
    first = '''
import pytest, tempfile
@pytest.fixture(autouse=True)
def direct_mktemp(tmp_path_factory):
    tmp_path_factory.mktemp("x")
@pytest.fixture(autouse=True)
def takes_tmp_path(tmp_path):
    return tmp_path
@pytest.fixture
def helper_fixture(tmp_path_factory):
    return tmp_path_factory.mktemp("h")
@pytest.fixture(autouse=True)
def via_helper_fixture(helper_fixture):
    return helper_fixture
def plain_helper(factory):
    return factory.mktemp("p")
@pytest.fixture(autouse=True, scope="function")
def via_plain_helper(tmp_path_factory):
    return plain_helper(tmp_path_factory)
@pytest.fixture(scope="session")
def session_root(tmp_path_factory):
    return tmp_path_factory.mktemp("s")
@pytest.fixture(autouse=True)
def ok_uses_session_root(session_root):
    return session_root
@pytest.fixture(autouse=True, scope="module")
def ok_module_scope(tmp_path_factory):
    return tmp_path_factory.mktemp("m")
@pytest.fixture(autouse=True)
def ok_tempfile(monkeypatch):
    return tempfile.mktemp()
@pytest.fixture
def ok_not_autouse(tmp_path):
    return tmp_path
'''
    second = '''
import pytest
@pytest.fixture(autouse=True)
def other_conftest_tmpdir(tmpdir):
    return tmpdir
'''
    assert _offenders([first, second]) == [
        "direct_mktemp", "other_conftest_tmpdir", "takes_tmp_path",
        "via_helper_fixture", "via_plain_helper"]


def test_fresh_isolation_dirs_are_distinct_new_empty_and_deterministic(tmp_path):
    """Review #9: a FRESH root numbers from 0, so shard naming is pinned exactly."""
    root = tmp_path / "root"
    root.mkdir()
    dirs = [conftest._fresh_isolation_dir(root) for _ in range(2500)]
    assert len(set(dirs)) == 2500
    assert [d.relative_to(root).as_posix() for d in (dirs[0], dirs[999], dirs[1000], dirs[2499])] == [
        "s0000/t000", "s0000/t999", "s0001/t000", "s0002/t499"]
    for d in dirs[:50]:
        assert d.is_dir() and not any(d.iterdir())
        (d / "written.json").write_text("{}")   # a test writing must not leak to the next
    assert max(len(os.listdir(p)) for p in root.iterdir()) <= 1000


def test_a_taken_name_is_skipped_never_reused(tmp_path):
    """Review #6: uniqueness must not rest on one process-wide counter — a name
    already on disk (a second conftest module object, a rerun) is skipped."""
    root = tmp_path / "root"
    (root / "s0000" / "t000").mkdir(parents=True)
    (root / "s0000" / "t000" / "dirty.json").write_text("{}")
    d = conftest._fresh_isolation_dir(root)
    assert d.relative_to(root).as_posix() == "s0000/t001"
    assert not any(d.iterdir())


def test_both_isolation_fixtures_share_one_per_test_dir():
    """Review #5: one directory per test, not one per fixture."""
    fx = _fixtures([open(os.path.join(_TESTS, "conftest.py"), encoding="utf-8").read()])
    for name in ("_isolate_broker_profile_store", "_isolate_node_cache_files"):
        assert "_per_test_isolation_dir" in fx[name]["args"], name
