"""update.sh installs the two log-volume drop-ins, so neither lives only as a
hand-placed file on a box.

2026-10-13: every box was found capped at 200M by hand-placed journald files
nobody remembered (2.5-4.7 days of history; a weekly timer could never be
judged). The 1G cap and the meshtasticd noise filter were rolled by hand the
same day; these blocks make a rebuilt or new box get them too.

Both tests run the REAL update.sh block with its paths pointed at a sandbox.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

REPO = Path(__file__).parent.parent
UPDATE = REPO / "scripts" / "update.sh"
FILTER = REPO / "templates" / "systemd" / "meshtasticd.service.d" / "30-log-filter.conf"
CAP = REPO / "templates" / "systemd" / "journald.conf.d" / "95-meshforge-journal-cap.conf"


def _block(start: str) -> str:
    text = UPDATE.read_text()
    i = text.index(start)
    j = text.index("\nfi\n", i) + len("\nfi\n")
    return text[i:j]


def _run(block: str) -> subprocess.CompletedProcess:
    script = (f'GREEN=""; YELLOW=""; NC=""; SVC_UPDATED=false; INSTALL_DIR="{REPO}"\n'
              f'{block}\necho "SVC_UPDATED=$SVC_UPDATED"\n')
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=30)


class TestMeshtasticdLogFilter:
    START = "# Deploy the meshtasticd log filter drop-in."

    def _run(self, tmp_path: Path, systemd_ver: str = "257", unit: bool = True):
        etc = tmp_path / "etc"
        etc.mkdir(exist_ok=True)
        if unit:
            (etc / "meshtasticd.service").write_text("[Service]\n")
        block = (_block(self.START)
                 .replace("/usr/lib/systemd/system", str(tmp_path / "none1"))
                 .replace("/etc/systemd/system", str(etc))
                 .replace("/lib/systemd/system", str(tmp_path / "none2"))
                 .replace("systemctl --version", f"echo systemd {systemd_ver}"))
        return _run(block), etc / "meshtasticd.service.d" / FILTER.name

    def test_installs_byte_for_byte_then_is_idempotent(self, tmp_path):
        first, dst = self._run(tmp_path)
        assert first.returncode == 0, first.stderr
        assert dst.read_text() == FILTER.read_text()
        assert "SVC_UPDATED=true" in first.stdout
        second, _ = self._run(tmp_path)
        assert "already current" in second.stdout and "SVC_UPDATED=false" in second.stdout

    def test_systemd_without_logfilterpatterns_gets_nothing_and_says_so(self, tmp_path):
        p, dst = self._run(tmp_path, systemd_ver="252")
        assert p.returncode == 0, p.stderr
        assert not dst.exists()
        assert "252" in p.stdout and "SVC_UPDATED=false" in p.stdout

    def test_a_box_without_meshtasticd_gets_nothing(self, tmp_path):
        p, dst = self._run(tmp_path, unit=False)
        assert p.returncode == 0, p.stderr
        assert not dst.exists()

    def test_never_restarts_the_radio(self):
        # The filter applies at meshtasticd's next start; update.sh must not
        # restart a radio to apply a log filter.
        assert "restart meshtasticd" not in _block(self.START)

    def test_filters_only_lines_nothing_reads(self):
        """Every pattern the drop-in drops must be absent from src/ and scripts/."""
        pats = [ln.split("=~", 1)[1].strip() for ln in FILTER.read_text().splitlines()
                if ln.startswith("LogFilterPatterns=~")]
        assert pats, "no deny patterns found"
        for pat in pats:
            for root in ("src", "scripts"):
                for f in (REPO / root).rglob("*"):
                    if f.suffix in (".py", ".sh") and f.is_file() and f != UPDATE:  # the installer, not a reader
                        assert pat not in f.read_text(errors="ignore"), f"{f} reads {pat!r}"


class TestJournalCap:
    START = "# Deploy the journald size cap drop-in."

    def _run(self, tmp_path: Path):
        d = tmp_path / "journald.conf.d"
        block = (_block(self.START)
                 .replace("/etc/systemd/journald.conf.d", str(d))
                 .replace("systemctl restart systemd-journald", "echo RESTARTED_JOURNALD"))
        return _run(block), d

    def test_installs_restarts_journald_once_then_is_idempotent(self, tmp_path):
        first, d = self._run(tmp_path)
        assert first.returncode == 0, first.stderr
        assert (d / CAP.name).read_text() == CAP.read_text()
        assert first.stdout.count("RESTARTED_JOURNALD") == 1
        second, _ = self._run(tmp_path)
        assert "RESTARTED_JOURNALD" not in second.stdout and "already current" in second.stdout

    def test_leaves_hand_placed_files_alone(self, tmp_path):
        d = tmp_path / "journald.conf.d"
        d.mkdir()
        old = d / "90-meshforge-persistent.conf"
        old.write_text("[Journal]\nSystemMaxUse=200M\n")
        p, _ = self._run(tmp_path)
        assert p.returncode == 0, p.stderr
        assert old.read_text() == "[Journal]\nSystemMaxUse=200M\n"

    def test_sorts_after_every_200M_file_found_on_the_fleet(self):
        # journald applies drop-ins in filename order; the last one wins.
        for found in ("40-rpi-volatile-storage.conf", "90-meshforge-persistent.conf"):
            assert CAP.name > found, found
