"""Regressions from the adversarial review.

Every test here corresponds to a defect that was REPRODUCED against the real
shim, and every one of them failed silently. They are pinned separately so the
provenance stays obvious.
"""

from __future__ import annotations

import pytest

from tether import paths, registry, router, store
from tether.registry import Agent
from tether.router import Lane, classify
from tether.run import _merge_resume_argv


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv(paths.HOME_ENV, str(tmp_path))
    paths.ensure_dirs()
    return tmp_path


# --- lane safety -----------------------------------------------------------


def test_disable_beats_force_lane():
    """TETHER_FORCE_LANE used to outrank the documented escape hatch."""
    a = Agent(name="x", source="builtin")
    env = {"TETHER_FORCE_LANE": "agent", "TETHER_DISABLE": "1"}
    assert classify(a, ["-p", "x"], env=env, tty=False).lane is Lane.PASSTHROUGH


def test_nesting_guard_beats_force_lane():
    a = Agent(name="x", source="builtin")
    env = {"TETHER_FORCE_LANE": "agent", "ZELLIJ_SESSION_NAME": "s"}
    assert classify(a, [], env=env, tty=False).lane is Lane.PASSTHROUGH


def test_unknown_agent_without_a_tty_passes_through():
    """A fallback Agent has no flag data, so it cannot classify anything.

    It used to send every headless call to the agent lane, meaning a disabled
    or unknown agent became MORE dangerous, not less.
    """
    unknown = registry.Registry().get("nosuchagent")
    assert not unknown.known
    assert classify(unknown, ["-p", "hi"], env={}, tty=False).lane is Lane.PASSTHROUGH
    assert classify(unknown, ["mcp", "list"], env={}, tty=False).lane is Lane.PASSTHROUGH


def test_unknown_agent_with_a_tty_is_still_tetherable():
    unknown = registry.Registry().get("nosuchagent")
    assert classify(unknown, [], env={}, tty=True).lane is Lane.HUMAN


@pytest.mark.parametrize(
    "argv",
    [
        ["--cd", "/repo", "exec", "run tests"],
        ["-C", "/repo", "exec", "x"],
        ["--sandbox", "danger-full-access", "exec", "x"],
        ["-a", "never", "exec", "x"],
        ["--some-future-flag", "/repo", "exec", "x"],
    ],
)
def test_codex_headless_exec_survives_flags_before_it(isolated, argv):
    """A value-taking flag we do not know about made the flag's VALUE look like
    the subcommand, so `codex --cd /repo exec ...` got tethered."""
    codex = registry.load().agents["codex"]
    assert classify(codex, argv, env={}, tty=False).lane is Lane.PASSTHROUGH


def test_codex_resume_is_still_tethered(isolated):
    codex = registry.load().agents["codex"]
    assert classify(codex, ["resume", "abc"], env={}, tty=False).lane is Lane.AGENT


def test_empty_session_id_is_not_treated_as_provided(isolated):
    """`--session-id ""` is falsy, so run.py prepended a SECOND one."""
    claude = registry.load().agents["claude"]
    assert router.provided_session_id(claude, ["--session-id", ""]) is None
    assert router.provided_session_id(claude, ["--session-id="]) is None


# --- restore fidelity ------------------------------------------------------


def test_resume_keeps_the_original_launch_flags(isolated):
    """A session started with --model opus must not come back without it."""
    claude = registry.load().agents["claude"]
    original = ["--session-id", "old-uuid", "--model", "opus", "--add-dir", "../shared"]
    merged = _merge_resume_argv(claude, original, ["--resume", "new-uuid"])
    assert merged[:2] == ["--resume", "new-uuid"]
    assert "--model" in merged and "opus" in merged
    assert "--add-dir" in merged and "../shared" in merged
    # the stale selector and its value are gone, and not duplicated
    assert "--session-id" not in merged
    assert "old-uuid" not in merged
    assert merged.count("--resume") == 1


def test_resume_strips_an_existing_resume_selector(isolated):
    claude = registry.load().agents["claude"]
    merged = _merge_resume_argv(claude, ["--resume", "old", "--model", "x"], ["--resume", "new"])
    assert merged.count("--resume") == 1
    assert "old" not in merged


def test_resume_strips_a_bare_subcommand_selector(isolated):
    codex = registry.load().agents["codex"]
    merged = _merge_resume_argv(codex, ["resume", "old", "--model", "x"], ["resume", "new"])
    assert merged == ["resume", "new", "--model", "x"]


# --- store -----------------------------------------------------------------


def test_find_by_subtree_ignores_a_sibling_with_a_shared_prefix(isolated, tmp_path):
    """/repo must not match /repo-backup."""
    a = tmp_path / "repo"
    b = tmp_path / "repo-backup"
    a.mkdir()
    b.mkdir()
    for name, cwd in (("in", a), ("out", b)):
        store.save(
            store.SessionRecord(
                session=name, agent="claude", binary="c", cwd=str(cwd), lane="human"
            )
        )
    assert {r.session for r in store.find(under=str(a))} == {"in"}


def test_socket_markers_are_recognised_on_posix(isolated, monkeypatch):
    """zellij's session markers are real unix SOCKETS on Linux and macOS.

    Path.is_file() is False for a socket, so the old probe returned an empty
    list on every POSIX machine - `tether ls` could never show a live session.
    Windows uses a regular-file marker, which is why only Linux CI caught it.
    """
    import socket as socket_mod

    from tether import zellij

    marker_dir = paths.socket_dir() / "contract_version_1"
    marker_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(zellij, "_run", lambda *a, **k: None)

    if hasattr(socket_mod, "AF_UNIX"):
        sock = socket_mod.socket(socket_mod.AF_UNIX, socket_mod.SOCK_STREAM)
        try:
            sock.bind(str(marker_dir / "tether-sock-session"))
            assert "tether-sock-session" in zellij.live_sessions()
        finally:
            sock.close()
    else:
        (marker_dir / "tether-file-session").write_text("", encoding="utf-8")
        assert "tether-file-session" in zellij.live_sessions()


def test_directories_are_not_mistaken_for_sessions(isolated, monkeypatch):
    from tether import zellij

    marker_dir = paths.socket_dir() / "contract_version_1"
    (marker_dir / "not-a-session").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(zellij, "_run", lambda *a, **k: None)
    assert "not-a-session" not in zellij.live_sessions()
