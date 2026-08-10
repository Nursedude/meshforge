"""
Pytest configuration for MeshForge test suite.

Handles CI-specific settings:
- Auto-skip hardware-dependent tests in CI
- Timeout defaults
- Fixtures for common mocks

TUI handler test infrastructure (FakeDialog, make_handler_context) lives
in ``tests/handler_test_utils.py`` — single source of truth. All handler
tests import from there. A duplicate copy used to live in this file; it
was unused (no test imported it) and was removed in the 2026-05-19
pattern-audit cleanup (Finding #9).
"""

import os
import sys
from contextlib import ExitStack

import pytest
from unittest.mock import MagicMock, patch

# Ensure src and launcher_tui are importable for handler tests
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src', 'launcher_tui'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

# Detect CI environment
CI = os.environ.get('CI', 'false').lower() == 'true'
MESHFORGE_CI = os.environ.get('MESHFORGE_CI', 'false').lower() == 'true'


def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line(
        "markers", "hardware: mark test as requiring hardware (skipped in CI)"
    )
    config.addinivalue_line(
        "markers", "slow: mark test as slow (may be skipped with --fast)"
    )
    config.addinivalue_line(
        "markers", "network: mark test as requiring network access"
    )

    # Disable cascade-detector probes during pytest. The
    # rns_rpc_wedge fingerprint calls `subprocess.run(["ss", ...],
    # timeout=2)`; when a daemon detector thread (started by any test
    # that exercises the full service init path) is still running
    # during another test's `with patch('subprocess.run')` window, the
    # probe's call leaks into the mock's call_args_list and trips
    # assertions like `assert call[1]['timeout'] == 60` —
    # observed on Python 3.9/3.11 in CI starting at commit 79f5d7b.
    # See project_ci_red_track0_followup.md for full forensics.
    # Tests that explicitly exercise probe behavior unset this via
    # `monkeypatch.delenv("MESHFORGE_CASCADE_PROBE_DISABLED", raising=False)`.
    os.environ["MESHFORGE_CASCADE_PROBE_DISABLED"] = "1"

    # Redirect the gateway's content_id_view state file (dedup arc STEP 4c)
    # to a throwaway tmp path for the whole suite. The producer hook fires
    # inside the real RNSMeshtasticBridge._rns_loop, which several bridge
    # tests drive (bridge._running = True), and without this it would write
    # the operator's actual ~/.local/state/meshforge/content_id_view.json.
    # setdefault so a test can still point it elsewhere.
    import tempfile as _tempfile
    os.environ.setdefault(
        "MESHFORGE_CONTENT_ID_VIEW_STATE",
        os.path.join(_tempfile.gettempdir(),
                     "meshforge-pytest-content_id_view.json"),
    )


def pytest_collection_modifyitems(config, items):
    """Auto-skip certain tests in CI environment."""
    if not (CI or MESHFORGE_CI):
        return

    skip_hardware = pytest.mark.skip(reason="Hardware not available in CI")
    skip_network = pytest.mark.skip(reason="Network tests skipped in CI")

    for item in items:
        # Skip hardware-marked tests
        if "hardware" in item.keywords:
            item.add_marker(skip_hardware)

        # Skip network-marked tests in CI
        if "network" in item.keywords:
            item.add_marker(skip_network)

        # Auto-detect likely hardware tests by name
        test_name = item.name.lower()
        if any(kw in test_name for kw in ['real_device', 'physical', 'actual_hardware']):
            item.add_marker(skip_hardware)


