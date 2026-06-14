#!/usr/bin/env python3
"""Capture presentation-ready screenshots from the live MeshForge map (:5000).

Drives headless Chromium via the DevTools Protocol (CDP over a WebSocket) so we
can switch views, toggle layers, and draw terrain coverage at a chosen antenna
height — things a plain ``chromium --screenshot`` can't do.

SOAK-SAFE / READ-ONLY INVARIANT
    The ONLY action the map page persists to the server is a region change
    (POST /api/settings {selected_region}). This tool NEVER changes the region.
    Everything it does is client-side view state, throwaway-profile localStorage,
    or read-only GET (/api/nodes/geojson, /api/coverage). So it is safe to run
    while a gateway soak is in progress — it cannot disturb any service.

MF015 (no operator-identifying data in published assets)
    * Map markers are clustered counts with no labels  -> safe.
    * Topology node labels are REAL callsigns/names     -> use --hide-labels
      (hides every <text class="topo-label"> before the shot). At 2x DPI the
      labels become legible, so --hide-labels is mandatory for any topology
      shot you intend to publish.
    * Terrain uses a SYNTHETIC station (you pass lat/lon), so no real node name
      ever appears in the UI. Always eyeball the final PNG before publishing.

Terrain coverage note (learned 2026-06-14)
    At a 10 m antenna the model is honestly red-dominant everywhere on Hawaii
    terrain (best site ~18% clear LOS). Antenna HEIGHT is the lever: a ~120 m
    mast at a coastal site (e.g. Hilo) reaches ~75% clear. Pick --alt to match
    the real deployment you are depicting, and SAY "from a tower/relay site" —
    never imply handheld coverage.

REQUIRES: a Chromium binary on PATH and the ``websockets`` Python package.

USAGE
    python3 scripts/capture_map_assets.py --view map      --out /tmp/map.png
    python3 scripts/capture_map_assets.py --view topology --hide-labels --dpr 2 --out /tmp/topo.png
    python3 scripts/capture_map_assets.py --view coverage --out /tmp/cov.png
    python3 scripts/capture_map_assets.py --view terrain  --lat 19.73 --lon -155.09 \
            --alt 120 --zoom 13 --dpr 2 --out /tmp/terrain.png

After capture, crop/label-check as needed (e.g. Pillow) before adding to a deck.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

CHROMIUM_CANDIDATES = ("chromium", "chromium-browser", "google-chrome", "chrome")


def find_chromium() -> str:
    for name in CHROMIUM_CANDIDATES:
        path = shutil.which(name)
        if path:
            return path
    sys.exit("ERROR: no Chromium binary found on PATH "
             f"(looked for: {', '.join(CHROMIUM_CANDIDATES)})")


def wait_for_cdp(port: int, timeout_s: float = 20.0) -> str:
    """Return the page target's WebSocket debugger URL once Chromium is ready."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(
                    f"http://localhost:{port}/json/list", timeout=2) as resp:
                targets = json.load(resp)
            pages = [t for t in targets if t.get("type") == "page"
                     and t.get("webSocketDebuggerUrl")]
            if pages:
                return pages[0]["webSocketDebuggerUrl"]
        except Exception:
            pass
        time.sleep(0.5)
    raise RuntimeError("Chromium CDP endpoint never came up")


# Terrain LOS colour logic — kept identical to the map's showTerrainCoverage().
_TERRAIN_DRAW = """
(async function(){{
  var r = await fetch('/api/coverage/{lat}/{lon}/{alt}?radius_km={radius}&resolution=24');
  var d = await r.json();
  if (!d.features) return 'no-features';
  terrainLayer.clearLayers();
  var clear = 0;
  d.features.forEach(function(f){{
    var c = f.geometry.coordinates, p = f.properties;
    if (p.is_clear) clear++;
    var col = !p.is_clear ? '#ef5350'
            : (p.fresnel_pct >= 100 ? '#66bb6a'
            : (p.fresnel_pct >= 60  ? '#ffca28' : '#ff7043'));
    L.circleMarker([c[1], c[0]], {{radius:6, fillColor:col, color:col,
      weight:1, opacity:0.85, fillOpacity:0.55, interactive:false}})
      .addTo(terrainLayer);
  }});
  return d.features.length + ' pts, ' + Math.round(100*clear/d.features.length) + '% clear';
}})()
"""


