"""MeshForge NomadNet wrapper — refuses-loud on rpc_key mismatch.

Version: 8

NomadNet's TextUI startup calls RNS.Reticulum.get_interface_stats(),
which opens a multiprocessing.connection.Client to rnsd's RPC socket.
Two distinct failures arrive through that codepath:

  - Transient (rnsd not yet up, or briefly down): swallow + degrade
    (empty interface stats, NomadNet UI still renders).
  - AuthenticationError: rpc_key mismatch — rnsd and NomadNet are using
    different config dirs / different identities / different rpc_keys.
    This is the Issue #46 fleet-wide failure mode. Swallowing it just
    hides the bug behind an empty stats panel and keeps the underlying
    "every RPC fails" condition unresolved. Refuse-loud, exit 87,
    and let the operator fix the alignment via:

      MeshForge TUI > NomadNet > Service Control > Repair RNS alignment
    or
      sudo /opt/meshforge/scripts/rns_alignment.py normalize

The systemd unit pairs this with StartLimitBurst to park the service
in failed state after a few retries, so the journal carries one clear
diagnostic line instead of 392 silent restarts.

Both ``scripts/install_nomadnet.sh`` and the TUI's
``_create_nomadnet_wrapper`` copy this file verbatim into
``~/.config/meshforge/nomadnet_wrapper.py``. Bumping the ``Version:``
line above forces both sides to refresh — see ``_WRAPPER_VERSION`` in
``src/launcher_tui/handlers/_nomadnet_install_utils.py``.
"""
import sys
from multiprocessing.context import AuthenticationError
import RNS

_orig_get_interface_stats = RNS.Reticulum.get_interface_stats

_FALLBACK = dict(interfaces=[])

# Transient RPC failures: degrade to empty stats, keep UI alive.
_TRANSIENT_EXC = (
    ConnectionRefusedError,
    BrokenPipeError,
    TypeError,
    KeyError,
    OSError,
)

# Sentinel exit code so systemd + the TUI can recognise this failure.
_EXIT_AUTH_MISMATCH = 87


def _safe_get_interface_stats(self):
    try:
        result = _orig_get_interface_stats(self)
    except AuthenticationError as e:
        # Refuse-loud — this is the actual root cause (Issue #46).
        sys.stderr.write(
            "\n[meshforge nomadnet_wrapper] RNS rpc_key MISMATCH detected.\n"
            f"  Underlying error: {type(e).__name__}: {e}\n"
            "  rnsd and NomadNet are using different identities / rpc_keys.\n"
            "  FIX:\n"
            "    MeshForge TUI > NomadNet > Service Control > Repair RNS alignment\n"
            "  or:\n"
            "    sudo python3 /opt/meshforge/scripts/rns_alignment.py normalize\n"
            "\n"
        )
        sys.stderr.flush()
        # Hard-exit before NomadNet's TUI loop starts crash-spinning.
        sys.exit(_EXIT_AUTH_MISMATCH)
    except _TRANSIENT_EXC:
        return _FALLBACK
    if not isinstance(result, dict) or 'interfaces' not in result:
        return _FALLBACK
    return result


RNS.Reticulum.get_interface_stats = _safe_get_interface_stats

from nomadnet.nomadnet import main
sys.exit(main())
