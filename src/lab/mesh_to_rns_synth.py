"""Mesh→RNS bridge synth — inject synthetic Meshtastic traffic into a live
gateway via MQTT (Thread-2 Phase 2).

The deterministic counterpart to ``scripts/validate_rns_to_mesh.py`` (which
drives R→M by sending an LXMF message the gateway bridges to mesh). This
drives the OTHER direction — M→R — by publishing a synthetic Meshtastic
JSON text message onto the gateway's MQTT broker exactly as ``meshtasticd``
would, so the gateway's ``MQTTBridgeHandler`` ingests it, bridges it to RNS,
and (with reply routing on) writes the ``m2r`` correlation row that
restart-survivable reply routing depends on. Its reason for existing: a
**deterministic checkpoint driver** so the Mesh→RNS path can be exercised on
demand instead of waiting on a quiet real mesh.

Injection contract (verified against ``gateway/mqtt_bridge_handler.py``
``_process_json_message``):
  topic   : ``{root}/[{region}/]2/json/{channel_name}/{node_id}``
  payload : ``{"from": <int>, "to": <int>, "sender": "!hex8",
              "channel": <int>, "type": "text", "payload": {"text": "..."}}``
  - ``to == 0xFFFFFFFF`` is a broadcast → bridged to the gateway's
    ``default_lxmf_destination`` fan-out (the path that writes m2r rows).
  - the text must NOT carry a leading ``[RNS:xxxx]`` tag or the gateway's
    loop guard (``is_already_bridged``) drops it before bridging.

Synthetic nodes live in a reserved, obviously-fake NodeNum range
(``0x5E700000 | index`` → ``!5e70xxxx``) so a synth injection can never be
mistaken for — or collide with — a real mesh node, and is trivially
greppable in journals.

⚠️ A LIVE broadcast injection fans out to the gateway's REAL
``default_lxmf_destination`` (operator NomadNet inboxes). Use ``--dry-run``
to verify the exact wire shape with no broker, and only inject live against a
broker you own / with operator awareness — this is a lab tool, not a prod path.
"""

from __future__ import annotations

import argparse
import json
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

logger = logging.getLogger("lab.mesh_to_rns_synth")

BROADCAST_NODE_NUM = 0xFFFFFFFF
SYNTH_NODE_BASE = 0x5E700000     # reserved synthetic range → !5e70xxxx
SYNTH_NODE_MASK = 0x0000FFFF
DEFAULT_ROOT = "msh"
DEFAULT_CHANNEL_NAME = "LongFast"

# The gateway drops any text whose leading tag marks it as already-bridged
# FROM RNS (loop guard). A synth PING must never wear that tag, or it is
# silently dropped before the M→R bridge — which would look like a synth
# failure that is really a (correct) loop-guard hit.
_LOOP_GUARD_PREFIX = "[RNS:"


# --- pure builders -------------------------------------------------------

def synth_node_num(index: int) -> int:
    """Stable synthetic NodeNum for synth user ``index`` (reserved range)."""
    return SYNTH_NODE_BASE | (int(index) & SYNTH_NODE_MASK)


def synth_node_id(index: int) -> str:
    """Synthetic ``!hex8`` node id for synth user ``index``."""
    return f"!{synth_node_num(index):08x}"


def build_meshtastic_text_json(
    *, from_num: int, text: str, to_num: int = BROADCAST_NODE_NUM,
    channel_index: int = 0,
) -> dict:
    """Build a meshtasticd-shaped JSON text message dict.

    Mirrors the fields ``MQTTBridgeHandler._process_json_message`` reads:
    ``from``/``to`` (int NodeNums), ``sender`` (!hex8 fallback), ``channel``
    (int), ``type``, and ``payload.text`` (the message body).
    """
    return {
        "from": int(from_num),
        "to": int(to_num),
        "sender": f"!{int(from_num):08x}",
        "channel": int(channel_index),
        "type": "text",
        "payload": {"text": text},
    }


