"""Everything that talks to zellij.

Isolated here so the rest of the tool is testable without zellij installed, and
so the several zellij behaviours that surprised us are documented in one place.
"""

from __future__ import annotations

import contextlib
import os
import re
import shutil
import subprocess
from pathlib import Path

from . import paths

MIN_VERSION = (0, 44, 0)  # native Windows support landed in 0.44.0


class ZellijMissing(RuntimeError):
    pass


def executable() -> str:
    exe = os.environ.get("TETHER_ZELLIJ") or shutil.which("zellij")
    if not exe:
        raise ZellijMissing(
            "zellij was not found on PATH.\n"
            "agent-tether is a thin layer over zellij and cannot work without it.\n"
            "  cargo install --locked zellij\n"
            "See docs/INSTALL.md for other options."
        )
    return exe


def version() -> tuple[int, int, int] | None:
    try:
        out = subprocess.run(
            [executable(), "--version"], capture_output=True, text=True, timeout=10
        ).stdout
    except Exception:
        return None
    # Match a leading x.y.z anywhere in the output. The old parser required
    # three purely numeric dot-separated parts, so "0.44.3+git" or "0.43.0-dev"
    # returned None - and doctor then SKIPPED the minimum-version check and
    # reported no problem. An indicator that cannot go red is not an indicator.
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", out)
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)))


def base_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ)
    env["ZELLIJ_SOCKET_DIR"] = str(paths.socket_dir())
    env["TETHER_ACTIVE"] = "1"  # recursion guard for anything spawned inside
    if extra:
        env.update(extra)
    return env


def _run(args: list[str], extra_env: dict[str, str] | None = None, **kw):
    return subprocess.run([executable(), *args], env=base_env(extra_env), **kw)


def live_sessions() -> list[str]:
    """Names of sessions that are actually running.

    NOT `zellij ls`. That lists resurrectable sessions too, and because
    zellij's resurrection cache lives in the OS cache dir and cannot be
    redirected on Windows, sessions from OTHER socket namespaces leak into it.
    Using it would report a dead session - or somebody else's - as live.

    A session is live iff it has a socket marker in OUR socket dir. We still
    invoke `zellij ls` first, because that is what probes each socket and reaps
    the stale markers a crash leaves behind.
    """
    with contextlib.suppress(Exception):
        _run(["ls"], capture_output=True, text=True, timeout=20)
    marker_dir = paths.socket_dir() / "contract_version_1"
    if not marker_dir.is_dir():
        return []
    try:
        # NOT is_file(). On Linux and macOS these markers are real unix domain
        # SOCKETS, and Path.is_file() is False for a socket - so this returned
        # an empty list on every POSIX machine and `tether ls` could never show
        # a live session. Windows uses named pipes with a regular file marker,
        # which is why it worked there and only Linux CI caught it.
        #
        # zellij makes the same distinction in its own source: is_socket() on
        # unix, is_file() on everything else. Accepting any non-directory entry
        # covers both without per-platform branching.
        return sorted(p.name for p in marker_dir.iterdir() if not p.is_dir())
    except OSError:
        return []


def recoverable_sessions() -> list[str]:
    """Sessions zellij offers to resurrect.

    These come from the shared resurrection cache, so entries created outside
    this socket namespace can appear. Callers must cross-check the registry
    before treating one as ours.
    """
    try:
        proc = _run(["ls"], capture_output=True, text=True, timeout=20)
    except Exception:
        return []
    out: list[str] = []
    for line in (proc.stdout or "").splitlines():
        clean = _strip_ansi(line).strip()
        if not clean or "EXITED" not in clean:
            continue
        out.append(clean.split()[0])
    return out


def _strip_ansi(text: str) -> str:
    result: list[str] = []
    i = 0
    while i < len(text):
        if text[i] == "\x1b":
            j = i + 1
            while j < len(text) and text[j] not in "mK":
                j += 1
            i = j + 1
            continue
        result.append(text[i])
        i += 1
    return "".join(result)


def kdl_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def write_layout(session: str, binary: str, argv: list[str], cwd: str) -> Path:
    """Generate the per-session layout.

    The command is baked into the layout rather than injected afterwards so
    that zellij serialises it, which is what makes the terminal recoverable.
    """
    paths.layouts_dir().mkdir(parents=True, exist_ok=True)
    lines = [
        "// generated by agent-tether - do not edit by hand",
        "layout {",
        f"    cwd {kdl_string(cwd)}",
        f"    pane command={kdl_string(binary)} {{",
    ]
    if argv:
        lines.append("        args " + " ".join(kdl_string(a) for a in argv))
    lines += ["    }", "}", ""]
    path = paths.layouts_dir() / f"{session}.kdl"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _option_args(layout: str) -> list[str]:
    return [
        "options",
        "--default-layout",
        layout,
        "--layout-dir",
        str(paths.layouts_dir()),
        "--show-startup-tips",
        "false",
    ]


def create(
    session: str, binary: str, argv: list[str], cwd: str, *, detached: bool, env: dict[str, str]
) -> int:
    write_layout(session, binary, argv, cwd)
    flag = "-b" if detached else "-c"
    args = ["--config", str(paths.zellij_config_file()), "attach", flag, session]
    args += _option_args(session)
    if not detached:
        # Interactive: zellij owns the terminal, so it must inherit stdio.
        return _run(args, extra_env=env).returncode
    # Detached: the caller's stdout carries the machine-readable session id and
    # nothing else, so zellij must not write to it.
    #
    # DEVNULL, NOT a pipe. capture_output=True deadlocks here: the detached
    # zellij SERVER inherits the pipe and never closes it, so subprocess.run
    # waits for EOF forever even though the client has exited.
    # stderr is left inherited on purpose - zellij's diagnostics should reach
    # the user, and stderr is not part of the contract.
    return _run(args, extra_env=env, stdout=subprocess.DEVNULL).returncode


def attach(
    session: str, *, force_run_commands: bool = False, env: dict[str, str] | None = None
) -> int:
    args = ["--config", str(paths.zellij_config_file()), "attach", session]
    if force_run_commands:
        args.append("--force-run-commands")
    return _run(args, extra_env=env).returncode


def kill(session: str) -> None:
    for sub in ("kill-session", "delete-session"):
        with contextlib.suppress(Exception):
            _run([sub, session], capture_output=True, timeout=20)


def delete(session: str) -> None:
    with contextlib.suppress(Exception):
        _run(["delete-session", session], capture_output=True, timeout=20)


def dump_screen(session: str) -> tuple[str, int]:
    """Return (screen, returncode).

    The return code matters: a missing session is not an empty screen, and a
    caller polling one must be able to tell the difference.
    """
    proc = _run(
        ["--session", session, "action", "dump-screen"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    return (proc.stdout or ""), proc.returncode


def write_chars(session: str, text: str) -> int:
    return _run(
        ["--session", session, "action", "write-chars", text], capture_output=True
    ).returncode


def send_enter(session: str) -> int:
    return _run(["--session", session, "action", "write", "13"], capture_output=True).returncode


def list_clients(session: str) -> str:
    proc = _run(
        ["--session", session, "action", "list-clients"], capture_output=True, text=True, timeout=20
    )
    return proc.stdout or ""
