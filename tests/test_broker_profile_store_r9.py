"""F8 v2 round 9 — fixes for the r8 non-author review (Fable 5.1, by running).

Record: ``.claude/plans/mqtt_f8_state_model_2026_11_02.md`` § review r8.
Operator 2026-11-04: "through R9 now then review as you advise".

- #1 Activate over a refused save left the screen saying "Active: Yes"
     (flags flipped in the caller's dict BEFORE the save). Pre-existing.
- #2 ``active_profile`` was derived from LOADED profiles only: a delete
     while the active profile is unparsed wrote ``null`` and undid r8 F10.
- #3 a defaults-write failure over an ``ok`` EMPTY store read "(0)", no ⚠.
- #4 an untraversable config dir raised out of ``path.exists()`` — the
     ``denied`` state was bypassed.
- #5 the main menu said "Active: None" for an unloaded active profile.
- #6 a symlinked store was replaced by a regular file (target kept old bytes).
- #7 Reset of a DIRECTORY store chmodded the backup dir 0600 (unenterable).
"""
import json
import os
import stat
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src', 'launcher_tui'))
sys.path.insert(0, os.path.dirname(__file__))

from handler_test_utils import make_handler_context  # noqa: E402
import handlers.broker  # noqa: E402,F401  (patched by _host_pinned)
from utils import broker_profiles as bp  # noqa: E402
from utils.broker_profiles import create_custom_profile, load_profiles  # noqa: E402

ROOT = os.geteuid() == 0


@pytest.fixture(autouse=True)
def _host_pinned():
    """No verdict here may depend on this host's mosquitto (CI 41809571004),
    and no test may reach the real installer."""
    with patch("handlers.broker.check_mosquitto_installed", return_value=(True, "ok")), \
            patch("handlers.broker.check_mosquitto_running", return_value=(True, "ok")), \
            patch("handlers.broker.BrokerHandler._install_mosquitto",
                  side_effect=AssertionError("a test reached the REAL mosquitto installer")):
        yield


@pytest.fixture
def store(tmp_path):
    path = tmp_path / ".config" / "meshforge" / "broker_profiles.json"
    path.parent.mkdir(parents=True)
    with patch("utils.broker_profiles._get_profiles_path", return_value=path):
        yield path


def _raw(name, active=False, **extra):
    p = create_custom_profile(name=name, host=f"{name}.example", port=1883)
    p.is_active = active
    d = bp.asdict(p)
    d.update(extra)
    return d


def _write(path, profiles, active=None):
    path.write_text(json.dumps({"profiles": profiles, "active_profile": active}))


def _tui(menu=(), inputs=(), yes=()):
    from handlers.broker import BrokerHandler
    ctx = make_handler_context()
    ctx.dialog._menu_returns = list(menu)
    ctx.dialog._inputbox_returns = list(inputs)
    ctx.dialog._yesno_returns = list(yes)
    h = BrokerHandler()
    h.set_context(ctx)
    return h, ctx


def _menus(ctx):
    return [c[1] for c in ctx.dialog.calls if c[0] == "menu"]


# ── #1 a refused activate changes nothing the screen shows ─────────────────

@pytest.mark.skipif(ROOT, reason="root writes a 0500 dir")
def test_1_refused_activate_leaves_flags_and_screen_unchanged(store):
    _write(store, {"a": _raw("a"), "b": _raw("b", active=True)}, active="b")
    profiles = load_profiles()
    os.chmod(store.parent, 0o500)
    try:
        assert bp.set_active_profile("a", profiles) is False
    finally:
        os.chmod(store.parent, 0o700)
    assert profiles["a"].is_active is False and profiles["b"].is_active is True


@pytest.mark.skipif(ROOT, reason="root writes a 0500 dir")
def test_1_detail_screen_never_says_active_after_a_refused_save(store):
    _write(store, {"a": _raw("a"), "b": _raw("b", active=True)}, active="b")
    profiles = load_profiles()
    h, ctx = _tui(menu=["activate", "back"])
    os.chmod(store.parent, 0o500)
    try:
        h._profile_detail_menu("a", profiles)
    finally:
        os.chmod(store.parent, 0o700)
    assert "Save Failed" in [c[1][0] for c in ctx.dialog.calls if c[0] == "msgbox"]
    assert "Active: No" in _menus(ctx)[1][1]


