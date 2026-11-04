"""F8 v2 round 8 — fixes for the r7 non-author review (Fable 5.1, by running).

Record: ``.claude/plans/mqtt_f8_state_model_2026_11_02.md`` § review r7.
Operator 2026-11-03: "r8 as listed - go".

- F1  a same-name setup over an UNPARSED profile destroyed it with no Yes
      and said "created" (r7 regression).
- F6  (a) permission denied read as "unreadable" -> Reset offered -> a
      healthy store moved aside; (b) a sudo save left the store root-owned.
- F2  Private setup installed mosquitto config for a REFUSED save.
- F5  default profiles listed though writing them failed.
- F7  an unreadable store and its Reset backup kept a wide mode.
- F9  Public setup replaced an existing profile without asking.
- F10 an active profile that did not load read as "no profile is active".
- F11 the Profiles sub-menu read "(0)" over an unreadable store.
- leak  three suite files reached the operator's real store (author find):
      conftest now redirects the store for every test.
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
from utils import broker_profiles as bp  # noqa: E402
from utils.broker_profiles import (  # noqa: E402
    create_custom_profile, create_private_profile, create_public_profile,
    load_profiles,
)
from utils.paths import get_real_user_home  # noqa: E402


@pytest.fixture
def store(tmp_path):
    path = tmp_path / ".config" / "meshforge" / "broker_profiles.json"
    path.parent.mkdir(parents=True)
    with patch("utils.broker_profiles._get_profiles_path", return_value=path):
        yield path


@pytest.fixture
def cfg(tmp_path):
    d = tmp_path / "mqtt"
    d.mkdir()
    with patch("monitoring.mqtt_subscriber.mqtt_config_dir", return_value=d), \
            patch("handlers.mqtt.MQTTHandler._detect_local_channel", return_value=None):
        yield d


def _raw(prof, **extra):
    d = bp.asdict(prof)
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


def _titles(ctx, kind):
    return [c[1][0] for c in ctx.dialog.calls if c[0] == kind]


def _mosq():
    return patch.multiple("handlers.broker",
                          check_mosquitto_installed=lambda: (True, ""),
                          check_mosquitto_running=lambda: (True, ""))


# ── the leak gate ──────────────────────────────────────────────────────────

def test_leak_gate_no_test_reaches_the_operators_store():
    """conftest redirects the store for EVERY test; a test that forgets its
    own patch lands in a tmp dir, never the operator's home."""
    for name in ("utils.broker_profiles", "src.utils.broker_profiles"):
        mod = sys.modules.get(name)
        if mod is None:
            continue
        path = mod._get_profiles_path()
        assert get_real_user_home() not in path.parents, (name, path)


# ── F1 / F9: replace asks — including over a profile that did not load ─────

UNPARSED_PRIVATE = _raw(create_private_profile(
    name="meshforge_private", username="nodes", password="NODES-USE-THIS"),
    future_field=1)


@pytest.mark.parametrize("answer", [False, None], ids=["no", "esc"])
def test_F1_private_setup_over_unparsed_profile_asks_and_keeps_it(store, cfg, answer):
    _write(store, {"meshforge_private": UNPARSED_PRIVATE})
    before = store.read_bytes()
    h, ctx = _tui(inputs=["LongFast", "US", "meshforge", "pw"], yes=[answer])
    with patch("handlers.broker.os.geteuid", return_value=1000):
        h._setup_private_broker()
    assert "Replace Profile?" in _titles(ctx, "yesno")
    (text,) = [c[1][1] for c in ctx.dialog.calls
               if c[0] == "yesno" and c[1][0] == "Replace Profile?"]
    assert "could not be loaded" in text
    assert store.read_bytes() == before


def test_F1_custom_setup_over_unparsed_profile_replaces_only_on_yes(store, cfg):
    old = _raw(create_custom_profile(name="mine", host="old.example", port=1883,
                                     username="u", password="OLDPASS"), future_field=1)
    _write(store, {"mine": old})
    h, ctx = _tui(inputs=["mine", "new.example", "1883", "", "US", "LongFast", ""],
                  yes=[True])
    h._setup_custom_broker()
    disk = json.loads(store.read_text())["profiles"]
    assert disk["mine"]["host"] == "new.example"
    text = [c[1][1] for c in ctx.dialog.calls
            if c[0] == "msgbox" and c[1][0] == "Custom Broker Saved"][0]
    assert "replaced" in text and "created" not in text


def test_F9_public_setup_asks_before_replacing(store, cfg):
    old = create_public_profile(region="EU_868", channel="MediumFast")
    _write(store, {"meshtastic_public": _raw(old)})
    before = store.read_bytes()
    h, ctx = _tui(inputs=["US", "LongFast"], yes=[False])
    with patch.object(h, "_apply_profile_to_mqtt") as apply_:
        h._setup_public_broker()
    assert "Replace Profile?" in _titles(ctx, "yesno")
    assert store.read_bytes() == before
    apply_.assert_not_called()


