"""Fleet / gateway / lab endpoint mixin for :class:`MapRequestHandler`.

Holds the fleet-observability and gateway-introspection surfaces:

- ``/fleet/slo``            — MeshAnchor ``/fleet/rollup`` peer contract
- ``/fleet/logs``           — allowlisted journalctl slice per unit
- ``/fleet/tracer-fires``   — per-fire tracer detail for one peer
- ``/fleet/tests``          — allowlisted lab-unit list (+ last fire)
- ``/fleet/run-test``       — POST: fire an allowlisted lab unit
- ``/fleet/cascade``        — cascade detector + recovery actor snapshot
- ``/api/gateway/delivery`` — DeliveryCounters snapshot (Fork C)
- ``/api/gateway/queue``    — queue backpressure stats (Issue #74)
- ``/api/network/interfaces`` — cached RNS Transport.interfaces snapshot
- ``/api/network/rns/paths``  — cached RNS path_table snapshot
- ``/lab/rollup*``          — lab rollup markdown variants

Extracted from ``map_http_handler.py`` to keep that file under the
1,500-line size cap (``CLAUDE.md``). No behaviour change — methods are
mixed into ``MapRequestHandler`` via inheritance and rely on the hub's
``self._serve_json`` / ``self._serve_text``.
"""

import logging
import os
import socket
import time

logger = logging.getLogger(__name__)

from utils.safe_import import safe_import

# Issue #74: the class is PersistentMessageQueue — the old
# 'MessageQueue' name never existed, so _HAS_MSG_QUEUE was always
# False and the /api/messages/queue SQLite branch was dead code
# (silently served the cache-file fallback).
_MessageQueue, _HAS_MSG_QUEUE = safe_import(
    'gateway.message_queue', 'PersistentMessageQueue'
)


