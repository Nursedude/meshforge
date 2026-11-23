"""r13.8 — "which config does rnsd load" is judged AS RNSD'S UID (Fable r19 #1).

RNS picks its configdir with ``os.path.isfile`` running as rnsd's uid, and an
EACCES on a dir it cannot search is False — it moves on to the home dirs. The
resolver used to judge as the OBSERVER (the root watchdog, a sudo TUI): with
``/etc/reticulum`` unsearchable by rnsd's user it named /etc while rnsd loaded
``~/.reticulum`` (6/18 cross-uid layouts wrong in r19's ``fz_xuid.py``).

Fleet shape (measured 2026-11-23): rnsd User=wh6gxz Group=wh6gxz everywhere,
the watchdog is root — observer != rnsd uid on every box, so "UNKNOWN when
the uids differ" would blind the probes fleet-wide. The seam is ``isfile_as``:
DAC search bits as rnsd's (uid, groups), UNKNOWN only where the observer
cannot see what rnsd sees (it cannot stat a component, or an ACL decides).

Modes/owners/ACLs are PRESENTED through ``paths._stat`` / ``paths._getxattr``
so the cross-uid cells run unprivileged (CI is not root).
"""
import errno
import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from utils import paths as paths_mod  # noqa: E402
from utils.paths import PathUnjudged, ReticulumPaths, isfile_as  # noqa: E402
from utils import rns_alignment as ra  # noqa: E402
from utils.rns_alignment import RnsdConfigdirUnknown, _resolve_rnsd_configdir  # noqa: E402

RNSD_UID = 4242          # never the test runner
RNSD_GID = 4243
OTHER_GID = 4244
REAL_STAT = os.stat


def _present(monkeypatch, modes: dict, *, deny=(), acl=()):
    """Present ``modes`` {path: (mode, uid, gid)} through ``paths._stat``;
    any other path reports 0o755 root:root (so the runner's own 0700 pytest
    basetemp does not decide). ``deny``: paths the OBSERVER cannot stat."""
    modes = {str(k): v for k, v in modes.items()}
    deny = {str(d) for d in deny}
    acl = {str(a) for a in acl}

    def fake_stat(p, *a, **kw):
        p = str(p)
        if p in deny or any(p.startswith(d + "/") for d in deny):
            raise PermissionError(errno.EACCES, "denied", p)
        st = REAL_STAT(p, *a, **kw)      # ENOENT stays ENOENT
        mode, uid, gid = modes.get(p, (0o755, 0, 0))
        fields = list(st)
        fields[0] = (st.st_mode & ~0o7777) | mode
        fields[4], fields[5] = uid, gid
        return os.stat_result(fields)

    def fake_getxattr(p, name):
        if str(p) in acl:
            return b"\x02\x00\x00\x00"
        raise OSError(errno.ENODATA, "no acl", str(p))

    monkeypatch.setattr(paths_mod, "_stat", fake_stat)
    monkeypatch.setattr(paths_mod, "_getxattr", fake_getxattr)


def _box(tmp_path, monkeypatch, *, xdg=False, dot=True):
    etc = tmp_path / "etc" / "reticulum"
    etc.mkdir(parents=True)
    (etc / "config").write_text("[reticulum]\n")
    home = tmp_path / "home" / "op"
    home.mkdir(parents=True)
    for on, d in ((xdg, home / ".config" / "reticulum"), (dot, home / ".reticulum")):
        if on:
            d.mkdir(parents=True)
            (d / "config").write_text("[reticulum]\n")
    monkeypatch.setattr(ReticulumPaths, "ETC_BASE", etc)
    return etc, home


def IDS(name, primary=None):
    gid = RNSD_GID if primary is None else primary
    return RNSD_UID, gid, (gid,)


def test_group_replaces_the_passwd_gid_it_does_not_add_to_it(tmp_path, monkeypatch):
    # systemd Group=X: gid X + getgrouplist(user, X); the passwd gid is
    # not kept unless /etc/group lists the user in it.
    etc, home = _box(tmp_path, monkeypatch)
    _present(monkeypatch, {etc: (0o1770, 0, RNSD_GID)})   # passwd gid's dir
    got = _resolve_rnsd_configdir("op", "/usr/local/bin/rnsd --service",
                                  group=str(OTHER_GID), home_of=lambda u: home,
                                  ids_of=IDS)
    assert got == home / ".reticulum"


