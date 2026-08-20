"""XDG-style paths, on every operating system.

We deliberately use ~/.config, ~/.local/share, ~/.local/state, ~/.local/bin and
~/.cache on Windows and macOS too, rather than %APPDATA% or
~/Library/Application Support.

Why: the tools this wraps already do it. Claude Code installs itself to
~/.local/bin on Windows. Keeping one layout across all three OSes means the
docs, the support answers and the user's muscle memory are identical
everywhere, and a config file can be copied between machines unchanged.

Every location is overridable, and the XDG_* environment variables are honoured
on all platforms so a user who has already decided where things go keeps that
decision.
"""

from __future__ import annotations

import os
from pathlib import Path

APP = "agent-tether"

#: Set this to relocate everything at once (handy for tests and for trying a
#: second, isolated installation side by side).
HOME_ENV = "TETHER_HOME"


def _home() -> Path:
    return Path.home()


def _xdg(var: str, default: str) -> Path:
    """Honour an XDG_* variable if it holds an absolute path, else fall back.

    The XDG spec says relative paths are invalid and must be ignored; obeying
    that avoids creating a stray `.config` inside whatever directory the user
    happened to be in.
    """
    raw = os.environ.get(var)
    if raw:
        p = Path(raw).expanduser()
        if p.is_absolute():
            return p
    return _home() / default


def _root_override() -> Path | None:
    raw = os.environ.get(HOME_ENV)
    if not raw:
        return None
    return Path(raw).expanduser().resolve()


def config_dir() -> Path:
    """User configuration: config.toml and the pluggable agents.d/ registry."""
    root = _root_override()
    if root:
        return root / "config"
    return _xdg("XDG_CONFIG_HOME", ".config") / APP


def data_dir() -> Path:
    """Generated, replaceable artefacts: shims, generated zellij layouts."""
    root = _root_override()
    if root:
        return root / "share"
    return _xdg("XDG_DATA_HOME", ".local/share") / APP


def state_dir() -> Path:
    """State that should persist but is not precious: the session registry."""
    root = _root_override()
    if root:
        return root / "state"
    return _xdg("XDG_STATE_HOME", ".local/state") / APP


def cache_dir() -> Path:
    root = _root_override()
    if root:
        return root / "cache"
    return _xdg("XDG_CACHE_HOME", ".cache") / APP


def bin_dir() -> Path:
    """Where generated shims go.

    NOT ~/.local/bin, and the reason is worth stating because it is
    counter-intuitive: a shim cannot live in the same directory as the binary
    it shadows.

    Claude Code installs claude.exe to ~/.local/bin on Windows. Within a single
    directory, Windows resolves extensions in PATHEXT order, and `.exe` comes
    before `.cmd` - so a `claude.cmd` shim sitting next to `claude.exe` would
    never run. PATH order only breaks ties BETWEEN directories.

    So shims get their own directory, which is then placed ahead of the real
    ones on PATH. It still lives under the XDG data root, so the "everything in
    one predictable tree" property holds.
    """
    raw = os.environ.get("TETHER_BIN_DIR")
    if raw:
        return Path(raw).expanduser()
    return data_dir() / "shims"


def runtime_dir() -> Path:
    """Where zellij's session sockets live for tethered sessions.

    Isolating this is what keeps tethered sessions out of the user's own
    `zellij ls`. Note it isolates LIVE sessions only - zellij's resurrection
    cache is not redirectable on Windows, which is why liveness is read from
    socket markers here and never from `zellij ls`.
    """
    raw = os.environ.get("XDG_RUNTIME_DIR")
    if raw and Path(raw).is_absolute() and not _root_override():
        return Path(raw) / APP
    return state_dir() / "run"


def socket_dir() -> Path:
    return runtime_dir() / "sock"


def sessions_dir() -> Path:
    """One JSON file per session, so concurrent writers never contend."""
    return state_dir() / "sessions"


def layouts_dir() -> Path:
    """Generated per-session zellij layouts."""
    return data_dir() / "layouts"


def agents_dropin_dir() -> Path:
    """User drop-ins. Adding an agent means adding a file here - never code."""
    return config_dir() / "agents.d"


def config_file() -> Path:
    return config_dir() / "config.toml"


def builtin_data_dir() -> Path:
    """Read-only data shipped inside the wheel."""
    return Path(__file__).resolve().parent / "data"


def zellij_config_file() -> Path:
    """The zellij --config file.

    It exists for exactly one reason: keybinds cannot be passed as CLI
    arguments, and a `keybinds` block inside a zellij *layout* is parsed,
    validated, then silently discarded. See docs/DESIGN.md.
    """
    override = os.environ.get("TETHER_ZELLIJ_CONFIG")
    if override:
        return Path(override).expanduser()
    user_copy = config_dir() / "zellij.kdl"
    if user_copy.exists():
        return user_copy
    return builtin_data_dir() / "zellij" / "tether.kdl"


def base_layout_dir() -> Path:
    return builtin_data_dir() / "zellij" / "layouts"


def ensure_dirs() -> None:
    for d in (
        config_dir(),
        agents_dropin_dir(),
        data_dir(),
        state_dir(),
        sessions_dir(),
        layouts_dir(),
        socket_dir(),
    ):
        d.mkdir(parents=True, exist_ok=True)


def describe() -> dict[str, str]:
    """Every resolved location, for `tether doctor --json`."""
    return {
        "config_dir": str(config_dir()),
        "config_file": str(config_file()),
        "agents_dropin_dir": str(agents_dropin_dir()),
        "data_dir": str(data_dir()),
        "state_dir": str(state_dir()),
        "cache_dir": str(cache_dir()),
        "bin_dir": str(bin_dir()),
        "socket_dir": str(socket_dir()),
        "sessions_dir": str(sessions_dir()),
        "layouts_dir": str(layouts_dir()),
        "zellij_config": str(zellij_config_file()),
        "builtin_data": str(builtin_data_dir()),
    }
