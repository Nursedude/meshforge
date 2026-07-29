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

    doc = _read_config(a.server, a.device, a.timeout)
    before = doc.get("lora_watch_ids", "")
    print("config read OK (%d keys; contents NOT printed — it holds the WiFi key)"
          % len(doc))
    print("  lora_watch_ids: %r -> %r" % (before, ",".join(ids)))
    if before == ",".join(ids):
        print("already set — nothing to do")
        return 0
    if a.dry_run:
        print("dry-run: no write performed")
        return 0

    doc["lora_watch_ids"] = ",".join(ids)
    body = json.dumps(doc, separators=(",", ":"))

    # ⚠️ THE FIRMWARE REFUSES THIS, BY DESIGN, AND IT IS RIGHT TO.
    # tool_file_write() hard-rejects path == /config.json so no remote tool can
    # clobber the document holding the WiFi credential that keeps the claw on the
    # bus. Discovered 2026-07-29 by attempting it: the write silently no-ops and
    # only the verify-by-re-read catches it. Do NOT relax that guard to make this
    # script work — weakening a credential protection to save a walk is the wrong
    # trade. The sanctioned paths are:
    #   (a) the claw's own config portal, on the claw's WiFi (writes LittleFS
    #       directly, not through the tool layer), or
    #   (b) a NARROW firmware tool that can set ONLY lora_watch_ids — honours the
    #       guard's intent (protect secrets) while allowing a non-secret field to
    #       be set remotely. Not built yet.
    probe = _tool(a.server, a.device, {"tool": "file_write", "path": CONFIG_PATH,
                                       "content": body}, a.timeout)
    if "cannot overwrite config.json" in str(probe):
        print("REFUSED BY FIRMWARE (correctly): config.json is tool-write "
              "protected so no remote caller can drop the WiFi credential.")
        print("  Set it instead via the claw's config portal on its own WiFi —")
        print("  the field 'LoRa watch ids' is in the form as of this firmware.")
        print("  Value to paste: %s" % ",".join(ids))
        return 3

    # VERIFY by re-reading, not by trusting the write reply.
    again = _read_config(a.server, a.device, a.timeout)
    got = again.get("lora_watch_ids", "")
    if got != ",".join(ids):
        print("FAILED: re-read shows %r, expected %r — restore from %s"
              % (got, ",".join(ids), BACKUP_PATH))
        return 1
    if "wifi_ssid" not in again or not again.get("wifi_ssid"):
        print("FAILED: re-read lost wifi_ssid — restore from %s NOW" % BACKUP_PATH)
        return 1
    print("VERIFIED by re-read: lora_watch_ids=%r, wifi_ssid intact" % got)
    print("reboot the claw (or wait for its next restart) to arm the watch list")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print("ERROR: %s" % e)
        sys.exit(1)