async def run_capture(args: argparse.Namespace) -> None:
    try:
        import websockets  # lazy: only needed at run time
    except ModuleNotFoundError:
        sys.exit("ERROR: the 'websockets' package is required "
                 "(pip install websockets).")

    chromium = find_chromium()
    profile = tempfile.mkdtemp(prefix="mf_cap_")
    proc = subprocess.Popen(
        [chromium, "--headless=new", "--disable-gpu", "--no-sandbox",
         "--disable-dev-shm-usage", "--hide-scrollbars",
         f"--window-size={args.width},{args.height}",
         f"--remote-debugging-port={args.port}",
         f"--user-data-dir={profile}", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    try:
        ws_url = wait_for_cdp(args.port)
        async with websockets.connect(ws_url, max_size=None) as ws:
            counter = {"n": 0}

            async def cmd(method: str, params: dict | None = None) -> dict:
                counter["n"] += 1
                mid = counter["n"]
                await ws.send(json.dumps(
                    {"id": mid, "method": method, "params": params or {}}))
                while True:
                    msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=120))
                    if msg.get("id") == mid:
                        return msg

            async def evaluate(expr: str, await_promise: bool = False):
                res = await cmd("Runtime.evaluate",
                                {"expression": expr, "returnByValue": True,
                                 "awaitPromise": await_promise})
                return res.get("result", {}).get("result", {}).get("value")

            await cmd("Page.enable")
            await cmd("Runtime.enable")
            await cmd("Emulation.setDeviceMetricsOverride",
                      {"width": args.width, "height": args.height,
                       "deviceScaleFactor": args.dpr, "mobile": False})
            await cmd("Page.navigate", {"url": args.url})
            await asyncio.sleep(args.wait)  # tiles + geojson load

            if args.view == "coverage":
                print("coverage ->", await evaluate(
                    "(function(){var c=document.getElementById('filter-coverage');"
                    "if(!c)return 'no-el'; if(!c.checked){c.checked=true;"
                    "c.dispatchEvent(new Event('change'));} return 'on';})()"))
                await asyncio.sleep(8)

            elif args.view == "topology":
                print("switchView ->", await evaluate(
                    "typeof switchView==='function'"
                    " ? (switchView('topology'),'ok') : 'NO-FN'"))
                await asyncio.sleep(13)  # let the force sim settle
                if args.hide_labels:
                    n = await evaluate(
                        "(function(){var n=0;document.querySelectorAll('.topo-label')"
                        ".forEach(function(e){e.style.display='none';n++;});return n;})()")
                    print(f"labels hidden: {n}")
                    await asyncio.sleep(2)

            elif args.view == "terrain":
                if args.lat is None or args.lon is None:
                    sys.exit("ERROR: --view terrain requires --lat and --lon")
                await evaluate(f"map.setView([{args.lat},{args.lon}],{args.zoom});0")
                await asyncio.sleep(7)
                print("terrain ->", await evaluate(
                    _TERRAIN_DRAW.format(lat=args.lat, lon=args.lon,
                                         alt=args.alt, radius=args.radius_km),
                    await_promise=True))
                await asyncio.sleep(3)

            shot = await cmd("Page.captureScreenshot", {"format": "png"})
            with open(args.out, "wb") as fh:
                fh.write(base64.b64decode(shot["result"]["data"]))
            print(f"WROTE {args.out}")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
        shutil.rmtree(profile, ignore_errors=True)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--view", required=True,
                   choices=("map", "topology", "coverage", "terrain"))
    p.add_argument("--out", required=True, help="output PNG path")
    p.add_argument("--url", default="http://localhost:5000/")
    p.add_argument("--dpr", type=int, default=1, help="device scale factor (2 = HD)")
    p.add_argument("--width", type=int, default=1920)
    p.add_argument("--height", type=int, default=1080)
    p.add_argument("--wait", type=int, default=18, help="seconds to wait for load")
    p.add_argument("--port", type=int, default=9222, help="CDP debugging port")
    p.add_argument("--hide-labels", action="store_true",
                   help="topology: hide node labels (MF015 — required to publish)")
    p.add_argument("--lat", type=float, help="terrain: station latitude")
    p.add_argument("--lon", type=float, help="terrain: station longitude")
    p.add_argument("--alt", type=int, default=120,
                   help="terrain: antenna height m (10=handheld, ~120=tower)")
    p.add_argument("--zoom", type=int, default=13, help="terrain: map zoom")
    p.add_argument("--radius-km", type=int, default=5, help="terrain: coverage radius")
    args = p.parse_args()
    asyncio.run(run_capture(args))


if __name__ == "__main__":
    main()
