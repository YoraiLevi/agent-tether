"""The management CLI.

Every command that an agent might call supports --json. The JSON shape is
versioned and is documented in docs/API.md; treat it as a contract.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

import click

from . import naming, paths, registry, shims, store, zellij
from .resolve import BinaryNotFound, next_binary

API_VERSION = 1


def _emit(payload: dict, as_json: bool) -> None:
    if as_json:
        click.echo(json.dumps({"schema": API_VERSION, **payload}, indent=2, sort_keys=True))


def _states() -> tuple[set[str], set[str]]:
    try:
        return set(zellij.live_sessions()), set(zellij.recoverable_sessions())
    except zellij.ZellijMissing:
        return set(), set()


def _state_of(session: str, live: set[str], recoverable: set[str]) -> str:
    if session in live:
        return "live"
    if session in recoverable:
        return "recoverable"
    return "gone"


def _record_json(rec: store.SessionRecord, state: str) -> dict:
    return {
        "session": rec.session,
        "agent": rec.agent,
        "state": state,
        "lane": rec.lane,
        "cwd": rec.cwd,
        "project": Path(rec.cwd).name,
        "provider_session_id": rec.provider_session_id,
        "created_at": rec.created_at,
        "can_restore_conversation": bool(rec.provider_session_id),
    }


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(package_name="agent-tether", prog_name="tether")
def main() -> None:
    """Durable, individually-addressable terminals for agent CLIs.

    Full docs: https://github.com/YoraiLevi/agent-tether
    """


# --------------------------------------------------------------------------
# discovery
# --------------------------------------------------------------------------


@main.command("ls")
@click.option("--agent", help="only this agent")
@click.option("--cwd", "cwd_", help="only sessions whose project is exactly this directory")
@click.option("--under", help="only sessions anywhere beneath this directory")
@click.option("--name", "name_", help="substring match on the session name")
@click.option("--id", "id_", help="match a tether session name or a vendor session id")
@click.option(
    "--state",
    type=click.Choice(["live", "recoverable", "gone", "any"]),
    default="any",
    help="filter by state",
)
@click.option("--json", "as_json", is_flag=True, help="machine-readable output")
def ls_cmd(agent, cwd_, under, name_, id_, state, as_json):
    """List tethered sessions."""
    live, recoverable = _states()
    records = store.find(agent=agent, cwd=cwd_, under=under, name_contains=name_, session_id=id_)
    rows = []
    for rec in records:
        st = _state_of(rec.session, live, recoverable)
        if state != "any" and st != state:
            continue
        rows.append(_record_json(rec, st))

    known = {r.session for r in records}
    if not any([agent, cwd_, under, name_, id_]):
        for s in sorted(live - known):
            if naming.is_tether_session(s):
                rows.append(
                    {
                        "session": s,
                        "agent": "?",
                        "state": "live",
                        "lane": "?",
                        "cwd": "",
                        "project": "",
                        "provider_session_id": "",
                        "created_at": 0,
                        "can_restore_conversation": False,
                    }
                )

    if as_json:
        _emit({"sessions": rows, "count": len(rows)}, True)
        return
    if not rows:
        click.echo("no tethered sessions")
        return
    width = max(len(r["session"]) for r in rows)
    for r in sorted(rows, key=lambda x: (x["state"], x["agent"], x["session"])):
        mark = {"live": "*", "recoverable": ".", "gone": " "}[r["state"]]
        click.echo(
            f"{mark} {r['session']:<{width}}  {r['agent']:<10} {r['state']:<12} {r['project']}"
        )


@main.command("get")
@click.argument("session")
@click.option("--json", "as_json", is_flag=True)
def get_cmd(session, as_json):
    """Show one session. Exit 4 if it does not exist."""
    rec = store.load(session)
    if rec is None:
        matches = store.find(session_id=session)
        rec = matches[0] if matches else None
    if rec is None:
        if as_json:
            _emit({"error": "not_found", "session": session}, True)
        else:
            click.echo(f"no such session: {session}", err=True)
        sys.exit(4)
    live, recoverable = _states()
    payload = _record_json(rec, _state_of(rec.session, live, recoverable))
    payload["argv"] = rec.argv
    payload["binary"] = rec.binary
    if as_json:
        _emit({"session": payload}, True)
    else:
        for k, v in payload.items():
            click.echo(f"{k:<26} {v}")


# --------------------------------------------------------------------------
# control
# --------------------------------------------------------------------------


@main.command("attach")
@click.argument("session")
def attach_cmd(session):
    """Attach to a session, resurrecting it first if needed."""
    live, recoverable = _states()
    if session in live:
        sys.exit(zellij.attach(session))
    rec = store.load(session)
    if session in recoverable:
        sys.exit(
            zellij.attach(session, force_run_commands=True, env=dict(rec.env) if rec else None)
        )
    click.echo(f"no live or recoverable session named {session!r}", err=True)
    sys.exit(4)


@main.command("new")
@click.argument("agent")
@click.argument("extra", nargs=-1, type=click.UNPROCESSED)
@click.option("--cwd", "cwd_", default=None, help="working directory for the session")
@click.option("--json", "as_json", is_flag=True)
def new_cmd(agent, extra, cwd_, as_json):
    """Create a DETACHED session and print its id.

    The spawn primitive for orchestrators. Prints exactly one line on stdout.
    """
    from .run import run_shim

    cwd_ = cwd_ or os.getcwd()
    prev = os.getcwd()
    try:
        os.chdir(cwd_)
        os.environ["TETHER_FORCE_LANE"] = "agent"
        rc = run_shim(agent, list(extra))
    finally:
        os.environ.pop("TETHER_FORCE_LANE", None)
        os.chdir(prev)
    sys.exit(rc)


@main.command("read")
@click.argument("session")
@click.option("--json", "as_json", is_flag=True)
def read_cmd(session, as_json):
    """Dump what is currently on the session's screen."""
    text = zellij.dump_screen(session)
    if as_json:
        _emit({"session": session, "screen": text}, True)
    else:
        click.echo(text, nl=False)


