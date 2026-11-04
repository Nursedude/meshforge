"""F8 v2 round 7 — the broker profile STORE never destroys what it could not read.

Record: ``.claude/plans/mqtt_f8_state_model_2026_11_02.md`` § "FOUND during
r6 — profile-store WIPE". Operator rulings 2026-11-03 (all three recs):

1. A profile on disk that fails to parse (unknown field from a newer
   version, missing field) is CARRIED THROUGH a save untouched — the
   operator never saw it, so its absence from the save is not a deletion.
2. An UNREADABLE store (bad JSON, wrong shape, OSError) refuses every save;
   the TUI says so and offers Reset, which keeps the file as
   ``broker_profiles.json.unreadable.<n>`` — never deleted.
3. The store holds passwords: written fsync'd with a unique temp name at
   mode 0600 (was a fixed ``.tmp``, no fsync, umask mode — 0664 live).

Pre-existing defect: ``93727ab6`` (2026-02-09).
"""
import json
import os
import stat
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from utils import broker_profiles as bp  # noqa: E402
from utils.broker_profiles import (  # noqa: E402
    create_custom_profile, load_profiles, save_profiles,
)


def _good(name, is_active=False, **kw):
    prof = create_custom_profile(name=name, host=f"{name}.example", port=1883, **kw)
    prof.is_active = is_active
    return bp.asdict(prof)


@pytest.fixture
def store(tmp_path):
    path = tmp_path / ".config" / "meshforge" / "broker_profiles.json"
    path.parent.mkdir(parents=True)
    with patch("utils.broker_profiles._get_profiles_path", return_value=path):
        yield path


def _write(path, profiles, active=None):
    path.write_text(json.dumps({"profiles": profiles, "active_profile": active}))


def _on_disk(path):
    return json.loads(path.read_text())["profiles"]


# ── control: the healthy path still saves and deletes ──────────────────────

def test_control_healthy_save_adds_and_deletes(store):
    _write(store, {"a": _good("a"), "b": _good("b")})
    profiles = load_profiles()
    del profiles["b"]                       # a deletion the operator SAW
    profiles["c"] = create_custom_profile(name="c", host="c.example", port=1883)
    assert save_profiles(profiles) is True
    assert sorted(_on_disk(store)) == ["a", "c"]


def test_control_absent_store_saves(store):
    assert not store.exists()
    assert save_profiles({"a": create_custom_profile(
        name="a", host="h", port=1883)}) is True
    assert list(_on_disk(store)) == ["a"]


# ── ruling 1: an unparsed profile is carried through ───────────────────────

@pytest.mark.parametrize("mangle", [
    lambda d: d.update(future_field=1),     # written by a newer version
    lambda d: d.pop("host"),                # missing a required field
], ids=["unknown_field", "missing_field"])
def test_unparsed_profile_survives_a_save(store, mangle):
    other = _good("other", username="u", password="keepme")
    mangle(other)
    _write(store, {"club": _good("club"), "other": other})
    profiles = load_profiles()
    assert "other" not in profiles           # still not loadable — honest
    profiles["club"].is_active = True
    assert save_profiles(profiles) is True
    disk = _on_disk(store)
    assert disk["other"] == other            # byte-for-byte the raw entry
    assert sorted(disk) == ["club", "other"]


def test_carried_profile_never_doubles_the_active_flag(store):
    other = _good("other", is_active=True)
    other["future_field"] = 1
    _write(store, {"club": _good("club"), "other": other}, active="other")
    profiles = load_profiles()
    profiles["club"].is_active = True
    assert save_profiles(profiles) is True
    data = json.loads(store.read_text())
    assert data["active_profile"] == "club"
    assert data["profiles"]["other"]["is_active"] is False


def test_status_names_the_skipped_profiles(store):
    other = _good("other")
    other["future_field"] = 1
    _write(store, {"club": _good("club"), "other": other})
    st = bp.profile_store_status()
    assert st.state == "ok"
    assert st.skipped == ["other"]


# ── ruling 2: an unreadable store refuses every save ───────────────────────

@pytest.mark.parametrize("content", [
    "",                                      # zero-byte power-loss corpse
    '{"profiles": {"a": ',                   # truncated
    "[]",                                    # wrong shape (top level)
    '{"profiles": []}',                      # wrong shape (profiles)
], ids=["zero_byte", "truncated", "list_top", "list_profiles"])
def test_unreadable_store_refuses_save_and_keeps_bytes(store, content):
    store.write_text(content)
    assert load_profiles() == {}             # load stays crash-free
    assert bp.profile_store_status().state == "unreadable"
    assert save_profiles({"new": create_custom_profile(
        name="new", host="h", port=1883)}) is False
    assert store.read_text() == content      # nothing destroyed


