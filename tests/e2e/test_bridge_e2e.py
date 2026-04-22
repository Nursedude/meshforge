"""End-to-end bridge tests via direct BridgedMessage injection.

These exercise the bridge → queue → handler → HTTP path with NO mocking
below the LXMF receive boundary — the layer Issue #40's bugs lived in.
Currently SKELETONS (xfail strict) until harness/pipeline.py is wired.

The test bodies show the intended assertion shape so the implementer
knows the target. Each captures one Issue #40 bug class:
  - bytes content reaches HTTP as decoded text
  - @address routes to a directed Meshtastic node
  - failed HTTP send requeues with JSON-safe payload
"""

from __future__ import annotations

import pytest

from tests.e2e.harness.pipeline import (
    build_bridge_with_real_pipeline,
    make_e2e_config,
    wait_for_queue_idle,
)


@pytest.mark.xfail(reason="harness/pipeline.py is skeleton — see module TODO", strict=True)
def test_lxmf_bytes_reaches_meshtasticd_as_text(
    mock_meshtasticd, gateway_identity_dir, queue_db_path
):
    config = make_e2e_config(
        gateway_identity_dir=gateway_identity_dir,
        queue_db_path=queue_db_path,
        meshtasticd_url=mock_meshtasticd.url,
    )
    bridge = build_bridge_with_real_pipeline(config=config, mock_daemon=mock_meshtasticd)

    from gateway.rns_bridge import BridgedMessage
    msg = BridgedMessage(
        source_network="rns",
        source_id="abcdef01",
        destination_id=None,
        content=b"hello from e2e",
    )
    bridge._process_rns_to_mesh(msg)
    wait_for_queue_idle(bridge, timeout=2.0)

    assert any(
        b"hello from e2e" in body for body in mock_meshtasticd.received_toradio
    )


@pytest.mark.xfail(reason="harness/pipeline.py is skeleton — see module TODO", strict=True)
def test_at_address_routes_directed_to_node(
    mock_meshtasticd, gateway_identity_dir, queue_db_path
):
    config = make_e2e_config(
        gateway_identity_dir=gateway_identity_dir,
        queue_db_path=queue_db_path,
        meshtasticd_url=mock_meshtasticd.url,
    )
    bridge = build_bridge_with_real_pipeline(config=config, mock_daemon=mock_meshtasticd)

    from gateway.rns_bridge import BridgedMessage
    msg = BridgedMessage(
        source_network="rns",
        source_id="abcdef01",
        destination_id=None,
        content=b"@!ebfa1b11 directed test",
    )
    bridge._process_rns_to_mesh(msg)
    wait_for_queue_idle(bridge, timeout=2.0)

    # TODO: parse meshtastic.ToRadio protobuf, assert .packet.to == 0xebfa1b11
    assert mock_meshtasticd.received_toradio, "expected at least one HTTP PUT"


@pytest.mark.xfail(reason="harness/pipeline.py is skeleton — see module TODO", strict=True)
def test_failed_send_requeues_with_str_payload(
    mock_meshtasticd, gateway_identity_dir, queue_db_path
):
    config = make_e2e_config(
        gateway_identity_dir=gateway_identity_dir,
        queue_db_path=queue_db_path,
        meshtasticd_url=mock_meshtasticd.url,
    )
    bridge = build_bridge_with_real_pipeline(config=config, mock_daemon=mock_meshtasticd)

    mock_meshtasticd.stop()  # force send failures

    from gateway.rns_bridge import BridgedMessage
    msg = BridgedMessage(
        source_network="rns",
        source_id="abc",
        destination_id=None,
        content=b"retry with bytes",
    )
    bridge._process_rns_to_mesh(msg)
    wait_for_queue_idle(bridge, timeout=2.0)

    # TODO: open queue_db_path with sqlite3, assert message.payload['message']
    # is str (not bytes) — Issue #40 root cause #1 regression guard at the
    # SQLite layer rather than the in-memory enqueue call.