# ── r19 #1, at the resolver: the watchdog's real call ───────────────────

@pytest.mark.parametrize("xdg,want", [(False, ".reticulum"), (True, ".config/reticulum")])
def test_etc_rnsd_cannot_search_is_skipped_like_rns_does(tmp_path, monkeypatch, xdg, want):
    etc, home = _box(tmp_path, monkeypatch, xdg=xdg)
    _present(monkeypatch, {etc: (0o700, 0, 0)})      # root-only /etc/reticulum
    got = _resolve_rnsd_configdir("op", "/usr/local/bin/rnsd --service",
                                  home_of=lambda u: home, ids_of=IDS)
    assert got == home / want


def test_fleet_shape_group_search_keeps_etc(tmp_path, monkeypatch):
    # /etc/reticulum drwxrwxr-t root:wh6gxz, rnsd Group=wh6gxz (every box).
    etc, home = _box(tmp_path, monkeypatch)
    _present(monkeypatch, {etc: (0o1770, 0, RNSD_GID)})
    got = _resolve_rnsd_configdir("op", "/usr/local/bin/rnsd --service",
                                  home_of=lambda u: home, ids_of=IDS)
    assert got == etc


def test_unit_group_overrides_the_passwd_primary_group(tmp_path, monkeypatch):
    # rnsd's passwd group cannot search /etc; the unit's Group= can.
    etc, home = _box(tmp_path, monkeypatch)
    _present(monkeypatch, {etc: (0o1770, 0, OTHER_GID)})
    kw = dict(home_of=lambda u: home, ids_of=IDS)
    assert _resolve_rnsd_configdir("op", "/usr/local/bin/rnsd --service", **kw) == home / ".reticulum"
    assert _resolve_rnsd_configdir("op", "/usr/local/bin/rnsd --service",
                                   group=str(OTHER_GID), **kw) == etc
    assert _resolve_rnsd_configdir("op", "/usr/local/bin/rnsd --service",
                                   supplementary=(str(OTHER_GID),), **kw) == etc


def test_observer_blind_to_a_dir_is_unknown_not_its_own_answer(tmp_path, monkeypatch):
    etc, home = _box(tmp_path, monkeypatch)
    _present(monkeypatch, {}, deny=(etc,))
    with pytest.raises(RnsdConfigdirUnknown):
        _resolve_rnsd_configdir("op", "/usr/local/bin/rnsd --service",
                                home_of=lambda u: home, ids_of=IDS)


def test_acl_on_the_path_is_unknown(tmp_path, monkeypatch):
    etc, home = _box(tmp_path, monkeypatch)
    _present(monkeypatch, {etc: (0o700, 0, 0)}, acl=(etc,))
    with pytest.raises(RnsdConfigdirUnknown):
        _resolve_rnsd_configdir("op", "/usr/local/bin/rnsd --service",
                                home_of=lambda u: home, ids_of=IDS)


def test_root_rnsd_searches_anything(tmp_path, monkeypatch):
    etc, home = _box(tmp_path, monkeypatch)
    _present(monkeypatch, {etc: (0o000, 0, 0)}, acl=(etc,))
    got = _resolve_rnsd_configdir(None, "/usr/local/bin/rnsd --service",
                                  home_of=lambda u: home,
                                  ids_of=lambda n, p=None: (0, 0, (0,)))
    assert got == etc


def test_config_flag_needs_no_path_judgement(tmp_path, monkeypatch):
    _present(monkeypatch, {}, deny=(Path("/srv"),))
    got = _resolve_rnsd_configdir("op", "/usr/local/bin/rnsd --config /srv/rns",
                                  ids_of=IDS)
    assert got == Path("/srv/rns")


