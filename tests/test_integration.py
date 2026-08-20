"""Integration tests. These need a real zellij on PATH.

Skipped unless TETHER_INTEGRATION=1, so a normal `pytest` run stays fast and
hermetic. CI runs them on Linux, where a headless runner can still create
DETACHED sessions - the agent lane needs no controlling terminal, which is
exactly what makes it testable here.

Everything runs inside a per-test TETHER_HOME, so these never touch the
developer's own sessions.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time

import pytest

from tether import paths, store, zellij

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("TETHER_INTEGRATION") != "1",
        reason="set TETHER_INTEGRATION=1 to run",
    ),
    pytest.mark.skipif(shutil.which("zellij") is None, reason="zellij not installed"),
]

LONG_RUNNING = [sys.executable, "-c", "import time;[time.sleep(1) for _ in range(600)]"]


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv(paths.HOME_ENV, str(tmp_path))
    monkeypatch.delenv("ZELLIJ", raising=False)
    monkeypatch.delenv("ZELLIJ_SESSION_NAME", raising=False)
    paths.ensure_dirs()
    yield tmp_path
    for name in zellij.live_sessions():
        zellij.kill(name)


def diagnose(session: str) -> str:
    """Everything needed to explain a failure on a machine we cannot reach.

    A bare 'session never became live' is unactionable in CI. This dumps what
    zellij was actually asked to do and what it actually produced.
    """
    import subprocess

    lines = [
        "",
        f"session       : {session}",
        f"socket_dir    : {paths.socket_dir()}  exists={paths.socket_dir().is_dir()}",
        f"headroom      : {paths.socket_path_headroom()}",
        f"layouts_dir   : {paths.layouts_dir()}",
    ]
    marker = paths.socket_dir() / "contract_version_1"
    lines.append(f"marker_dir    : {marker}  exists={marker.is_dir()}")
    if marker.is_dir():
        lines.append(f"markers       : {[p.name for p in marker.iterdir()]}")
    layout = paths.layouts_dir() / f"{session}.kdl"
    if layout.is_file():
        lines.append("layout        : " + layout.read_text(encoding="utf-8").replace("\n", " | "))
    try:
        proc = subprocess.run(
            [zellij.executable(), "ls"],
            env=zellij.base_env(),
            capture_output=True,
            text=True,
            timeout=20,
        )
        lines.append(f"zellij ls rc  : {proc.returncode}")
        lines.append(f"zellij ls out : {proc.stdout.strip()!r}")
        lines.append(f"zellij ls err : {proc.stderr.strip()!r}")
    except Exception as exc:  # noqa: BLE001
        lines.append(f"zellij ls     : raised {exc}")
    return "\n".join(lines)


def wait_for(predicate, timeout=30.0, interval=0.5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def test_zellij_is_new_enough():
    ver = zellij.version()
    assert ver is not None, "could not parse zellij --version"
    assert ver >= zellij.MIN_VERSION, f"zellij {ver} is older than {zellij.MIN_VERSION}"


def test_create_detached_then_see_it_live(isolated):
    session = "tether-itest-create"
    rc = zellij.create(
        session,
        LONG_RUNNING[0],
        LONG_RUNNING[1:],
        str(isolated),
        detached=True,
        env={},
    )
    assert rc == 0, diagnose(session)
    assert wait_for(lambda: session in zellij.live_sessions()), diagnose(session)

    # A live session must be discoverable from the socket markers, which is
    # what `tether ls` relies on instead of `zellij ls`.
    assert session in zellij.live_sessions()

    zellij.kill(session)
    assert wait_for(lambda: session not in zellij.live_sessions())


def test_generated_layout_round_trips_awkward_arguments(isolated):
    """Arguments with quotes and backslashes must survive KDL generation."""
    session = "tether-itest-args"
    argv = ['a "quoted" arg', "back\\slash", "spaces here", ""]
    path = zellij.write_layout(session, "/bin/echo", argv, str(isolated))
    text = path.read_text(encoding="utf-8")
    assert '\\"quoted\\"' in text
    assert "back\\\\slash" in text


def test_live_sessions_does_not_leak_other_namespaces(isolated):
    """Our liveness probe must only see OUR socket dir.

    zellij's resurrection cache is machine-wide and cannot be redirected on
    Windows, so a probe based on `zellij ls` would report foreign sessions.
    """
    assert zellij.live_sessions() == []


def test_kill_removes_the_registry_record(isolated):
    session = "tether-itest-record"
    store.save(
        store.SessionRecord(
            session=session,
            agent="fake",
            binary=LONG_RUNNING[0],
            cwd=str(isolated),
            lane="agent",
        )
    )
    assert store.load(session) is not None
    store.delete(session)
    assert store.load(session) is None


def test_cli_ls_json_is_valid_and_versioned(isolated):
    import json

    proc = subprocess.run(
        [sys.executable, "-m", "tether", "ls", "--json"],
        capture_output=True,
        text=True,
        env={**os.environ, paths.HOME_ENV: str(isolated)},
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["schema"] == 1
    assert "sessions" in payload


def test_cli_doctor_json_reports_zellij(isolated):
    import json

    proc = subprocess.run(
        [sys.executable, "-m", "tether", "doctor", "--json"],
        capture_output=True,
        text=True,
        env={**os.environ, paths.HOME_ENV: str(isolated)},
    )
    payload = json.loads(proc.stdout)
    assert payload["zellij"]["path"], "doctor did not find zellij"
    assert payload["schema"] == 1
