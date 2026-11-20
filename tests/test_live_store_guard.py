"""A Claude-session driver must not write the operator's LIVE delivery stores.

Deferred row ``drill-scripts-write-live-delivery-db`` (root-caused 10-27): a
non-author reviewer RAN code outside pytest (``PYTHONPATH=src python3
$D/exp_qu…``) against real MessageQueues with clock steps; conftest
isolation does not apply outside pytest, so ~1,000 fabricated ``expired``
rows landed in the live ``delivery_counters.db`` and read as the manager box
conf_rate 0.0 for 10 days.

The arming signal is the harness's own env, MEASURED 2026-11-19 on the
manager box: ``CLAUDECODE=1`` is in the session's Bash shells and in NO
daemon (map, mini, digest, root watchdog_runner, unattended-upgrades), and
``systemd-run --user`` children do not inherit it. Seatbelt scope: accidental
LOCAL drivers. A command sent over ssh does not carry it.
"""

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from utils import live_store_guard as g


@pytest.fixture
def armed(monkeypatch, tmp_path):
    """Armed exactly as a session driver is: CLAUDECODE=1, not under pytest."""
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.delenv("MESHFORGE_ALLOW_LIVE_STORES", raising=False)
    # conftest's scratch overrides STAY set (review #5: deleting them let a
    # singleton built mid-test resolve to the real home). The guard judges
    # the PATH, so a live path is refused whatever an override says.
    monkeypatch.setattr(g, "_under_pytest", lambda: False)
    monkeypatch.setattr(g, "_operator_homes", lambda: {tmp_path / "home"})
    return tmp_path / "home"


def test_session_driver_is_refused_the_live_counters_db(armed):
    live = armed / ".local/share/meshforge/delivery_counters.db"
    with pytest.raises(g.LiveStoreRefused) as e:
        g.refuse_live_store(live, "MESHFORGE_DELIVERY_COUNTERS_DB")
    msg = str(e.value)
    assert "MESHFORGE_DELIVERY_COUNTERS_DB" in msg       # the knob, literally
    assert "MESHFORGE_ALLOW_LIVE_STORES=1" in msg


def test_refusal_cannot_be_swallowed_by_except_exception(armed):
    """The bridge's send paths sit in broad `except Exception` — a refusal
    they could absorb would degrade back into a silent write elsewhere."""
    assert not issubclass(g.LiveStoreRefused, Exception)
    assert issubclass(g.LiveStoreRefused, BaseException)


def test_scratch_path_is_allowed(armed, tmp_path):
    g.refuse_live_store(tmp_path / "scratch" / "dc.db", "MESHFORGE_DELIVERY_COUNTERS_DB")


def test_the_override_path_itself_is_allowed_even_inside_the_home(armed, monkeypatch):
    """Naming the store's own override is the deliberate scratch knob."""
    target = armed / "scratch" / "dc.db"
    monkeypatch.setenv("MESHFORGE_DELIVERY_COUNTERS_DB", str(target))
    g.refuse_live_store(target, "MESHFORGE_DELIVERY_COUNTERS_DB")


def test_an_override_does_not_unlock_a_different_live_path(armed, monkeypatch, tmp_path):
    """Review #4: an exported override must not disable the guard for an
    explicit live db_path (mesh_bridge passes its own)."""
    monkeypatch.setenv("MESHFORGE_MESSAGE_QUEUE_DB", str(tmp_path / "x.db"))
    with pytest.raises(g.LiveStoreRefused):
        g.refuse_live_store(armed / ".config/meshforge/mesh_bridge_queues/p2s.db",
                            "MESHFORGE_MESSAGE_QUEUE_DB")


def test_operator_opt_in_is_allowed(armed, monkeypatch):
    monkeypatch.setenv("MESHFORGE_ALLOW_LIVE_STORES", "1")
    g.refuse_live_store(armed / ".config/meshforge/message_queue.db",
                        "MESHFORGE_MESSAGE_QUEUE_DB")


def test_daemon_without_claudecode_is_never_armed(armed, monkeypatch):
    monkeypatch.delenv("CLAUDECODE")
    g.refuse_live_store(armed / ".local/share/meshforge/delivery_counters.db",
                        "MESHFORGE_DELIVERY_COUNTERS_DB")


