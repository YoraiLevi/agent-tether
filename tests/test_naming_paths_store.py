"""Naming, paths and the session registry.

The naming tests encode the property that makes the daily loop work: the SAME
project must always produce the SAME session name, across symlinks, trailing
slashes and case differences. Two names for one project silently gives the user
two agents and half their context.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tether import naming, paths, store

# --------------------------------------------------------------------------
# naming
# --------------------------------------------------------------------------


def test_same_project_same_name(tmp_path):
    a = naming.for_cwd("claude", str(tmp_path))
    b = naming.for_cwd("claude", str(tmp_path) + os.sep)
    assert a == b


def test_case_differences_do_not_fork_a_session(tmp_path):
    p = tmp_path / "Project"
    p.mkdir()
    a = naming.for_cwd("claude", str(p))
    b = naming.for_cwd("claude", str(p).swapcase() if os.name == "nt" else str(p))
    if os.name == "nt":
        assert a == b


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
def test_symlink_and_target_share_a_session(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    assert naming.for_cwd("claude", str(link)) == naming.for_cwd("claude", str(real))


def test_non_ascii_project_name_produces_a_usable_name(tmp_path):
    p = tmp_path / "проект-café"
    p.mkdir()
    name = naming.for_cwd("claude", str(p))
    assert name.isascii()
    assert " " not in name
    assert naming.is_tether_session(name)


def test_spaces_in_path_are_handled(tmp_path):
    p = tmp_path / "my project dir"
    p.mkdir()
    name = naming.for_cwd("claude", str(p))
    assert " " not in name


def test_names_are_bounded(tmp_path):
    deep = tmp_path / ("x" * 200)
    name = naming.for_cwd("claude", str(deep))
    assert len(name) <= naming.MAX_NAME


def test_same_session_id_same_name():
    assert naming.for_session_id("claude", "abc") == naming.for_session_id("claude", "abc")


def test_different_agents_do_not_collide_in_one_directory(tmp_path):
    assert naming.for_cwd("claude", str(tmp_path)) != naming.for_cwd("codex", str(tmp_path))


def test_new_names_are_unique():
    assert naming.for_new("claude") != naming.for_new("claude")


def test_new_session_id_is_a_uuid():
    import uuid

    uuid.UUID(naming.new_session_id())  # raises if malformed


# --------------------------------------------------------------------------
# paths
# --------------------------------------------------------------------------


def test_tether_home_relocates_everything(tmp_path, monkeypatch):
    monkeypatch.setenv(paths.HOME_ENV, str(tmp_path))
    for value in paths.describe().values():
        if value.startswith(str(paths.builtin_data_dir())):
            continue
        assert str(tmp_path) in value or "data" in value


def test_xdg_vars_are_honoured_on_every_os(tmp_path, monkeypatch):
    monkeypatch.delenv(paths.HOME_ENV, raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    assert paths.config_dir() == tmp_path / "cfg" / paths.APP


def test_relative_xdg_value_is_ignored(monkeypatch):
    monkeypatch.delenv(paths.HOME_ENV, raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", "relative/path")
    assert paths.config_dir().is_absolute()


def test_shim_dir_is_not_the_same_dir_as_installed_clis(monkeypatch, tmp_path):
    """A shim beside claude.exe would lose to PATHEXT on Windows."""
    monkeypatch.delenv(paths.HOME_ENV, raising=False)
    monkeypatch.delenv("TETHER_BIN_DIR", raising=False)
    assert paths.bin_dir() != Path.home() / ".local" / "bin"


def test_ensure_dirs_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv(paths.HOME_ENV, str(tmp_path))
    paths.ensure_dirs()
    paths.ensure_dirs()
    assert paths.sessions_dir().is_dir()


# --------------------------------------------------------------------------
# store
# --------------------------------------------------------------------------


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv(paths.HOME_ENV, str(tmp_path))
    paths.ensure_dirs()
    return tmp_path


def record(session="tether-claude-abc", cwd="/tmp/p", agent="claude", pid=""):
    return store.SessionRecord(
        session=session,
        agent=agent,
        binary="/usr/bin/claude",
        cwd=cwd,
        lane="human",
        provider_session_id=pid,
    )


def test_roundtrip(isolated):
    store.save(record())
    got = store.load("tether-claude-abc")
    assert got is not None and got.agent == "claude"


def test_corrupt_record_costs_one_session_not_all(isolated):
    store.save(record(session="good"))
    (paths.sessions_dir() / "bad.json").write_text("{not json", encoding="utf-8")
    assert [r.session for r in store.all_records()] == ["good"]
    assert store.load("bad") is None
    assert any("bad.json" in p for p in store.corrupt_records())


def test_partial_write_leaves_no_visible_record(isolated):
    """Atomic writes: a crash mid-save must not produce a half record."""
    store.save(record(session="s1"))
    tmp = paths.sessions_dir() / ".tmp-half.json"
    tmp.write_text('{"session": "hal', encoding="utf-8")
    assert [r.session for r in store.all_records()] == ["s1"]


def test_carried_env_takes_orchestrator_identity_only():
    env = {
        "ORCA_PANE_KEY": "a:b",
        "ORCA_AGENT_HOOK_ENDPOINT": "/x",
        "CLAUDE_CODE_SESSION_ID": "leaky",
        "PATH": "/usr/bin",
    }
    carried = store.carried_env(env)
    assert carried == {"ORCA_PANE_KEY": "a:b", "ORCA_AGENT_HOOK_ENDPOINT": "/x"}
    assert "CLAUDE_CODE_SESSION_ID" not in carried, "vendor session vars must not leak into a child"


def test_find_by_exact_cwd_and_by_subtree(isolated, tmp_path):
    repo = tmp_path / "repo"
    sub = repo / "packages" / "web"
    sub.mkdir(parents=True)
    store.save(record(session="s-root", cwd=str(repo)))
    store.save(record(session="s-sub", cwd=str(sub)))

    exact = store.find(cwd=str(repo))
    assert {r.session for r in exact} == {"s-root"}

    subtree = store.find(under=str(repo))
    assert {r.session for r in subtree} == {"s-root", "s-sub"}


def test_find_by_vendor_session_id(isolated):
    store.save(record(session="s1", pid="uuid-42"))
    assert [r.session for r in store.find(session_id="uuid-42")] == ["s1"]


def test_find_by_agent(isolated):
    store.save(record(session="a", agent="claude"))
    store.save(record(session="b", agent="codex"))
    assert [r.session for r in store.find(agent="codex")] == ["b"]