@pytest.fixture(autouse=True)
def _isolate_node_cache_files(tmp_path_factory):
    """Keep the node-cache writers out of the operator's live data directory.

    ``UnifiedNodeTracker._save_cache`` writes TWO files: the one
    ``get_cache_file()`` names (``~/.config/meshforge/node_cache.json``) and an
    operator-owned copy at ``MeshForgePaths.rns_nodes_cache_path()``
    (``~/.cache/meshforge/rns_nodes.json``). Any test that constructs a bare
    ``UnifiedNodeTracker()`` therefore reads and overwrites real fleet data —
    measured 2026-08-03: a full-suite run on this box replaced a live
    ``rns_nodes.json`` with 917 KB it had loaded from the box's own config,
    and on a GATEWAY box that file is the 10 MB RNS node cache the map
    collectors read.

    Autouse and suite-wide on purpose. A per-file guard only holds until the
    next test file constructs a tracker (``test_gateway_integration`` already
    did), so this is a gate, not a convention: a test's verdict must not depend
    on which box ran it, and neither must its side effects
    (feedback_tests_must_pin_ambient_state).

    Tests that assert on cache CONTENT patch these to their own tmp paths;
    an inner patch wins, so this only catches what would otherwise escape.

    BOTH import aliases are patched (2026-08-03, found porting this fixture to
    MeshAnchor). ``sys.path`` carries the repo root AND ``src/``, so
    ``gateway.node_tracker`` and ``src.gateway.node_tracker`` are two distinct
    module objects holding two distinct ``UnifiedNodeTracker`` classes —
    measured, not assumed: under the single-alias version of this fixture the
    ``src.*`` class still returned ``~/.config/meshforge/node_cache.json``.
    ``test_gateway_integration`` and ``test_node_tracker`` import via ``src.``,
    so the gate was covering the alias those files do NOT use. The md5 check
    that blessed it held only because those files also patch locally — a
    property of today's tests, not of the gate. Patching the alias in front of
    you is exactly the half-covered guard this fixture exists to prevent.
    """
    root = tmp_path_factory.mktemp("node_cache_isolation")
    targets = [
        ('utils.paths.MeshForgePaths.rns_nodes_cache_path', root / "rns_nodes.json"),
        ('src.utils.paths.MeshForgePaths.rns_nodes_cache_path', root / "rns_nodes.json"),
        ('gateway.node_tracker.UnifiedNodeTracker.get_cache_file', root / "node_cache.json"),
        ('src.gateway.node_tracker.UnifiedNodeTracker.get_cache_file', root / "node_cache.json"),
    ]
    with ExitStack() as stack:
        patched = 0
        for target, value in targets:
            try:
                stack.enter_context(patch(target, return_value=value))
                patched += 1
            except (ImportError, AttributeError, ModuleNotFoundError):
                # An alias that is not importable in this environment cannot
                # be the one writing to the live path either.
                continue
        for target in ('utils.paths.chown_to_operator',
                       'src.utils.paths.chown_to_operator'):
            try:
                stack.enter_context(patch(target))
            except (ImportError, AttributeError, ModuleNotFoundError):
                continue
        if not patched:
            raise RuntimeError(
                "node cache isolation patched NOTHING — the suite would write "
                "to the operator's live node_cache.json / rns_nodes.json"
            )
        yield root


#: Modules that resolve an operator data store as
#: ``get_real_user_home() / ".config" / "meshforge" / <name>`` and WRITE it.
#: Each is patched at its own module attribute (both import aliases), which
#: redirects only that module — patching ``utils.paths.get_real_user_home``
#: itself would break the tests that assert what it returns under sudo.
#: Found by directory sweep, not by memory: see _isolate_operator_data_stores.
_OPERATOR_STORE_MODULES = (
    "commands.messaging",              # messages.db
    "gateway.correlation_store",       # gateway_correlation.db
    "gateway.message_queue",           # message_queue.db
    "commands.rns",                    # lxmf_storage/ (LXMF ratchets)
    "gateway._rns_bridge_connection",  # lxmf_storage/ (LXMF ratchets)
    # templates/exported_<ts>.json — LITTER, a NEW file every run by design
    # ("timestamp suffix makes each export unique", so nothing ever
    # overwrites and nothing ever cleans up). 856 had accumulated since
    # 2026-04-21 before this line existed. Reached under three aliases
    # because sys.path carries src/ AND src/launcher_tui.
    "launcher_tui.handlers._gateway_preflight_template",
    "handlers._gateway_preflight_template",
)