def test_1_control_activate_that_saves_flips_flags(store):
    _write(store, {"a": _raw("a"), "b": _raw("b", active=True)}, active="b")
    profiles = load_profiles()
    assert bp.set_active_profile("a", profiles) is True
    assert profiles["a"].is_active is True and profiles["b"].is_active is False
    assert json.loads(store.read_text())["active_profile"] == "a"


# ── #2 the active key survives when the active profile is the unloaded one ─

def test_2_delete_keeps_an_unloaded_active_profile_active(store):
    _write(store, {"club": _raw("club", active=True, future_field=1), "x": _raw("x")},
           active="club")
    profiles = load_profiles()
    del profiles["x"]
    assert bp.save_profiles(profiles) is True
    data = json.loads(store.read_text())
    assert data["active_profile"] == "club"
    assert data["profiles"]["club"]["is_active"] is True
    st = bp.profile_store_status()
    assert st.active == "club" and "club" in st.skipped


# ── #3 an empty list is never shown as healthy ─────────────────────────────
# r10 superseded the inference: nothing auto-writes, so an empty store says
# "No broker profiles" (pinned in test_broker_profile_store_r10.py).

@pytest.mark.skipif(ROOT, reason="root writes a 0500 dir")
def test_3_empty_ok_store_is_named_empty_and_nothing_written(store):
    store.write_text(json.dumps({"profiles": {}}))
    before = store.read_bytes()
    os.chmod(store.parent, 0o500)
    try:
        h, ctx = _tui(menu=["profiles", "back", "back"])
        h._broker_menu()
    finally:
        os.chmod(store.parent, 0o700)
    for m in _menus(ctx):
        assert "No broker profiles" in m[1], m[0]
    assert store.read_bytes() == before


# ── #4 an untraversable config dir is `denied`, never a crash ──────────────

@pytest.mark.skipif(ROOT, reason="root traverses a 0000 dir")
def test_4_untraversable_dir_is_denied_and_the_menu_opens(store):
    _write(store, {"a": _raw("a")})
    os.chmod(store.parent, 0o000)
    try:
        assert bp.profile_store_status().state == "denied"
        assert load_profiles() == {}
        assert bp.save_profiles({}) is False
        h, ctx = _tui(menu=["back"])
        h._broker_menu()
        (menu,) = _menus(ctx)
        assert "NOT ACCESSIBLE" in menu[1]
        assert "reset" not in [k for k, _ in menu[2]]
    finally:
        os.chmod(store.parent, 0o700)
    assert list(json.loads(store.read_text())["profiles"]) == ["a"]


# ── #5 the main menu names an unloaded active profile ──────────────────────

def test_5_main_menu_names_the_unloaded_active_profile(store):
    _write(store, {"club": _raw("club", active=True, future_field=1), "x": _raw("x")},
           active="club")
    h, ctx = _tui(menu=["back"])
    h._broker_menu()
    (menu,) = _menus(ctx)
    label = dict(menu[2])["profiles"]
    assert "club" in label and "did not load" in label
    assert "None" not in label


# ── #6 a symlinked store is written THROUGH the link ───────────────────────

def test_6_symlinked_store_is_written_through_the_link(store, tmp_path):
    real = tmp_path / "elsewhere" / "profiles.json"
    real.parent.mkdir()
    _write(real, {"old": _raw("old", password="OLDPASS")})
    store.symlink_to(real)
    profiles = load_profiles()
    profiles["new"] = create_custom_profile(name="new", host="h", port=1883)
    assert bp.save_profiles(profiles) is True
    assert store.is_symlink()
    assert sorted(json.loads(real.read_text())["profiles"]) == ["new", "old"]


# ── #7 a reset directory backup stays enterable by its owner ───────────────

def test_7_reset_directory_store_backup_is_0700(store):
    store.mkdir()
    (store / "inner.txt").write_text("x")
    os.chmod(store, 0o775)
    backup = bp.reset_unreadable_profile_store()
    assert backup is not None
    assert stat.S_IMODE(backup.stat().st_mode) == 0o700
    assert (backup / "inner.txt").read_text() == "x"
