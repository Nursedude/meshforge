#!/usr/bin/env python3
"""rnode_fw_precache.py — make RNode firmware flashable with the uplink DOWN.

Born 2026-09-08, from the field-kit arc. ``rnodeconf`` downloads the firmware
zip at FLASH time: it fetches ``release.json`` from GitHub to learn the latest
version, then pulls the matching zip. That is fine at a desk and useless in the
field — and it is not hypothetical here, because the box that flashes boards is
the one whose WAN path ``wan_path_degraded_any`` has been calling lossy.

An empty cache is also INVISIBLE until it hurts: nothing on the box reports
"you cannot flash a board right now". You find out with the hardware on the
bench and the antenna already up the tree. So this tool does two things:

  ``--fetch``   populate rnodeconf's OWN cache, sha256-verified
  ``--verify``  answer, offline, "could I flash this board right now?" (exit 0/1)

``--verify`` is the half that earns the tool. A fetch you cannot re-check is a
belief; the verify pass re-derives readiness from the files on disk every time,
and it is the one you run the morning of a deployment.

What "ready" means here
-----------------------
Not "a zip exists". The zip is opened and its MEMBERS are checked against the
shape ``rnodeconf`` will actually extract and flash:

  * ESP32 / ESP32-S3 — ``<stem>.bin``, ``.bootloader``, ``.partitions``,
    ``.boot_app0``, plus ``console_image.bin`` and the bundled ``esptool.py``
    (rnodeconf uses the zip's own flasher; there is no system esptool
    dependency, and discovering that at flash time is too late)
  * nRF52 DFU — ``<stem>.bin``, ``<stem>.dat``, ``manifest.json``

A zip matching NEITHER shape fails loud rather than being accepted as "present"
— a truncated or wrong-target download is exactly the degraded state that would
otherwise render as a valid-looking cache entry (honest_failure_modes #1).

Where the board list comes from
-------------------------------
``rnodeconf.models`` and ``rnodeconf.products`` — imported, never re-typed.
Band edges, TX power ceiling, transceiver and firmware filename are all read
from the tool that will do the flashing, so this cannot drift from it
(honest_failure_modes #5). The only local constant is ``FIELD_KIT`` — which
boards WE care about — and that is a fleet decision, not a fact about RNode.

⚠️ The sudo trap
----------------
``rnodeconf`` computes its cache dir as ``os.path.expanduser("~/.config/rnodeconf")``
at import time. Under ``sudo`` that is ``/root/.config/rnodeconf`` — so a
privileged fetch would fill a cache the operator's own ``rnodeconf`` never
reads, and ``--verify`` would then pass against a directory the flash will not
use. The cache dir here is derived with ``get_real_user_home()`` (MF001) and
cross-checked against rnodeconf's own ``UPD_DIR``; a mismatch is reported
rather than silently preferred.

Usage
-----
    python3 scripts/rnode_fw_precache.py --list
    python3 scripts/rnode_fw_precache.py --fetch            # field-kit set
    python3 scripts/rnode_fw_precache.py --fetch --board heltec32v3
    python3 scripts/rnode_fw_precache.py --verify           # offline, exit 0/1

Then flash with no network at all:

    rnodeconf -a --fw-version <VERSION> --nocheck /dev/ttyUSB0

``--nocheck`` skips the online manifest fetch and ``--fw-version`` supplies what
it would have learned; rnodeconf then reads the hash from the ``.version`` file
written beside the zip.
"""

import argparse
import hashlib
import json
import os
import sys
import zipfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.error import URLError, HTTPError
from urllib.request import urlretrieve

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from utils.paths import get_real_user_home  # noqa: E402
from utils.safe_import import safe_import  # noqa: E402

rc, _HAS_RNODECONF = safe_import('RNS.Utilities.rnodeconf')