@pytest.mark.parametrize("key", [
    "Environment=HOME=/srv/rnshome", 'Environment="A=1" "HOME=/srv/x"',
    "EnvironmentFile=/etc/default/rnsd", "ProtectHome=yes", "RootDirectory=/jail",
    "DynamicUser=yes", "TemporaryFileSystem=/home", "InaccessiblePaths=/etc/reticulum",
    "BindPaths=/srv:/home/op",
])
def test_unit_keys_that_change_rnsds_view_are_unknown(tmp_path, monkeypatch, key):
    # r19 NIT 3 (unit-level HOME) + the namespace keys: what rnsd SEES is not
    # what we stat. None of these is on the fleet (survey 2026-11-23).
    unit = tmp_path / "rnsd.service"
    unit.write_text(f"[Service]\nUser=op\nGroup=op\n{key}\n"
                    "Environment=RNS_EXIT_ON_HOST_LOSS=1\n"
                    "ExecStart=/usr/local/bin/rnsd --service\n")
    parsed = ra._read_systemd_unit(str(unit))
    assert parsed["view_keys"] or parsed["namespace_keys"], key
    with pytest.raises(RnsdConfigdirUnknown):
        _resolve_rnsd_configdir(parsed["user"], parsed["exec_start"],
                                view_keys=parsed["view_keys"],
                                namespace_keys=parsed["namespace_keys"], ids_of=IDS,
                                home_of=lambda u: tmp_path)


def test_harmless_environment_is_not_a_view_key(tmp_path):
    unit = tmp_path / "rnsd.service"
    unit.write_text("[Service]\nUser=wh6gxz\nGroup=wh6gxz\n"
                    "Environment=RNS_EXIT_ON_HOST_LOSS=1\nEnvironment=XHOME=1\n"
                    "ProtectHome=no\nExecStart=/usr/local/bin/rnsd --service\n")
    parsed = ra._read_systemd_unit(str(unit))
    assert parsed["view_keys"] == []
    assert parsed["group"] == "wh6gxz"


def test_unit_parser_supplementary_groups_accumulate_and_reset(tmp_path):
    base = tmp_path / "rnsd.service"
    base.write_text("[Service]\nSupplementaryGroups=a b\nSupplementaryGroups=c\n")
    drop = tmp_path / "10.conf"
    drop.write_text("[Service]\nSupplementaryGroups=\nSupplementaryGroups=d\n")
    assert ra._read_systemd_unit(str(base))["supplementary_groups"] == ["a", "b", "c"]
    assert ra._read_systemd_unit(str(base), str(drop))["supplementary_groups"] == ["d"]


def test_unknown_group_name_is_unknown(tmp_path, monkeypatch):
    etc, home = _box(tmp_path, monkeypatch)
    with pytest.raises(RnsdConfigdirUnknown):
        _resolve_rnsd_configdir("op", "/usr/local/bin/rnsd --service",
                                group="no-such-group-r138", home_of=lambda u: home,
                                ids_of=IDS)


def test_of_record_passes_the_units_group(tmp_path, monkeypatch):
    # The live entry point wires Group= through (fleet: Group=wh6gxz).
    etc, home = _box(tmp_path, monkeypatch)
    unit = tmp_path / "rnsd.service"
    unit.write_text(f"[Service]\nUser=op\nGroup={OTHER_GID}\n"
                    "ExecStart=/usr/local/bin/rnsd --service\n")
    monkeypatch.setattr(ra, "rnsd_unit_paths", lambda: [str(unit)])
    monkeypatch.setattr(ra, "_rnsd_home_of", lambda n: home)
    monkeypatch.setattr(ra, "_rnsd_ids_of", IDS)
    _present(monkeypatch, {etc: (0o1770, 0, OTHER_GID)})
    assert ra.rnsd_configdir_of_record() == etc
    _present(monkeypatch, {etc: (0o1770, 0, 9999)})
    assert ra.rnsd_configdir_of_record() == home / ".reticulum"


# ── isfile_as, the cells directly ──────────────────────────────────────

def _f(tmp_path):
    d = tmp_path / "d"
    d.mkdir()
    (d / "config").write_text("x")
    return d, d / "config"