def build_json_topic(
    *, root: str, channel_name: str, node_id: str, region: str = "",
) -> str:
    """Build the ``{root}/[{region}/]2/json/{channel_name}/{node_id}`` topic.

    ``region`` empty → the region-less shape (meshtasticd 2.7.15+, and the
    fleet's ``region=""`` default per Issue #34). Matches the gateway's
    ``{root}/+/2/json/{ch}/#`` AND ``{root}/2/json/{ch}/#`` subscriptions.
    """
    nid = node_id if node_id.startswith("!") else f"!{node_id}"
    if region:
        return f"{root}/{region}/2/json/{channel_name}/{nid}"
    return f"{root}/2/json/{channel_name}/{nid}"


def _default_text(seq: int, user_index: int) -> str:
    """A readable, loop-guard-safe synth PING body (greppable, seq-stamped)."""
    return f"synth-m2r seq={seq} from=synth-u{user_index}"


# --- result records ------------------------------------------------------

@dataclass(frozen=True)
class InjectionRecord:
    """One published synthetic message (what + where)."""
    seq: int
    topic: str
    node_id: str
    text: str
    broadcast: bool


@dataclass
class SynthReport:
    """Outcome of a synth run."""
    injected: int = 0
    failed: int = 0
    records: List[InjectionRecord] = field(default_factory=list)

    def as_dict(self) -> Dict:
        return {
            "injected": self.injected,
            "failed": self.failed,
            "records": [
                {"seq": r.seq, "topic": r.topic, "node_id": r.node_id,
                 "text": r.text, "broadcast": r.broadcast}
                for r in self.records
            ],
        }


# --- orchestrator --------------------------------------------------------

# A publisher is any callable (topic, payload_json_str) -> None. In
# production it is paho's ``client.publish``; in tests it is a fake that
# records calls — so the orchestrator is fully testable without a broker.
Publisher = Callable[[str, str], None]


class MeshToRnsSynth:
    """Drives synthetic Meshtastic→RNS injections through a publisher."""

    def __init__(
        self, publisher: Publisher, *, root: str = DEFAULT_ROOT,
        channel_name: str = DEFAULT_CHANNEL_NAME, channel_index: int = 0,
        region: str = "",
    ):
        self._publish = publisher
        self._root = root
        self._channel_name = channel_name
        self._channel_index = int(channel_index)
        self._region = region

    def inject(
        self, text: str, *, seq: int = 0, user_index: int = 0,
        broadcast: bool = True, to_num: Optional[int] = None,
    ) -> InjectionRecord:
        """Publish one synthetic Meshtastic text message.

        Raises ``ValueError`` on a loop-guard-tagged text (it would be
        silently dropped by the gateway — fail loud instead).
        """
        if text.lstrip().startswith(_LOOP_GUARD_PREFIX):
            raise ValueError(
                f"synth text starts with the {_LOOP_GUARD_PREFIX} loop-guard "
                "tag — the gateway would drop it before bridging")
        from_num = synth_node_num(user_index)
        node_id = synth_node_id(user_index)
        dest = BROADCAST_NODE_NUM if broadcast else int(
            to_num if to_num is not None else BROADCAST_NODE_NUM)
        payload = build_meshtastic_text_json(
            from_num=from_num, text=text, to_num=dest,
            channel_index=self._channel_index)
        topic = build_json_topic(
            root=self._root, channel_name=self._channel_name,
            node_id=node_id, region=self._region)
        self._publish(topic, json.dumps(payload))
        return InjectionRecord(
            seq=seq, topic=topic, node_id=node_id, text=text,
            broadcast=dest == BROADCAST_NODE_NUM)

    def run(
        self, count: int, *, interval_s: float = 1.0,
        text_fn: Optional[Callable[[int, int], str]] = None,
        user_index: int = 0, broadcast: bool = True,
        stop_event: Optional[threading.Event] = None,
    ) -> SynthReport:
        """Inject ``count`` messages, paced ``interval_s`` apart.

        ``text_fn(seq, user_index)`` builds each body (defaults to a
        seq-stamped synth PING). A ``stop_event`` (if given) makes the
        pacing interruptible (MF010) and ends the run early when set.
        """
        text_fn = text_fn or _default_text
        report = SynthReport()
        for seq in range(count):
            if stop_event is not None and stop_event.is_set():
                break
            body = text_fn(seq, user_index)
            try:
                rec = self.inject(
                    body, seq=seq, user_index=user_index, broadcast=broadcast)
                report.injected += 1
                report.records.append(rec)
                logger.info("synth m2r tx seq=%d topic=%s text=%r",
                            seq, rec.topic, body)
            except Exception as e:
                report.failed += 1
                logger.warning("synth m2r inject failed seq=%d: %s", seq, e)
            if seq + 1 < count:
                if stop_event is not None:
                    if stop_event.wait(interval_s):
                        break
                else:
                    time.sleep(interval_s)
        return report