# Which boards the field kit actually carries. A fleet decision, so it lives
# here; everything else about these boards is read from rnodeconf.
FIELD_KIT = [
    "rnode_firmware_heltec32v3.zip",    # bench / link-test board
    "rnode_firmware_heltec32v4pa.zip",  # 28 dBm PA variant
    "rnode_firmware_rak4631.zip",       # remote node (nRF52840, low idle draw)
]

DOWNLOAD_TIMEOUT = 120

ESP32_SUFFIXES = (".bin", ".bootloader", ".partitions", ".boot_app0")
ESP32_EXTRAS = ("console_image.bin", "esptool.py")
NRF52_SUFFIXES = (".bin", ".dat")
NRF52_EXTRAS = ("manifest.json",)


def cache_dirs() -> Tuple[Path, Optional[str]]:
    """Return (update dir, warning) — MF001-safe, cross-checked against rnodeconf."""
    upd = get_real_user_home() / ".config" / "rnodeconf" / "update"
    warning = None
    if _HAS_RNODECONF and getattr(rc, "UPD_DIR", None):
        rc_upd = Path(rc.UPD_DIR)
        if rc_upd != upd:
            warning = (
                f"rnodeconf's own cache dir is {rc_upd}, but the real user's is {upd}. "
                "Running under sudo? rnodeconf will read ITS path at flash time; "
                "re-run without sudo so the cache lands where the flash will look."
            )
    return upd, warning


RNODECONF_MISSING = (
    "RNS.Utilities.rnodeconf is not importable — is the rns package installed? "
    "This tool deliberately reads its board table and firmware URLs from "
    "rnodeconf rather than carrying its own copy, so it cannot run without it."
)


def _require_rnodeconf():
    """Fail with the actual cause, not with ``'NoneType' has no attribute``.

    ``safe_import`` hands back None when the dependency is absent — the correct
    degraded value, but it sits in the same slot a module occupies, so the first
    attribute access downstream raises an AttributeError naming NoneType and not
    one word about rns being missing. ``main()`` has always guarded this; the
    library functions had not, so anything importing this module (the test
    suite, on a CI runner that installs the minimal-deps profile) hit the
    confusing form instead. Same class as everything else in this repo: a
    degraded value that reads as a valid one until a consumer trips over it.
    """
    if not _HAS_RNODECONF:
        raise RuntimeError(RNODECONF_MISSING)
    return rc


def known_firmware() -> Dict[str, List[dict]]:
    """Map firmware zip -> the models that use it, straight from rnodeconf.models."""
    out: Dict[str, List[dict]] = {}
    for model_id, spec in _require_rnodeconf().models.items():
        low, high, dbm, band, fw, chip = spec[0], spec[1], spec[2], spec[3], spec[4], spec[5]
        if not fw:
            # Homebrew models (0xFE/0xFF) carry a None firmware name on purpose:
            # the user supplies their own build, so there is nothing to cache.
            # Letting a None key through makes every name match blow up on
            # `want in fw` -- the degraded value sitting in the healthy domain.
            continue
        out.setdefault(fw, []).append({
            "model": model_id, "low": low, "high": high,
            "dbm": dbm, "band": band, "chip": chip,
        })
    return out


def zip_shape_ok(zpath: Path, fw: str) -> Tuple[bool, str]:
    """Does the zip carry every member rnodeconf will extract and flash?"""
    stem = fw[:-4] if fw.endswith(".zip") else fw
    try:
        with zipfile.ZipFile(zpath) as z:
            names = set(z.namelist())
    except (zipfile.BadZipFile, OSError) as e:
        return False, f"unreadable zip: {e}"

    esp = [stem + s for s in ESP32_SUFFIXES] + list(ESP32_EXTRAS)
    nrf = [stem + s for s in NRF52_SUFFIXES] + list(NRF52_EXTRAS)

    if not set(esp) - names:
        return True, "ESP32 flash set complete"
    if not set(nrf) - names:
        return True, "nRF52 DFU set complete"

    missing_esp = sorted(set(esp) - names)
    missing_nrf = sorted(set(nrf) - names)
    return False, (
        "matches NEITHER known flash shape — "
        f"ESP32 missing {missing_esp}; nRF52 missing {missing_nrf}"
    )