@pytest.mark.parametrize("mode,uid,gid,want", [
    (0o700, RNSD_UID, 0, True),       # owner x
    (0o070, RNSD_UID, RNSD_GID, False),  # owner bits decide, even with group x
    (0o710, 0, RNSD_GID, True),       # group x
    (0o701, 0, RNSD_GID, False),      # in the group → group bits decide
    (0o701, 0, 0, True),              # other x
    (0o770, 0, 0, False),             # no x for other
])
def test_dac_search_cells(tmp_path, monkeypatch, mode, uid, gid, want):
    d, cfg = _f(tmp_path)
    _present(monkeypatch, {d: (mode, uid, gid)})
    assert isfile_as(cfg, RNSD_UID, {RNSD_GID}) is want


def test_file_bits_never_matter(tmp_path, monkeypatch):
    d, cfg = _f(tmp_path)
    _present(monkeypatch, {cfg: (0o000, 0, 0)})
    assert isfile_as(cfg, RNSD_UID, {RNSD_GID}) is True


def test_absent_and_non_regular_are_false_for_everyone(tmp_path, monkeypatch):
    _present(monkeypatch, {})
    assert isfile_as(tmp_path / "nope" / "config", RNSD_UID, {RNSD_GID}) is False
    (tmp_path / "adir").mkdir()
    assert isfile_as(tmp_path / "adir", RNSD_UID, {RNSD_GID}) is False


def test_symlinked_dir_target_ancestors_are_walked(tmp_path, monkeypatch):
    real = tmp_path / "real"
    real.mkdir()
    (real / "config").write_text("x")
    link = tmp_path / "link"
    link.symlink_to(real)
    _present(monkeypatch, {real: (0o700, 0, 0)})     # target unsearchable
    assert isfile_as(link / "config", RNSD_UID, {RNSD_GID}) is False


def test_observer_denied_is_false_only_for_its_own_creds(tmp_path, monkeypatch):
    d, cfg = _f(tmp_path)
    _present(monkeypatch, {}, deny=(d,))
    me = os.geteuid()
    mine = set(os.getgroups()) | {os.getegid()}
    assert isfile_as(cfg, me, mine) is False
    with pytest.raises(PathUnjudged):
        isfile_as(cfg, RNSD_UID, {RNSD_GID})


def test_pathunjudged_is_an_oserror_so_old_catchers_degrade_to_unknown():
    assert issubclass(PathUnjudged, OSError)


def test_no_ids_keeps_the_observers_own_view(tmp_path, monkeypatch):
    # get_config_dir ("my own config") and the CLI are unchanged.
    etc, home = _box(tmp_path, monkeypatch)
    assert ReticulumPaths.rnsd_config_dir_for_home(home) == etc


# ── r13.8.1 — Fable r20 ────────────────────────────────────────────────

@pytest.mark.parametrize("key", [
    "EnvironmentFile=-/etc/default/rnsd", "Environment=HOME=/srv/x", "ProtectHome=yes",
    "DynamicUser=yes", "AmbientCapabilities=CAP_DAC_READ_SEARCH", "PAMName=login",
])
def test_home_or_identity_key_does_not_blind_a_config_unit(tmp_path, key):
    # r20 #1 (IMPORTANT): these change which HOME/uid rnsd has, never which
    # dir ``--config`` names. OLD named the dir; r13.8 went UNKNOWN and the
    # instance-name reader fell to the stale-home guess.
    unit = tmp_path / "rnsd.service"
    unit.write_text(f"[Service]\nUser=op\n{key}\n"
                    "ExecStart=/usr/local/bin/rnsd --config /etc/reticulum --service\n")
    parsed = ra._read_systemd_unit(str(unit))
    assert parsed["view_keys"] and not parsed["namespace_keys"], key
    assert _resolve_rnsd_configdir(**ra._unit_kwargs(parsed), ids_of=IDS) == Path("/etc/reticulum")


def test_consumer_reads_the_config_flag_name_not_the_home_guess(tmp_path, monkeypatch):
    # r20 #1 at the END: the name the watchdog probes.
    etc = tmp_path / "etc"
    etc.mkdir()
    (etc / "config").write_text("[reticulum]\n  instance_name = ETC-NAME\n")
    home = tmp_path / "home"
    (home / ".reticulum").mkdir(parents=True)
    (home / ".reticulum" / "config").write_text("[reticulum]\n  instance_name = STALE-HOME\n")
    unit = tmp_path / "rnsd.service"
    unit.write_text(f"[Service]\nUser=op\nEnvironmentFile=-/etc/default/rnsd\n"
                    f"ExecStart=/usr/local/bin/rnsd --config {etc} --service\n")
    monkeypatch.setattr(ra, "rnsd_unit_paths", lambda: [str(unit)])
    monkeypatch.setattr(paths_mod, "get_real_user_home", lambda: home)
    assert ra.rnsd_configdir_of_record() == etc
    assert ra.read_rns_instance_name() == "ETC-NAME"


