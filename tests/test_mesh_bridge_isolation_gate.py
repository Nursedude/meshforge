"""The suite must never open the operator's live mesh_bridge queues.

``MeshtasticPresetBridge.__init__`` builds ``p2s.db`` / ``s2p.db`` under
``gateway.mesh_bridge.get_real_user_home()/.config/meshforge/
mesh_bridge_queues`` through ITS OWN import. Until 2026-11-11 only each test
FILE's own ``monkeypatch`` fixture redirected it, so a new test file that
forgot the fixture — or a red-first run against old code — opened the live
queues (this repo's live dir on the manager box carries a ``p2s.db-shm`` touched
2026-10-29 on a box that runs no gateway). The conftest
``_OPERATOR_STORE_MODULES`` gate must redirect THAT import.
"""

from gateway import mesh_bridge
from utils.paths import get_real_user_home


def test_mesh_bridge_home_is_redirected(_isolate_operator_data_stores):
    root = _isolate_operator_data_stores
    assert mesh_bridge.get_real_user_home() == root, (
        "gateway.mesh_bridge.get_real_user_home is NOT redirected — a "
        "MeshtasticPresetBridge() in a test would open the live queue DBs")


def test_default_bridge_queues_land_under_isolated_root(
        _isolate_operator_data_stores):
    from gateway.config import GatewayConfig
    cfg = GatewayConfig()
    cfg.mesh_bridge.enabled = True
    bridge = mesh_bridge.MeshtasticPresetBridge(config=cfg)
    try:
        for q in (bridge._primary_to_secondary, bridge._secondary_to_primary):
            path = str(q._db_path)
            assert path.startswith(str(_isolate_operator_data_stores)), path
            assert not path.startswith(str(get_real_user_home())), path
    finally:
        for q in (bridge._primary_to_secondary, bridge._secondary_to_primary):
            close = getattr(q, "close", None)
            if callable(close):
                close()