@main.command("send")
@click.argument("session")
@click.argument("text")
@click.option("--enter/--no-enter", default=True, help="submit with Enter afterwards")
def send_cmd(session, text, enter):
    """Type TEXT into a session.

    NOTE: this writes to the pane's stdin. It bypasses zellij's keybinding
    layer, so it cannot be used to trigger key bindings.
    """
    rc = zellij.write_chars(session, text)
    if enter and rc == 0:
        rc = zellij.send_enter(session)
    sys.exit(rc)


@main.command("kill")
@click.argument("session")
def kill_cmd(session):
    """End a session and forget it."""
    zellij.kill(session)
    store.delete(session)
    click.echo(f"killed {session}")


@main.command("reap")
@click.option("--all", "all_", is_flag=True, help="also delete recoverable sessions")
@click.option("--dry-run", is_flag=True)
def reap_cmd(all_, dry_run):
    """Delete finished sessions so listings stay useful."""
    live, recoverable = _states()
    n = 0
    for rec in store.all_records():
        if rec.session in live:
            continue
        if not all_ and rec.session in recoverable:
            continue
        click.echo(f"{'would reap' if dry_run else 'reaped'} {rec.session}")
        if not dry_run:
            zellij.delete(rec.session)
            store.delete(rec.session)
        n += 1
    click.echo(f"{n} session(s)")


@main.command("restore")
@click.option("--json", "as_json", is_flag=True)
@click.option("--dry-run", is_flag=True)
def restore_cmd(as_json, dry_run):
    """Bring every recoverable session back, detached. Run this after a reboot."""
    cfg, _ = registry.load_config()
    reg = registry.load(cfg)
    live, recoverable = _states()
    results = []

    for rec in store.all_records():
        if rec.session in live:
            results.append({"session": rec.session, "result": "already_live"})
            continue

        agent = reg.get(rec.agent)
        resume_argv = agent.resume_argv(rec.provider_session_id)
        cwd_missing = not Path(rec.cwd).is_dir()

        if cwd_missing:
            results.append({"session": rec.session, "result": "skipped", "reason": "cwd is gone"})
            continue

        if resume_argv:
            # Rebuild rather than replay. Replaying the original launch line is
            # the silent-empty-restore trap: grok's --session-id explicitly
            # does not resume, and gemini exits fatally on a duplicate.
            if not dry_run:
                zellij.delete(rec.session)
                zellij.create(
                    rec.session,
                    rec.binary,
                    resume_argv,
                    rec.cwd,
                    detached=True,
                    env=dict(rec.env),
                )
            results.append({"session": rec.session, "result": "conversation_and_terminal"})
            continue

        if rec.session in recoverable:
            if not dry_run:
                zellij.attach(rec.session, force_run_commands=True, env=dict(rec.env))
            results.append(
                {
                    "session": rec.session,
                    "result": "terminal_only",
                    "reason": f"{rec.agent} never told us its session id",
                }
            )
            continue

        results.append(
            {"session": rec.session, "result": "skipped", "reason": "nothing to resurrect"}
        )

    if as_json:
        _emit({"restored": results}, True)
        return

    for r in results:
        colour = {"conversation_and_terminal": "green", "terminal_only": "yellow"}.get(r["result"])
        click.secho(f"{r['result']:<26} {r['session']}  {r.get('reason', '')}", fg=colour)
    only = sum(1 for r in results if r["result"] == "terminal_only")
    if only:
        click.echo("")
        click.secho(
            f"{only} session(s) restored the TERMINAL but not the CONVERSATION - "
            "those agents start fresh. See docs/AGENTS.md.",
            fg="yellow",
        )