@pytest.mark.parametrize("key", [
    "RootDirectory=/jail", "RootImage=/x.img", "BindPaths=/srv:/etc/reticulum",
    "BindReadOnlyPaths=/srv:/etc/reticulum", "TemporaryFileSystem=/etc",
    "InaccessiblePaths=/etc/reticulum", "MountImages=/x.img:/etc",
    "ExtensionDirectories=/x", "ExtensionImages=/x.img",
])
def test_namespace_key_blinds_even_a_config_unit(tmp_path, key):
    unit = tmp_path / "rnsd.service"
    unit.write_text(f"[Service]\nUser=op\n{key}\n"
                    "ExecStart=/usr/local/bin/rnsd --config /etc/reticulum --service\n")
    parsed = ra._read_systemd_unit(str(unit))
    assert parsed["namespace_keys"], key
    with pytest.raises(RnsdConfigdirUnknown):
        _resolve_rnsd_configdir(**ra._unit_kwargs(parsed), ids_of=IDS)


@pytest.mark.parametrize("text,want", [
    ("ProtectHome = yes\n", True),                       # spaces round '='
    ("Environment = HOME=/srv/x\n", True),
    ("Environment=A=1 \\\n  HOME=/srv/x\n", True),       # continuation
    ("ProtectHome=yes\n", True),
    ("ProtectHome=n\n", False), ("ProtectHome=f\n", False), ("ProtectHome=false\n", False),
])
def test_unit_key_shapes_systemd_accepts(tmp_path, text, want):
    unit = tmp_path / "rnsd.service"
    unit.write_text("[Service]\nUser=op\n" + text)
    assert bool(ra._read_systemd_unit(str(unit))["view_keys"]) is want, text


def test_a_later_dropin_unsets_a_view_key(tmp_path):
    base = tmp_path / "rnsd.service"
    base.write_text("[Service]\nProtectHome=yes\nEnvironment=HOME=/x\n")
    drop = tmp_path / "10.conf"
    drop.write_text("[Service]\nProtectHome=no\nEnvironment=\n")
    assert ra._read_systemd_unit(str(base), str(drop))["view_keys"] == []


def test_group_with_spaces_round_equals_is_read(tmp_path):
    unit = tmp_path / "rnsd.service"
    unit.write_text("[Service]\nUser = op\nGroup = staff\n")
    parsed = ra._read_systemd_unit(str(unit))
    assert (parsed["user"], parsed["group"]) == ("op", "staff")


@pytest.mark.parametrize("group", ["²", "4294967296", "99999999999999999999"])
def test_bad_group_values_are_unknown_not_a_crash(tmp_path, monkeypatch, group):
    # r20 #5: ValueError ('²'.isdigit()) / OverflowError (getgrouplist) —
    # through the PRODUCTION identity source, as the real user.
    import pwd
    me = pwd.getpwuid(os.getuid()).pw_name
    etc, home = _box(tmp_path, monkeypatch)
    with pytest.raises(RnsdConfigdirUnknown):
        _resolve_rnsd_configdir(me, "/usr/local/bin/rnsd --service", group=group,
                                home_of=lambda u: home)
    with pytest.raises(RnsdConfigdirUnknown):
        _resolve_rnsd_configdir(me, "/usr/local/bin/rnsd --service",
                                supplementary=(group,), home_of=lambda u: home)


# production identity source — never injected here (r20 #7, A26/A27/A17)
def test_production_ids_of_is_passwd_and_getgrouplist():
    import pwd
    me = pwd.getpwuid(os.getuid())
    uid, gid, groups = ra._rnsd_ids_of(me.pw_name)
    assert (uid, gid) == (me.pw_uid, me.pw_gid)
    assert set(groups) == set(os.getgrouplist(me.pw_name, me.pw_gid))
    uid2, gid2, groups2 = ra._rnsd_ids_of(me.pw_name, 4245)
    assert gid2 == 4245 and 4245 in groups2