def test_reset_keeps_the_unreadable_file_as_a_backup(store):
    store.write_text('{"profiles": {"a": ')
    backup = bp.reset_unreadable_profile_store()
    assert backup is not None and backup.name == "broker_profiles.json.unreadable.1"
    assert backup.read_text() == '{"profiles": {"a": '
    assert not store.exists()
    assert bp.profile_store_status().state == "absent"
    store.write_text("garbage")
    assert bp.reset_unreadable_profile_store().name.endswith(".unreadable.2")


def test_reset_refuses_a_readable_store(store):
    _write(store, {"a": _good("a")})
    assert bp.reset_unreadable_profile_store() is None
    assert list(_on_disk(store)) == ["a"]


# ── ruling 3: the write is durable and private ─────────────────────────────

def test_saved_store_is_mode_0600(store):
    store.write_text(json.dumps({"profiles": {}}))
    os.chmod(store, 0o664)                   # the live mode measured 11-03
    assert save_profiles({"a": create_custom_profile(
        name="a", host="h", port=1883)}) is True
    assert stat.S_IMODE(store.stat().st_mode) == 0o600


def test_save_uses_the_shared_atomic_writer(store):
    with patch("utils.broker_profiles.atomic_write_text",
               wraps=bp.atomic_write_text) as w:
        assert save_profiles({}) is True
    assert w.call_count == 1
    assert not (store.parent / "broker_profiles.tmp").exists()


# ── the consumer path: TUI flows over an unreadable store ──────────────────

def test_public_setup_over_unreadable_store_writes_nothing_and_says_why(store, tmp_path):
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src', 'launcher_tui'))
    sys.path.insert(0, os.path.dirname(__file__))
    from handler_test_utils import make_handler_context
    from handlers.broker import BrokerHandler
    store.write_text("")
    ctx = make_handler_context()
    ctx.dialog._inputbox_returns = ["US", "LongFast"]
    h = BrokerHandler()
    h.set_context(ctx)
    with patch.object(h, "_apply_profile_to_mqtt") as apply_:
        h._setup_public_broker()
    assert store.read_text() == ""
    apply_.assert_not_called()
    shown = " ".join(str(c[1]) for c in ctx.dialog.calls if c[0] == "msgbox")
    assert "unreadable" in shown.lower() and "Reset" in shown


def _tui(menu=(), yes=()):
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src', 'launcher_tui'))
    sys.path.insert(0, os.path.dirname(__file__))
    from handler_test_utils import make_handler_context
    from handlers.broker import BrokerHandler
    ctx = make_handler_context()
    ctx.dialog._menu_returns = list(menu)
    ctx.dialog._yesno_returns = list(yes)
    h = BrokerHandler()
    h.set_context(ctx)
    return h, ctx


def test_defaults_are_never_fabricated_over_an_unreadable_store(store):
    store.write_text("")
    assert bp.ensure_default_profiles() == {}
    assert store.read_text() == ""


def test_broker_menu_says_unreadable_and_reset_keeps_a_backup(store):
    store.write_text('{"profiles": {"a": ')
    h, ctx = _tui(menu=["reset", "back"], yes=[True])
    with patch("handlers.broker.check_mosquitto_installed", return_value=(True, "")), \
            patch("handlers.broker.check_mosquitto_running", return_value=(True, "")):
        h._broker_menu()
    menus = [c[1] for c in ctx.dialog.calls if c[0] == "menu"]
    assert "UNREADABLE" in menus[0][1]
    assert "reset" in [k for k, _ in menus[0][2]]
    assert "reset" not in [k for k, _ in menus[1][2]]      # gone once reset
    backup = store.with_name("broker_profiles.json.unreadable.1")
    assert backup.read_text() == '{"profiles": {"a": '
    assert sorted(_on_disk(store)) == ["meshforge_private", "meshtastic_public"]


def test_reset_declined_changes_nothing(store):
    store.write_text("garbage")
    h, ctx = _tui(yes=[False])
    h._reset_profile_store()
    assert store.read_text() == "garbage"
    assert not list(store.parent.glob("*.unreadable.*"))


def test_broker_menu_names_skipped_profiles(store):
    other = _good("other")
    other["future_field"] = 1
    _write(store, {"club": _good("club"), "other": other})
    h, ctx = _tui(menu=["back"])
    with patch("handlers.broker.check_mosquitto_installed", return_value=(True, "")), \
            patch("handlers.broker.check_mosquitto_running", return_value=(True, "")):
        h._broker_menu()
    (menu,) = [c[1] for c in ctx.dialog.calls if c[0] == "menu"]
    assert "did not load (other)" in menu[1]
    assert "reset" not in [k for k, _ in menu[2]]


def test_load_tightens_an_existing_world_readable_store(store):
    _write(store, {"a": _good("a", username="u", password="pw")})
    os.chmod(store, 0o664)                   # every pre-r7 store on the fleet
    assert list(load_profiles()) == ["a"]
    assert stat.S_IMODE(store.stat().st_mode) == 0o600