# --------------------------------------------------------------------------
# installation
# --------------------------------------------------------------------------


@main.command("install")
@click.argument("agents", nargs=-1)
@click.option("--all", "all_", is_flag=True, help="every agent found on PATH")
@click.option("--dir", "directory", type=click.Path(path_type=Path), default=None)
def install_cmd(agents, all_, directory):
    """Generate shims for AGENTS (or every agent found on PATH with --all)."""
    cfg, _ = registry.load_config()
    reg = registry.load(cfg)
    directory = directory or paths.bin_dir()

    if not agents:
        if not all_:
            click.echo("name at least one agent, or pass --all", err=True)
            click.echo(f"known: {', '.join(reg.names())}", err=True)
            sys.exit(2)
        agents = tuple(a for a in reg.names() if shutil.which(a))
        if not agents:
            click.echo("no known agent CLIs found on PATH", err=True)
            sys.exit(1)

    paths.ensure_dirs()
    for name in agents:
        result = shims.install(name, directory)
        click.echo(f"  {result.action:<10} {result.path}  {result.detail}")

    click.echo("")
    if shims.on_path(directory):
        click.secho("shim directory is on PATH", fg="green")
        for name in agents:
            ok, detail = shims.shadows_correctly(name)
            click.secho(
                f"  {name:<10} {'shadowed' if ok else 'NOT shadowed'}  {detail}",
                fg="green" if ok else "yellow",
            )
    else:
        click.secho("shim directory is NOT on PATH yet. Add it:", fg="yellow")
        click.echo(f"  {shims.path_hint(directory)}")
    click.echo("\nThen run:  tether doctor")


@main.command("uninstall")
@click.argument("agents", nargs=-1)
@click.option("--all", "all_", is_flag=True)
@click.option("--dir", "directory", type=click.Path(path_type=Path), default=None)
def uninstall_cmd(agents, all_, directory):
    """Remove shims. Never deletes a file it did not create."""
    directory = directory or paths.bin_dir()
    names = list(agents) or (shims.installed(directory) if all_ else [])
    if not names:
        click.echo("name at least one agent, or pass --all", err=True)
        sys.exit(2)
    for name in names:
        result = shims.uninstall(name, directory)
        click.echo(f"  {result.action:<10} {result.path}  {result.detail}")
    click.echo("\nRunning sessions are untouched. `tether ls` still sees them.")


# --------------------------------------------------------------------------
# introspection
# --------------------------------------------------------------------------


@main.command("agents")
@click.option("--json", "as_json", is_flag=True)
def agents_cmd(as_json):
    """List known agent CLIs and what we can do for each."""
    cfg, cfg_errors = registry.load_config()
    reg = registry.load(cfg)
    rows = []
    for name in reg.names():
        a = reg.agents[name]
        rows.append(
            {
                **a.to_dict(),
                "on_path": bool(shutil.which(name)),
                "shimmed": shims.shadows_correctly(name)[0],
            }
        )
    if as_json:
        _emit({"agents": rows, "errors": [list(e) for e in reg.errors + cfg_errors]}, True)
        return
    click.echo(f"{'agent':<12}{'source':<18}{'verified':<10}{'restore':<22}{'on PATH':<9}shimmed")
    for r in rows:
        restore = (
            "conversation" if r["can_choose_id"] else ("terminal only" if r["can_resume"] else "no")
        )
        click.echo(
            f"{r['name']:<12}{r['source']:<18}{str(r['verified']):<10}{restore:<22}"
            f"{str(r['on_path']):<9}{r['shimmed']}"
        )
    for path, msg in reg.errors + cfg_errors:
        click.secho(f"config problem: {path}: {msg}", fg="red")