def read_version_file(upd: Path, version: str, fw: str) -> Optional[Tuple[str, str]]:
    vpath = upd / version / (fw + ".version")
    try:
        info = vpath.read_text(encoding="utf-8").strip().split()
    except OSError:
        return None
    if len(info) != 2:
        return None
    return info[0], info[1]


def cached_versions(upd: Path, fw: str) -> List[str]:
    """Every version dir on disk that holds this firmware, newest-looking last."""
    if not upd.is_dir():
        return []
    found = []
    for child in sorted(upd.iterdir()):
        if child.is_dir() and (child / fw).is_file():
            found.append(child.name)
    return found


def do_list() -> int:
    fws = known_firmware()
    print(f"rnodeconf knows {len(fws)} firmware variant(s) across {len(rc.models)} model(s)\n")
    for fw in sorted(fws):
        mark = " *" if fw in FIELD_KIT else "  "
        print(f"{mark} {fw}")
        for m in sorted(fws[fw], key=lambda x: x["low"]):
            print(f"      model 0x{m['model']:02X}  {m['band']:>16}  "
                  f"{m['dbm']:>2} dBm  {m['chip']}")
    print("\n  * = field-kit set (fetched by default)")
    return 0


def do_fetch(upd: Path, wanted: List[str]) -> int:
    upd.mkdir(parents=True, exist_ok=True)
    rel = upd / "release_info.json"

    print(f"manifest : {rc.firmware_version_url}")
    try:
        urlretrieve(rc.firmware_version_url, rel)
    except (URLError, HTTPError, OSError) as e:
        print(f"FAIL: could not retrieve the release manifest: {e}")
        print("      Without it there is no version or hash to trust. Not guessing.")
        return 1

    try:
        manifest = json.loads(rel.read_bytes())
    except (json.JSONDecodeError, OSError) as e:
        print(f"FAIL: release manifest is not readable JSON: {e}")
        return 1
    print(f"           lists {len(manifest)} variant(s)\n")

    failures = []
    for fw in wanted:
        if fw not in manifest:
            print(f"[MISS] {fw}: not in this release — unsupported by the current firmware")
            failures.append(fw)
            continue

        version = str(manifest[fw]["version"])
        want_hash = str(manifest[fw]["hash"])
        vdir = upd / version
        vdir.mkdir(parents=True, exist_ok=True)

        info = f"{version} {want_hash}".encode("utf-8")
        (upd / (fw + ".version.latest")).write_bytes(info)
        (vdir / (fw + ".version")).write_bytes(info)

        zpath = vdir / fw
        if zpath.is_file():
            print(f"[HAVE] {fw} v{version} (already cached)")
        else:
            url = rc.firmware_update_url + version + "/" + fw
            print(f"[GET ] {fw} v{version}")
            try:
                urlretrieve(url, zpath)
            except (URLError, HTTPError, OSError) as e:
                print(f"[FAIL] {fw}: download failed: {e}")
                failures.append(fw)
                continue

        got = hashlib.sha256(zpath.read_bytes()).hexdigest()
        if got != want_hash:
            print(f"[FAIL] {fw}: sha256 {got} != {want_hash} — corrupt, removing")
            zpath.unlink(missing_ok=True)
            failures.append(fw)
            continue

        shape_ok, why = zip_shape_ok(zpath, fw)
        if not shape_ok:
            print(f"[FAIL] {fw}: {why}")
            failures.append(fw)
            continue

        print(f"[ OK ] {fw} v{version}  sha256 verified  "
              f"{zpath.stat().st_size} bytes  {why}")

    print()
    if failures:
        print(f"RESULT: FAILED for {len(failures)}/{len(wanted)}: {', '.join(failures)}")
        return 1
    print(f"RESULT: OK — {len(wanted)}/{len(wanted)} cached, sha256- and shape-verified")
    return 0


