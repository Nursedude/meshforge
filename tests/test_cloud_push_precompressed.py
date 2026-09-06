"""The cloud snapshot is pushed and served PRECOMPRESSED (2026-09-06).

Why: the raw geojson is 4.2 MB and gzips to ~250 KB. Caddy's on-the-fly
``encode`` never compressed it (its default MIME match excludes
application/geo+json), so every visitor fetched 4.2 MB every 30 s and every
push carried the raw file's delta over a residential uplink — the night the
ISP's transit ran 10% loss, no push completed for hours.

These pin the three halves that must agree, across bash/Caddyfile/systemd
boundaries where a shared constant is impossible (honest_failure_modes #4/#5):
the WRITER (push_snapshot.sh ships a --rsyncable sidecar and re-inflates the
raw file on the VPS), the SERVER (Caddyfile declares ``precompressed gzip``),
and the READER (verify_cloud_assets.sh proves browsers get gzip — with an exit
code the unit cannot mistake for success).
"""

import os
import re

_ROOT = os.path.join(os.path.dirname(__file__), "..")


def _read(rel):
    with open(os.path.join(_ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


class TestWriter:
    def test_pusher_gzips_rsyncable_and_ships_only_the_sidecar(self):
        s = _read("scripts/cloud/push_snapshot.sh")
        assert re.search(r"gzip -9 --rsyncable -c \"\$SNAPSHOT_FINAL\"", s), (
            "the sidecar must be built with --rsyncable or rsync's delta "
            "transfer stops working on it")
        m = re.search(r'PUSH_FILES=\((.*?)\)', s)
        assert m and '"$GZ_FINAL"' in m.group(1)
        assert '"$SNAPSHOT_FINAL"' not in m.group(1), (
            "the raw 4.2 MB geojson must NOT cross the wire — that is the "
            "whole point")

    def test_pusher_reinflates_the_raw_file_on_the_vps_atomically(self):
        s = _read("scripts/cloud/push_snapshot.sh")
        assert "gzip -dc" in s and "data.geojson.inflate" in s and "mv -f" in s, (
            "Caddy's precompressed lookup stats the ORIGINAL path and the "
            "freshness checker reads its Last-Modified — the raw file must "
            "be re-created on the VPS, atomically")
        # and a failed inflate is a FAILED push, not a 'pushed' log line
        i = s.index("remote inflate failed")
        assert "exit 1" in s[i:i + 200]


class TestServer:
    def test_caddyfile_serves_precompressed_gzip(self):
        c = _read("templates/cloud/Caddyfile.j2")
        assert re.search(r"file_server\s*\{[^}]*precompressed gzip", c, re.S), (
            "without `precompressed gzip` the sidecar is dead weight and "
            "browsers get the raw file")


class TestReader:
    def test_verifier_proves_browsers_get_gzip(self):
        v = _read("scripts/cloud/verify_cloud_assets.sh")
        assert 'Accept-Encoding: gzip' in v
        assert 'content-encoding: *gzip' in v

    def test_verifier_exit_code_is_outside_the_units_success_set(self):
        """SuccessExitStatus=0 1 on the unit means an exit 1 from the
        ExecStartPost verifier would be counted as success and never surface.
        The verifier must fail with a code the unit does not forgive."""
        v = _read("scripts/cloud/verify_cloud_assets.sh")
        u = _read("templates/cloud/meshforge-cloud-push.service")
        m = re.search(r"^SuccessExitStatus=(.+)$", u, re.M)
        assert m, "unit no longer declares SuccessExitStatus; re-derive this test"
        forgiven = set(m.group(1).split())
        fm = re.search(r'fail\(\) \{[^}]*exit (\d+)', v)
        assert fm, "verifier's fail() no longer exits with a literal code"
        assert fm.group(1) not in forgiven, (
            "verify_cloud_assets.sh exits %s on FAIL but the unit forgives %s — "
            "a failed verification would read as a successful push"
            % (fm.group(1), sorted(forgiven)))
