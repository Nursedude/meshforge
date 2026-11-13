"""MQTT downlink injection — display cross-bridge traffic with TRUE origin.

The default mesh injection path (`send_text_direct` → `/api/v1/toradio`)
rewrites the packet's `from` to the local radio's node id, so meshtasticd's
web client at :9443 never renders gateway-injected traffic as *incoming* —
the speaker-not-listener blind spot (see
`project_moc_lf_visibility_dig_2026_06_03` / `project_moc_dual_radio_st_bridge`).

This module crafts a Meshtastic protobuf MQTT **downlink** ServiceEnvelope
carrying the real origin node id and publishes it to meshtasticd's broker on
the channel's encrypted (`-e-`) topic. meshtasticd decrypts it (channel PSK),
treats it as a heard packet, attributes it to the spoofed origin, and pushes
it to API clients — so :9443 shows "moc2: hello" natively.

Step-0 field proof (moc, 2026-06-03): both a known node (moc2) and a
never-heard id decoded + displayed with true-origin attribution; meshtasticd
did NOT re-uplink the `via_mqtt` packet to json and the gateway can't decode
the raw `-e-` protobuf, so this injection path has no MQTT re-bridge loop.
See `project_mqtt_downlink_injection_arc_plan`.

Wire-format constants are pinned by tests against the exact bytes meshtasticd
accepted in step 0 — do not change them without re-proving on a live radio.
"""

import base64
import logging
import secrets
import socket
import struct
import threading
import time
from typing import Optional, Tuple

from utils.safe_import import safe_import
from utils.tx_guard import TransmitBlocked, assert_tx_allowed

logger = logging.getLogger(__name__)

# Protobuf — external dep, optional
_mesh_pb2, _HAS_MESH_PB2 = safe_import('meshtastic.protobuf.mesh_pb2')
_mqtt_pb2, _HAS_MQTT_PB2 = safe_import('meshtastic.protobuf.mqtt_pb2')
_portnums_pb2, _HAS_PORTNUMS = safe_import('meshtastic.protobuf.portnums_pb2')
_mqtt_mod, _HAS_MQTT = safe_import('paho.mqtt.client', 'Client')

# cryptography — external dep, optional
try:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    _HAS_CRYPTO = True
except ImportError:  # pragma: no cover - exercised only on minimal installs
    Cipher = algorithms = modes = None  # type: ignore
    _HAS_CRYPTO = False

_HAS_DOWNLINK_DEPS = (
    _HAS_MESH_PB2 and _HAS_MQTT_PB2 and _HAS_PORTNUMS and _HAS_MQTT and _HAS_CRYPTO
)

BROADCAST_NUM = 0xFFFFFFFF

#: How long _ensure_connected waits for the broker's CONNACK (#25). The
#: broker is local (meshtasticd's mosquitto), so a healthy one answers in ms;
#: this bounds what a wedged one costs each send before the caller falls back.
CONNACK_TIMEOUT_S = 5.0

#: After a failed connect, inject() refuses fast (False → caller's toradio
#: fallback) for this long instead of paying CONNACK_TIMEOUT_S again per call
#: (two calls per message: nodeinfo + text).
CONNECT_RETRY_S = 30.0

#: Bound on wait_for_publish; a QoS-0 message not written to the socket
#: within it is a failed publish.
PUBLISH_TIMEOUT_S = 5.0

#: MQTT keepalive to the (local) broker. It sets the stall window: QoS-0
#: True means "written to the socket", so a broker that wedges AFTER CONNACK
#: reads True until paho's keepalive gives up at 2 x KEEPALIVE_S (Fable r2
#: F1, real mosquitto: 60 s / 16 sends lost at 30). QoS 0 kept by operator
#: decision 2026-11-12; the window is MEASURED, not accepted — re-measure it
#: with any change here.
KEEPALIVE_S = 5

#: How long _teardown lets a healthy session close itself before breaking
#: the socket.
TEARDOWN_GRACE_S = 0.5


def _xor_hash(data: bytes) -> int:
    """Meshtastic's xorHash — XOR-fold of all bytes (used for channel hash)."""
    h = 0
    for b in data:
        h ^= b
    return h


def channel_hash(channel_name: str, psk: bytes) -> int:
    """Single-byte channel hash = xorHash(name) ^ xorHash(psk).

    Pinned: "meshforge" + the moc ch2 PSK == 0x7a (step-0 accepted value).
    """
    return _xor_hash(channel_name.encode()) ^ _xor_hash(psk)