@pytest.mark.parametrize("value", ["0", "", "true", "yes"])
def test_only_the_exact_harness_value_arms(armed, monkeypatch, value):
    monkeypatch.setenv("CLAUDECODE", value)
    g.refuse_live_store(armed / ".local/share/meshforge/delivery_counters.db",
                        "MESHFORGE_DELIVERY_COUNTERS_DB")


def test_pytest_is_never_armed(monkeypatch, tmp_path):
    """conftest isolates the suite; the guard must not double-gate it."""
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setattr(g, "_operator_homes", lambda: {tmp_path})
    assert os.environ.get(g.PYTEST_MARKER) == "1"      # conftest set it at import
    g.refuse_live_store(tmp_path / "delivery_counters.db", "X")   # real _under_pytest


def test_importing_pytest_alone_does_not_disarm(monkeypatch):
    """Review #6: a reviewer driver that imports a test helper (and so
    pytest) is the incident's population — it must stay armed."""
    import sys
    assert "pytest" in sys.modules
    monkeypatch.delenv(g.PYTEST_MARKER, raising=False)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    assert g._under_pytest() is False


def test_path_spelled_with_dotdot_is_still_live(armed):
    sneaky = armed / "tmp" / ".." / ".local/share/meshforge/delivery_counters.db"
    with pytest.raises(g.LiveStoreRefused):
        g.refuse_live_store(sneaky, "MESHFORGE_DELIVERY_COUNTERS_DB")


def test_operator_homes_includes_sudo_user_when_running_as_root(monkeypatch):
    """Under sudo the uid's home is /root; the operator's comes from SUDO_USER."""
    import pwd
    me = pwd.getpwuid(os.getuid())
    monkeypatch.setattr(g.os, "getuid", lambda: 0)
    monkeypatch.setenv("SUDO_USER", me.pw_name)
    monkeypatch.delenv("LOGNAME", raising=False)   # SUDO_USER alone must find it
    homes = g._operator_homes()
    assert Path(me.pw_dir).resolve() in homes
    assert Path(pwd.getpwuid(0).pw_dir).resolve() in homes


def test_operator_homes_includes_logname_when_running_as_root(monkeypatch):
    """Review #3: get_real_user_home also consults LOGNAME — so must the guard."""
    import pwd
    me = pwd.getpwuid(os.getuid())
    monkeypatch.setattr(g.os, "getuid", lambda: 0)
    monkeypatch.delenv("SUDO_USER", raising=False)
    monkeypatch.setenv("LOGNAME", me.pw_name)
    assert Path(me.pw_dir).resolve() in g._operator_homes()


