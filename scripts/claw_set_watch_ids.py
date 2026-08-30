#!/usr/bin/env python3
"""Set a dude-claw's LoRa WATCH LIST — per-id last-heard, secret-safe.

WHY THIS EXISTS (2026-07-29)
---------------------------
``mesh_heard_age_s`` answers "is the channel silent?". It CANNOT answer "is OUR
transmitter reaching the air?" — measured that day, both claws sat at
heard_age_s=5 with neighbours chattering at 6-8 pkt/min, so a dead PA on our own
gateway would have left every silence check clean. The watch list reports each
tracked node id separately, and a watched id that has never been heard reports
``never`` (not 0, which would read as "heard just now").

WHY A SCRIPT AND NOT THE PORTAL
-------------------------------
The claw's config portal lives on the claw's own /28, which no fleet box can
route to — so the portal needs a human on that WiFi. This runs on the box hosting
the claw's NATS bus and goes over the already-pinholed local bus.

SECRET-SAFE, deliberately: the claw's config.json also holds its WiFi password.
This does a READ / MODIFY / WRITE and **never prints the document**, never logs
it, never writes it to disk. Same trust model as claw_set_fleet_channel.py. If
you need to see the config, read it on the device, not through here.

SAFETY
------
* The read document must parse as JSON AND carry ``wifi_ssid``, or we refuse:
  writing a config that lacks the WiFi credential would cut the claw off the bus
  on its next boot, and the only recovery is physical/USB.
* A backup is written to /config.json.bak on the device FIRST, so a bad write is
  recoverable without a bench visit.
* The write is verified by re-reading and re-parsing, never by trusting the
  write tool's own reply (calibrated_claims #7).
* Ids are normalised and validated as 8-hex-digit node ids; anything else is
  rejected rather than silently dropped, because a typo'd id would report
  ``never`` forever and look exactly like a dead transmitter.

    PYTHONPATH=/opt/meshforge/src python3 scripts/claw_set_watch_ids.py \
        --device dudeclaw-01 --ids 32962f10,ebfa1b11 --dry-run
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time

sys.path.insert(0, "/opt/meshforge/src")
from mini_dudeai.nats_client import request  # noqa: E402

CONFIG_PATH = "/config.json"
BACKUP_PATH = "/config.json.bak"
_ID_RE = re.compile(r"^[0-9a-f]{8}$")


def _tool(server: str, device: str, payload: dict, timeout: float):
    resp = request(server, f"{device}.tool_exec", json.dumps(payload),
                   timeout_s=timeout)
    if resp is None:
        raise RuntimeError("no reply from %s (claw dark, or wrong bus?)" % device)
    return resp


def _read_config(server: str, device: str, timeout: float) -> dict:
    resp = _tool(server, device, {"tool": "file_read", "path": CONFIG_PATH}, timeout)
    text = resp if isinstance(resp, str) else json.dumps(resp)
    # The tool may wrap the file in a JSON envelope; find the document itself.
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise RuntimeError("config read did not return a JSON document")
    for cand in (text[start:end + 1],):
        try:
            doc = json.loads(cand)
        except ValueError:
            continue
        if isinstance(doc, dict) and "wifi_ssid" in doc:
            return doc
        if isinstance(doc, dict) and "result" in doc:
            inner = doc["result"]
            if isinstance(inner, str):
                s, e = inner.find("{"), inner.rfind("}")
                if s >= 0 and e > s:
                    doc2 = json.loads(inner[s:e + 1])
                    if isinstance(doc2, dict) and "wifi_ssid" in doc2:
                        return doc2
    raise RuntimeError("could not locate a config document carrying wifi_ssid — "
                       "REFUSING to write (a config without it strands the claw)")


_RE_UPTIME = re.compile(r"Uptime:\s*(\d+)\s*seconds", re.IGNORECASE)
_RE_RESET = re.compile(r"Reset reason:\s*([^,]+)", re.IGNORECASE)


def _device_state(server: str, device: str, timeout: float):
    """``(uptime_s, reset_reason)`` from the claw's own ``device_info``.

    Parsed from the firmware's plain-text reply (``src/tools.cpp ::
    tool_device_info``), not JSON — that IS its wire format. Either field
    coming back None means the reply did not carry it; the caller must treat
    that as UNKNOWN, never as a healthy default.
    """
    try:
        text = str(_tool(server, device, {"tool": "device_info"}, timeout))
    except RuntimeError:
        return None, None          # dark right now — not an answer, and not a failure
    mu, mr = _RE_UPTIME.search(text), _RE_RESET.search(text)
    return (int(mu.group(1)) if mu else None,
            mr.group(1).strip() if mr else None)


def _reboot_and_verify(server: str, device: str, timeout: float,
                       wait_s: float) -> int:
    """Restart the claw and PROVE it came back. 0 verified / 1 refused / 2 unknown.

    The proof is ``uptime went DOWN``, not ``uptime is small``. The firmware
    defers the restart ~2 s so its reply can flush, so the first device_info
    after the call is answered by the OUTGOING process and reports the OLD
    uptime — accepting it would ratify a reboot that never happened. So a
    baseline is captured BEFORE the call and the poll waits for an uptime
    strictly below it (the restart-verification trap: require the artifact to
    be newer than a t0 captured beforehand, never merely 'recent').

    A device that does not return inside the window is UNKNOWN (exit 2), never
    failure and never success: the config change is already committed on-device,
    so 'did not come back yet' and 'is bricked' are different claims and this
    cannot tell them apart. Unobservable is not unhealthy — and is not healthy.
    """
    before_uptime, before_reason = _device_state(server, device, timeout)
    if before_uptime is None:
        print("REFUSING to reboot: no device_info baseline — without it a "
              "restart cannot be distinguished from a claw that never went "
              "down (the outgoing process answers for ~2s)")
        return 1
    print("baseline: uptime=%ss reset_reason=%s" % (before_uptime, before_reason))

    resp = str(_tool(server, device, {"tool": "reboot"}, timeout))
    print("device says: %s" % resp[:200])
    if "reboot armed" not in resp:
        # The firmware refuses rather than strands (unreadable config, or a
        # missing/empty wifi_ssid). That refusal is the tool working.
        print("REFUSED by device — not rebooted; the reason above is the finding")
        return 1

    deadline = time.time() + wait_s
    while time.time() < deadline:
        time.sleep(3)
        up, reason = _device_state(server, device, timeout)
        if up is None:
            continue                       # still down, or bus not answering yet
        if up >= before_uptime:
            continue                       # the OUTGOING process, pre-restart
        print("VERIFIED restart: uptime %ss -> %ss, reset_reason=%s"
              % (before_uptime, up, reason))
        if reason != "sw-restart":
            print("⚠️  came back, but reset_reason is %r, not 'sw-restart' — it "
                  "restarted for some OTHER cause (power? crash?); treat this "
                  "as a finding, not a clean apply" % reason)
            return 2
        print("watch list is now ARMED on %s" % device)
        return 0

    print("UNKNOWN: %s did not report a lower uptime within %.0fs. The config "
          "change is COMMITTED on-device; whether it rebooted and is still "
          "coming up, or is off the bus, is not observable from here. Check "
          "the next capture's reset_reason/uptime before assuming either."
          % (device, wait_s))
    return 2


def _normalise(raw: str):
    out = []
    for tok in raw.split(","):
        tok = tok.strip().lstrip("!").lower()
        if not tok:
            continue
        if not _ID_RE.match(tok):
            raise ValueError("not an 8-hex-digit node id: %r — refusing (a typo'd "
                             "id reports 'never' forever and mimics a dead PA)" % tok)
        if tok not in out:
            out.append(tok)
    if not out:
        raise ValueError("no valid ids given")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--device", required=True)
    ap.add_argument("--ids", required=True,
                    help="comma-separated node ids, with or without '!'")
    ap.add_argument("--server", default="localhost:4222")
    ap.add_argument("--timeout", type=float, default=8.0)
    ap.add_argument("--dry-run", action="store_true",
                    help="read + show what WOULD change; writes nothing")
    ap.add_argument("--reboot", action="store_true",
                    help="after a VERIFIED write, restart the claw over the bus "
                         "so the list actually arms, and prove it came back "
                         "(uptime went down + reset_reason=sw-restart). Default "
                         "OFF: a write and a restart are different blast radii. "
                         "Exit 2 = write committed but reboot UNOBSERVABLE.")
    ap.add_argument("--reboot-wait", type=float, default=90.0,
                    help="seconds to wait for the claw to come back (default 90)")
    a = ap.parse_args()

    ids = _normalise(a.ids)
    print("watch ids to set: %s" % ",".join("!" + i for i in ids))

    if a.dry_run:
        print("dry-run: would send config_set lora_watch_ids=%s" % ",".join(ids))
        return 0

    # Uses the NARROW config_set tool (firmware >= the 2026-07-29 build): it sets
    # ONE allowlisted non-secret key on-device, verifies by re-reading, and never
    # echoes the document. The old approach — file_read + file_write of
    # /config.json — is refused by the firmware BY DESIGN (no remote caller may
    # clobber the file holding the WiFi credential), and rightly so.
    resp = _tool(a.server, a.device,
                 {"tool": "config_set", "key": "lora_watch_ids",
                  "value": ",".join(ids)}, a.timeout)
    text = str(resp)
    print("device says: %s" % text[:200])
    if "verified=yes" not in text:
        print("FAILED: device did not confirm the write")
        return 1
    if "wifi_ssid_intact=yes" not in text:
        print("FAILED: device reports wifi_ssid NOT intact — reprovision via the "
              "portal before rebooting it")
        return 1
    print("VERIFIED on-device: lora_watch_ids set, wifi_ssid intact")
    if a.reboot:
        return _reboot_and_verify(a.server, a.device, a.timeout, a.reboot_wait)
    print("reboot the claw (or wait for its next restart) to arm the watch list")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print("ERROR: %s" % e)
        sys.exit(1)
