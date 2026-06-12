#!/usr/bin/env python3
"""Put the dude-claw on a private (fleet) LoRa channel — secret-safe.

Runs on the box that hosts the claw's NATS server (moc2). Sends the claw a
`mesh_set_channel` with the channel NAME (not secret) and PSK. The key is
read via getpass (no echo, no shell history); it is sent over the local
pinholed NATS bus and NEVER printed and NEVER written to git. By default it
is persisted to the claw's OWN flash (a dedicated /lora_channel.json, the
same trust model as Meshtastic's channel store) so it survives reboots;
--no-persist keeps it RAM-only. Use --verify to fire the proof send in the
same process (no reboot can revert the channel between set and send).

The claw replies with the channel name and the resulting channel-HASH byte —
that byte is the cleartext LoRa header field, not a secret. Cross-check it
against the fleet channel's expected hash, then send a test message and watch
the gateway decode it.

    # interactive (you hold the key):
    PYTHONPATH=/opt/meshforge/src python3 scripts/claw_set_fleet_channel.py \
        --device dudeclaw-01 --name meshforge

    # auto-extract from a local meshtasticd channel store (run where it lives,
    # e.g. on the gateway with sudo); pipe the export, never print it:
    PYTHONPATH=/opt/meshforge/src python3 scripts/claw_set_fleet_channel.py \
        --device dudeclaw-01 --name meshforge --psk-stdin < key.b64
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import sys

from mini_dudeai.nats_client import request


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--device", required=True, help="claw device name")
    ap.add_argument("--name", required=True,
                    help="LoRa channel name (drives the hash; not secret)")
    ap.add_argument("--server", default="127.0.0.1:4222",
                    help="NATS server (default the local claw bus)")
    ap.add_argument("--psk-stdin", action="store_true",
                    help="read the PSK (hex/base64) from stdin instead of "
                         "prompting (for piping; the value is still not echoed)")
    ap.add_argument("--persist", action="store_true", default=True,
                    help="write the channel to the claw's flash so it survives "
                         "reboots (default on; --no-persist for RAM-only)")
    ap.add_argument("--no-persist", dest="persist", action="store_false")
    ap.add_argument("--verify", action="store_true",
                    help="fire a test mesh_send in the SAME process right after "
                         "the set, so no reboot can revert the channel first")
    ap.add_argument("--verify-text", default="dude-claw on the fleet channel",
                    help="text for the --verify test broadcast")
    ap.add_argument("--timeout", type=float, default=15.0)
    args = ap.parse_args()

    if args.psk_stdin:
        psk = sys.stdin.readline().strip()
    else:
        psk = getpass.getpass(f"PSK for channel {args.name!r} "
                              "(hex or base64, blank = public default): ")

    payload = {"tool": "mesh_set_channel", "name": args.name, "psk": psk,
               "persist": 1 if args.persist else 0}
    # Scrub our own copy as soon as it is serialized — minimize the window.
    body = json.dumps(payload)
    del psk, payload

    resp = request(args.server, f"{args.device}.tool_exec", body,
                   timeout_s=args.timeout)
    del body
    if isinstance(resp, (bytes, bytearray)):
        resp = resp.decode()
    if isinstance(resp, str):
        try:
            resp = json.loads(resp)
        except ValueError:
            print(resp)
            return 0
    # The claw echoes name + hash only (never the key). That hash is the
    # on-air header byte — confirm it equals the fleet channel's hash.
    print(resp.get("result", resp))
    ok = isinstance(resp, dict) and resp.get("ok") is not False

    if args.verify and ok:
        # Fire a test broadcast in the SAME process, immediately after the set,
        # so no reboot can revert the channel in between (the gap that lost the
        # first attempt). The send result shows the channel hash actually used.
        send = request(args.server, f"{args.device}.tool_exec",
                       json.dumps({"tool": "mesh_send",
                                   "text": args.verify_text}),
                       timeout_s=args.timeout)
        if isinstance(send, (bytes, bytearray)):
            send = send.decode()
        if isinstance(send, str):
            try:
                send = json.loads(send)
            except ValueError:
                pass
        print("sent:", send.get("result", send) if isinstance(send, dict)
              else send)
        print("\nNow watch the gateway decode it ON THE FLEET CHANNEL:\n"
              "  ssh <gateway> \"journalctl -u meshtasticd --since '-2 min' "
              "| grep 'Received text msg'\"\n"
              "(the send line above must show the fleet hash, NOT 0x08)")
    else:
        print("\nNext: send a test message and watch the gateway decode it on "
              "the fleet channel:\n"
              f"  PYTHONPATH=/opt/meshforge/src python3 -m "
              f"mini_dudeai.nats_client \\\n"
              f"    req {args.device}.tool_exec "
              f"'{{\"tool\":\"mesh_send\",\"text\":\"claw on fleet ch\"}}'")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