class FleetEndpointsMixin:
    """Fleet dashboard, gateway introspection and lab rollup endpoints."""

    def _serve_fleet_cascade(self):
        """Cascade detector snapshot — pre-failure fingerprints state.

        Track 0C of the federation→DB pressure→wedge cascade arc.
        Surfaces degraded-but-not-dead subsystem state BEFORE it
        cascades to the operator-visible failure mode (tracer rollup
        100% timeout, etc.). See plan:
        ``~/.claude/plans/we-have-a-cycle-jolly-wadler.md``.

        Returns the in-memory state from `cascade_detector.get_singleton()`.
        Cheap — single dict copy under the detector's lock.
        """
        try:
            from utils.cascade_detector import get_singleton
            snap = get_singleton().get_snapshot()
            # Fork B — recovery actor state co-located here so operators
            # have one place to see "what tripped" + "what we did about
            # it." Missing block (early-boot or import failure) is
            # treated as "not active" rather than failing the whole
            # endpoint.
            try:
                from utils.cascade_recovery_actions import get_singleton as get_recovery
                snap["recovery"] = get_recovery().snapshot()
            except Exception as re:
                snap["recovery"] = {
                    "started": False,
                    "error": f"recovery_unavailable: {re}",
                }
            self._serve_json(snap, status=200)
        except Exception as e:
            self._serve_json(
                {"error": "detector_unavailable", "reason": str(e)},
                status=500,
            )

    def _serve_gateway_delivery(self):
        """Fork C — honest delivery lifecycle counters.

        Returns the in-memory ``DeliveryCounters.snapshot()`` from the
        gateway's per-process counter set: per-state totals
        (QUEUED/SENT/CONFIRMED/DROPPED), drop-reason histogram,
        per-protocol breakdown, confirmation_rate, and a recent-events
        ring (last 50 by default).

        Counters are process-lifetime monotonic. Restart resets — for
        durable per-message trace, use the queue's
        ``message_lifecycle`` table.

        Pairs with ``/fleet/cascade`` (Fork A+B) so operators have
        "what's wedging" + "what's actually delivering" in one place.
        """
        try:
            from gateway.delivery_counters import snapshot as _delivery_snap
            self._serve_json(_delivery_snap(), status=200)
        except Exception as e:
            self._serve_json(
                {"error": "delivery_counters_unavailable", "reason": str(e)},
                status=500,
            )

    def _serve_gateway_queue(self):
        """Queue backpressure stats for the watchdog (Issue #74).

        Serves ``PersistentMessageQueue.get_stats()`` — queue_depth,
        max_queue_size, queue_usage_pct, dead_letter, pending,
        in_progress, failed — so ``probe_queue_backlog`` can judge
        backlog/dead-letter pressure over localhost HTTP. The watchdog
        runs as root in a hardened sandbox and must NOT resolve the
        operator's home to open the queue DB itself (the #60-class
        trap); the map daemon already runs as the operator and owns
        the read.
        """
        if not _HAS_MSG_QUEUE:
            self._serve_json(
                {"error": "message_queue_unavailable"}, status=503,
            )
            return
        try:
            queue = _MessageQueue()
            stats = queue.get_stats()
            stats["timestamp"] = time.time()
            self._serve_json(stats, status=200)
        except Exception as e:
            self._serve_json(
                {"error": "queue_stats_unavailable", "reason": str(e)},
                status=500,
            )

    def _serve_rns_interfaces(self):
        """Read-only snapshot of RNS.Transport.interfaces.

        Cache refreshed by `_collect_rns_direct` every ~60s. Endpoint
        is a single dict copy — never walks Transport.interfaces.
        Shape:

            {"ts": unix_ts, "available": bool, "reason": str|None,
             "interfaces": [
                {"name": str, "kind": str, "online": bool,
                 "rxb": int, "txb": int, "bitrate": int|None,
                 "hw_mtu": int|None, "age_s": float|None}
             ]}

        Track 2.3 of the federation→DB pressure→wedge cascade arc.
        Pairs with `/api/network/rns/paths` to answer "which interface
        is this message flowing through" + "does the destination have a
        known path." See `we-have-a-cycle-jolly-wadler.md`.
        """
        try:
            from utils._map_collector_rns import get_cached_interface_snapshot
            snap = get_cached_interface_snapshot()
            self._serve_json(snap, status=200)
        except Exception as e:
            self._serve_json(
                {"error": "snapshot_unavailable", "reason": str(e)},
                status=500,
            )

    def _serve_rns_paths(self):
        """Read-only snapshot of RNS Transport.path_table.

        Cache refreshed by `_collect_rns_direct` every ~60s. Endpoint
        itself never walks path_table — single dict copy. Shape:

            {"ts": unix_ts, "available": bool, "reason": str|None,
             "paths": [
                {"dest_hash": "<hex>", "hops": int|None,
                 "next_hop": "<hex>"|None,
                 "via_interface": "<name>"|None,
                 "last_heard": float|None}
             ]}

        Track 0B of the federation→DB pressure→wedge cascade arc — gives
        the operator the "where did this go?" answer for the RNS half.
        See `we-have-a-cycle-jolly-wadler.md`.
        """
        try:
            from utils._map_collector_rns import get_cached_path_table_snapshot
            snap = get_cached_path_table_snapshot()
            self._serve_json(snap, status=200)
        except Exception as e:
            self._serve_json(
                {"error": "snapshot_unavailable", "reason": str(e)},
                status=500,
            )

    def _serve_fleet_slo(self):
        """Serve the MeshAnchor `/fleet/slo` peer contract.

        MA's `/fleet/rollup` polls this on every peer in its fleet.json.
        Producing the same shape MA emits for itself lets a MF box show
        up in the rollup without running a full MA daemon — Path B per
        the `feedback_consolidate_dont_add` consolidation pattern.
        """
        from utils.fleet_snapshot import build_slo_snapshot
        self._serve_json(build_slo_snapshot(collector=self.collector))

    def _serve_fleet_truth(self):
        """Serve the honest fleet-truth SSOT (`/api/fleet/truth`).

        The tri-state whole-domain truth layer that BOTH the visual NOC
        (`web/fleet.html`) and an incoming Claude session's orientation read —
        same bytes, so the human view and the machine view can never disagree.
        Self-aggregates `/fleet/slo`+`/api/status` across `fleet_hosts`
        (TTL-cached); a peer that can't be reached is a DARK box, never a
        dropped row. Aggregates only data these two endpoints already LAN-expose
        (no new secret surface), and carries `resolution_method` + `alias` for
        DNS visibility — never a raw LAN IP (MF014/MF015).
        """
        from utils.fleet_truth_collector import DEFAULT_PORT, get_fleet_truth
        # Self-fetch on the ACTUAL bound port (2026-07-19 review) — a map on a
        # non-default port previously self-fetched :5000 and read itself dark
        # (or read whatever else listened there). Mirrors the MA twin.
        try:
            port = int(getattr(self.server, "server_port", 0)) or DEFAULT_PORT
        except (TypeError, ValueError):
            port = DEFAULT_PORT
        self._serve_json(get_fleet_truth(port=port))

    # Cross-box dedup rollup is considered stale (collector likely dead) past
    # this age — surfaced as its own axis so a frozen file can't read as live.
    _FLEET_DUPS_STALE_S = 1800.0

    def _serve_fleet_dups(self):
        """Serve the cross-box dedup rollup (dedup/identity arc STEP 4c).

        Reads the rollup state file written by the operator-cron collector
        (``utils.fleet_dup_collector``). The handler does NO ssh — collection
        runs in the operator's context where the fleet ssh keys live, and a
        gateway-only box (moc3) is only reachable by ssh-cat, so the cron
        owns the round trips and the endpoint just serves the cached result
        (fast + safe).

        Honest self-guards (honest_failure_modes #2): an absent file is
        reported as ``unavailable`` (never a healthy "0 dups"); a rollup
        older than the stale window carries ``freshness.stale=true`` on its
        OWN axis so a dead collector can't serve a frozen verdict as live.
        The JOIN's own ``status`` (ok / indeterminate) is left intact —
        collection-freshness and the dup-verdict are two separate axes."""
        import json as _json
        import time as _time
        try:
            from utils.fleet_dup_collector import rollup_state_path
            try:
                raw = rollup_state_path().read_text(encoding="utf-8")
            except FileNotFoundError:
                self._serve_json({
                    "status": "unavailable",
                    "reason": ("no rollup yet — fleet_dup_collector has not "
                               "run on this box (wire its cron on the "
                               "manager)"),
                }, status=200)
                return
            rollup = _json.loads(raw)
            gen = rollup.get("generated_at")
            if isinstance(gen, (int, float)) and not isinstance(gen, bool):
                age = max(0.0, _time.time() - float(gen))
                rollup["freshness"] = {
                    "age_s": age,
                    "stale": age > self._FLEET_DUPS_STALE_S,
                    "threshold_s": self._FLEET_DUPS_STALE_S,
                }
            else:
                rollup["freshness"] = {"age_s": None, "stale": True,
                                       "threshold_s": self._FLEET_DUPS_STALE_S}
            self._serve_json(rollup, status=200)
        except Exception as e:
            self._serve_json(
                {"error": "fleet_dups_unavailable", "reason": str(e)},
                status=500,
            )

    # ─────────────────────────────────────────────────────────────────
    # /fleet/logs — T1 of fleet dashboard roadmap
    #
    # Returns a recent slice of a systemd unit's journal so the operator
    # can scan ERROR/WARN across the fleet from the dashboard instead of
    # ssh-tailing per box. Unit allowlist prevents arbitrary log dump
    # (the daemon runs as a user; sudo isn't required for the units we
    # ship, but we still want the surface bounded).
    # ─────────────────────────────────────────────────────────────────
    _FLEET_LOG_UNITS = {
        # System-scope units (sudo journalctl -u <unit>)
        "meshforge-map":   ("system", "Map dashboard daemon"),
        "meshforge":       ("system", "MeshForge gateway"),
        "meshforge-maps":  ("system", "MeshForge maps :8808"),
        "rnsd":            ("system", "Reticulum daemon"),
        "meshtasticd":     ("system", "Meshtastic radio daemon"),
        "mosquitto":       ("system", "MQTT broker"),
        # User-scope units (journalctl --user -u <unit>)
        "meshforge-tracer":     ("user", "Lab tracer (10-min fire)"),
        "meshforge-echo":       ("user", "Lab echo responder"),
        "meshforge-synth-soak": ("user", "Lab synth soak (hourly fire)"),
        "meshforge-lab-rollup": ("user", "Lab rollup writer"),
        "nomadnet":             ("user", "NomadNet TUI (tmux)"),
    }

    _FLEET_LOG_MAX_N = 200
    _FLEET_LOG_DEFAULT_N = 50

    def _serve_fleet_logs(self):
        """Return recent journalctl lines for an allowlisted unit.

        Query params:
          unit=<allowlisted-name>        required
          n=<int 1..200>                 optional (default 50)
          priority=err|warn|info|debug   optional (default warn)

        Response shape:
          {
            "unit": "meshforge-tracer",
            "scope": "user",
            "priority": "warn",
            "n": 50,
            "host": "<hostname>",
            "lines": [
              {"ts": <unix float>, "level": "WARNING", "msg": "..."},
              ...
            ]
          }

        Errors:
          400 if unit unknown or n/priority out of range
          403 if the caller is not loopback / a configured LAN origin
          500 on journalctl failure (returns lines:[] + error string)
        """
        # Journal slices routinely carry LAN IPs, peer hostnames, and debug
        # secrets; on a 0.0.0.0 bind this must not be readable by an untrusted
        # network. The MA dashboard fetches it from the LAN, so LAN is trusted.
        if self._reject_if_untrusted():
            return
        from urllib.parse import urlparse, parse_qs

        qs = parse_qs(urlparse(self.path).query)
        unit = (qs.get("unit", [""])[0] or "").strip()
        if unit not in self._FLEET_LOG_UNITS:
            self._serve_json(
                {"error": "unit not allowlisted",
                 "allowed": sorted(self._FLEET_LOG_UNITS.keys())},
                status=400,
            )
            return
        scope, _desc = self._FLEET_LOG_UNITS[unit]

        try:
            n = int(qs.get("n", [self._FLEET_LOG_DEFAULT_N])[0])
        except (ValueError, TypeError):
            n = self._FLEET_LOG_DEFAULT_N
        n = max(1, min(self._FLEET_LOG_MAX_N, n))

        priority = (qs.get("priority", ["warn"])[0] or "warn").lower()
        if priority not in ("err", "warning", "warn", "info", "debug",
                            "notice", "crit", "alert", "emerg"):
            priority = "warn"
        # journalctl accepts "warning" but the operator-facing alias is "warn".
        journal_priority = "warning" if priority == "warn" else priority

        from utils.fleet_logs import fetch_unit_logs
        payload = fetch_unit_logs(
            unit=unit, scope=scope, n=n, priority=journal_priority,
        )
        self._serve_json(payload)

    def _serve_fleet_tracer_fires(self):
        """Return this host's recent tracer fires for one peer.

        T1 drilldown surface: the dashboard's Federation Round-Trip
        table shows aggregate stats per (src, dst) pair. Operator
        clicks a cell → MA's JS fetches
        ``http://<src>:5000/fleet/tracer-fires?peer=<dst>&since=<unix>``
        and renders the per-fire detail (timestamp, RTT, result).

        Data source: per-fire JSON in
        ``~/.local/state/meshforge/tracer/tracer-<unix>.json``
        (written by the meshforge-tracer.service). This endpoint
        reads ONE host's state — cross-host aggregation belongs
        elsewhere (the lab rollup already does that via ssh+cat).

        Query params:
          peer=<short-name>      required (operator short hostname)
          since=<unix>           optional (default now - 1h, clamped to 24h)
          limit=<int 1..200>     optional (default 60)

        Response shape:
          {
            "host": "<this hostname>",
            "peer": "<peer>",
            "since_unix": <float>,
            "fires": [{
              "fire_at_unix": <float>,
              "fire_at_iso": "<iso>",
              "self_short": "<host's own short name>",
              "seq": <int>,
              "result": "ok"|"fail"|...,
              "rtt_ms": <int|null>
            }, ...],
            "fires_total_seen": <int>,
            "truncated": <bool>
          }
        """
        from urllib.parse import urlparse, parse_qs
        from utils.tracer_fires import parse_query, get_recent_fires

        qs = parse_qs(urlparse(self.path).query)
        peer, since_unix, limit, err = parse_query(qs)
        if err is not None:
            self._serve_json({"error": err}, status=400)
            return
        payload = get_recent_fires(
            peer=peer, since_unix=since_unix, limit=limit,
        )
        self._serve_json(payload)

    # ─────────────────────────────────────────────────────────────────
    # /fleet/tests — T1.5 of fleet dashboard roadmap
    #
    # Operator-triggered, allowlisted lab-unit fires from the dashboard.
    # GET /fleet/tests           → list the available tests + last-fire
    #                              metadata (when did it last run)
    # POST /fleet/run-test       → fire one (body: {"test": "<id>"})
    #
    # Each test maps to a `systemctl --user start <unit>` call (or the
    # system equivalent). Oneshot units finish on their own; the runner
    # returns immediately with the just-fired unit name + start time.
    # The operator can then refresh the Logs panel to see the result.
    #
    # No new code path on the device — these are the SAME units the
    # timers fire on cadence. The button is a manual extra fire, not a
    # separate test harness, so we don't have to maintain two paths.
    # ─────────────────────────────────────────────────────────────────
    _FLEET_TESTS = {
        # id: (unit, scope, human-label, what-it-does)
        "tracer": (
            "meshforge-tracer.service", "user",
            "Tracer fire",
            "Send one LXMF PING to each fleet peer and record ACK RTTs.",
        ),
        "synth-soak": (
            "meshforge-synth-soak.service", "user",
            "Synth soak fire",
            "Multi-user LXMF load fire (~60s).",
        ),
        "lab-rollup": (
            "meshforge-lab-rollup.service", "user",
            "Refresh lab rollup",
            "Re-aggregate tracer state files into the markdown panel.",
        ),
        "ci-status": (
            "meshforge-ci-status.service", "user",
            "Refresh CI status",
            "Poll GitHub Actions for fleet-repo build state.",
        ),
    }

    def _serve_fleet_tests_list(self):
        """Return the list of allowlisted tests + last-fire info per unit.

        The MA dashboard uses this both to render the buttons (no
        hardcoded list on the JS side) and to show "last fired Xs ago"
        next to each — operator scan signal for "did this just run."

        Two signals merge into ``last_fire_unix``:
          * the paired ``.timer``'s ``LastTriggerUSec`` (timer-driven)
          * the service's ``ExecMainExitTimestamp`` /
            ``ActiveEnterTimestamp`` (manual-fire-driven)
        We pick whichever is more recent so a manual click in the
        dashboard advances the chip. Without this, "Refresh lab
        rollup" fires the unit successfully but the chip stays stale
        because timer-driven activations are the only thing that
        update ``LastTriggerUSec``.

        ``not_installed`` (LoadState != "loaded") lets the UI grey out
        buttons whose unit isn't deployed on this host instead of
        silently returning exit=5 from systemctl (e.g. synth-soak on
        every fleet box except moc — see project_lab_traffic_soak_l1).
        """
        from utils.fleet_snapshot import (
            _list_timers_scope, _normalize_timer,
            _show_unit_props, _parse_unix_at,
        )
        import time as _time

        now = _time.time()
        # Index timers across both scopes so we can pick out the .timer
        # paired with each service (last_fire/next_fire). Not all tests
        # have a timer — manual-only tests show last_fire=None.
        timer_index = {}
        for scope in ("system", "user"):
            # _list_timers_scope now returns None on probe failure (M3);
            # `or []` keeps the /fleet/tests index working (no timer data
            # → manual-only display) without crashing on None.
            for raw in (_list_timers_scope(scope) or []):
                entry = _normalize_timer(raw, scope, now)
                if entry is None:
                    continue
                # Map the .timer back to the .service it activates.
                activates = raw.get("activates") or ""
                if activates:
                    timer_index[activates] = entry

        tests = []
        for test_id, (unit, scope, label, desc) in self._FLEET_TESTS.items():
            paired = timer_index.get(unit)
            timer_last = paired["last_fire_unix"] if paired else None
            next_fire = paired["next_fire_unix"] if paired else None

            props = _show_unit_props(
                unit, scope,
                ["LoadState", "ExecMainExitTimestamp",
                 "ActiveEnterTimestamp"],
            )
            not_installed = (props.get("LoadState") or "loaded") != "loaded"
            svc_exit = _parse_unix_at(props.get("ExecMainExitTimestamp", ""))
            svc_enter = _parse_unix_at(props.get("ActiveEnterTimestamp", ""))

            # Most-recent wins. None-tolerant max.
            candidates = [t for t in (timer_last, svc_exit, svc_enter)
                          if t is not None]
            last_fire = max(candidates) if candidates else None
            age_s = round(now - last_fire, 1) if last_fire is not None else None

            tests.append({
                "id": test_id,
                "unit": unit,
                "scope": scope,
                "label": label,
                "description": desc,
                "last_fire_unix": last_fire,
                "next_fire_unix": next_fire,
                "age_s": age_s,
                "not_installed": not_installed,
            })

        self._serve_json({"tests": tests, "host": socket.gethostname()})

    def _serve_fleet_run_test(self):
        """POST /fleet/run-test — fire an allowlisted lab unit.

        Body: {"test": "<id>"}     # id must be a key of _FLEET_TESTS
        Returns: {ok, test, unit, scope, started_at_unix, error}

        Firing fleet lab units (LXMF pings, synth-soak) is a state-changing
        action, gated to loopback / the configured LAN like the RF-transmit
        endpoint — not any host that can reach 0.0.0.0:5000.
        """
        if self._reject_if_untrusted():
            return
        import json as _json
        import socket as _socket

        try:
            length = int(self.headers.get("Content-Length", "0") or "0")
            body_raw = self.rfile.read(length) if length > 0 else b""
            body = _json.loads(body_raw.decode("utf-8") or "{}")
        except (ValueError, UnicodeDecodeError):
            self._serve_json(
                {"ok": False, "error": "body must be valid JSON"},
                status=400,
            )
            return

        test_id = (body.get("test") or "").strip()
        if test_id not in self._FLEET_TESTS:
            self._serve_json(
                {"ok": False, "error": "test not allowlisted",
                 "allowed": sorted(self._FLEET_TESTS.keys())},
                status=400,
            )
            return

        unit, scope, _label, _desc = self._FLEET_TESTS[test_id]
        from utils.fleet_test_runner import fire_unit
        result = fire_unit(unit=unit, scope=scope)
        result["test"] = test_id
        result["host"] = _socket.gethostname()
        self._serve_json(result, status=200 if result.get("ok") else 500)

    # Lab rollup state files written every 10min by
    # meshforge-lab-rollup.service. Variants:
    #   leaderboard   — traffic rollup sorted worst-fail-first (default)
    #   alphabetical  — traffic rollup sorted by pair (historical)
    #   synth         — multi-user synth-soak rollup (only on synth boxes)
    _LAB_ROLLUP_FILES = {
        'leaderboard':  'lab-traffic-rollup-leaderboard.md',
        'alphabetical': 'lab-traffic-rollup.md',
        'synth':        'lab-synth-soak-rollup.md',
    }

    def _serve_lab_rollup(self, variant: str):
        """Serve the lab rollup markdown produced by meshforge-lab-rollup.

        State file lives under $XDG_STATE_HOME/meshforge/ (default
        ~/.local/state/meshforge). 404s with a fix-hint when the file
        doesn't exist — typical cause is the rollup timer never having
        been installed on this host.
        """
        filename = self._LAB_ROLLUP_FILES.get(variant)
        if filename is None:
            self._serve_text(f"unknown rollup variant: {variant}\n",
                             status=404, content_type='text/plain')
            return

        # XDG_STATE_HOME with the standard ~/.local/state fallback.
        state_home = os.environ.get('XDG_STATE_HOME') or \
            os.path.join(os.path.expanduser('~'), '.local', 'state')
        path = os.path.join(state_home, 'meshforge', filename)

        try:
            with open(path, 'rb') as f:
                data = f.read()
        except FileNotFoundError:
            hint = (
                f"# lab rollup not found\n\n"
                f"`{path}` does not exist on this host.\n\n"
                f"Fix: install and enable the rollup timer on this box:\n"
                f"```\n"
                f"cp /opt/meshforge/templates/systemd/meshforge-lab-rollup-user.service \\\n"
                f"   ~/.config/systemd/user/meshforge-lab-rollup.service\n"
                f"cp /opt/meshforge/templates/systemd/meshforge-lab-rollup-user.timer \\\n"
                f"   ~/.config/systemd/user/meshforge-lab-rollup.timer\n"
                f"systemctl --user daemon-reload\n"
                f"systemctl --user enable --now meshforge-lab-rollup.timer\n"
                f"```\n"
            )
            self._serve_text(hint, status=404, content_type='text/markdown')
            return
        except OSError as e:
            self._serve_text(f"# error reading rollup\n\n{e}\n",
                             status=500, content_type='text/markdown')
            return

        self._serve_text(data, status=200, content_type='text/markdown')
