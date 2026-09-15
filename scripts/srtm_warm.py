#!/usr/bin/env python3
"""srtm_warm.py — the operator's lever for terrain tiles on THIS box.

Since 2026-09-14 the map's request path never downloads a tile (an
unauthenticated client must not choose what this box fetches from S3).
Tiles arrive two ways: the map warms the tiles around its own LOCAL nodes
once at start-up, and an operator runs this script.

    python3 scripts/srtm_warm.py                       # around the local map's own nodes
    python3 scripts/srtm_warm.py 19.72,-155.09 20.76,-156.45   # around given points
    python3 scripts/srtm_warm.py --max 20 --no-neighbors ...

Bounded like the in-process warm: --max tiles (default 9) and a 2 GiB
free-disk floor. Exit 0 when nothing failed, 1 when a fetch failed, 2 when
there was nothing to do (no points).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from utils.terrain import SRTMProvider  # noqa: E402


def _local_points(port: int):
    url = f"http://127.0.0.1:{port}/api/nodes/geojson"
    with urllib.request.urlopen(url, timeout=30) as r:
        d = json.load(r)
    pts = []
    for f in d.get("features") or []:
        props = f.get("properties") or {}
        if props.get("source_origin") in ("meshcore_public", "aredn_worldmap",
                                          "public_fallback", "mqtt_global", "federation") \
                and not props.get("is_local"):
            continue
        c = (f.get("geometry") or {}).get("coordinates") or []
        if len(c) >= 2 and not (abs(c[0]) < 1e-6 and abs(c[1]) < 1e-6):
            pts.append((float(c[1]), float(c[0])))
    return pts


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("points", nargs="*", help="lat,lon pairs; none = the local map's own nodes")
    ap.add_argument("--max", type=int, default=9, help="tiles to download at most (default 9)")
    ap.add_argument("--no-neighbors", action="store_true", help="only the tiles under the points")
    ap.add_argument("--port", type=int, default=5000)
    a = ap.parse_args()

    if a.points:
        pts = []
        for p in a.points:
            lat, lon = p.split(",", 1)
            pts.append((float(lat), float(lon)))
    else:
        try:
            pts = _local_points(a.port)
        except Exception as e:
            print(f"could not read the local map on :{a.port} ({e}); pass lat,lon points", file=sys.stderr)
            return 2
    if not pts:
        print("nothing to warm: no points", file=sys.stderr)
        return 2

    prov = SRTMProvider(auto_download=False)
    s = prov.warm_tiles(pts, max_tiles=a.max, neighbors=not a.no_neighbors)
    for k in ("wanted", "cached", "downloaded", "failed", "skipped_budget", "skipped_disk"):
        print(f"{k:15s} {len(s[k]):3d}  {' '.join(s[k])}")
    return 1 if s["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
