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
import tempfile
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


#: Unix domain socket paths are bounded by sockaddr_un.sun_path: 108 bytes on
#: Linux, 104 on macOS. zellij appends "/contract_version_1/<session>" to
#: whatever we hand it, so our directory must leave room for that.
SUN_PATH_MAX = 104
_SOCKET_SUFFIX_BUDGET = 60  # "/contract_version_1/" + a bounded session name


def _uid_tag() -> str:
    try:
        return str(os.getuid())  # type: ignore[attr-defined]
    except AttributeError:
        return os.environ.get("USERNAME", "user")


def _short_tmp() -> Path:
    """A genuinely SHORT temp base, for paths bounded by sun_path.

    NOT tempfile.gettempdir(). On macOS that resolves to a per-user folder like
    /var/folders/df/djsxfhc17x95674wsm_g8s980000gn/T/ - 49 characters before we
    add anything, which defeats the whole point of falling back. Literal /tmp
    exists on both Linux and macOS (where it symlinks to /private/tmp) and
    leaves ample room.
    """
    literal = Path("/tmp")
    if literal.is_dir():
        return literal
    return Path(tempfile.gettempdir())


def runtime_dir() -> Path:
    """Where zellij's session sockets live for tethered sessions.

    Isolating this is what keeps tethered sessions out of the user's own
    `zellij ls`. It isolates LIVE sessions only - zellij's resurrection cache
    is not redirectable on Windows, which is why liveness is read from socket
    markers and never from `zellij ls`.

    Sockets deliberately do NOT live under the XDG state tree by default.
    A path like ~/.local/state/agent-tether/run/sock/contract_version_1/<name>
    is well over the 104-byte sun_path limit once a real home directory and
    session name are substituted, and the failure is a cryptic
    "IPC socket path is too long" from zellij. zellij itself uses
    $XDG_RUNTIME_DIR or /tmp/zellij-<uid> for the same reason.
    """
    raw = os.environ.get("TETHER_SOCKET_DIR")
    if raw:
        return Path(raw).expanduser()

    xdg = os.environ.get("XDG_RUNTIME_DIR")
    if xdg and Path(xdg).is_absolute():
        return Path(xdg) / APP

    root = _root_override()
    if root:
        # Honour the isolation TETHER_HOME asks for, but not at the cost of an
        # unusable socket. If the isolated path is too long, fall back to a
        # short one still keyed to this root, so separate roots stay separate.
        candidate = root / "run"
        if len(str(candidate)) + _SOCKET_SUFFIX_BUDGET <= SUN_PATH_MAX or os.name == "nt":
            return candidate
        import hashlib

        tag = hashlib.sha256(str(root).encode()).hexdigest()[:8]
        return _short_tmp() / f"{APP}-{_uid_tag()}-{tag}"

    if os.name == "nt":
        # Windows uses named pipes, not filesystem sockets; the limit does not
        # apply, so keep it tidy under the state tree.
        return state_dir() / "run"
    return _short_tmp() / f"{APP}-{_uid_tag()}"


def socket_dir() -> Path:
    return runtime_dir() / "sock"


def socket_path_headroom() -> int:
    """Bytes left for zellij's own suffix. Negative means sockets will fail."""
    if os.name == "nt":
        return SUN_PATH_MAX
    return SUN_PATH_MAX - len(str(socket_dir())) - len("/contract_version_1/")


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
        "socket_path_headroom": str(socket_path_headroom()),
    }