def test_a_scratch_HOME_is_not_an_operator_home(monkeypatch, tmp_path):
    """The sanctioned isolation recipe (HOME=<scratch>, SUDO_USER/LOGNAME
    unset) must keep working: $HOME is never treated as an operator home."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("SUDO_USER", raising=False)
    monkeypatch.delenv("LOGNAME", raising=False)
    assert tmp_path.resolve() not in g._operator_homes()


def test_dotdot_escaping_the_home_is_judged_where_it_lands(armed, tmp_path):
    """Resolve before judging: `home/../elsewhere` is NOT in the home."""
    (tmp_path / "elsewhere").mkdir()
    g.refuse_live_store(armed / ".." / "elsewhere" / "dc.db", "MESHFORGE_DELIVERY_COUNTERS_DB")


def test_symlink_from_scratch_into_the_home_is_refused(armed, tmp_path):
    (armed / ".local/share/meshforge").mkdir(parents=True)
    link = tmp_path / "scratch_link"
    link.symlink_to(armed / ".local/share/meshforge")
    with pytest.raises(g.LiveStoreRefused):
        g.refuse_live_store(link / "delivery_counters.db", "MESHFORGE_DELIVERY_COUNTERS_DB")


# ── the consumers: both constructors consult the guard ────────────────────

def test_delivery_counters_default_path_consults_the_guard(armed, monkeypatch):
    from gateway import delivery_counters as dc
    monkeypatch.delenv("MESHFORGE_DELIVERY_COUNTERS_DB", raising=False)
    monkeypatch.setattr(dc, "get_real_user_home", lambda: armed)
    with pytest.raises(g.LiveStoreRefused):
        dc.DeliveryCounters(run_preflight=False)
    assert not (armed / ".local/share/meshforge/delivery_counters.db").exists()


def test_delivery_counters_explicit_scratch_path_works(armed, tmp_path):
    from gateway import delivery_counters as dc
    dc.DeliveryCounters(db_path=tmp_path / "s.db", run_preflight=False)


def test_message_queue_default_path_consults_the_guard(armed, monkeypatch):
    from gateway import message_queue as mq
    monkeypatch.delenv("MESHFORGE_MESSAGE_QUEUE_DB", raising=False)
    monkeypatch.setattr(mq, "get_real_user_home", lambda: armed)
    with pytest.raises(g.LiveStoreRefused):
        mq.PersistentMessageQueue()
    assert not (armed / ".config/meshforge/message_queue.db").exists()


def test_message_queue_env_override_is_the_scratch_knob(armed, monkeypatch, tmp_path):
    from gateway import message_queue as mq
    target = tmp_path / "q.db"
    monkeypatch.setenv("MESHFORGE_MESSAGE_QUEUE_DB", str(target))
    q = mq.PersistentMessageQueue()
    assert Path(q._db_path) == target


def test_opt_in_name_follows_the_store_prefix():
    """One byte-identical module serves both twins' env vocabularies."""
    assert g.allow_env_for("MESHFORGE_DELIVERY_COUNTERS_DB") == "MESHFORGE_ALLOW_LIVE_STORES"
    assert g.allow_env_for("MESHANCHOR_MESSAGE_QUEUE_DB") == "MESHANCHOR_ALLOW_LIVE_STORES"


def test_the_other_twins_opt_in_does_not_unlock_this_one(armed, monkeypatch):
    monkeypatch.setenv("MESHANCHOR_ALLOW_LIVE_STORES", "1")
    with pytest.raises(g.LiveStoreRefused):
        g.refuse_live_store(armed / ".local/share/meshforge/delivery_counters.db",
                            "MESHFORGE_DELIVERY_COUNTERS_DB")


# ── review #1 / #2 / #7: the guard's other consumers ──────────────────────

def test_prometheus_exporter_reports_queue_stats_unobservable_not_dead(armed, monkeypatch):
    """Review #1: a session-run exporter must not die on the refusal (it is a
    BaseException, past every `except Exception`)."""
    from utils import prometheus_exporter as pe
    if not pe._HAS_MESSAGE_QUEUE:
        pytest.skip("message queue not importable here")

    def refuse(*a, **k):
        raise g.LiveStoreRefused("drill")
    monkeypatch.setattr(pe, "PersistentMessageQueue", refuse)
    exporter = pe.PrometheusExporter.__new__(pe.PrometheusExporter)
    assert isinstance(exporter._collect_message_metrics(), list)


def test_influx_exporter_queue_refusal_is_caught():
    import inspect
    from utils import influxdb_exporter as ie
    src = inspect.getsource(ie)
    i = src.index("queue = PersistentMessageQueue()")
    assert "except LiveStoreRefused" in src[i:i + 2000]


@pytest.mark.parametrize("mod", ["lab.gateway_rt_canary", "lab.gateway_resource_canary"])
def test_canaries_opt_in_deliberately(mod, monkeypatch):
    """They measure the REAL pipeline; their writes are real traffic."""
    import importlib
    m = importlib.import_module(mod)
    monkeypatch.delenv("MESHFORGE_ALLOW_LIVE_STORES", raising=False)
    with pytest.raises(SystemExit):
        m.main(["--help"])
    assert os.environ.get("MESHFORGE_ALLOW_LIVE_STORES") == "1"


def test_db_inventory_follows_the_queue_override(monkeypatch, tmp_path):
    """Review #7: writer and inventory read ONE path (honest_failure_modes #5)."""
    from utils.db_inventory import INVENTORY
    spec = next(x for x in INVENTORY if x.name == "message_queue")
    monkeypatch.setenv("MESHFORGE_MESSAGE_QUEUE_DB", str(tmp_path / "q.db"))
    assert spec.path_factory() == tmp_path / "q.db"