@pytest.fixture(scope="session", autouse=True)
def _isolate_operator_data_stores(tmp_path_factory):
    """Keep the suite out of the operator's message store, queue, correlation
    DB and LXMF ratchets.

    Third sibling of ``_isolate_node_cache_files`` and
    ``_isolate_delivery_counters_db``. Measured 2026-08-05 by sweeping the
    whole operator tree (``~/.config/meshforge`` + ``~/.local/share/meshforge``
    + ``~/.cache/meshforge``) across one suite run: 22 files touched, and —
    after subtracting an idle CONTROL run to remove the live map service's own
    writes — FOUR genuinely mutated:

        lxmf_storage/lxmf/ratchets/*.ratchets   LXMF crypto ratchet state
        messages.db                             message store
        message_queue.db                        persistent queue
        gateway_correlation.db                  correlation store

    The ratchets are the reason this is a gate and not a note: they are
    per-destination cryptographic state on the box the fleet treats as its
    source of truth, and corrupting them fails opaquely and late.
    ``device_config.yaml`` (the RADIO config) is also rewritten by the suite
    and survives only because the fixture happens to match what is there —
    it is covered here too, via ``commands.messaging``'s sibling paths, by
    virtue of the whole config dir moving.

    Session-scoped: redirection is idempotent and O(1), and a per-test
    version of the delivery-counters guard cost 45% of suite wall-clock
    before it was fixed. Files needing per-test isolation still bring their
    own fixtures, which patch inside this one and win.
    """
    root = tmp_path_factory.mktemp("operator_home_isolation")
    (root / ".config" / "meshforge").mkdir(parents=True, exist_ok=True)
    with ExitStack() as stack:
        patched = []
        # device_config.yaml — the RADIO config, and the one file here that
        # could do real-world harm. Its header says "Re-applied automatically
        # after meshtasticd restart", so a wrong value is pushed to the radio.
        # On this box the suite rewrote it BYTE-IDENTICALLY, which read as
        # harmless — but that is a coincidence of the fixture matching this
        # box's LONG_FAST/ch20. moc3 runs SHORT_TURBO/ch8: the same write
        # there would take a gateway off its preset and break the
        # moc2<->moc3 RNS leg. Gate the single chokepoint both writers use
        # (`save_device_setting` / `save_device_settings` both go through
        # `_get_config_path`), rather than hunting whichever test does it.
        for alias in ("utils.device_config_store", "src.utils.device_config_store"):
            try:
                stack.enter_context(patch(
                    f"{alias}._get_config_path",
                    return_value=root / ".config" / "meshforge" / "device_config.yaml"))
                patched.append(alias)
            except (ImportError, AttributeError, ModuleNotFoundError):
                continue
        for mod in _OPERATOR_STORE_MODULES:
            for alias in (mod, f"src.{mod}"):
                try:
                    stack.enter_context(
                        patch(f"{alias}.get_real_user_home", return_value=root))
                    patched.append(alias)
                except (ImportError, AttributeError, ModuleNotFoundError):
                    continue
        if not patched:
            raise RuntimeError(
                "operator data-store isolation patched NOTHING — the suite "
                "would write the operator's messages.db / message_queue.db / "
                "gateway_correlation.db / LXMF ratchets"
            )
        yield root


