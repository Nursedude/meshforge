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
    print("reboot the claw (or wait for its next restart) to arm the watch list")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print("ERROR: %s" % e)
        sys.exit(1)
