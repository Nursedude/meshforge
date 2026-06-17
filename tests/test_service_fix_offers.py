"""Fix-hint -> in-app remediation offer (In-Domain Principle).

The "complete in-pane" follow-on converted service-FIX hint strings
("sudo systemctl start/restart X") into one-keystroke in-app fixes at clean
dialog points: after the action/error, the handler calls
service_remediation.offer_service_fix(ctx, service, running), which routes
through the remediation surface (profile-gated, ratified, applied in-app) —
no shell command for the operator to type.

These pin the wiring at a local-import site (meshtasticd_nodedb) and a
module-import site (rns_interfaces).
"""

import os
import sys
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src', 'launcher_tui'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(__file__))

from handler_test_utils import make_handler_context


def test_nodedb_unreachable_offers_meshtasticd_fix():
    """meshtasticd HTTP API unreachable -> in-app offer to start meshtasticd
    (running=False), not a 'sudo systemctl start' instruction."""
    from handlers.meshtasticd_nodedb import MeshtasticdNodeDBHandler
    h = MeshtasticdNodeDBHandler()
    h.set_context(make_handler_context())

    fake_client = MagicMock()
    fake_client.is_available = False

    with patch('handlers.meshtasticd_nodedb._get_http_client',
               return_value=fake_client):
        with patch('service_remediation.offer_service_fix') as offer:
            h._scan_phantom_nodes()

    assert offer.called, "expected an in-app service-fix offer"
    args, kwargs = offer.call_args
    # offer_service_fix(ctx, "meshtasticd", running=False)
    assert args[1] == "meshtasticd"
    running = kwargs.get("running", args[2] if len(args) > 2 else None)
    assert running is False


def test_rns_remove_interface_offers_rnsd_restart():
    """Removing an interface offers an in-app rnsd restart (running=True),
    not a 'sudo systemctl restart rnsd' instruction."""
    from handlers.rns_interfaces import RNSInterfacesHandler
    h = RNSInterfacesHandler()
    h.set_context(make_handler_context())
    h.ctx.dialog._yesno_returns = [True]  # confirm remove

    cmd_mod = MagicMock()
    cmd_mod.remove_interface.return_value = MagicMock(success=True, message="ok")

    with patch.object(h, '_import_rns_commands', return_value=cmd_mod):
        with patch.object(h, '_rns_pick_interface', return_value="MyIface"):
            with patch('handlers.rns_interfaces.offer_service_fix') as offer:
                h._rns_remove_interface()

    assert offer.called, "expected an in-app rnsd restart offer after removal"
    args, kwargs = offer.call_args
    assert args[1] == "rnsd"
    running = kwargs.get("running", args[2] if len(args) > 2 else None)
    assert running is True


def test_auth_token_diagnostic_launches_repair_wizard():
    """An RPC auth-token mismatch offers the in-app guided RNS Repair wizard
    (which clears stale tokens + restarts rnsd), not a shell rm/systemctl
    runbook."""
    from handlers import _rns_diagnostics_engine as eng
    handler = MagicMock()
    handler.ctx = make_handler_context()
    handler.ctx.dialog._yesno_returns = [True]  # accept the repair
    with patch('handlers._rns_repair.repair_rns_shared_instance') as repair:
        eng.diagnose_rns_connectivity(handler, "RNS: AuthenticationError digest rejected")
    assert repair.called, "auth-token diagnostic must launch the in-app RNS Repair wizard"
    assert repair.call_args[0][0] is handler


def test_auth_token_diagnostic_repair_declined():
    """Declining the offer runs nothing (no auto-repair)."""
    from handlers import _rns_diagnostics_engine as eng
    handler = MagicMock()
    handler.ctx = make_handler_context()
    handler.ctx.dialog._yesno_returns = [False]  # decline
    with patch('handlers._rns_repair.repair_rns_shared_instance') as repair:
        eng.diagnose_rns_connectivity(handler, "RNS: AuthenticationError digest rejected")
    assert not repair.called


# ----------------------------------------------------------------------
# RNS Repair wizard: rpc_key generation step (Issue #41/#37)
# ----------------------------------------------------------------------
def _fake_cfg(exists=True):
    p = MagicMock()
    p.exists.return_value = exists
    return p


def test_rpc_key_step_skips_when_resolvable():
    """A box that already resolves an rpc_key (explicit OR identity-derived)
    is NEVER repinned — that would break its clients."""
    from handlers import _rns_repair
    from utils.paths import ReticulumPaths
    with patch.object(ReticulumPaths, 'get_shared_rpc_key', return_value='a' * 64):
        with patch('utils.rns_config_setup.ensure_rpc_key') as ek:
            result = _rns_repair._ensure_rnsd_rpc_key()
    assert result is False
    assert not ek.called, "must not pin a key when one is already resolvable"


def test_rpc_key_step_pins_when_none():
    """No resolvable key (incl. a deployed __RPC_KEY__ placeholder) -> pin a
    fresh key via the existing ensure_rpc_key writer."""
    from handlers import _rns_repair
    from utils.paths import ReticulumPaths
    with patch.object(ReticulumPaths, 'get_shared_rpc_key', return_value=None):
        with patch.object(ReticulumPaths, 'get_config_file', return_value=_fake_cfg(True)):
            with patch('utils.rns_config_setup.ensure_rpc_key',
                       return_value=('b' * 64, True)) as ek:
                result = _rns_repair._ensure_rnsd_rpc_key()
    assert result is True
    assert ek.called


def test_rpc_key_step_skips_when_no_config():
    from handlers import _rns_repair
    from utils.paths import ReticulumPaths
    with patch.object(ReticulumPaths, 'get_shared_rpc_key', return_value=None):
        with patch.object(ReticulumPaths, 'get_config_file', return_value=_fake_cfg(False)):
            with patch('utils.rns_config_setup.ensure_rpc_key') as ek:
                result = _rns_repair._ensure_rnsd_rpc_key()
    assert result is False
    assert not ek.called


def test_rpc_key_step_oserror_is_graceful():
    """A write failure (no Admin) returns False, never raises into the wizard."""
    from handlers import _rns_repair
    from utils.paths import ReticulumPaths
    with patch.object(ReticulumPaths, 'get_shared_rpc_key', return_value=None):
        with patch.object(ReticulumPaths, 'get_config_file', return_value=_fake_cfg(True)):
            with patch('utils.rns_config_setup.ensure_rpc_key',
                       side_effect=OSError("permission denied")):
                result = _rns_repair._ensure_rnsd_rpc_key()
    assert result is False


def test_offer_rns_client_restarts_targets_gateway_and_map():
    """After a fresh key is pinned, the clients (which rebuild config at start)
    are offered a restart so they read the new key."""
    from handlers import _rns_repair
    ctx = make_handler_context()
    with patch('service_remediation.offer_service_fix') as offer:
        _rns_repair._offer_rns_client_restarts(ctx)
    svcs = [c[0][1] for c in offer.call_args_list]
    assert 'meshforge-gateway' in svcs and 'meshforge-map' in svcs
