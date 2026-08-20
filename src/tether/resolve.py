"""Finding the REAL binary behind a shim of the same name.

This is the recursion problem: a file called `claude` on PATH must locate the
*other* file called `claude` on PATH. Get it wrong and the shim calls itself
forever, taking the agent CLI off the machine.

Two independent defences, because either alone has a hole:

  1. Same-file detection. On POSIX, compare (st_dev, st_ino) against our own
     shim - that is exact even through symlinks, hardlinks and relative PATH
     entries. On Windows, compare resolved paths case-insensitively.
  2. Marker detection. Every generated shim contains the string
     `agent-tether-shim`. This catches shims we did not generate in this run,
     and shims left behind by an older install.

Chaining is a feature, not an accident: if other software also shadows
`claude`, we skip only OUR shims, so theirs is still reached through us.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

MARKER = "agent-tether-shim"

#: Extensions Windows will execute from PATH, in the order cmd.exe tries them.
WINDOWS_EXTS = (".com", ".exe", ".bat", ".cmd", ".ps1", "")
POSIX_EXTS = ("",)


class BinaryNotFound(RuntimeError):
    pass


def launcher_exe() -> Path | None:
    """The installed `tether-shim.exe` console-script launcher, if present.

    This lives in RESOLUTION, not just generation, and that placement is the
    whole point. On Windows our shim is a byte-copy of this launcher named
    `claude.exe`, because CreateProcess appends only `.exe` and never consults
    PATHEXT - so a `.cmd` shim is invisible to a bare
    `subprocess.Popen(["claude"])`, which is the orchestrator case this tool
    exists for.

    A copied binary cannot carry a marker string. If resolution failed to
    recognise it, next_binary() would hand back our own shim and it would call
    itself forever.
    """
    if os.name != "nt":
        return None
    found = shutil.which("tether-shim")
    if found and found.lower().endswith(".exe"):
        return Path(found)
    candidate = Path(sys.executable).parent / "tether-shim.exe"
    return candidate if candidate.is_file() else None


def _is_our_shim(path: Path) -> bool:
    """True for both shim flavours: marked text, or a copy of our launcher."""
    try:
        if path.suffix.lower() == ".exe":
            source = launcher_exe()
            if source is None or not source.is_file():
                return False
            try:
                return path.stat().st_size == source.stat().st_size and (
                    path.read_bytes() == source.read_bytes()
                )
            except OSError:
                return False
        if path.stat().st_size > 64 * 1024:
            return False  # a real binary; do not read megabytes off disk
        with path.open("rb") as fh:
            return MARKER.encode() in fh.read(4096)
    except OSError:
        return False


def _same_file(a: Path, b: Path) -> bool:
    try:
        return os.path.samefile(a, b)
    except OSError:
        try:
            return a.resolve().as_posix().lower() == b.resolve().as_posix().lower()
        except OSError:
            return False


def _self_paths() -> list[Path]:
    """Paths that would mean "this is me": argv[0] and anything it links to."""
    out: list[Path] = []
    argv0 = os.environ.get("TETHER_SHIM_PATH") or ""
    if argv0:
        out.append(Path(argv0))
    return out


def candidates(name: str) -> list[Path]:
    """Every executable called `name` on PATH, in PATH order."""
    exts = WINDOWS_EXTS if os.name == "nt" else POSIX_EXTS
    found: list[Path] = []
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if not entry:
            continue
        base = Path(entry).expanduser()
        for ext in exts:
            candidate = base / f"{name}{ext}"
            try:
                if not candidate.is_file():
                    continue
            except OSError:
                continue
            if os.name != "nt" and not os.access(candidate, os.X_OK):
                continue
            found.append(candidate)
    return found


def next_binary(name: str, *, configured: str | None = None) -> str:
    """Resolve the binary a shim should hand off to.

    Precedence: explicit env override, then a configured absolute path, then
    the first PATH entry that is not one of our shims.
    """
    env_key = f"TETHER_TARGET_{name.upper().replace('-', '_')}"
    override = os.environ.get(env_key)
    if override:
        p = Path(override).expanduser()
        if p.is_file():
            return str(p)
        raise BinaryNotFound(f"{env_key} points at a missing file: {override}")

    if configured and (os.sep in configured or (os.altsep and os.altsep in configured)):
        p = Path(configured).expanduser()
        if p.is_file():
            return str(p)

    mine = _self_paths()
    for candidate in candidates(name):
        if any(_same_file(candidate, m) for m in mine):
            continue
        if _is_our_shim(candidate):
            continue
        return str(candidate)

    fallback = shutil.which(name)
    if fallback and not _is_our_shim(Path(fallback)):
        return fallback

    raise BinaryNotFound(
        f"no '{name}' executable found on PATH behind the agent-tether shim.\n"
        f"Set {env_key} to point at it explicitly, or run `tether doctor`."
    )