def test_resolver_judges_with_production_ids_when_not_injected(tmp_path, monkeypatch):
    import pwd
    me = pwd.getpwuid(os.getuid()).pw_name
    seen = {}
    real = ReticulumPaths.rnsd_config_dir_for_home.__func__

    def spy(cls, home, *, as_ids=None):
        seen["as_ids"] = as_ids
        return real(cls, home, as_ids=as_ids)
    monkeypatch.setattr(ReticulumPaths, "rnsd_config_dir_for_home", classmethod(spy))
    etc, home = _box(tmp_path, monkeypatch)
    _resolve_rnsd_configdir(me, "/usr/local/bin/rnsd --service", home_of=lambda u: home,
                            supplementary=("4246",))
    uid, gids = seen["as_ids"]
    assert uid == os.getuid() and os.getgid() in gids and 4246 in gids


def test_unit_kwargs_carries_every_identity_field():
    unit = {"user": "op", "exec_start": "x", "group": "g",
            "supplementary_groups": ["a"], "view_keys": ["ProtectHome=yes"],
            "namespace_keys": ["RootDirectory=/j"]}
    assert ra._unit_kwargs(unit) == {
        "user": "op", "exec_start": "x", "group": "g", "supplementary": ("a",),
        "view_keys": ("ProtectHome=yes",), "namespace_keys": ("RootDirectory=/j",)}


def test_probe_local_passes_the_whole_unit(monkeypatch):
    unit = {"user": "op", "exec_start": "/usr/local/bin/rnsd --service", "group": "g",
            "supplementary_groups": ["a"], "view_keys": [], "namespace_keys": []}
    monkeypatch.setattr(ra, "_read_systemd_unit", lambda *p: unit)
    got = {}

    def fake(**kw):
        got.update(kw)
        raise RnsdConfigdirUnknown("stop here")
    monkeypatch.setattr(ra, "_resolve_rnsd_configdir", fake)
    try:
        ra.probe_local()
    except Exception:
        pass
    assert got == ra._unit_kwargs(unit)


# ── symlinks walked hop by hop, as the kernel does (r20 #2, #8) ─────────

def test_multi_hop_chain_through_an_unsearchable_mid_dir(tmp_path, monkeypatch):
    real = tmp_path / "real"
    real.mkdir()
    (real / "config").write_text("x")
    mid = tmp_path / "mid"
    mid.mkdir()
    (mid / "hop").symlink_to(real)
    p = tmp_path / "P"
    p.symlink_to(mid / "hop")
    _present(monkeypatch, {mid: (0o700, 0, 0)})
    assert isfile_as(p / "config", RNSD_UID, {RNSD_GID}) is False


def test_single_hop_dotdot_through_an_unsearchable_dir(tmp_path, monkeypatch):
    (tmp_path / "q").mkdir()
    real = tmp_path / "realP"
    real.mkdir()
    (real / "config").write_text("x")
    p = tmp_path / "P"
    p.symlink_to("q/../realP")
    _present(monkeypatch, {tmp_path / "q": (0o700, 0, 0)})
    assert isfile_as(p / "config", RNSD_UID, {RNSD_GID}) is False
    _present(monkeypatch, {})
    assert isfile_as(p / "config", RNSD_UID, {RNSD_GID}) is True


def test_symlink_target_ancestor_unsearchable(tmp_path, monkeypatch):
    # the target's PARENT, not the target, refuses (P12 survived before)
    outer = tmp_path / "outer"
    (outer / "real").mkdir(parents=True)
    (outer / "real" / "config").write_text("x")
    link = tmp_path / "link"
    link.symlink_to(outer / "real")
    _present(monkeypatch, {outer: (0o700, 0, 0)})
    assert isfile_as(link / "config", RNSD_UID, {RNSD_GID}) is False


def test_symlink_loop_is_false_like_rns(tmp_path, monkeypatch):
    loop = tmp_path / "config"
    loop.symlink_to(loop)
    _present(monkeypatch, {})
    assert isfile_as(loop, RNSD_UID, {RNSD_GID}) is False


