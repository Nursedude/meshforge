"""F8 v2 round 10 — fixes for the r9 non-author review (Fable 5.1, by running).

Record: ``.claude/plans/mqtt_f8_state_model_2026_11_02.md`` § review r9.
Operator 2026-11-04: "Yes, go I'll follow your advice".

- F1/F2 (V13, pre-existing): opening Broker Manager over an empty store
     WROTE two default profiles (a new private password) nobody saw or
     confirmed; after the operator deleted them they came back. Opening a
     screen now writes nothing — "Create Default Profiles" is an explicit
     item with a Yes/No naming both profiles (Esc/No = nothing written, Q5).
- F1 (r9): the empty-store note no longer claims a write failed.
- F4 (V13, pre-existing): a JSON object with no ``profiles`` key read as an
     ok EMPTY store (then defaults overwrote it). It is ``unreadable``.
- F3: unknown top-level keys survive a save.
"""
import json
import os
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


def _raw(name, active=False):
    p = create_custom_profile(name=name, host=f"{name}.example", port=1883)
    p.is_active = active
    return bp.asdict(p)


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


def _yesnos(ctx):
    return [c[1] for c in ctx.dialog.calls if c[0] == "yesno"]


# ── F1/F2: opening a screen writes nothing ─────────────────────────────────

def test_F2_opening_broker_manager_over_absent_store_writes_nothing(store):
    h, ctx = _tui(menu=["profiles", "back", "back"])
    h._broker_menu()
    assert not store.exists()


def test_F2_deleted_defaults_do_not_come_back(store):
    store.write_text(json.dumps({"profiles": {}, "active_profile": None}))
    before = store.read_bytes()
    h, ctx = _tui(menu=["profiles", "back", "back"])
    h._broker_menu()
    assert store.read_bytes() == before


def test_F1_empty_store_note_never_claims_a_failed_write(store):
    store.write_text(json.dumps({"profiles": {}}))
    h, ctx = _tui(menu=["profiles", "back", "back"])
    h._broker_menu()
    for m in _menus(ctx):
        assert "could not be saved" not in m[1], m[0]
        assert "No broker profiles" in m[1], m[0]


def test_F2_create_defaults_item_offered_only_when_empty(store):
    h, ctx = _tui(menu=["back"])
    h._broker_menu()
    assert "defaults" in [k for k, _ in _menus(ctx)[0][2]]
    store.write_text(json.dumps({"profiles": {"a": _raw("a")}}))
    h, ctx = _tui(menu=["back"])
    h._broker_menu()
    assert "defaults" not in [k for k, _ in _menus(ctx)[0][2]]


def test_F2_create_defaults_asks_and_names_both_profiles(store):
    h, ctx = _tui(menu=["defaults", "back"], yes=[True])
    h._broker_menu()
    (q,) = _yesnos(ctx)
    body = q[1]
    assert "meshforge_private" in body and "meshtastic_public" in body
    assert "password" in body.lower()
    saved = json.loads(store.read_text())["profiles"]
    assert sorted(saved) == ["meshforge_private", "meshtastic_public"]


@pytest.mark.parametrize("answer", [False, None])
def test_F2_create_defaults_no_or_esc_writes_nothing(store, answer):
    h, ctx = _tui(menu=["defaults", "back"], yes=[answer])
    h._broker_menu()
    assert not store.exists()


def test_F2_create_defaults_refuses_once_the_store_is_no_longer_empty(store):
    store.write_text(json.dumps({"profiles": {"a": _raw("a")}}))
    before = store.read_bytes()
    ok, _why = bp.create_default_profiles()
    assert ok is False
    assert store.read_bytes() == before


@pytest.mark.skipif(ROOT, reason="root writes a 0500 dir")
def test_F2_create_defaults_failed_write_says_so(store):
    os.chmod(store.parent, 0o500)
    try:
        h, ctx = _tui(menu=["defaults", "back"], yes=[True])
        h._broker_menu()
    finally:
        os.chmod(store.parent, 0o700)
    assert not store.exists()
    titles = [c[1][0] for c in ctx.dialog.calls if c[0] == "msgbox"]
    assert any("Fail" in t for t in titles), titles


# ── F4: a JSON object with no `profiles` key is not a profile store ────────

@pytest.mark.parametrize("doc", [{}, {"something": "else"}, {"active_profile": "x"}])
def test_F4_object_without_profiles_key_is_unreadable(store, doc):
    store.write_text(json.dumps(doc))
    before = store.read_bytes()
    assert bp.profile_store_status().state == "unreadable"
    assert bp.save_profiles({"a": create_custom_profile(name="a", host="h")}) is False
    assert bp.create_default_profiles()[0] is False
    assert store.read_bytes() == before


def test_F4_control_empty_profiles_object_is_ok(store):
    store.write_text(json.dumps({"profiles": {}}))
    assert bp.profile_store_status().state == "ok"


# ── F3: unknown top-level keys survive a save ──────────────────────────────

def test_F3_unknown_top_level_keys_survive_a_save(store):
    store.write_text(json.dumps({"profiles": {"a": _raw("a")}, "active_profile": None,
                                 "schema_version": 2, "future": {"x": 1}}))
    profiles = load_profiles()
    profiles["b"] = create_custom_profile(name="b", host="h")
    assert bp.save_profiles(profiles) is True
    data = json.loads(store.read_text())
    assert data["schema_version"] == 2 and data["future"] == {"x": 1}
    assert sorted(data["profiles"]) == ["a", "b"]


# ── r10b: the r10 non-author review (Fable 5.1, by running) ────────────────
# F1 a refused Create said "nothing written" AND "Could not write"; F2 a
# profile named `defaults` opened the Create dialog; F3 the Profiles screen
# named setup items it does not have.

def test_r10b_F1_refusal_over_a_filled_store_never_claims_a_failed_write(store):
    h, ctx = _tui(yes=[True])
    store.write_text(json.dumps({"profiles": {"a": _raw("a")}}))   # filled meanwhile
    before = store.read_bytes()
    h._create_default_profiles()
    (box,) = [c[1] for c in ctx.dialog.calls if c[0] == "msgbox"]
    assert "no longer empty" in box[1]
    assert "Could not write" not in box[1]
    assert store.read_bytes() == before


@pytest.mark.skipif(ROOT, reason="root writes a 0500 dir")
def test_r10b_F1_control_a_real_write_failure_still_says_so(store):
    os.chmod(store.parent, 0o500)
    try:
        h, ctx = _tui(yes=[True])
        h._create_default_profiles()
    finally:
        os.chmod(store.parent, 0o700)
    (box,) = [c[1] for c in ctx.dialog.calls if c[0] == "msgbox"]
    assert "Could not write" in box[1]


def test_r10b_F2_a_profile_named_defaults_opens_its_own_detail(store):
    store.write_text(json.dumps({"profiles": {"defaults": _raw("defaults")}}))
    h, ctx = _tui(menu=["defaults", "back", "back"])
    h._broker_profiles_menu()
    assert _yesnos(ctx) == []
    assert [m[0] for m in _menus(ctx)][1] == "Profile: defaults"


def test_r10b_F3_profiles_screen_note_names_only_what_it_offers(store):
    h, ctx = _tui(menu=["back"])
    h._broker_profiles_menu()
    (menu,) = _menus(ctx)
    assert "setup item below" not in menu[1]
    assert "Broker Manager" in menu[1]
