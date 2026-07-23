#!/usr/bin/env python3
"""host_probe_check — out-of-band liveness collector (Leg C, 2026-06-17).

Polls the dude-claw's ``host_probe`` tool over NATS for each configured target
and writes a verdict file (``~/host_probe_state.json``) that the watchdog's
``probe_host_frozen`` reads. The claw sits on the watched box's OWN subnet, so
it distinguishes a wedged-userspace freeze (kernel/IP stack answers but the app
port serves no banner) from an unreachable host — the swap-thrash class the
box's own self-petted HW watchdog can't catch.

This is the out-of-band collector half: the NATS call lives HERE, not in the
sandboxed watchdog — mirroring fleet_offline_check.sh's role for
``fleet_box_unreachable``. Runs ONLY where a config file is present, which
self-gates it to the claw's brain box (so the watchdog probe is INERT
everywhere else — no verdict file). Cron it with ``cron_verdict.sh`` so a dead
collector is caught by ``cron_verdict_stale``.

Config (operator-specific values live HERE, never in repo source — MF014):

    ~/.config/meshforge/host_probe_targets.json
    {
      "nats": "localhost:4222",
      "claw_device": "dudeclaw-01",           # default witness (back-compat)
      "timeout_s": 8,
      "targets": [
        # each target may name its OWN ordered witness claw(s): the first is
        # the authoritative primary (the claw with a route to it); the rest are
        # failovers, tried only when the primary claw is DARK. A failover claw
        # that can't reach the target reports UNKNOWN, never a false host-down.
        {"name": "box-a", "host": "<box-a-lan-ip>", "app_port": 22, "closed_port": 9,
         "witness": ["dudeclaw-01"]},
        {"name": "box-b", "host": "<box-b-lan-ip>", "app_port": 22, "closed_port": 9,
         "witness": ["dudeclaw-02"]}
      ]
    }

``app_port`` MUST be a service that emits an unsolicited banner on connect
(sshd :22 sends ``SSH-2.0-...``); a no-banner service like HTTP would read as a
false HOST_FROZEN. Exit 0 when a verdict file was written (even if a target is
HOST_FROZEN — that is a successful collection); non-zero only if the collector
itself could not run (no/unreadable config, or it could not write the file). A
target the claw could not be reached for is written verdict UNKNOWN, exit 0 —
the witness is blind, which is honest, not a false OK or false FROZEN.
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import time

# Make src/ importable however we're invoked (cron may not set PYTHONPATH).
_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from utils.paths import get_real_user_home  # noqa: E402

CONFIG_REL = os.path.join(".config", "meshforge", "host_probe_targets.json")
STATE_BASENAME = "host_probe_state.json"
DEFAULT_NATS = "localhost:4222"
DEFAULT_DEVICE = "dudeclaw-01"
DEFAULT_TIMEOUT_S = 8.0


def _parse_probe_result(result_line: str) -> dict:
    """Parse the claw's host_probe result string into fields. Missing fields
    stay None — we never invent a value the radio did not report."""
    fields: dict = {"ip_alive": None, "app_port": None, "app_state": None,
                    "banner": None, "kstack": None, "rtt_ms": None}
    m = re.search(r"ip_alive=(\d+)", result_line)
    if m:
        fields["ip_alive"] = int(m.group(1))
    m = re.search(r"app(\d+)=(\w+)", result_line)
    if m:
        fields["app_port"] = int(m.group(1))
        fields["app_state"] = m.group(2)
    m = re.search(r"banner=(\d+)B", result_line)
    if m:
        fields["banner"] = int(m.group(1))
    m = re.search(r"kstack=(\d+)", result_line)
    if m:
        fields["kstack"] = int(m.group(1))
    m = re.search(r"rtt_ms=(-?\d+)", result_line)
    if m:
        fields["rtt_ms"] = int(m.group(1))
    return fields


def _verdict(fields: dict, collector_ok: bool) -> str:
    """Map probe fields to a verdict. UNKNOWN when the witness itself was blind
    (collector couldn't reach the claw, or the line didn't parse) — lost
    visibility is NOT read as OK."""
    if not collector_ok or fields.get("ip_alive") is None:
        return "UNKNOWN"
    if fields["ip_alive"] == 0:
        return "UNREACHABLE"
    # app port completed the TCP handshake (kernel accept) but served no banner.
    # banner==0 ALONE is AMBIGUOUS: a swap-wedged userspace (the real freeze
    # class) AND a merely slow/loaded box both miss the banner window — the
    # .32 Pi Zero W under two mesh bots false-fired HOST_FROZEN for days this
    # way (2026-06-23). Corroborate with the kernel hung-task signal:
    #   kstack==1 → the kernel itself flagged a stuck task = the genuine freeze
    #               the self-petted HW watchdog can't catch → HOST_FROZEN.
    #   kstack==0 → kernel healthy, sshd just slow to send the banner = NOT a
    #               freeze (don't page; app_state=open already proves sshd is
    #               accepting, ip_alive proves the NIC/kernel are up).
    #   kstack missing (older claw tool didn't report it) → can't corroborate;
    #               preserve the conservative freeze verdict rather than risk a
    #               missed freeze (honest_failure_modes #2: absent corroboration
    #               is not evidence of health).
    # honest_failure_modes #1: never map one ambiguous observation (banner==0)
    # straight onto a definitive wedge verdict.
    if fields.get("app_state") == "open" and (fields.get("banner") or 0) == 0:
        if fields.get("kstack") == 0:
            return "OK"
        return "HOST_FROZEN"
    return "OK"


def _probe_once(req, server: str, device: str, target: dict,
                timeout_s: float) -> dict:
    """Probe a single target via the claw ONCE. Never raises — a transport
    failure becomes a UNKNOWN verdict with the error recorded."""
    name = str(target.get("name") or target.get("host") or "?")
    host = str(target.get("host") or "")
    out = {"name": name, "host": host, "verdict": "UNKNOWN",
           "ip_alive": None, "app_state": None, "banner": None,
           "kstack": None, "rtt_ms": None, "raw": "", "error": None}
    if not host:
        out["error"] = "no host in config"
        return out
    args = {"tool": "host_probe", "host": host}
    if target.get("app_port") is not None:
        args["app_port"] = int(target["app_port"])
    if target.get("closed_port") is not None:
        args["closed_port"] = int(target["closed_port"])
    try:
        reply = req(server, f"{device}.tool_exec", json.dumps(args),
                    timeout_s=timeout_s)
        if isinstance(reply, (bytes, bytearray)):
            reply = reply.decode("utf-8", "replace")
        # nats_client.request() may hand back an already-parsed dict or a raw
        # JSON string — accept either, never assume.
        doc = json.loads(reply) if isinstance(reply, str) else reply
        if not isinstance(doc, dict):
            out["error"] = f"unexpected reply type: {type(reply).__name__}"
            return out
        result = str(doc.get("result") or "")
        out["raw"] = result
        if not doc.get("ok"):
            out["error"] = str(doc.get("error") or "claw returned ok=false")
            return out                       # UNKNOWN — claw errored
        fields = _parse_probe_result(result)
        out.update({k: fields.get(k) for k in
                    ("ip_alive", "app_state", "banner", "kstack", "rtt_ms")})
        out["verdict"] = _verdict(fields, collector_ok=True)
    except Exception as e:  # transport / parse failure → blind, not healthy
        out["error"] = f"{type(e).__name__}: {e}"
    return out


def _probe_target(req, server: str, device: str, target: dict,
                  timeout_s: float, attempts: int = 3) -> dict:
    """Confirm a wedge before claiming one. A loaded box (the .32 Pi Zero W at
    load ~3.5) can miss the 800ms banner window on one probe and look
    HOST_FROZEN, then serve the banner fine on the next — observed firing a
    false wedge the day Leg C shipped. So: re-probe a non-OK verdict; if ANY
    attempt shows the box serving (OK), it is ALIVE and we report OK. Only a
    verdict that PERSISTS across every attempt is reported (a truly frozen box
    gives banner=0 every time; a down box is UNREACHABLE every time). Records
    the attempt count. (This re-probe handles the TRANSIENT slow box; the
    SUSTAINED-slow box — banner=0 every attempt but kernel-healthy — is now
    disambiguated from a real freeze by the kstack hung-task signal in
    _verdict, so a chronically loaded box no longer false-pages HOST_FROZEN.)"""
    last = None
    dark_streak = 0
    for i in range(max(1, attempts)):
        last = _probe_once(req, server, device, target, timeout_s)
        if last["verdict"] == "OK":
            last["attempts"] = i + 1
            return last                  # alive — stop, never a transient wedge
        # A transport-DARK claw can never produce an OK, so burning every
        # remaining attempt's full timeout on it only stacks worst-case
        # runtime (attempts × timeout per dark claw per target — the 07-23
        # audit's budget finding). Two consecutive transport failures =
        # the claw is dark this run; stop. Answering-but-not-OK verdicts
        # keep the full re-probe budget (that is the false-wedge guard).
        if last["error"] is not None:
            dark_streak += 1
            if dark_streak >= 2:
                last["attempts"] = i + 1
                return last
        else:
            dark_streak = 0
    last["attempts"] = max(1, attempts)
    return last                          # every attempt agreed it's not OK


def _resolve_witnesses(target: dict, default_device: str) -> list:
    """Ordered witness claws for a target: ``target['witness']`` (primary first,
    failovers after) else ``[default_device]``.

    The FIRST claw is AUTHORITATIVE — it is the witness known to have a route to
    this target, so its ``UNREACHABLE`` is a real host-down. A non-primary
    (failover) claw's ``UNREACHABLE`` is NOT trusted as a down verdict: the
    fleet's claws sit on disjoint subnets (claw-01 on the AREDN site, claw-02 on
    DudeNET2), so a failover claw that cannot reach a target may simply lack a
    route, not have found it dead.
    """
    w = target.get("witness")
    if isinstance(w, str) and w.strip():
        # A bare string is the natural one-element spelling. Silently
        # absorbing it into the DEFAULT claw would make a wrong-subnet
        # witness authoritative and manufacture a sustained false host-down
        # (07-23 audit) — honor the author's obvious intent instead.
        w = [w]
    if isinstance(w, list):
        devs = [str(d) for d in w if str(d).strip()]
        if devs:
            return devs
        # explicitly-empty list = "use the default" (pinned back-compat)
        return [str(default_device)]
    if w is not None:
        # Present but unusable (dict, number, blank string): the author
        # cannot have meant "use the default claw" — refuse loud rather than
        # probe from a vantage they never chose (honest_failure_modes #3).
        raise ValueError(
            f"malformed 'witness' for target {target.get('name')!r}: {w!r} "
            f"(want a claw name or a list of claw names)")
    return [str(default_device)]


def _probe_target_with_failover(req, server: str, witnesses: list, target: dict,
                                timeout_s: float, attempts: int = 3) -> dict:
    """Probe a target through its ordered witness claws with honest failover.

    The PRIMARY witness (``witnesses[0]``) is authoritative: whatever it reports
    stands, because it is the claw known to have a route to the target. Failover
    claws are consulted ONLY when the primary claw itself is DARK (its NATS
    request errored — the *instrument* is unreachable, not the target). A
    failover claw that SEES the target's IP stack (``ip_alive=1`` → OK /
    HOST_FROZEN) restores the witness. But a failover claw reporting UNREACHABLE
    is AMBIGUOUS — target-down vs no-route from that vantage — and is downgraded
    to UNKNOWN, never a false 'host down' (honest_failure_modes #1). Records
    provenance: ``witness_device``, ``failover``, ``attempted``.
    """
    primary = witnesses[0]
    res = _probe_target(req, server, primary, target, timeout_s, attempts)
    res["witness_device"] = primary
    res["failover"] = False
    res["attempted"] = [primary]
    if res["error"] is None and res.get("ip_alive") is not None:
        return res                       # primary answered with a parseable
                                         # observation → authoritative
    if res["error"] is None:
        # Primary ANSWERED but its result parsed to nothing (all fields None
        # — firmware/format drift): the instrument is BLIND, not the target.
        # Before 07-23 this blocked failover entirely (error None read as
        # "authoritative") and every target it witnessed sat in permanent
        # UNKNOWN while a healthy failover claw idled.
        res["note"] = (
            f"primary witness {primary} answered but its result was "
            f"unparseable — instrument blind; consulting failovers")

    # primary claw is DARK or BLIND → consult failover claws
    attempted = [primary]
    fallback = None
    for dev in witnesses[1:]:
        fres = _probe_target(req, server, dev, target, timeout_s, attempts)
        attempted.append(dev)
        fres["witness_device"] = dev
        fres["failover"] = True
        fres["attempted"] = list(attempted)
        if fres["error"] is None and fres["verdict"] in ("OK", "HOST_FROZEN"):
            return fres                  # failover saw the stack → definitive
        if fres["error"] is None:        # answered, but UNREACHABLE/UNKNOWN
            if fres.get("ip_alive") is None:
                # Answered garbage — no observation happened; don't describe
                # a routing gap that was never observed (07-23 audit).
                fres["note"] = (
                    f"failover witness {dev} answered but its result was "
                    f"unparseable — instrument blind; reporting UNKNOWN")
            else:
                fres["note"] = (
                    f"failover witness {dev} could not confirm the target "
                    f"(unreachable from its vantage — possible routing gap, "
                    f"not a confirmed host-down); reporting UNKNOWN")
            fres["verdict"] = "UNKNOWN"
            fallback = fres
        else:                            # this failover claw is also dark
            fres["note"] = f"failover witness {dev} unreachable (claw dark)"
            fallback = fallback or fres
    if fallback is not None:
        fallback["attempted"] = list(attempted)
        return fallback
    res["attempted"] = list(attempted)   # primary dark, no failover configured
    return res


def main() -> int:
    home = get_real_user_home()
    config_path = os.path.join(str(home), CONFIG_REL)
    try:
        with open(config_path, "r", encoding="utf-8") as fh:
            cfg = json.load(fh)
    except FileNotFoundError:
        # No config here → not the claw's brain box. Nothing to do; the watchdog
        # probe self-guards INERT (no verdict file). Clean no-op.
        return 0
    except (OSError, ValueError) as e:
        sys.stderr.write(f"host_probe_check: unreadable config {config_path}: {e}\n")
        return 1

    server = str(cfg.get("nats") or DEFAULT_NATS)
    device = str(cfg.get("claw_device") or DEFAULT_DEVICE)
    token = cfg.get("nats_token") or None
    timeout_s = float(cfg.get("timeout_s") or DEFAULT_TIMEOUT_S)
    targets = cfg.get("targets") or []
    if not isinstance(targets, list):
        sys.stderr.write("host_probe_check: 'targets' must be a list\n")
        return 1

    from mini_dudeai.nats_client import request as _request

    def req(srv, subj, payload, *, timeout_s):
        return _request(srv, subj, payload, token=token, timeout_s=timeout_s)

    # Each target names its own ordered witness claw(s) (per-target ``witness``,
    # else the top-level ``claw_device``). The two fleet claws sit on disjoint
    # subnets, so each target's PRIMARY witness is the claw with a route to it;
    # a failover claw is used only when the primary claw itself is dark, and
    # cannot manufacture a 'host down' from a subnet it can't reach.
    results = []
    for t in targets:
        if not isinstance(t, dict):
            continue
        try:
            wl = _resolve_witnesses(t, device)
        except ValueError as e:
            # Malformed witness config: an honest UNKNOWN with the config
            # error as the witness — never probed from a default vantage the
            # author didn't choose (honest_failure_modes #3).
            sys.stderr.write(f"host_probe_check: {e}\n")
            results.append({
                "name": str(t.get("name") or t.get("host") or "?"),
                "host": str(t.get("host") or ""), "verdict": "UNKNOWN",
                "ip_alive": None, "app_state": None, "banner": None,
                "kstack": None, "rtt_ms": None, "raw": "",
                "error": f"config: {e}", "witness_device": None,
                "failover": False, "attempted": []})
            continue
        results.append(_probe_target_with_failover(
            req, server, wl, t, timeout_s))
    collector_ok = all(r["error"] is None for r in results) if results else True

    state = {"ts": time.time(), "collector_ok": collector_ok,
             "device": device, "targets": results}
    state_path = os.path.join(str(home), STATE_BASENAME)
    try:
        fd, tmp = tempfile.mkstemp(dir=str(home), prefix=".host_probe_", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(state, fh, separators=(",", ":"))
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, state_path)
    except OSError as e:
        sys.stderr.write(f"host_probe_check: cannot write {state_path}: {e}\n")
        return 1

    # One-line summary for the cron log; exit 0 = collection ran (a HOST_FROZEN
    # target is still a successful collection — the watchdog renders the alert).
    summary = ", ".join(
        f"{r['name']}={r['verdict']}@{r.get('witness_device', '?')}"
        + ("(failover)" if r.get("failover") else "")
        for r in results) or "no targets"
    sys.stdout.write(f"host_probe_check: {summary}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
