"""The shim entry point. Runs on EVERY shimmed invocation, so it is the one
module in this package that is optimised for start-up cost and for not failing.

Two rules govern everything here:

  1. Never import click. This module runs thousands of times a day; the
     management CLI does not.
  2. Never raise. Any unexpected error falls back to executing the real binary.
     A bug in the tether must not be able to take an agent CLI off the machine.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import sys

WINDOWS = os.name == "nt"


def _fallback_exec(agent: str, argv: list[str]) -> int:
    """Last resort: run the real binary with no tethering at all.

    Reached when something in our own logic broke. Losing durability for one
    call is survivable; making `claude` stop working is not.

    The WHOLE body is guarded: previously only the lookup was, so an OSError
    from executing the target (a .ps1 on Windows raises WinError 193) escaped
    as a traceback from the very handler whose job is to prevent that.
    """
    try:
        from .resolve import next_binary

        return exec_passthrough(next_binary(agent), argv)
    except BaseException as exc:  # noqa: BLE001 - this is the last line of defence
        sys.stderr.write(f"tether: could not run {agent}: {exc}\n")
        return 127


def exec_passthrough(binary: str, argv: list[str]) -> int:
    """Become the real binary, as transparently as the OS allows.

    POSIX: os.execv REPLACES this process. The child inherits the pid, the
    terminal, the process group and the signal disposition, so Ctrl-C, job
    control and exit codes behave exactly as if the shim were never there.
    Nothing to propagate because there is no longer a parent.

    Windows: os.execv does NOT replace the process - it spawns a new one and
    the original exits immediately. A waiting parent would see the shim exit
    while the agent is still running, and the exit code would be lost. So we
    spawn and wait instead, and let the child share our console so Ctrl-C is
    delivered by the OS to the whole console process group.
    """
    env = dict(os.environ)
    env["TETHER_ACTIVE"] = "1"  # recursion guard

    if not WINDOWS:
        # execve REPLACES this process: same pid, same terminal, same process
        # group. Ctrl-C, job control and exit codes then behave exactly as if
        # the shim were never in the picture. If it fails we fall through.
        with contextlib.suppress(OSError):
            os.execve(binary, [binary, *argv], env)

    try:
        completed = subprocess.run([binary, *argv], env=env)
        return completed.returncode
    except FileNotFoundError:
        sys.stderr.write(f"tether: cannot execute {binary!r}\n")
        return 127
    except KeyboardInterrupt:
        # The child already received Ctrl-C from the console. 130 is the
        # conventional shell encoding of SIGINT.
        return 130


def _agent_name(raw: list[str]) -> str:
    """Which agent are we standing in for?

    Preference order, and the order matters:

    1. argv[0]'s stem. On Windows the shim is a COPY of the console-script
       launcher named `claude.exe`, so its own filename is the agent. This is
       the only channel that survives a bare CreateProcess spawn - which is
       precisely the orchestrator case this product exists for.
    2. TETHER_SHIM_AGENT, set by the POSIX sh shim.
    3. The first argument, for `tether-shim claude ...` by hand.
    """
    stem = ""
    try:
        stem = os.path.splitext(os.path.basename(sys.argv[0]))[0]
    except Exception:  # noqa: BLE001
        stem = ""
    if stem and stem not in {"tether-shim", "tether", "__main__", "shim", "python", "python3"}:
        return stem
    env_name = os.environ.get("TETHER_SHIM_AGENT", "")
    if env_name:
        return env_name
    return raw[0] if raw else ""


def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)

    agent = _agent_name(raw)
    if not agent:
        sys.stderr.write("tether-shim: no agent specified\n")
        return 2
    if raw and not os.environ.get("TETHER_SHIM_AGENT") and raw[0] == agent:
        raw = raw[1:]

    try:
        from .run import run_shim

        return run_shim(agent, raw)
    except KeyboardInterrupt:
        return 130
    except SystemExit as exc:  # argparse-style exits from deeper code
        return int(exc.code or 0)
    except BaseException as exc:  # noqa: BLE001 - deliberate catch-all
        if os.environ.get("TETHER_DEBUG") == "1":
            raise
        sys.stderr.write(
            f"tether: internal error ({type(exc).__name__}: {exc}); running {agent} untethered\n"
        )
        return _fallback_exec(agent, raw)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