def test_same_creds_needs_the_same_groups(tmp_path, monkeypatch):
    # P14: uid match with DIFFERENT groups must take the model path.
    d, cfg = _f(tmp_path)
    _present(monkeypatch, {}, deny=(d,))
    with pytest.raises(PathUnjudged):
        isfile_as(cfg, os.geteuid(), {4247})


@pytest.mark.parametrize("err", [errno.EIO, errno.ELOOP + 1000])
def test_non_eacces_stat_errors_are_unknown(tmp_path, monkeypatch, err):
    d, cfg = _f(tmp_path)
    _present(monkeypatch, {})
    inner = paths_mod._stat

    def boom(p, *a, **k):
        if str(p) == str(d):
            raise OSError(err, "boom", str(p))
        return inner(p, *a, **k)
    monkeypatch.setattr(paths_mod, "_stat", boom)
    with pytest.raises(PathUnjudged):
        isfile_as(cfg, RNSD_UID, {RNSD_GID})


def test_enotdir_is_false(tmp_path, monkeypatch):
    f = tmp_path / "file"
    f.write_text("x")
    _present(monkeypatch, {})
    assert isfile_as(f / "config", RNSD_UID, {RNSD_GID}) is False


@pytest.mark.parametrize("getx,want", [
    (None, "raise"),
    ("eio", "raise"),
    ("enotsup", False),
])
def test_acl_read_legs(tmp_path, monkeypatch, getx, want):
    d, cfg = _f(tmp_path)
    _present(monkeypatch, {d: (0o711, 0, 0)})
    if getx is None:
        monkeypatch.setattr(paths_mod, "_getxattr", None)
    else:
        code = errno.EIO if getx == "eio" else errno.ENOTSUP

        def gx(p, n):
            raise OSError(code, "x", p)
        monkeypatch.setattr(paths_mod, "_getxattr", gx)
    if want == "raise":
        with pytest.raises(PathUnjudged):
            isfile_as(cfg, RNSD_UID, {RNSD_GID})
    else:
        assert isfile_as(cfg, RNSD_UID, {RNSD_GID}) is True


# ── r21 NIT 2: walker behaviours only the reviewer's differential caught ─

def test_absolute_symlink_target_restarts_at_root(tmp_path, monkeypatch):
    # W3 survived: an absolute target must resolve from '/', not from the
    # dir holding the link (else the walk finds nothing → False).
    real = tmp_path / "real"
    real.mkdir()
    (real / "config").write_text("x")
    holder = tmp_path / "holder"
    holder.mkdir()
    (holder / "P").symlink_to(str(real))          # absolute
    _present(monkeypatch, {})
    assert isfile_as(holder / "P" / "config", RNSD_UID, {RNSD_GID}) is True


def test_last_component_symlink_is_expanded(tmp_path, monkeypatch):
    # W11 survived: config ITSELF a symlink into a dir rnsd cannot search.
    hidden = tmp_path / "hidden"
    hidden.mkdir()
    (hidden / "config").write_text("x")
    d = tmp_path / "d"
    d.mkdir()
    (d / "config").symlink_to(hidden / "config")
    _present(monkeypatch, {hidden: (0o700, 0, 0)})
    assert isfile_as(d / "config", RNSD_UID, {RNSD_GID}) is False
    _present(monkeypatch, {})
    assert isfile_as(d / "config", RNSD_UID, {RNSD_GID}) is True


@pytest.mark.parametrize("hops,want", [(39, True), (40, True), (41, False)])
def test_symlink_hop_limit_is_the_kernels(tmp_path, monkeypatch, hops, want):
    # W1a survived: Linux follows 40 links and refuses the 41st (ELOOP).
    real = tmp_path / "real"
    real.mkdir()
    (real / "config").write_text("x")
    prev = real
    for i in range(hops):
        ln = tmp_path / f"l{i}"
        ln.symlink_to(prev)
        prev = ln
    _present(monkeypatch, {})
    assert os.path.isfile(prev / "config") is want     # the kernel's own answer
    assert isfile_as(prev / "config", RNSD_UID, {RNSD_GID}) is want
