"""verify_post_install.sh reads the install mode the installer DECLARED.

WHY (2026-10-04): install_noc.sh writes /etc/meshforge/noc.yaml with
services.meshtasticd.managed, and the verify script never read it. A box
installed --client-only (meshtasticd deliberately not here) got two FAILs and
"Installation needs attention" — found by the gate-5 stranger drill on a fresh
Debian box. Not-managed must SKIP (never PASS: the box makes no radio claim);
an unreadable declaration is UNKNOWN and the checks apply as before.

The parser is tested by extracting the REAL function from the script. The
end-to-end tests assert only what the declaration determines; every other
check reads the machine running the test and is left alone.
"""

import os
import re
import subprocess

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts",
                      "verify_post_install.sh")

NOC = """noc:
  mode: "{mode}"
  services:
    meshtasticd:
      managed: {mtd}
      auto_start: {mtd}
      port: 4403

    rnsd:
      managed: {rns}
      auto_start: {rns}
"""


def _helper(tmp_path):
    src = open(SCRIPT).read()
    m = re.search(r"^noc_service_managed\(\) \{.*?^\}", src, re.S | re.M)
    assert m, "noc_service_managed() not found in verify_post_install.sh"
    p = tmp_path / "helper.sh"
    p.write_text(m.group(0) + "\n")
    return p


def _managed(tmp_path, yaml_text, svc):
    helper = _helper(tmp_path)
    noc = tmp_path / "noc.yaml"
    if yaml_text is None:
        noc = tmp_path / "absent.yaml"
    else:
        noc.write_text(yaml_text)
    r = subprocess.run(["bash", "-c", f"source {helper}; noc_service_managed {svc}"],
                       env=dict(os.environ, NOC_YAML=str(noc)),
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def test_client_mode_reads_false(tmp_path):
    y = NOC.format(mode="client", mtd="false", rns="true")
    assert _managed(tmp_path, y, "meshtasticd") == "false"


def test_managed_reads_true_and_sibling_does_not_leak(tmp_path):
    # rnsd's "managed: false" must not be read as meshtasticd's answer
    y = NOC.format(mode="local", mtd="true", rns="false")
    assert _managed(tmp_path, y, "meshtasticd") == "true"
    assert _managed(tmp_path, y, "rnsd") == "false"


def test_missing_or_garbage_is_unknown_never_false(tmp_path):
    assert _managed(tmp_path, None, "meshtasticd") == "unknown"
    assert _managed(tmp_path, "garbage: [\n", "meshtasticd") == "unknown"
    assert _managed(tmp_path, "noc:\n  services:\n    meshtasticd:\n      port: 1\n",
                    "meshtasticd") == "unknown"


def _verify(tmp_path, yaml_text):
    noc = tmp_path / "noc.yaml"
    if yaml_text is not None:
        noc.write_text(yaml_text)
    r = subprocess.run(["bash", SCRIPT], capture_output=True, text=True, timeout=180,
                       env=dict(os.environ, MESHFORGE_NOC_YAML=str(noc)))
    return re.sub(r"\x1b\[[0-9;]*m", "", r.stdout + r.stderr)


def test_client_install_gets_skips_not_fails(tmp_path):
    out = _verify(tmp_path, NOC.format(mode="client", mtd="false", rns="true"))
    assert "[SKIP] meshtasticd install + config" in out
    assert "[SKIP] meshtasticd service" in out
    for line in ("[FAIL] Config directory", "[FAIL] config.yaml exists"):
        assert line not in out, out


def test_unknown_mode_is_said_out_loud(tmp_path):
    out = _verify(tmp_path, None)
    assert "[INFO] Install mode" in out
    assert "[SKIP] meshtasticd install + config" not in out
