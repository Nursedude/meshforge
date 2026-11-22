#!/usr/bin/env bash
# Root-cause drill for the 11-21 stock witness result (runs ON meshforge-lab).
# Measures the per-interface filtered-announce counter (afrxc, `rnstatus -F`)
# across an idle window and a probe window, enforce on then off.
set -uo pipefail
W=/opt/meshforge/scripts/stock_rns_witness.sh
B=$HOME/.local/share/meshforge/stock-witness
OUT=$HOME/.cache/meshforge-review/witness-rootcause-$(date +%Y%m%dT%H%M%S)
mkdir -p "$OUT"
T="moc_gw=3dfbdb5d24c6de195ae4f3c0f56b5ea5 moc3_gw=f68c2f56cb61527b6c9ad603b9a5009a ma_bcast=627fa5662039afbee52d76d448ed2528 moc3_nn=6b1a0120941444587d7d1dc1bf6d64d7 ma_gw=58cecbd0ab5e17750c3d45411c913b75 moc_nn=7cda0fabcf4e714c421813c22897a565 volc_nn=9217147e24d1640d204dde3f413eb521 volc_mcx=f0a6899b67761e93529bf811d1313b7b tablet=<personal-device>"
snap() { timeout 20 "$B/venv/bin/rnstatus" -F --config "$B/rns" > "$OUT/$1.rnstatus" 2>&1; echo "rc=$?" >> "$OUT/$1.rnstatus"; }
arm() {
  local name=$1 flag=$2
  echo "== arm $name ($(date +%T))"
  bash "$W" stop >/dev/null 2>&1
  bash "$W" start $flag || { echo "start failed"; return; }
  sleep 60
  snap "${name}_t0"; local t0=$SECONDS
  sleep 90
  snap "${name}_t1"; local t1=$SECONDS
  bash "$W" probe $T > "$OUT/${name}_probe.txt" 2>&1
  snap "${name}_t2"; local t2=$SECONDS
  echo "idle_s=$((t1-t0)) probe_s=$((t2-t1))" > "$OUT/${name}_windows.txt"
  cp "$B/nomadnet/logfile" "$OUT/${name}_nomadnet.log" 2>/dev/null
  bash "$W" stop
}
arm B --enforce
arm A ""
echo "OUT=$OUT"
