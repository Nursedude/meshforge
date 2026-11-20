"""Refuse a Claude-session driver's writes to the operator's LIVE stores.

Born from deferred row ``drill-scripts-write-live-delivery-db`` (10-27): a
non-author reviewer RAN real MessageQueues outside pytest, with clock steps,
from a session shell. conftest's isolation fixtures only exist under pytest,
so ~1,000 fabricated ``expired`` rows landed in the live
``delivery_counters.db`` and read as the manager box conf_rate 0.0 for 10 days —
fabricated traffic in a truth source (honest_failure_modes #1, via tooling).

Arming (all must hold), MEASURED 2026-11-19 on the manager box:
  * ``CLAUDECODE == "1"`` — set in the harness's Bash shells; carried by NO
    daemon (map, mini, digest, root watchdog_runner, unattended-upgrades);
    NOT inherited by ``systemd-run --user`` children.
  * not under pytest — the suite has its own isolation (conftest) and the
    guard must not double-gate it. Keyed on the marker conftest sets at import
    (:data:`PYTEST_MARKER`) or PYTEST_CURRENT_TEST — NOT on ``pytest`` being
    imported: a reviewer driver that imports a test helper is exactly the
    population this guards.
  * no ``<APP>_ALLOW_LIVE_STORES=1`` — the deliberate opt-in, its prefix taken
    from the store's own override (``MESHFORGE_…`` / ``MESHANCHOR_…``), so this
    file stays BYTE-IDENTICAL across the twins (``scripts/parity_check.py``).

When armed, a store path under an operator home (the uid's, SUDO_USER's,
LOGNAME's — the same identities ``utils.paths.get_real_user_home`` consults,
never ``$HOME``: a scratch ``HOME`` with SUDO_USER/LOGNAME unset is the
sanctioned isolation recipe) is refused with :class:`LiveStoreRefused` —
unless the path IS the store's own env override (the deliberate scratch
knob; any other path is still judged). A ``BaseException`` like ``tx_guard.TransmitBlocked``: every write
path here sits in a broad ``except Exception``, and a refusal it could absorb
would leave the driver running on a silently-missing store.

SEATBELT, not a jail: it stops ACCIDENTAL session drivers on this box. A
command sent over ssh does not carry CLAUDECODE, and anyone can unset it.
"""

import os
from pathlib import Path
from typing import Set, Union

ALLOW_SUFFIX = "_ALLOW_LIVE_STORES"
#: Set by each twin's tests/conftest.py at import (twin-neutral name).
PYTEST_MARKER = "LIVE_STORE_GUARD_UNDER_PYTEST"


class LiveStoreRefused(BaseException):
    """A session driver tried to write an operator's live store."""


def _under_pytest() -> bool:
    return (os.environ.get(PYTEST_MARKER) == "1"
            or "PYTEST_CURRENT_TEST" in os.environ)


def _operator_homes() -> Set[Path]:
    import pwd
    homes = set()
    names = [os.environ.get("SUDO_USER", ""), os.environ.get("LOGNAME", "")]
    try:
        homes.add(Path(pwd.getpwuid(os.getuid()).pw_dir).resolve())
    except KeyError:
        pass
    for name in names:
        if name and name != "root":
            try:
                homes.add(Path(pwd.getpwnam(name).pw_dir).resolve())
            except KeyError:
                pass
    return homes


def allow_env_for(override_env: str) -> str:
    """``MESHFORGE_DELIVERY_COUNTERS_DB`` -> ``MESHFORGE_ALLOW_LIVE_STORES``."""
    return override_env.split("_", 1)[0] + ALLOW_SUFFIX


def armed(override_env: str) -> bool:
    return (os.environ.get("CLAUDECODE") == "1"
            and os.environ.get(allow_env_for(override_env)) != "1"
            and not _under_pytest())


def refuse_live_store(path: Union[str, Path], override_env: str) -> None:
    """Raise :class:`LiveStoreRefused` if an armed session would write the
    operator's live ``path``. No-op for daemons, pytest, and opt-ins."""
    if not armed(override_env):
        return
    resolved = Path(path).expanduser().resolve()  # symlinks + .. (homes resolve too)
    override = os.environ.get(override_env)
    if override and Path(override).expanduser().resolve() == resolved:
        return
    for home in _operator_homes():
        if resolved == home or home in resolved.parents:
            raise LiveStoreRefused(
                f"refusing to write the LIVE store {resolved} from a Claude "
                f"session driver (CLAUDECODE=1, outside pytest) - fabricated "
                f"rows become real telemetry. Point it at scratch: "
                f"{override_env}=<scratch path>; or, deliberately: "
                f"{allow_env_for(override_env)}=1")