@pytest.fixture(scope="session", autouse=True)
def _block_radio_egress():
    """FOURTH sibling of the isolation gates — and the first that fences the
    ANTENNA rather than the disk.

    2026-08-09: a full suite run on a fleet radio box transmitted the e2e fixture
    ``[RNS:abc] retry with bytes`` onto a live statewide public channel. The
    suite reported 10,535 passed while keying the air; the operator found out
    by looking at his phone. Three session-scoped gates isolated databases and
    NOTHING isolated egress — we fenced the disk and left the antenna open.

    This is the backstop layer. The primary layer is ``utils.tx_guard``, wired
    into each send site, which names the hop and is the ONLY thing that can
    stop the CLI fallback (``meshtastic --host X --sendtext``) because that
    egress happens in a SUBPROCESS this patch cannot see. What this fixture
    adds is coverage of sites nobody guarded — including the meshtastic
    library's own ``TCPInterface``, which opens its socket well below any code
    we wrote.

    Deliberately narrow: only the meshtasticd radio ports. A blanket
    "block non-loopback" fixture would NOT have caught the 08-09 leak, because
    the radio is ON loopback here — and a blanket block would break the
    map-server and MQTT harnesses that legitimately bind sockets.
    """
    import socket as _socket

    from utils import tx_guard

    radio_ports = {4403, 9443}
    orig_connect = _socket.socket.connect
    orig_connect_ex = _socket.socket.connect_ex

    def _check(address, probe_idiom):
        if not (isinstance(address, tuple) and len(address) >= 2):
            return
        host, port = address[0], address[1]
        if port not in radio_ports:
            return
        # `connect_ex` returns an errno instead of raising — that IS the
        # reachability-probe idiom, and ~15 sites in this tree use it to ask
        # "is the radio port open?". Blocking those converts a benign probe
        # into a failure (CI found exactly that in fleet_snapshot._probe_radio)
        # without catching anything: meshtastic's TCPInterface reaches the
        # radio through create_connection -> sock.connect, which stays blocked.
        # A prober that must use plain connect declares tx_guard.probe_connect().
        if probe_idiom or tx_guard.in_probe():
            return
        tx_guard.assert_tx_allowed(
            host, port, kind="socket_tripwire",
            detail="raw socket connect to a meshtasticd radio port",
        )

    def _connect(self, address):
        _check(address, probe_idiom=False)
        return orig_connect(self, address)

    def _connect_ex(self, address):
        _check(address, probe_idiom=True)
        return orig_connect_ex(self, address)

    _socket.socket.connect = _connect
    _socket.socket.connect_ex = _connect_ex
    try:
        yield
    finally:
        _socket.socket.connect = orig_connect
        _socket.socket.connect_ex = orig_connect_ex


@pytest.fixture
def allow_local_radio_tx():
    """Allowlist the local radio for a test whose TRANSPORT IS MOCKED.

    ⚠️ Opt-in, never autouse. Requesting this fixture is a test AUTHOR
    asserting "my socket / subprocess / interface is a double, so permitting
    this target cannot key anything." If that assertion is wrong, the test
    transmits — which is precisely the 2026-08-09 failure.

    Use it for tests that exercise send LOGIC (truncation, dedup registration,
    ACK bookkeeping, return-value handling) where the I/O below is patched.
    Do NOT use it to make a red test green: if a test fails with
    ``TransmitBlocked`` and its transport is NOT mocked, the guard just found a
    real leak.
    """
    from utils import tx_guard

    # 4403/9443 = the radio; 1883 = the local broker meshtasticd relays
    # to RF (a downlink inject keys the radio just as toradio does).
    with tx_guard.allow_targets("127.0.0.1:4403", "127.0.0.1:9443",
                                "127.0.0.1:1883"):
        yield