def _encrypt(packet_id: int, from_node: int, psk: bytes, plaintext: bytes) -> bytes:
    """AES-256-CTR with Meshtastic's nonce layout.

    Nonce = packetId (u64 LE) || fromNode (u32 LE) || 0x00000000 (16 bytes).
    """
    nonce = struct.pack("<QI", packet_id, from_node) + b"\x00" * 4
    cipher = Cipher(algorithms.AES(psk), modes.CTR(nonce))
    enc = cipher.encryptor()
    return enc.update(plaintext) + enc.finalize()


def build_downlink_envelope(
    channel_name: str,
    psk: bytes,
    origin_node: int,
    text: str,
    *,
    packet_id: Optional[int] = None,
    destination: int = BROADCAST_NUM,
    hop_limit: int = 3,
    root_topic: str = "msh",
) -> Tuple[str, bytes, int]:
    """Build the (topic, serialized ServiceEnvelope, packet_id) for a TEXT downlink.

    Pure function — no I/O — so the wire format is unit-testable against the
    step-0-accepted bytes. ``origin_node`` is the TRUE source node number the
    message should display as (e.g. 0xddfb8065 for moc2).
    """
    data = _mesh_pb2.Data()
    data.portnum = _portnums_pb2.PortNum.TEXT_MESSAGE_APP
    data.payload = text.encode("utf-8")
    return _build_envelope(
        channel_name, psk, origin_node, data,
        packet_id=packet_id, destination=destination,
        hop_limit=hop_limit, root_topic=root_topic,
    )


def build_nodeinfo_envelope(
    channel_name: str,
    psk: bytes,
    origin_node: int,
    long_name: str,
    short_name: str,
    *,
    hw_model: Optional[str] = None,
    packet_id: Optional[int] = None,
    destination: int = BROADCAST_NUM,
    hop_limit: int = 3,
    root_topic: str = "msh",
) -> Tuple[str, bytes, int]:
    """Build a NODEINFO_APP downlink so the receiving radio learns a node's NAME.

    Without this the radio shows a bridged origin as bare hex (!ddfb8065)
    because it has never heard that node's NodeInfo (it's on the other RF
    segment). Inject this once per origin before its first text downlink so
    :9443 renders "moc2: ..." instead of "!ddfb8065: ...".
    """
    user = _mesh_pb2.User()
    user.id = f"!{origin_node:08x}"
    user.long_name = long_name
    user.short_name = short_name
    if hw_model:
        # Tolerate unknown/missing hw model — name display doesn't need it.
        hw_enum = getattr(_mesh_pb2.HardwareModel, str(hw_model).upper(), None)
        if hw_enum is not None:
            user.hw_model = hw_enum

    data = _mesh_pb2.Data()
    data.portnum = _portnums_pb2.PortNum.NODEINFO_APP
    data.payload = user.SerializeToString()
    return _build_envelope(
        channel_name, psk, origin_node, data,
        packet_id=packet_id, destination=destination,
        hop_limit=hop_limit, root_topic=root_topic,
    )


def _build_envelope(
    channel_name: str,
    psk: bytes,
    origin_node: int,
    data,
    *,
    packet_id: Optional[int],
    destination: int,
    hop_limit: int,
    root_topic: str,
) -> Tuple[str, bytes, int]:
    """Wrap+encrypt a Data protobuf into a published ServiceEnvelope tuple."""
    if not _HAS_DOWNLINK_DEPS:
        raise RuntimeError("downlink injection deps unavailable (protobuf/crypto/paho)")
    if len(psk) != 32:
        raise ValueError(f"expected 32-byte AES-256 PSK, got {len(psk)}")

    if packet_id is None:
        # High bit set to avoid colliding with meshtasticd's small ids.
        packet_id = secrets.randbits(31) | 0x40000000

    pkt = _mesh_pb2.MeshPacket()
    pkt.id = packet_id
    setattr(pkt, "from", origin_node)
    pkt.to = destination
    pkt.channel = channel_hash(channel_name, psk)
    pkt.hop_limit = hop_limit
    pkt.hop_start = hop_limit
    pkt.encrypted = _encrypt(packet_id, origin_node, psk, data.SerializeToString())

    env = _mqtt_pb2.ServiceEnvelope()
    env.packet.CopyFrom(pkt)
    env.channel_id = channel_name
    env.gateway_id = f"!{origin_node:08x}"

    topic = f"{root_topic}/2/e/{channel_name}/!{origin_node:08x}"
    return topic, env.SerializeToString(), packet_id