# --- CLI -----------------------------------------------------------------

def _dry_run_publisher(topic: str, payload: str) -> None:
    """Print what WOULD be published (no broker)."""
    print(f"PUBLISH {topic}\n        {payload}")


def _make_mqtt_publisher(host: str, port: int):
    """Build a live paho-MQTT publisher (connects + loop_start)."""
    from utils.safe_import import safe_import
    # Module-only: "client" is neither an attribute nor a submodule of
    # paho.mqtt.client, so the attr form reported "not installed" on boxes
    # where paho IS installed (2026-07-21 re-review of the 54a65060 flip).
    mqtt, has_mqtt = safe_import("paho.mqtt.client")
    if not has_mqtt:
        raise RuntimeError(
            "paho-mqtt not installed — use --dry-run or `pip install paho-mqtt`")
    if hasattr(mqtt, "CallbackAPIVersion"):
        client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION1)
    else:
        client = mqtt.Client()
    client.connect(host, port, keepalive=30)
    client.loop_start()

    def _publish(topic: str, payload: str) -> None:
        client.publish(topic, payload, qos=0)

    _publish._client = client  # keep a handle for teardown
    return _publish


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--count", type=int, default=1,
                        help="number of messages to inject (default 1)")
    parser.add_argument("--interval", type=float, default=1.0,
                        help="seconds between injections (default 1.0)")
    parser.add_argument("--text", default=None,
                        help="message body (default: seq-stamped synth PING)")
    parser.add_argument("--user-index", type=int, default=0,
                        help="synth user index → !5e70xxxx node (default 0)")
    parser.add_argument("--root", default=DEFAULT_ROOT,
                        help="MQTT root topic (default 'msh')")
    parser.add_argument("--channel-name", default=DEFAULT_CHANNEL_NAME,
                        help="channel NAME in the topic (default 'LongFast')")
    parser.add_argument("--channel-index", type=int, default=0,
                        help="channel index in the payload (default 0)")
    parser.add_argument("--region", default="",
                        help="region segment (default '' = region-less)")
    parser.add_argument("--broker-host", default="127.0.0.1")
    parser.add_argument("--broker-port", type=int, default=1883)
    parser.add_argument("--dry-run", action="store_true",
                        help="print messages instead of publishing (no broker)")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    publisher = None
    if args.dry_run:
        publisher = _dry_run_publisher
    else:
        try:
            publisher = _make_mqtt_publisher(args.broker_host, args.broker_port)
        except Exception as e:
            logger.error("MQTT connect failed: %s", e)
            return 2

    synth = MeshToRnsSynth(
        publisher, root=args.root, channel_name=args.channel_name,
        channel_index=args.channel_index, region=args.region)

    text_fn = (lambda seq, ui: args.text) if args.text else None
    report = synth.run(
        args.count, interval_s=args.interval, text_fn=text_fn,
        user_index=args.user_index)

    client = getattr(publisher, "_client", None)
    if client is not None:
        try:
            client.loop_stop()
            client.disconnect()
        except Exception:
            pass

    logger.info("synth done: injected=%d failed=%d",
                report.injected, report.failed)
    return 0 if report.failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
