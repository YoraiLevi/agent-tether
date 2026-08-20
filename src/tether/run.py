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


def _merge_resume_argv(spec, original_argv: list[str], resume_argv: list[str]) -> list[str]:
    """Keep the original launch flags, swap the session selector.

    A session started as `claude --model opus --add-dir ../shared` must come
    back with those flags. Restoring a bare `claude --resume <id>` silently
    drops the model and the extra directories while reporting full success.

    Any pre-existing resume/continue/set-id selector is stripped first, so we
    never end up with two of them - the same splice an orchestrator has to do.
    """
    selectors = {
        *spec.all_resume_flags,
        *spec.all_set_id_flags,
        *([spec.continue_flag] if spec.continue_flag else []),
    }
    kept: list[str] = []
    skip = False
    for token in original_argv:
        if skip:
            skip = False
            continue
        bare = token.split("=", 1)[0]
        if bare in selectors:
            if "=" not in token:
                skip = True  # also drop its value
            continue
        if spec.resume_subcommand and token == spec.resume_subcommand:
            skip = True
            continue
        kept.append(token)
    return [*resume_argv, *kept]


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
        # A vendor-issued id must reach the session that already holds that
        # conversation, whatever that session happens to be called. Look it up
        # rather than deriving a name from the id, so this still works for a
        # human session named after its directory.
        existing = store.find(session_id=provided)
        session = existing[0].session if existing else naming.for_session_id(agent_name, provided)
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
        spec = reg.get(record.agent)
        resume_argv = spec.resume_argv(record.provider_session_id)

        if resume_argv:
            # REBUILD, never replay. `zellij attach --force-run-commands`
            # re-runs the command zellij serialized - the ORIGINAL launch line,
            # including `--session-id <uuid>`. For grok that flag is launch-only
            # and yields a brand-new empty conversation; gemini exits fatally on
            # a duplicate. This is the same rebuild `tether restore` performs,
            # and it has to live here too because `cd project && claude` after a
            # reboot is the far more common path.
            merged = _merge_resume_argv(spec, record.argv, resume_argv)
            zellij.delete(session)
            if human:
                _banner(session)
                return zellij.create(
                    session, record.binary, merged, record.cwd, detached=False, env=env
                )
            rc = zellij.create(session, record.binary, merged, record.cwd, detached=True, env=env)
            os.environ["_TETHER_CHILD_LAUNCHED"] = "1"
            if rc != 0:
                sys.stderr.write(f"tether: failed to resurrect {session}\n")
                return rc
            print(session)
            return 0

        if human:
            _banner(session)
            return zellij.attach(session, force_run_commands=True, env=env)

        # The agent lane must NEVER foreground-attach: `zellij attach` without
        # -b blocks until a human detaches and paints a TUI onto the stdout the
        # caller is parsing. Resurrect detached instead.
        rc = zellij.attach(session, force_run_commands=True, env=env, detached=True)
        if rc != 0:
            sys.stderr.write(f"tether: failed to resurrect {session}\n")
            return rc
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
        if not human:
            # ONLY outside the human lane. Re-deriving the name from a freshly
            # minted UUID here used to overwrite the per-directory name, so the
            # next day `cd project && claude` computed the cwd name, missed,
            # minted another UUID and opened a brand-new empty conversation -
            # while yesterday's agent stayed live and orphaned.
            #
            # That silently broke the single property the whole tool is sold
            # on, for every vendor that can be told its id: claude, gemini,
            # pi, grok. The id lives in the record; the NAME stays the cwd.
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
    os.environ["_TETHER_CHILD_LAUNCHED"] = "1"
    if rc != 0:
        sys.stderr.write(f"tether: failed to create session {session}\n")
        return rc
    # Lane contract: exactly one line on stdout, the session id, so a spawning
    # agent can parse it with no heuristics. Everything else goes to stderr.
    print(session)
    return 0
