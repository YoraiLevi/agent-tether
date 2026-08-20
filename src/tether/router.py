"""Lane classification: what does this invocation actually want?

The single most important property of this module is its FAIL-SAFE DIRECTION.

When the router is unsure, it must choose PASS-THROUGH. Reasoning:

  - Wrongly passing through  -> the user gets the real CLI, unwrapped. They
    lose durability for that one call. Annoying, obvious, recoverable.
  - Wrongly tethering        -> a headless caller expecting text on stdout gets
    a terminal UI, or blocks forever. Silent, catastrophic, and it corrupts
    whatever pipeline was reading that output.

Asymmetric costs, so every ambiguity resolves toward pass-through.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from enum import Enum

from .registry import Agent


class Lane(str, Enum):
    PASSTHROUGH = "passthrough"
    HUMAN = "human"
    AGENT = "agent"


@dataclass
class Decision:
    lane: Lane
    reason: str  # human-readable, surfaced by `tether explain`


def _has_tty() -> bool:
    """A human at a terminal has one; a spawned process does not.

    Both stdout and stdin are required. A caller doing `claude < prompt.txt`
    wants text back even though stdout may still be a terminal, and a caller
    doing `claude | tee log` is scripting. Requiring both keeps us on the
    pass-through side of the ambiguity, which is the safe side.
    """
    try:
        return sys.stdout.isatty() and sys.stdin.isatty()
    except Exception:
        return False


def _first_positional(argv: list[str], agent: Agent) -> str | None:
    """First non-flag token, skipping values consumed by flags.

    Without the value-skip, `claude --model exec` would treat `exec` as a
    subcommand. Vendors differ enough that value_flags is per-agent data.
    """
    skip = False
    for token in argv:
        if skip:
            skip = False
            continue
        if token == "--":
            continue
        if token.startswith("-"):
            if "=" not in token and token in agent.value_flags:
                skip = True
            continue
        return token
    return None


def classify(
    agent: Agent,
    argv: list[str],
    env: dict[str, str] | None = None,
    tty: bool | None = None,
) -> Decision:
    """Decide the lane. Pure: no I/O, so it is fully unit-testable."""
    env = os.environ if env is None else env

    forced = env.get("TETHER_FORCE_LANE")
    if forced in {"agent", "human", "passthrough"}:
        # `tether new` uses this so an orchestrator can request a detached
        # session even when it happens to have a terminal attached.
        return Decision(Lane(forced), "TETHER_FORCE_LANE")

    if env.get("TETHER_DISABLE") == "1":
        return Decision(Lane.PASSTHROUGH, "TETHER_DISABLE=1")
    if env.get("TETHER_ACTIVE") == "1":
        return Decision(Lane.PASSTHROUGH, "recursion guard: already inside the shim")
    if env.get("ZELLIJ_SESSION_NAME") or env.get("ZELLIJ"):
        return Decision(Lane.PASSTHROUGH, "already inside a zellij session; refusing to nest")

    # interactive_flags beat headless_flags. agy's -i/--prompt-interactive
    # reads like a headless prompt flag but opens a real session.
    has_interactive = any(t in agent.interactive_flags for t in argv)

    for token in argv:
        bare = token.split("=", 1)[0]
        if bare in agent.info_flags:
            return Decision(Lane.PASSTHROUGH, f"info flag {bare}")
        if not has_interactive and bare in agent.headless_flags:
            return Decision(Lane.PASSTHROUGH, f"headless flag {bare}")

    first = _first_positional(argv, agent)
    if first:
        if first in agent.headless_subcommands:
            return Decision(Lane.PASSTHROUGH, f"headless subcommand '{first}'")
        if first in agent.subcommands and first not in agent.session_start_subcommands:
            return Decision(Lane.PASSTHROUGH, f"management subcommand '{first}'")

    # The vendor's own background flag. Intercepting it yields a background
    # agent that can be ATTACHED to, which is strictly more capability.
    if any(t in agent.background_flags for t in argv):
        return Decision(Lane.AGENT, "vendor background flag")

    if tty if tty is not None else _has_tty():
        return Decision(Lane.HUMAN, "interactive terminal")

    return Decision(Lane.AGENT, "no controlling terminal")


def provided_session_id(agent: Agent, argv: list[str]) -> str | None:
    """Pull an explicit session id out of argv.

    Used so a vendor-issued `--resume <id>` lands back on the SAME tether
    session instead of forking a second one for the same conversation.
    """
    wanted = {*agent.all_resume_flags, *agent.all_set_id_flags}
    for i, token in enumerate(argv):
        if "=" in token:
            head, _, tail = token.partition("=")
            if head in wanted and tail:
                return tail
            continue
        if token in wanted and i + 1 < len(argv):
            nxt = argv[i + 1]
            if not nxt.startswith("-"):
                return nxt
    if agent.resume_subcommand:
        for i, token in enumerate(argv):
            if token == agent.resume_subcommand and i + 1 < len(argv):
                nxt = argv[i + 1]
                if not nxt.startswith("-"):
                    return nxt
    return None