def do_verify(upd: Path, wanted: List[str]) -> int:
    """Offline readiness. No network is touched — that is the point."""
    print(f"cache: {upd}\n")
    not_ready = []
    for fw in wanted:
        versions = cached_versions(upd, fw)
        if not versions:
            print(f"[NONE] {fw}: no cached copy — cannot flash this board offline")
            not_ready.append(fw)
            continue

        ok_any = False
        for version in versions:
            zpath = upd / version / fw
            vinfo = read_version_file(upd, version, fw)
            if vinfo is None:
                print(f"[BAD ] {fw} v{version}: no readable .version file beside the zip "
                      "— rnodeconf cannot verify its hash offline")
                continue
            _, want_hash = vinfo
            got = hashlib.sha256(zpath.read_bytes()).hexdigest()
            if got != want_hash:
                print(f"[BAD ] {fw} v{version}: sha256 mismatch — corrupt cache entry")
                continue
            shape_ok, why = zip_shape_ok(zpath, fw)
            if not shape_ok:
                print(f"[BAD ] {fw} v{version}: {why}")
                continue
            print(f"[ OK ] {fw} v{version}  {why}")
            print(f"           rnodeconf -a --fw-version {version} --nocheck <port>")
            ok_any = True

        if not ok_any:
            not_ready.append(fw)

    print()
    if not_ready:
        print(f"RESULT: NOT READY — {len(not_ready)}/{len(wanted)} unflashable offline: "
              f"{', '.join(not_ready)}")
        print("        Fix with: python3 scripts/rnode_fw_precache.py --fetch")
        return 1
    print(f"RESULT: READY — {len(wanted)}/{len(wanted)} flashable with the uplink down")
    return 0


def resolve_boards(requested: List[str]) -> Tuple[List[str], List[str]]:
    """Match user shorthand ('heltec32v3') against rnodeconf's firmware names."""
    known = set(known_firmware().keys())
    resolved, unknown = [], []
    for want in requested:
        if want in known:
            resolved.append(want)
            continue
        hits = sorted(fw for fw in known if want in fw)
        if len(hits) == 1:
            resolved.append(hits[0])
        elif not hits:
            unknown.append(f"{want}: no firmware matches")
        else:
            unknown.append(f"{want}: ambiguous, matches {hits}")
    return resolved, unknown


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Pre-cache RNode firmware so a board can be flashed offline.")
    ap.add_argument("--list", action="store_true",
                    help="show every firmware rnodeconf knows, with band and TX power")
    ap.add_argument("--fetch", action="store_true",
                    help="download and verify into rnodeconf's cache (needs network)")
    ap.add_argument("--verify", action="store_true",
                    help="offline readiness check; exit 0 = flashable now")
    ap.add_argument("--board", action="append", default=[], metavar="NAME",
                    help="firmware name or unique substring; repeatable "
                         "(default: the field-kit set)")
    args = ap.parse_args()

    if not _HAS_RNODECONF:
        print(f"FAIL: {RNODECONF_MISSING}")
        return 1

    if not (args.list or args.fetch or args.verify):
        ap.print_help()
        return 2

    upd, warning = cache_dirs()
    if warning:
        print(f"WARNING: {warning}\n")

    if args.list:
        return do_list()

    if args.board:
        wanted, unknown = resolve_boards(args.board)
        for u in unknown:
            print(f"FAIL: {u}")
        if unknown:
            print("      Run --list to see the known firmware names.")
            return 1
    else:
        wanted = list(FIELD_KIT)

    rc_code = 0
    if args.fetch:
        rc_code |= do_fetch(upd, wanted)
    if args.verify:
        rc_code |= do_verify(upd, wanted)
    return 1 if rc_code else 0


if __name__ == "__main__":
    sys.exit(main())