@pytest.fixture(scope="session", autouse=True)
def _isolate_delivery_counters_db(tmp_path_factory):
    """Keep delivery-counter writes out of the operator's live DB.

    Sibling of ``_isolate_node_cache_files``, same class, found the same
    way. ``delivery_counters`` is SQLite-backed at
    ``~/.local/share/meshforge/delivery_counters.db``, and the coupling is
    INDIRECT: a test never names it — it constructs a
    ``PersistentMessageQueue`` or a bridge, and lifecycle events flow
    through. So per-file fixtures kept missing it.
    ``test_delivery_counters.py`` and ``test_rns_bridge.py`` had one;
    ``test_message_queue.py`` did not, and its module docstring said
    "isolated tests" because its OWN queue DB was.

    ⚠️ FOUR seams, not three. The first version pinned the three
    delivery_counters paths and I verified it by watching exactly those
    two files' mtimes — so ``queue_stats.json``, written by
    ``message_queue`` through its OWN seam, kept being overwritten and my
    "operator DB untouched: YES" check could not see it. Verify a guard
    against EVERY artifact the guarded code writes, not the ones you
    happened to think of; the check that only looks where you already
    looked cannot find what you missed.

    Measured 2026-08-05: the manager box — which runs no gateway — held
    252k queued delivery events accumulated since May, purely from suite
    runs. A full run added ~240; test_message_queue alone ~111. That
    garbage is indistinguishable from real telemetry (``queued`` /
    ``dropped`` with ``queue_shed``, protocols ``meshtastic`` / ``rns`` /
    ``primary``) and was briefly reasoned about AS fleet evidence before
    the write schedule gave it away — three bursts in a 500-event ring,
    two of them during that session's own pytest runs.

    Suite-wide and autouse on purpose: a gate, not a convention. A file
    that sets the env var itself still wins (both point at tmp dirs), and
    a test that needs the real resolution deletes the var explicitly
    (``test_state_path_is_in_share_dir_next_to_db`` already does).

    ⚠️ SESSION-scoped deliberately. The first version was function-scoped
    and cost a ``mktemp`` + two imports + two singleton resets PER TEST —
    across 10,333 tests that added ~45% to suite wall-clock (6:08 → 8:54
    locally) and CANCELLED the CI 3.9 job on its timeout. Redirection is
    the whole job here and it is idempotent, so it belongs at session
    scope; per-test ISOLATION between cases that assert on counter
    CONTENT is the business of those files' own fixtures, which already
    exist and still win. A guard that makes the suite 45% more expensive
    is the wrong trade on a Pi fleet — cheap gates get kept.
    """
    root = tmp_path_factory.mktemp("delivery_counters_isolation")
    prior = {}
    for var, name in (("MESHFORGE_DELIVERY_COUNTERS_DB", "delivery_counters.db"),
                      ("MESHFORGE_DELIVERY_SNAPSHOT_STATE", "delivery_snapshot.json"),
                      ("MESHFORGE_CONTENT_ID_VIEW_STATE", "content_id_view.json"),
                      ("MESHFORGE_QUEUE_STATS_STATE", "queue_stats.json")):
        prior[var] = os.environ.get(var)
        os.environ[var] = str(root / name)
    for alias in ("gateway.delivery_counters", "src.gateway.delivery_counters"):
        try:
            __import__(alias)
            sys.modules[alias]._reset_singleton_for_tests()
        except (ImportError, AttributeError, ModuleNotFoundError, KeyError):
            continue
    yield root
    for var, was in prior.items():
        if was is None:
            os.environ.pop(var, None)
        else:
            os.environ[var] = was


@pytest.fixture
def mock_meshtastic():
    """Mock meshtastic module for tests that don't need real hardware."""
    mock_module = MagicMock()
    mock_interface = MagicMock()
    mock_interface.nodes = {}
    mock_interface.myInfo = MagicMock()
    mock_interface.myInfo.my_node_num = 12345678

    mock_module.serial_interface.SerialInterface.return_value = mock_interface
    mock_module.tcp_interface.TCPInterface.return_value = mock_interface

    with patch.dict('sys.modules', {
        'meshtastic': mock_module,
        'meshtastic.serial_interface': mock_module.serial_interface,
        'meshtastic.tcp_interface': mock_module.tcp_interface,
    }):
        yield mock_module


@pytest.fixture
def mock_rns():
    """Mock RNS module for tests that don't need real Reticulum."""
    mock_module = MagicMock()

    with patch.dict('sys.modules', {
        'RNS': mock_module,
    }):
        yield mock_module


@pytest.fixture
def no_network():
    """Block network access for isolated tests."""
    import socket
    original_socket = socket.socket

    def guarded_socket(*args, **kwargs):
        raise OSError("Network access blocked in test")

    with patch.object(socket, 'socket', guarded_socket):
        yield


# TUI Handler Test Infrastructure (FakeDialog, make_handler_context) lives
# in tests/handler_test_utils.py — the single source of truth. Don't
# re-add definitions here; they were duplicated previously and the copy
# in this file was unused (no test imported it from conftest). Re-adding
# definitions risks silent divergence the next time someone edits one
# and not the other.