@main.command("explain")
@click.argument("agent")
@click.argument("args", nargs=-1, type=click.UNPROCESSED)
def explain_cmd(agent, args):
    """Show which lane an invocation would take, and why.

    The routing rules are per-vendor and non-obvious (`-p` is --profile on
    codex), so this exists to make them inspectable instead of mysterious.
    """
    from . import router

    cfg, _ = registry.load_config()
    reg = registry.load(cfg)
    spec = reg.get(agent)
    decision = router.classify(spec, list(args), tty=sys.stdout.isatty())
    click.echo(f"agent   {agent}  (source: {spec.source})")
    click.echo(f"argv    {list(args)}")
    click.echo(f"lane    {decision.lane.value}")
    click.echo(f"reason  {decision.reason}")


@main.command("doctor")
@click.option("--json", "as_json", is_flag=True)
def doctor_cmd(as_json):
    """Check the installation and report anything that would break it."""
    cfg, cfg_errors = registry.load_config()
    reg = registry.load(cfg)
    problems: list[str] = []

    zellij_path = shutil.which("zellij")
    zver = zellij.version() if zellij_path else None
    if not zellij_path:
        problems.append("zellij not found on PATH - install it: cargo install --locked zellij")
    elif zver and zver < zellij.MIN_VERSION:
        problems.append(
            f"zellij {'.'.join(map(str, zver))} is older than "
            f"{'.'.join(map(str, zellij.MIN_VERSION))} (needed for Windows support)"
        )

    bindir = paths.bin_dir()
    if shims.installed(bindir) and not shims.on_path(bindir):
        problems.append(f"shim dir is not on PATH: {shims.path_hint(bindir)}")

    headroom = paths.socket_path_headroom()
    if headroom < 0:
        problems.append(
            f"socket dir path is too long for a unix socket by {-headroom} bytes: "
            f"{paths.socket_dir()} - set TETHER_SOCKET_DIR to something shorter"
        )

    for path, msg in reg.errors + cfg_errors:
        problems.append(f"config: {path}: {msg}")
    for bad in store.corrupt_records():
        problems.append(f"unreadable session record: {bad}")

    shim_rows = []
    for name in shims.installed(bindir):
        ok, detail = shims.shadows_correctly(name)
        try:
            target = next_binary(name, configured=reg.get(name).binary)
        except BinaryNotFound as exc:
            target = f"UNRESOLVED: {exc}"
            problems.append(f"{name}: {exc}")
        shim_rows.append({"agent": name, "shadowing": ok, "detail": detail, "chains_to": target})

    payload = {
        "paths": paths.describe(),
        "zellij": {"path": zellij_path, "version": ".".join(map(str, zver)) if zver else None},
        "shims": shim_rows,
        "agents_known": reg.names(),
        "problems": problems,
    }

    if as_json:
        _emit(payload, True)
        sys.exit(1 if problems else 0)

    click.echo("paths")
    for k, v in payload["paths"].items():
        click.echo(f"  {k:<20} {v}")
    click.echo(f"\nzellij  {zellij_path or 'NOT FOUND'}  {payload['zellij']['version'] or ''}")
    click.echo("\nshims")
    if not shim_rows:
        click.echo("  none installed - try:  tether install --all")
    for r in shim_rows:
        click.secho(
            f"  {r['agent']:<12} {'shadowing' if r['shadowing'] else 'NOT shadowing':<15} -> {r['chains_to']}",
            fg="green" if r["shadowing"] else "yellow",
        )
    if problems:
        click.echo("")
        for p in problems:
            click.secho(f"problem: {p}", fg="red")
        sys.exit(1)
    click.echo("\nno problems found")


@main.command("paths")
@click.option("--json", "as_json", is_flag=True)
def paths_cmd(as_json):
    """Show every location this tool uses."""
    if as_json:
        _emit({"paths": paths.describe()}, True)
        return
    for k, v in paths.describe().items():
        click.echo(f"{k:<20} {v}")


if __name__ == "__main__":  # pragma: no cover
    main()
