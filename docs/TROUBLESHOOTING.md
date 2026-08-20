# Troubleshooting

Start here:

```console
$ tether doctor
```

It checks every failure mode below and exits non-zero if it finds one. Most of
this page is only needed when `doctor` is clean but something still feels wrong.

---

## The shim is not being used

**Symptom:** no `[tether]` banner when you start an agent; `tether ls` stays
empty.

```console
$ tether doctor
shims
  claude       NOT shadowing   resolves to /usr/local/bin/claude (not our shim)
```

| Cause | Fix |
|---|---|
| shim directory not on `PATH` | run the line from `tether install`, open a **new** terminal |
| shim directory is on `PATH` but *after* the real binary | move it earlier; `PATH` is first-match-wins |
| shim placed in the same directory as the real binary on Windows | use a separate directory — within one directory `PATHEXT` puts `.exe` before `.cmd`, so the shim can never win |
| shim never generated | `tether install <agent>` |
| `TETHER_DISABLE=1` still set | `unset TETHER_DISABLE` |

Confirm with `which claude` (POSIX) or `where claude` (Windows) — the first hit
should be in the shim directory.

---

## "no 'X' executable found on PATH behind the agent-tether shim"

The shim cannot find the real binary. It deliberately skips files carrying the
`agent-tether-shim` marker so it never calls itself.

| Cause | Fix |
|---|---|
| the real CLI was uninstalled or moved | reinstall it, or point at it: `TETHER_TARGET_CLAUDE=/path/to/claude` |
| the only `claude` on `PATH` is our shim | add the real one back to `PATH` |
| the CLI lives somewhere not on `PATH` | set `TETHER_TARGET_<AGENT>`, or `binary = "/abs/path"` in a drop-in |

---

## An agent starts untethered

**Symptom:** the agent runs, but no session appears.

That is often correct. Headless invocations pass through on purpose. Check:

```console
$ tether explain claude -p "hi"
lane    passthrough
reason  headless flag -p
```

If it says `passthrough` for something you expected to be tethered, the
registry's flag data for that agent is wrong — see
[CONFIG.md](CONFIG.md) to override it in a drop-in.

If it says `human`/`agent` but nothing happened, look for a warning on stderr:

```
tether: zellij was not found on PATH
tether: running claude untethered
```

That is the fail-safe. Install zellij and it will tether from then on.

---

## `Ctrl+q` does not detach

| Cause | Fix |
|---|---|
| your terminal or shell intercepts `Ctrl+q` (classic XON flow control) | run `zellij action detach` inside the session, or just close the window |
| you are not actually in a tethered session | check for the `[tether]` banner |

Closing the window always detaches — the session survives by design.

To rebind, copy the shipped zellij config to `~/.config/agent-tether/zellij.kdl`
and edit the `keybinds` block. It is picked up automatically. Note that keybinds
**must** live in that config file: zellij parses a `keybinds` block inside a
layout, validates it, and then silently ignores it.

---

## Restore said `terminal_only`

Working as intended, and worth understanding.

zellij restores the *terminal*. Only the vendor CLI can restore the
*conversation*, and it can only do that if we knew its session id. Most vendors
mint an id after launch and never tell us.

The restored agent is **brand new and empty**. Re-brief it.

Which agents can restore a conversation: [AGENTS.md](AGENTS.md).

---

## Restore skipped a session

| `reason` | Meaning |
|---|---|
| `cwd is gone` | the project directory no longer exists; recreate it or `tether kill` the record |
| `nothing to resurrect` | zellij has no serialized state; the session is unrecoverable |

---

## Sessions I did not create appear in `tether ls`

Sessions whose state is `recoverable` can come from zellij's resurrection
cache, which is shared machine-wide and cannot be redirected on Windows. Only
`live` state is namespaced to tether.

Anything with `agent: "?"` is a live session in our namespace with no registry
record — usually left over from an interrupted spawn. `tether reap` clears it.

---

## My own zellij sessions disappeared

They did not. Tethered sessions use a separate socket directory, so
`tether ls` and `zellij ls` show different worlds.

If your **own** `zellij ls` looks wrong, check whether `ZELLIJ_SOCKET_DIR` is
still set in that shell — that variable persists for the whole shell session.

```console
$ echo $ZELLIJ_SOCKET_DIR      # should be empty in your normal terminals
```

---

## A config file of mine is ignored

```console
$ tether agents
config problem: /home/you/.config/agent-tether/agents.d/mytool.toml: could not parse: ...
```

Malformed files are **skipped, never fatal** — a crash there would break every
shimmed command on the machine at once. `tether agents` and `tether doctor`
list every file that was refused and why.

Also check `schema = 1` at the top: a file declaring a newer schema is skipped
rather than misread.

---

## It is slow

Each shimmed call starts a Python process, roughly 50–150 ms. Invisible
interactively, noticeable in a loop.

```bash
TETHER_DISABLE=1 ./my-script-that-calls-claude-1000-times
```

Headless calls (`claude -p`, `codex exec`) already skip most of the work, but
the interpreter still starts.

---

## An orchestrator stopped showing agent status

Expected with some tools. Orchestrators that identify the running agent by
walking the pane's process tree cannot see a tethered agent, because it runs
outside that tree — which is precisely what makes it survive.

Durability and reattachment are unaffected. Full analysis and mitigations:
[ORCA.md](ORCA.md).

---

## Getting a useful bug report together

```console
$ tether doctor --json
$ tether agents --json
$ tether explain <agent> <the exact args>
$ TETHER_DEBUG=1 <agent> <args>     # re-raises instead of falling back
```

`TETHER_DEBUG=1` disables the safety net that normally runs your agent
untethered on an internal error, so you get the traceback instead.

Report at https://github.com/YoraiLevi/agent-tether/issues — include OS, the
`doctor --json` output, and the exact command.