class DownlinkInjector:
    """Persistent MQTT client that injects true-origin downlinks.

    Lazily connects; ``inject`` returns False on any failure so callers can
    fall back to the toradio path (never silently drop a message).
    """

    def __init__(
        self,
        broker: str,
        port: int,
        channel_name: str,
        psk_b64: str,
        *,
        username: str = "",
        password: str = "",
        root_topic: str = "msh",
        client_id: Optional[str] = None,
    ):
        self._broker = broker
        self._port = port
        self._channel_name = channel_name
        self._username = username
        self._password = password
        self._root_topic = root_topic
        self._client_id = client_id or f"meshforge-downlink-{secrets.randbits(16):04x}"

        self._psk = base64.b64decode(psk_b64) if psk_b64 else b""
        self._lock = threading.Lock()
        self._client = None
        self._retry_after = 0.0   # monotonic; refuse-fast window after a failed connect
        self._closed = False      # close() is terminal until reopen()

        # Surface a clear reason once if the injector can't operate, so a
        # misconfigured downlink mode degrades loudly to toradio instead of
        # silently no-op'ing every message.
        self._fatal_reason: Optional[str] = None
        if not _HAS_DOWNLINK_DEPS:
            self._fatal_reason = "downlink deps unavailable (protobuf/crypto/paho)"
        elif len(self._psk) != 32:
            self._fatal_reason = (
                f"downlink_psk must decode to 32 bytes, got {len(self._psk)}"
            )

    @property
    def usable(self) -> bool:
        return self._fatal_reason is None

    @property
    def fatal_reason(self) -> Optional[str]:
        return self._fatal_reason

    @property
    def connected(self) -> bool:
        """paho's own session state: True only after CONNACK rc 0 and until
        the session is lost (paho ``_handle_connack`` / connection-lost)."""
        client = self._client
        return client is not None and client.is_connected()

    def _teardown(self) -> None:
        """Stop the current client's network thread (never raises).

        Without this a reconnect left the old client's loop thread running —
        and paho auto-reconnecting it — beside the new one."""
        client, self._client = self._client, None
        if client is None:
            return
        # Each step on its own: a raise in one must not skip loop_stop(), or
        # the old client keeps auto-reconnecting under our client_id beside
        # the next one (Fable r3 F2).
        try:
            # Graceful first: a healthy session writes DISCONNECT and paho's
            # loop thread closes the socket itself, within ms.
            client.disconnect()
            deadline = time.monotonic() + TEARDOWN_GRACE_S
            while client.socket() is not None and time.monotonic() < deadline:
                time.sleep(0.01)
        except Exception as e:  # teardown of a dead session; nothing to save
            logger.debug("Downlink injector teardown (disconnect): %s", e)
        try:
            # Then break the socket: paho's loop thread exits only once its
            # output queue drains, so on a wedged peer loop_stop() joined
            # until keepalive closed it — up to 2 x KEEPALIVE_S under
            # self._lock (Fable r2 F2/F4).
            sock = client.socket()
            if sock is not None:
                sock.shutdown(socket.SHUT_RDWR)
        except Exception as e:
            logger.debug("Downlink injector teardown (shutdown): %s", e)
        finally:
            try:
                client.loop_stop()
            except Exception as e:
                logger.debug("Downlink injector teardown (loop_stop): %s", e)

    def _ensure_connected(self) -> bool:
        """True only while the broker has ACCEPTED the session (CONNACK rc 0).

        #25 (2026-11-12, measured on paho 2.1.0): ``connect()`` returns once
        TCP is up and paho will write a QoS-0 PUBLISH before any CONNACK, so a
        broker that accepts TCP but never answers (a wedged mosquitto) read as
        connected and every inject() returned True into the void. The truth
        is paho's ``is_connected()``, never a flag of ours (review r1 F4/F8).

        After a failed connect, refuse fast for ``CONNECT_RETRY_S`` so a wedged
        broker costs one CONNACK wait per window, not one per call — the
        caller's toradio fallback is the honest path meanwhile (r1 F2).
        """
        if self._closed:
            return False
        if self.connected:
            return True
        now = time.monotonic()
        if now < self._retry_after:
            return False
        self._teardown()
        accepted = threading.Event()
        result = {"rc": None}

        def _on_connect(client, userdata, flags, rc, *rest):
            result["rc"] = int(getattr(rc, "value", rc))
            accepted.set()

        try:
            client = _mqtt_mod(client_id=self._client_id)
            if self._username:
                client.username_pw_set(self._username, self._password)
            client.on_connect = _on_connect
            self._client = client
            client.connect(self._broker, self._port, keepalive=KEEPALIVE_S)
            client.loop_start()
            accepted.wait(CONNACK_TIMEOUT_S)
            ok = client.is_connected()
        except Exception as e:
            logger.warning("Downlink injector connect failed: %s", e)
            ok = False
            result["rc"] = result["rc"] if result["rc"] is not None else "error"
        if not ok:
            rc = result["rc"]
            if rc is None:
                reason = f"no CONNACK within {CONNACK_TIMEOUT_S:g} s"
            elif rc == "error":
                reason = "connect error"
            else:
                reason = f"broker refused the session (CONNACK rc {rc})"
            logger.warning(
                "Downlink injector connect to %s:%d failed: %s — refusing for "
                "%g s (callers fall back to toradio)",
                self._broker, self._port, reason, CONNECT_RETRY_S)
            self._teardown()
            self._retry_after = time.monotonic() + CONNECT_RETRY_S
            return False
        self._retry_after = 0.0
        logger.info(
            "Downlink injector connected to %s:%d (channel=%s)",
            self._broker, self._port, self._channel_name,
        )
        return True

    def _publish(self, kind: str, origin_node: int, builder) -> bool:
        """Build (via ``builder``) and publish one envelope. Returns True only
        on confirmed publish; never raises (callers fall back)."""
        if not self.usable:
            return False
        # RF egress chokepoint. This publish is not "just MQTT": meshtasticd
        # subscribes to this topic and TRANSMITS the envelope, so it keys the
        # radio as surely as /api/v1/toradio does. Guarded before connecting.
        # The catch is DELIBERATE (see tx_guard docstring): this method's
        # contract is "never raises, callers fall back", every fallback path
        # carries its own guard, and the refusal is already recorded+logged
        # by the guard — so False here is loud degradation, not a swallow.
        try:
            assert_tx_allowed(self._broker, self._port,
                              kind="mqtt_downlink_inject",
                              detail=f"downlink inject kind={kind}")
        except TransmitBlocked:
            return False
        # Build BEFORE touching the session: a bad input is not a broker
        # fault and must not tear down a healthy session (Fable r2 F5).
        try:
            topic, payload, packet_id = builder()
        except Exception as e:
            logger.warning("Downlink %s envelope build failed: %s", kind, e)
            return False
        with self._lock:
            if not self._ensure_connected():
                return False
            try:
                info = self._client.publish(topic, payload, qos=0)
                info.wait_for_publish(timeout=PUBLISH_TIMEOUT_S)
                # wait_for_publish RETURNS on timeout (paho 2.x); a message
                # still queued in the client is not published (r1 F1).
                if not info.is_published():
                    raise RuntimeError(
                        f"not written to the broker within {PUBLISH_TIMEOUT_S:g} s")
                logger.info(
                    "Downlink %s injected: id=0x%08x from=!%08x topic=%s",
                    kind, packet_id, origin_node, topic,
                )
                return True
            except Exception as e:
                logger.warning("Downlink %s inject failed: %s", kind, e)
                # Drop the session so the next attempt reconnects cleanly.
                self._teardown()
                return False

    def inject(
        self,
        text: str,
        origin_node: int,
        *,
        destination: int = BROADCAST_NUM,
        hop_limit: int = 3,
    ) -> bool:
        """Publish one TEXT downlink. Returns True only on confirmed publish."""
        return self._publish("text", origin_node, lambda: build_downlink_envelope(
            self._channel_name, self._psk, origin_node, text,
            destination=destination, hop_limit=hop_limit, root_topic=self._root_topic,
        ))

    def inject_nodeinfo(
        self,
        origin_node: int,
        long_name: str,
        short_name: str,
        *,
        hw_model: Optional[str] = None,
        destination: int = BROADCAST_NUM,
        hop_limit: int = 3,
    ) -> bool:
        """Publish a NODEINFO downlink so the radio learns the node's NAME."""
        return self._publish("nodeinfo", origin_node, lambda: build_nodeinfo_envelope(
            self._channel_name, self._psk, origin_node, long_name, short_name,
            hw_model=hw_model, destination=destination, hop_limit=hop_limit,
            root_topic=self._root_topic,
        ))

    def close(self):
        """Terminal until reopen(): an inject queued on the lock behind this
        must not open a session nobody will close (Fable r3 F1)."""
        with self._lock:
            self._closed = True
            self._teardown()

    def reopen(self):
        """Undo close() for a restart in place: MeshBridge.stop() closes,
        start() reopens, so any stop() -> start() on one bridge keeps downlink."""
        with self._lock:
            self._closed = False
            self._retry_after = 0.0
