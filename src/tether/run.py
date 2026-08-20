"""What actually happens for one shimmed invocation."""

from __future__ import annotations

import os
import sys

from . import naming, paths, registry, router, store, zellij
from .resolve import next_binary
from .shim import exec_passthrough


def _banner(session: str) -> None:
    """Tell a human they are tethered, and how to leave.

    Without this the human lane is indistinguishable from running the CLI
    normally, so nobody discovers that Ctrl q detaches or that closing the
    window is safe. One dim line, suppressible, on stderr so it can never
    pollute stdout.
    """
    if os.environ.get("TETHER_QUIET") == "1":
        return
    sys.stderr.write(f"\033[2m[tether] {session} - Ctrl+q detaches, session keeps running\033[0m\n")


def run_shim(agent_name: str, argv: list[str]) -> int:
    cfg, cfg_errors = registry.load_config()
    reg = registry.load(cfg)
    agent = reg.get(agent_name)

    binary = next_binary(agent_name, configured=agent.binary)
    decision = router.classify(agent, argv)

    if decision.lane is router.Lane.PASSTHROUGH:
        return exec_passthrough(binary, argv)

    try:
        zellij.executable()
    except zellij.ZellijMissing as exc:
        # Fail safe: no zellij means no tethering, but the agent must still run.
        sys.stderr.write(f"tether: {exc}\ntether: running {agent_name} untethered\n")
        return exec_passthrough(binary, argv)

    paths.ensure_dirs()
    cwd = naming.canonical_cwd(os.getcwd())
    provided = router.provided_session_id(agent, argv)
    human = decision.lane is router.Lane.HUMAN

    if provided:
        session = naming.for_session_id(agent_name, provided)
    elif human:
        session = naming.for_cwd(agent_name, cwd)
    else:
        session = naming.for_new(agent_name)

    live = zellij.live_sessions()

    # Already running: join it. This is what makes `claude` idempotent per
    # project, and what makes a vendor `--resume <id>` land on the SAME
    # tether session rather than forking a second one.
    if session in live:
        if human:
            _banner(session)
            return zellij.attach(session)
        print(session)
        return 0

    record = store.load(session)
    if record and session in zellij.recoverable_sessions():
        env = dict(record.env)
        if human:
            _banner(session)
            return zellij.attach(session, force_run_commands=True, env=env)
        zellij.attach(session, force_run_commands=True, env=env)
        print(session)
        return 0

    # Fresh session. Where the vendor allows it, choose the conversation id up
    # front so ONE identifier names both layers - without this, a restored
    # terminal comes back running a brand-new empty agent that looks like
    # success. Only some vendors support it; see docs/AGENTS.md.
    launch_argv = list(argv)
    provider_id = provided or ""
    if agent.can_choose_id and not provided:
        provider_id = naming.new_session_id()
        launch_argv = [agent.set_id_flag, provider_id, *launch_argv]
        session = naming.for_session_id(agent_name, provider_id)

    env = store.carried_env()
    store.save(
        store.SessionRecord(
            session=session,
            agent=agent_name,
            binary=binary,
            cwd=cwd,
            lane=decision.lane.value,
            argv=launch_argv,
            provider_session_id=provider_id,
            env=env,
        )
    )

    if human:
        _banner(session)
        return zellij.create(session, binary, launch_argv, cwd, detached=False, env=env)

    rc = zellij.create(session, binary, launch_argv, cwd, detached=True, env=env)
    if rc != 0:
        sys.stderr.write(f"tether: failed to create session {session}\n")
        return rc
    # Lane contract: exactly one line on stdout, the session id, so a spawning
    # agent can parse it with no heuristics. Everything else goes to stderr.
    print(session)
    return 0
