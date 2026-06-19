"""Status endpoint mixin for :class:`MapRequestHandler`.

Holds the ``/api/status`` surface — the server health rollup that the
federation poll cycle and the /fleet dashboard consume — plus its
private readers:

- ``_serve_status``            — ``/api/status`` (history/directory stats,
                                 Issue #70/#71 response-cache stats blocks,
                                 federation, radio, watchdog, mini-dudeai)
- ``_get_radio_status_summary`` — TCP/USB radio connectivity summary
- ``_read_watchdog_block``     — /var/lib/meshforge/watchdog.json stitch
- ``_read_mini_state_block``   — ~/mini_dudeai_state.json stitch
- ``_get_local_radio_config``  — local HAT LoRa config via meshtasticd HTTP

The cache-stats shapes surfaced under ``status["directory"]["cache"]``,
``status["geojson"]["cache"]`` and ``status["topology"]["cache"]`` are
test-pinned (Issues #70/#71) — do not alter them here.

Extracted from ``map_http_handler.py`` to keep that file under the
1,500-line size cap (``CLAUDE.md``). No behaviour change — methods are
mixed into ``MapRequestHandler`` via inheritance.
"""

import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

from utils.safe_import import safe_import

_get_connection_manager, _ConnectionMode, _HAS_MESHTASTIC_CONN = safe_import(
    'utils.meshtastic_connection', 'get_connection_manager', 'ConnectionMode'
)


def _serialize_peer_status(s: Any) -> Dict[str, Any]:
    """Serialize one FederationPeerStatus into the /api/status.federation
    peer_status[] shape. Extracted so the field set (and the federated `claw`
    block the node_map claw card reads) is unit-testable without standing up
    the whole status handler."""
    return {
        "hostname": s.hostname,
        "peer_name": s.peer_name,
        "ok": s.ok,
        "last_sync": s.last_sync,
        "last_attempt": s.last_attempt,
        "last_error": s.last_error,
        "last_count": s.last_count,
        "last_latency_ms": s.last_latency_ms,
        "consecutive_failures": s.consecutive_failures,
        # Backoff state (Issue #59): a peer the collector is intentionally
        # not polling right now still appears here, with these fields set,
        # so the operator sees a labeled row rather than a mysteriously-
        # absent peer.
        "in_backoff": s.in_backoff,
        "backoff_multiplier": s.backoff_multiplier,
        "next_eligible_poll_ts": s.next_eligible_poll_ts,
        # Federated dude-claw edge telemetry (None on claw-less peers) — read
        # by the node_map claw card so the federator shows a peer's claw.
        "claw": getattr(s, "claw", None),
    }