# ── F2: no mosquitto install for a save that was refused ───────────────────

def test_F2_private_setup_never_installs_for_a_refused_save(store, cfg):
    store.write_text('{"profiles": {"a": ')
    h, ctx = _tui(inputs=["LongFast", "US", "meshforge", "s3cret"], yes=[True, True])
    with patch("handlers.broker.os.geteuid", return_value=0), \
            patch.object(h, "_install_broker_config") as install:
        h._setup_private_broker()
    install.assert_not_called()
    assert "Install Config" not in _titles(ctx, "yesno")
    assert "Save Failed" in _titles(ctx, "msgbox")


def test_F2_control_saved_private_setup_still_offers_install(store, cfg):
    h, ctx = _tui(inputs=["LongFast", "US", "meshforge", "s3cret"], yes=[True])
    with patch("handlers.broker.os.geteuid", return_value=0), \
            patch.object(h, "_install_broker_config") as install, \
            patch.object(h, "_apply_profile_to_mqtt"):
        h._setup_private_broker()
    install.assert_called_once()


# ── F6: permission denied is its own state, never Reset ────────────────────

@pytest.mark.skipif(os.geteuid() == 0, reason="root reads a 000 file")
def test_F6a_permission_denied_is_not_unreadable_and_never_reset(store):
    _write(store, {"keep": _raw(create_custom_profile(
        name="keep", host="h", port=1883, password="KEEP-ME"))})
    before = store.read_bytes()
    os.chmod(store, 0o000)
    try:
        st = bp.profile_store_status()
        assert st.state == "denied"
        assert bp.reset_unreadable_profile_store() is None
        assert bp.save_profiles({}) is False
        h, ctx = _tui(menu=["back"])
        with _mosq():
            h._broker_menu()
        (menu,) = [c[1] for c in ctx.dialog.calls if c[0] == "menu"]
        assert "reset" not in [k for k, _ in menu[2]]
        assert "PERMISSION" in menu[1].upper()
    finally:
        os.chmod(store, 0o600)
    assert store.read_bytes() == before
    assert not list(store.parent.glob("*.unreadable.*"))


def test_F6b_a_sudo_save_hands_the_store_back_to_the_operator(store):
    with patch("utils.broker_profiles.chown_to_operator") as chown:
        assert bp.save_profiles({}) is True
    chown.assert_called_once_with(store)


# ── F5: defaults are listed only once they are on disk ─────────────────────

@pytest.mark.skipif(os.geteuid() == 0, reason="root writes a 0500 dir")
def test_F5_defaults_not_listed_when_their_write_failed(store):
    os.chmod(store.parent, 0o500)
    try:
        assert bp.ensure_default_profiles() == {}
        h, ctx = _tui(menu=["back"])
        h._broker_profiles_menu()
        (menu,) = [c[1] for c in ctx.dialog.calls if c[0] == "menu"]
        assert "could not be saved" in menu[1]
    finally:
        os.chmod(store.parent, 0o700)
    assert not store.exists()


# ── F7: the store and its backup are private even when unreadable ──────────

def test_F7_unreadable_store_and_backup_are_0600(store):
    store.write_text('{"profiles": {"a": {"password": "x"')
    os.chmod(store, 0o664)
    load_profiles()
    assert stat.S_IMODE(store.stat().st_mode) == 0o600
    os.chmod(store, 0o664)
    backup = bp.reset_unreadable_profile_store()
    assert stat.S_IMODE(backup.stat().st_mode) == 0o600


# ── F10 / F11: sub-screens name the store's real state ─────────────────────

def test_F10_active_unparsed_profile_is_named_not_none(store):
    other = _raw(create_custom_profile(name="club", host="c", port=1883),
                 is_active=True, future_field=1)
    _write(store, {"club": other}, active="club")
    h, ctx = _tui()
    h._radio_mqtt_setup()
    shown = " ".join(c[1][1] for c in ctx.dialog.calls if c[0] == "msgbox")
    assert "club" in shown and "could not be loaded" in shown


def test_F11_profiles_submenu_warns_over_unreadable_store(store):
    store.write_text("")
    h, ctx = _tui(menu=["back"])
    h._broker_profiles_menu()
    (menu,) = [c[1] for c in ctx.dialog.calls if c[0] == "menu"]
    assert "UNREADABLE" in menu[1]
    assert store.read_text() == ""


def test_F6_admin_mode_open_hands_a_root_owned_store_back(store):
    """The in-app remedy the denied screen names: an Admin-mode (sudo) open
    of the store calls chown_to_operator (it only acts on a root-owned file
    under SUDO_USER)."""
    store.write_text(json.dumps({"profiles": {}}))
    with patch("utils.broker_profiles.chown_to_operator") as chown:
        load_profiles()
    chown.assert_called_once_with(store)
