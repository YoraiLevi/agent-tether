"""The pluggable agent registry.

An "agent" is described entirely by data. Adding support for a new CLI means
dropping a TOML file into ~/.config/agent-tether/agents.d/ - no source change,
no pull request, no release.

Layering, lowest precedence first:

    1. builtin      src/tether/data/agents.d/*.toml   (shipped in the wheel)
    2. user drop-in ~/.config/agent-tether/agents.d/*.toml
    3. inline       [agents.<name>] tables in config.toml
    4. environment  TETHER_TARGET_<AGENT> overrides just the binary

Merging is per-key, so a user can override ONE field of a builtin agent and
inherit the rest:

    # ~/.config/agent-tether/agents.d/claude.toml
    [agent]
    name = "claude"
    binary = "/opt/custom/claude"      # everything else still comes from builtin

This is the systemd drop-in / conf.d pattern, chosen because users already know
it and because it survives upgrades: we can ship a better builtin without
clobbering a user's customisation.

ROBUSTNESS RULE: a malformed user TOML must NEVER break the shim path. A broken
file is skipped, the error is remembered, and `tether doctor` reports it. The
alternative - crashing - would make every shimmed command on the machine fail
at once, which is a far worse failure than one unrecognised agent.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised only on 3.10
    import tomli as tomllib  # type: ignore[no-redef]

from . import paths

#: Bumped when the TOML shape changes incompatibly. A file may declare
#: `schema = N`; unknown future versions are skipped with a clear message
#: rather than being misinterpreted.
SCHEMA_VERSION = 1

_LIST_FIELDS = (
    "headless_flags",
    "headless_subcommands",
    "interactive_flags",
    "info_flags",
    "subcommands",
    "session_start_subcommands",
    "background_flags",
    "resume_aliases",
    "set_id_aliases",
    "value_flags",
)

_STR_FIELDS = (
    "binary",
    "display_name",
    "resume_flag",
    "resume_subcommand",
    "continue_flag",
    "set_id_flag",
    "transcript_dir",
    "install_hint",
    "notes",
)

_BOOL_FIELDS = ("set_id_launch_only", "id_is_path", "verified", "enabled")


@dataclass
class Agent:
    """Everything the router needs to know about one vendor CLI."""

    name: str
    binary: str = ""
    display_name: str = ""

    # --- lane classification -------------------------------------------------
    #: Flags meaning "print and exit". NOT assumed to be -p: that is --profile
    #: on codex, takes a value on grok, and is --password inside `mimo run`.
    headless_flags: list[str] = field(default_factory=list)
    #: Subcommands that are headless: `codex exec`, `opencode run`, `mimo run`.
    headless_subcommands: list[str] = field(default_factory=list)
    #: Flags that LOOK headless but open a session. These win over
    #: headless_flags - e.g. agy's `-i/--prompt-interactive`.
    interactive_flags: list[str] = field(default_factory=list)
    info_flags: list[str] = field(default_factory=lambda: ["-h", "--help", "--version"])
    #: Management subcommands: `claude mcp`, `codex login`.
    subcommands: list[str] = field(default_factory=list)
    #: Subcommands that START a session and so must be tethered, even though
    #: they are subcommands: `codex resume <id>`.
    session_start_subcommands: list[str] = field(default_factory=list)
    #: Vendor's own background flag. Intercepted so the result is a background
    #: agent you can ATTACH to.
    background_flags: list[str] = field(default_factory=list)

    # --- session identity ----------------------------------------------------
    resume_flag: str = ""
    resume_aliases: list[str] = field(default_factory=list)
    resume_subcommand: str = ""
    continue_flag: str = ""
    #: The valuable one: lets us choose the id up front so a single identifier
    #: names both the zellij session and the vendor's conversation.
    set_id_flag: str = ""
    set_id_aliases: list[str] = field(default_factory=list)
    #: True when the set-id flag only works for NEW sessions and must never be
    #: replayed on restore (grok says so in its own --help).
    set_id_launch_only: bool = False
    id_is_path: bool = False

    #: Flags that consume the following token. Needed so we do not mistake a
    #: flag's VALUE for a subcommand.
    value_flags: list[str] = field(default_factory=list)

    transcript_dir: str = ""
    install_hint: str = ""
    notes: str = ""
    verified: bool = False
    enabled: bool = True
    source: str = "builtin"
    #: Keys this layer actually set, so merging can distinguish "unset" from
    #: "deliberately set to the default value".
    explicit: set[str] = field(default_factory=set)

    # -- derived helpers ------------------------------------------------------

    @property
    def known(self) -> bool:
        """False when no config layer described this agent.

        A fallback Agent has empty flag lists, so classification cannot
        distinguish a headless call from an interactive one. The router uses
        this to refuse the dangerous lane rather than guess.
        """
        return self.source != "fallback" and not self.source.startswith("fallback+")

    @property
    def all_resume_flags(self) -> list[str]:
        return [f for f in [self.resume_flag, *self.resume_aliases] if f]

    @property
    def all_set_id_flags(self) -> list[str]:
        return [f for f in [self.set_id_flag, *self.set_id_aliases] if f]

    @property
    def can_choose_id(self) -> bool:
        """Can we name the conversation up front?

        When False, a crash restore can bring the terminal back but NOT the
        conversation, and the tool must say so instead of implying success.

        `set_id_launch_only` does NOT disqualify a vendor here: choosing the id
        at launch is exactly how we learn it, which is what makes a later
        resume possible. It only forbids REPLAYING that flag to resume - see
        resume_argv, which never emits a set-id flag.
        """
        return bool(self.set_id_flag)

    @property
    def can_resume(self) -> bool:
        return bool(self.resume_flag or self.resume_subcommand)

    def resume_argv(self, session_id: str) -> list[str] | None:
        """argv that makes this vendor rehydrate its own transcript.

        Deliberately rebuilt rather than replaying the original launch line:
        replay is the silent-empty-restore trap.
        """
        if not session_id:
            return None
        if self.resume_subcommand:
            return [self.resume_subcommand, session_id]
        if self.resume_flag:
            return [self.resume_flag, session_id]
        # Deliberately NOT falling back to set_id_flag. For grok that flag is
        # launch-only ("Does not resume existing sessions" - its own --help),
        # and for vendors that create-if-missing it would silently produce a
        # brand-new empty conversation while reporting success.
        return None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"name": self.name, "source": self.source}
        for f in (*_STR_FIELDS, *_BOOL_FIELDS, *_LIST_FIELDS):
            d[f] = getattr(self, f)
        d["can_choose_id"] = self.can_choose_id
        d["can_resume"] = self.can_resume
        return d


@dataclass
class Registry:
    agents: dict[str, Agent] = field(default_factory=dict)
    #: (path, message) for every file we refused to load. Never raised - the
    #: shim path must keep working - but surfaced by `tether doctor`.
    errors: list[tuple[str, str]] = field(default_factory=list)

    def get(self, name: str) -> Agent:
        """Look up an agent, falling back to safe defaults.

        An unknown agent is still tetherable; we simply cannot classify its
        flags or resume it. Returning a default beats refusing to run.
        """
        found = self.agents.get(name)
        if found and found.enabled:
            return found
        fallback = Agent(name=name, binary=name, source="fallback")
        # Apply the env override here too. Without this, TETHER_TARGET_<AGENT>
        # worked for unknown agents inside resolve.next_binary but the registry
        # disagreed about where the binary was - two sources of truth.
        env_key = f"TETHER_TARGET_{name.upper().replace('-', '_')}"
        override = os.environ.get(env_key)
        if override:
            fallback.binary = override
            fallback.source = "fallback+env"
        return fallback

    def names(self) -> list[str]:
        return sorted(n for n, a in self.agents.items() if a.enabled)


def _coerce(raw: dict[str, Any], name: str, source: str) -> tuple[Agent | None, str | None]:
    agent = Agent(name=name, source=source)
    for f in _STR_FIELDS:
        if f in raw:
            if not isinstance(raw[f], str):
                return None, f"'{f}' must be a string"
            setattr(agent, f, raw[f])
    for f in _BOOL_FIELDS:
        if f in raw:
            if not isinstance(raw[f], bool):
                return None, f"'{f}' must be true or false"
            setattr(agent, f, raw[f])
    for f in _LIST_FIELDS:
        if f in raw:
            v = raw[f]
            if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
                return None, f"'{f}' must be a list of strings"
            setattr(agent, f, list(v))

    # Record which keys the file ACTUALLY set. Merging must not infer this by
    # comparing against defaults: that makes it impossible to override a field
    # back TO its default - you could never clear a builtin's headless_flags to
    # [], nor set verified = false over a builtin true. Those edits would be
    # silently ignored, which is the worst way for a config system to behave.
    agent.explicit = {f for f in (*_STR_FIELDS, *_BOOL_FIELDS, *_LIST_FIELDS) if f in raw}

    if not agent.binary and "binary" not in agent.explicit:
        agent.binary = name
    return agent, None


def _merge(base: Agent, over: Agent) -> Agent:
    """Per-key merge so a drop-in can override one field and inherit the rest.

    A key is taken from `over` iff `over` explicitly set it - see _coerce.
    """
    merged = Agent(name=base.name, source=f"{base.source}+{over.source}")
    for f in (*_STR_FIELDS, *_BOOL_FIELDS, *_LIST_FIELDS):
        take_over = f in over.explicit
        setattr(merged, f, getattr(over, f) if take_over else getattr(base, f))
    merged.explicit = base.explicit | over.explicit
    return merged


def _load_dir(directory: Path, source: str) -> tuple[dict[str, Agent], list[tuple[str, str]]]:
    out: dict[str, Agent] = {}
    errors: list[tuple[str, str]] = []
    if not directory.is_dir():
        return out, errors
    for path in sorted(directory.glob("*.toml")):
        try:
            with path.open("rb") as fh:
                doc = tomllib.load(fh)
        except Exception as exc:  # malformed TOML must not break the shim path
            errors.append((str(path), f"could not parse: {exc}"))
            continue

        declared = doc.get("schema", SCHEMA_VERSION)
        if not isinstance(declared, int) or declared > SCHEMA_VERSION:
            errors.append(
                (str(path), f"schema {declared} is newer than supported {SCHEMA_VERSION}; skipped")
            )
            continue

        table = doc.get("agent")
        if table is None:
            errors.append((str(path), "missing an [agent] table"))
            continue
        entries = table if isinstance(table, list) else [table]
        for raw in entries:
            if not isinstance(raw, dict):
                errors.append((str(path), "[agent] must be a table"))
                continue
            name = raw.get("name") or path.stem
            if not isinstance(name, str) or not name:
                errors.append((str(path), "agent name must be a non-empty string"))
                continue
            agent, err = _coerce(raw, name, source)
            if err or agent is None:
                errors.append((str(path), f"{name}: {err}"))
                continue
            out[name] = _merge(out[name], agent) if name in out else agent
    return out, errors


def load(config_overrides: dict[str, Any] | None = None) -> Registry:
    """Build the effective registry from every layer."""
    reg = Registry()

    builtin, errs = _load_dir(paths.builtin_data_dir() / "agents.d", "builtin")
    reg.agents.update(builtin)
    reg.errors.extend(errs)

    user, errs = _load_dir(paths.agents_dropin_dir(), "user")
    reg.errors.extend(errs)
    for name, agent in user.items():
        reg.agents[name] = _merge(reg.agents[name], agent) if name in reg.agents else agent

    inline = (config_overrides or {}).get("agents") or {}
    if isinstance(inline, dict):
        for name, raw in inline.items():
            if not isinstance(raw, dict):
                reg.errors.append((str(paths.config_file()), f"agents.{name} must be a table"))
                continue
            agent, err = _coerce(raw, name, "config.toml")
            if err or agent is None:
                reg.errors.append((str(paths.config_file()), f"agents.{name}: {err}"))
                continue
            reg.agents[name] = _merge(reg.agents[name], agent) if name in reg.agents else agent

    # Environment wins over every file: TETHER_TARGET_CLAUDE=/path/to/claude
    for name in list(reg.agents):
        env_key = f"TETHER_TARGET_{name.upper().replace('-', '_')}"
        override = os.environ.get(env_key)
        if override:
            reg.agents[name].binary = override
            reg.agents[name].source += "+env"

    return reg


def load_config() -> tuple[dict[str, Any], list[tuple[str, str]]]:
    """Read config.toml. A broken file yields defaults plus a reported error."""
    path = paths.config_file()
    if not path.is_file():
        return {}, []
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh), []
    except Exception as exc:
        return {}, [(str(path), f"could not parse: {exc}")]