class StatusEndpointsMixin:
    """``/api/status`` endpoint + its watchdog/mini/radio readers."""

    def _serve_status(self):
        """Serve server status including radio connection info."""
        status = {
            "status": "running",
            "time": datetime.now().isoformat(),
            "collector": self.collector is not None,
        }

        # Include history stats if available
        if self.collector and self.collector._history:
            try:
                status["history"] = self.collector._history.get_stats()
            except Exception:
                status["history"] = None

            # Directory stats (Issue #49) — persistent per-node cache
            # across protocols, with tiered retention. Surfaces total
            # count, by-network, by-source-origin, last-seen range so
            # operators can see at a glance how many MeshCore/AREDN/RNS
            # nodes are cached and which retention tier they fall into.
            try:
                status["directory"] = self.collector._history.get_directory_stats()
            except Exception as e:
                logger.debug(f"directory stats lookup failed: {e}")
                status["directory"] = None

            # Response-cache observability (Issue #70). Surfaces hit/miss
            # counts so operators can spot regressions where the cache
            # gets bypassed — e.g. a future endpoint refactor that
            # stops calling get_or_build, or a TTL too short to coalesce
            # the federation poll pattern. Pattern mirrors Issue #63's
            # always-on canary visibility.
            try:
                cache = getattr(
                    self.collector, "_directory_response_cache", None
                )
                if cache is not None and isinstance(status.get("directory"), dict):
                    status["directory"]["cache"] = cache.stats()
                    status["directory"]["cache"]["ttl_s"] = cache.ttl_s
            except Exception as e:
                logger.debug(f"directory cache stats lookup failed: {e}")

            # External-bulk bbox-filter + federation-skip counters (node
            # count optimization §E). Same shape pattern as the cache
            # blocks above. Surfaces:
            #   - bbox: the active filter rectangle (None if disabled)
            #   - bbox_dropped: per-source counts of out-of-bbox features
            #     dropped during the most recent collect()
            #   - federated_skipped_persistence: federation rows held in
            #     memory only and not UPSERTed to nodes/
            try:
                if (hasattr(self.collector, "get_bbox_filter_stats")
                        and isinstance(status.get("directory"), dict)):
                    status["directory"]["bbox_filter"] = (
                        self.collector.get_bbox_filter_stats()
                    )
            except Exception as e:
                logger.debug(f"bbox_filter stats lookup failed: {e}")

        # /api/nodes/geojson response cache (Issue #71). Same shape as
        # the directory cache block above. Surfaced under its own top-
        # level key because geojson has no parent stats block to attach
        # to. Missing-attr fallback covers fresh-after-upgrade collectors.
        if self.collector:
            try:
                geo_cache = getattr(
                    self.collector, "_geojson_response_cache", None
                )
                if geo_cache is not None:
                    status["geojson"] = {
                        "cache": {**geo_cache.stats(), "ttl_s": geo_cache.ttl_s}
                    }
            except Exception as e:
                logger.debug(f"geojson cache stats lookup failed: {e}")

            # /api/network/topology response cache (Issue #71). Same
            # shape as the geojson block above; third instance of the
            # wedge class that #70 + #71 closed across the daemon.
            try:
                topo_cache = getattr(
                    self.collector, "_topology_response_cache", None
                )
                if topo_cache is not None:
                    status["topology"] = {
                        "cache": {**topo_cache.stats(), "ttl_s": topo_cache.ttl_s}
                    }
            except Exception as e:
                logger.debug(f"topology cache stats lookup failed: {e}")

        # Per-source collection diagnostics from the most recent collect() call.
        # Operators use this to answer "why is source X empty" without a code reader.
        if self.collector:
            try:
                status["source_diagnostics"] = self.collector.get_source_diagnostics()
            except Exception as e:
                logger.debug(f"Failed to fetch source diagnostics: {e}")

            # Per-network breakdown of position-less nodes (MeshCore lives here).
            try:
                no_pos = self.collector.get_nodes_without_position()
                by_network: Dict[str, int] = {}
                for entry in no_pos:
                    net = entry.get("network", "unknown")
                    by_network[net] = by_network.get(net, 0) + 1
                status["nodes_without_position"] = {
                    "total": len(no_pos),
                    "by_network": by_network,
                }
            except Exception as e:
                logger.debug(f"Failed to summarize nodes_without_position: {e}")

        # Federation health (Issue #49 follow-up). Surfaces per-peer
        # status so monitoring can alert on stale federation: a peer
        # in `consecutive_failures > 3` for >5min means we're missing
        # whatever directory entries that box was contributing.
        if self.collector and getattr(self.collector, "_federation", None):
            try:
                snap = self.collector._federation.get_snapshot()
                status["federation"] = {
                    "enabled": True,
                    "peers": list(self.collector._federation.peers),
                    "last_sync": snap.last_sync,
                    "last_attempt": snap.last_attempt,
                    "federated_node_count": len(snap.by_node),
                    "peer_status": [
                        _serialize_peer_status(s)
                        for s in snap.peer_status.values()
                    ],
                }
            except Exception as e:
                logger.debug(f"federation status lookup failed: {e}")
                status["federation"] = {"enabled": True, "error": str(e)[:200]}
        else:
            status["federation"] = {"enabled": False, "peers": [], "peer_status": []}

        # Include radio connection status + LOCAL radio config
        # (helps operators diff heterogeneous fleet boxes — e.g. LongFast vs SHORT_TURBO)
        status["radio"] = self._get_radio_status_summary()
        status["radio_config"] = self._get_local_radio_config()

        # Watchdog passthrough (Phase 1 reliability layer). The watchdog
        # daemon writes /var/lib/meshforge/watchdog.json atomic-rename
        # every 30s; we just read and stitch into /api/status so the
        # existing federation poll cycle carries the signal to the
        # /fleet rollup. Degrades silently if the file's absent (the
        # watchdog isn't installed yet on this box, or hasn't ticked
        # since boot). Cheap: file is small (~1-2 KB even with many
        # signals), one read per /api/status hit.
        status["watchdog"] = self._read_watchdog_block()

        # mini-dudeai passthrough (the local 24/7 sub-agent). Same pattern as
        # the watchdog block: read its operator-home state file and stitch it
        # in so federation carries "is the local watcher alive + what's it
        # seeing" to the /fleet rollup and to a warm cloud session. Degrades
        # silently if mini isn't installed on this box.
        status["mini_dudeai"] = self._read_mini_state_block()

        # dude-claw passthrough (the ESP32-S3 edge node). Same read-a-state-file
        # pattern: claw_metrics_push.py captures the claw's last NATS telemetry
        # tick to ~/claw_last_tick.json; we stitch it in so federation carries
        # the claw's posture to the /fleet rollup. Absent on every box without a
        # claw (silently {"installed": false}). Display only — no probe/DB; the
        # claw_metrics cron already pages on the claw's death.
        status["claw"] = self._read_claw_state_block()

        data = json.dumps(status).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self._send_cors_header()
        self.end_headers()
        self.wfile.write(data)

    def _get_radio_status_summary(self) -> Dict[str, Any]:
        """Get a summary of radio connection status for the status endpoint."""
        if not _HAS_MESHTASTIC_CONN:
            return {"available": False, "error": "meshtastic library not installed"}

        # Check TCP port (meshtasticd)
        tcp_available = False
        try:
            import socket
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(1)
                tcp_available = sock.connect_ex(('localhost', 4403)) == 0
        except Exception as e:
            logger.debug(f"TCP port check failed: {e}")

        # Check USB serial device
        import glob
        usb_devices = glob.glob('/dev/ttyUSB*') + glob.glob('/dev/ttyACM*')
        usb_available = len(usb_devices) > 0

        # Determine connection mode
        if tcp_available:
            mode = "tcp"
            connected = True
        elif usb_available:
            mode = "serial"
            connected = True
        else:
            mode = "none"
            connected = False

        return {
            "connected": connected,
            "mode": mode,
            "tcp_available": tcp_available,
            "usb_available": usb_available,
            "usb_devices": usb_devices if usb_available else [],
        }

    _WATCHDOG_STATE_PATH = Path("/var/lib/meshforge/watchdog.json")
    _WATCHDOG_STALE_S = 300.0  # 5 min — 10x the watchdog's 30s tick

    def _read_watchdog_block(self) -> Dict[str, Any]:
        """Stitch /var/lib/meshforge/watchdog.json into /api/status.

        Federation polls /api/status across peers, so signals ride that
        cycle to the /fleet rollup with no new HTTP plumbing. Issue #54
        peer_name correlation labels rows for free.

        Degrades silently when the watchdog isn't installed yet
        (returns ``{"installed": false}``) so legacy boxes during
        rollout report a coherent shape rather than a missing key.
        Reports ``stale`` when the JSON exists but is older than
        ``_WATCHDOG_STALE_S`` so the operator sees "watchdog wedged"
        even when the watchdog itself is the wedged service.
        """
        try:
            raw = self._WATCHDOG_STATE_PATH.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {"installed": False, "reason": "no_state_file"}
        except OSError as exc:
            return {"installed": False, "reason": f"read_error: {exc}"}

        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, ValueError) as exc:
            return {"installed": True, "ok": False,
                    "reason": f"malformed_json: {exc}"}

        if not isinstance(payload, dict):
            return {"installed": True, "ok": False,
                    "reason": "malformed_json: not an object"}

        ts = payload.get("ts")
        age_s: Optional[float] = None
        if isinstance(ts, (int, float)):
            age_s = max(0.0, time.time() - float(ts))

        stale = bool(age_s is not None and age_s > self._WATCHDOG_STALE_S)
        block = {
            "installed": True,
            "ok": bool(payload.get("ok", True)) and not stale,
            "ts": ts,
            "age_s": age_s,
            "probe_count": payload.get("probe_count"),
            "signals": payload.get("signals", []),
        }
        if stale:
            block["reason"] = (
                f"stale: last write {age_s:.0f}s ago "
                f"(threshold {self._WATCHDOG_STALE_S:.0f}s) — watchdog "
                f"daemon may have crashed"
            )
        return block

    _MINI_STALE_S = 300.0  # 5 min — 10x mini-dudeai's 30s tick

    def _read_mini_state_block(self) -> Dict[str, Any]:
        """Stitch ~/mini_dudeai_state.json into /api/status.

        mini-dudeai is the local 24/7 sub-agent; surfacing its state here lets
        the federation poll carry "is the watcher alive, what's active, what's
        firing" to the fleet rollup and to a warm cloud session — the same
        zero-new-plumbing trick as the watchdog block. Operator-home path (the
        daemon runs as the operator, not root), so resolve via
        get_real_user_home() (MF001). Degrades silently when mini isn't
        installed on this box; reports stale when the tick clock has stopped.
        """
        try:
            from utils.paths import get_real_user_home
            path = get_real_user_home() / "mini_dudeai_state.json"
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {"installed": False, "reason": "no_state_file"}
        except OSError as exc:
            return {"installed": False, "reason": f"read_error: {exc}"}

        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, ValueError) as exc:
            return {"installed": True, "ok": False, "reason": f"malformed_json: {exc}"}
        if not isinstance(payload, dict):
            return {"installed": True, "ok": False, "reason": "malformed_json: not an object"}

        ts = payload.get("last_tick_ts")
        age_s: Optional[float] = None
        if isinstance(ts, (int, float)):
            age_s = max(0.0, time.time() - float(ts))
        stale = bool(age_s is not None and age_s > self._MINI_STALE_S)

        rules = payload.get("rules") or {}
        active = [
            {"rule_id": rs.get("rule_id"), "subject": rs.get("subject"),
             "detail": rs.get("last_detail", "")}
            for rs in rules.values()
            if isinstance(rs, dict) and rs.get("currently_active")
        ]
        top = sorted(
            ({"rule_id": rs.get("rule_id"), "subject": rs.get("subject"),
              "fire_count_24h": rs.get("fire_count_24h", 0)}
             for rs in rules.values()
             if isinstance(rs, dict) and rs.get("fire_count_24h")),
            key=lambda r: r["fire_count_24h"], reverse=True,
        )[:5]

        block = {
            "installed": True,
            "ok": not stale,
            "ts": ts,
            "last_tick_iso": payload.get("last_tick_iso"),
            "age_s": age_s,
            "host": payload.get("host"),
            "rule_count": payload.get("rule_count"),
            "error_count": payload.get("error_count"),
            "active_rules": active,
            "top_rules_24h": top,
        }
        if stale:
            block["reason"] = (
                f"stale: last tick {age_s:.0f}s ago "
                f"(threshold {self._MINI_STALE_S:.0f}s) — mini-dudeai "
                f"daemon may have crashed"
            )
        return block

    # 3x the */5-min claw_metrics capture cadence: captured every 5 min, called
    # stale (capture cron stopped) after 15.
    _CLAW_STALE_S = 900.0

    def _read_claw_state_block(self) -> Dict[str, Any]:
        """Stitch ~/claw_last_tick.json into /api/status.

        claw_metrics_push.py captures the dude-claw's last NATS telemetry tick
        (heap/uptime/wifi-rssi/ble counters) to this operator-home file every
        5 min; we surface it so federation carries the claw's posture to the
        /fleet rollup with no new plumbing. Operator-home path (MF001).

        Honesty (the design's "stale must render stale, never green-with-old-
        numbers"): two distinct degraded states both force ``ok`` False with a
        reason — ``stale`` (capture cron stopped) and an unreachable tick (the
        claw didn't answer at last capture, ``ok`` False in the file). Absent
        file → ``{"installed": false}`` so claw-less boxes report a coherent
        shape rather than a missing key.
        """
        try:
            from utils.paths import get_real_user_home
            path = get_real_user_home() / "claw_last_tick.json"
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {"installed": False, "reason": "no_state_file"}
        except OSError as exc:
            return {"installed": False, "reason": f"read_error: {exc}"}

        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, ValueError) as exc:
            return {"installed": True, "ok": False, "reason": f"malformed_json: {exc}"}
        if not isinstance(payload, dict):
            return {"installed": True, "ok": False, "reason": "malformed_json: not an object"}

        ts = payload.get("captured_at")
        age_s: Optional[float] = None
        if isinstance(ts, (int, float)):
            age_s = max(0.0, time.time() - float(ts))
        stale = bool(age_s is not None and age_s > self._CLAW_STALE_S)
        tick_ok = bool(payload.get("ok"))

        block = {
            "installed": True,
            "ok": tick_ok and not stale,
            "captured_at": ts,
            "captured_iso": payload.get("captured_iso"),
            "age_s": age_s,
            "host": payload.get("host"),
            "device": payload.get("device"),
            "device_info": payload.get("device_info"),
            "ble": payload.get("ble"),
        }
        if stale:
            block["reason"] = (
                f"stale: last capture {age_s:.0f}s ago "
                f"(threshold {self._CLAW_STALE_S:.0f}s) — claw_metrics cron "
                f"may have stopped"
            )
        elif not tick_ok:
            errs = payload.get("errors") or {}
            detail = ", ".join(f"{k}: {v}" for k, v in errs.items()) if isinstance(errs, dict) else ""
            block["reason"] = (
                f"claw_unreachable: claw did not answer at last capture"
                + (f" ({detail})" if detail else "")
            )
        return block

    def _get_local_radio_config(self) -> Dict[str, Any]:
        """Read the LOCAL Meshtastic HAT's LoRa config via meshtasticd HTTP /json/report.

        Surfaced in /api/status so operators can diff two fleet boxes on incompatible
        presets (e.g. one on SHORT_TURBO vs another on LongFast) without SSHing to
        each to query meshtasticd — they legitimately can't hear each other over RF.

        Returns a dict with frequency_hz / lora_channel / region / modem_preset
        (whatever meshtasticd exposes) plus an 'available' bool.
        """
        try:
            from utils.meshtastic_http import get_http_client
            client = get_http_client()
            if not client.is_available:
                # availability_reason distinguishes "webserver down" from
                # "meshtasticd never serves /json/*" (Issue #76)
                return {"available": False, "reason": client.availability_reason}
            raw = client.get_report_raw()
            if not raw:
                return {"available": False, "reason": "no /json/report response"}
            radio = raw.get("radio", {}) or {}
            config = raw.get("config", {}) or {}
            lora = config.get("lora", {}) or {}
            return {
                "available": True,
                "frequency_hz": radio.get("frequency"),
                "lora_channel": radio.get("lora_channel"),
                "region": radio.get("region") or lora.get("region"),
                "modem_preset": radio.get("modem_preset") or lora.get("modem_preset"),
                "channel_num": lora.get("channel_num"),
                "hw_model": (raw.get("device", {}) or {}).get("hw_model"),
            }
        except Exception as e:
            logger.debug(f"Local radio config lookup failed: {e}")
            return {"available": False, "reason": str(e)[:120]}
