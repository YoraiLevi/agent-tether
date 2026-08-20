# Tutorial: from install to surviving a crash

For a human at a keyboard. Every step says what it does, exactly what to type,
and what you should see. If your output differs, that is the point at which to
stop — each step has a check for a reason.

Takes about fifteen minutes. Nothing here is destructive, and
[Undo everything](#undo-everything) reverses all of it.

**Placeholders:** `~/src/demo` is a scratch project — substitute any directory
you like. `claude` stands in for whichever agent CLI you have installed; the
[three-agent section](#4-the-other-agents-codex-and-agy) covers `codex` and
`agy` specifically.

---

## 0. Check what you have

```console
$ uv --version
$ zellij --version
$ claude --version
```

You need `uv`, `zellij` **0.44.0 or newer**, and at least one agent CLI. If any
is missing, stop and follow [INSTALL.md](INSTALL.md) — the rest of this will not
work without them.

Now take a baseline. Run this before anything, and remember what it says:

```console
$ zellij ls
```

Whatever sessions are listed here are *yours*. Tethered sessions live in a
separate namespace and will never appear in this list. We will check that again
at the end.

---

## 1. Install

```console
$ uv tool install agent-tether
$ tether install --all
```

Expected — one line per agent, then a verdict:

```
  created    /home/you/.local/share/agent-tether/shims/claude
  created    /home/you/.local/share/agent-tether/shims/codex

shim directory is NOT on PATH yet. Add it:
  export PATH="/home/you/.local/share/agent-tether/shims:$PATH"
```

**If it says NOT on PATH**, run the line it printed, add it to your shell
profile (`~/.bashrc`, `~/.zshrc`, or on Windows use the `setx` line it gives
you), and open a **new** terminal.

Now the check that matters:

```console
$ tether doctor
```

```
shims
  claude       shadowing       -> /home/you/.local/bin/claude
```

**`shadowing` is what you are looking for.** It means typing `claude` reaches
the shim, and the shim can still find the real binary behind it. If it says
`NOT shadowing`, doctor names the file winning instead — usually a `PATH`
ordering problem.

---

## 2. Your first tethered session

```console
$ mkdir -p ~/src/demo && cd ~/src/demo
$ claude
```

Expected: one dim line, then your agent as normal.

```
[tether] tether-claude-demo-1a2b3c4d - Ctrl+q detaches, session keeps running
```

Type something so the session has history worth keeping — ask it anything.

### Detach

Press **`Ctrl+q`**. You are back at your shell; the agent is still running.

From the same terminal:

```console
$ tether ls
* tether-claude-demo-1a2b3c4d  claude  live  demo
```

The `*` means live.

### Prove it survives the terminal

**Close the whole terminal window.** Open a new one.

```console
$ tether ls
* tether-claude-demo-1a2b3c4d  claude  live  demo
```

Still live. That is the product.

### Get back in

```console
$ cd ~/src/demo
$ claude
```

Same command as before, and you are back in the same session with its history.
You never typed a session id. Naming is per-directory, so `claude` in a project
always means "that project's agent".

> **Check:** `zellij ls` should still show only the sessions from step 0.
> Tethered ones are namespaced separately.

---

## 3. Prove the crash recovery

Detach again (`Ctrl+q`), then simulate a hard crash. This kills the zellij
processes with no cleanup — closer to a power cut than a quit.

```console
# Linux / macOS
$ pkill -9 zellij

# Windows PowerShell
> Get-Process zellij | Stop-Process -Force
```

Check the damage:

```console
$ tether ls
. tether-claude-demo-1a2b3c4d  claude  recoverable  demo
```

`.` and `recoverable` — the session is down but resurrectable.

Bring it back:

```console
$ tether restore
conversation_and_terminal  tether-claude-demo-1a2b3c4d

1 restored with conversation, 0 terminal-only
```

Then reattach and **check your earlier conversation is there**:

```console
$ cd ~/src/demo && claude
```

### The result to actually read

`conversation_and_terminal` means the agent came back knowing what you had
discussed. `terminal_only` means the terminal came back and **the agent is
brand new and empty** — it looks identical on screen, which is exactly why the
tool labels it.

Which agents can do which: [AGENTS.md](AGENTS.md).

---

## 4. The other agents: codex and agy

The routing rules differ per vendor because their flags differ. Before running
anything unfamiliar, you can ask which way it will go:

```console
$ tether explain codex -p myprofile
lane    human
reason  interactive terminal
```

### Codex

```console
$ cd ~/src/demo
$ codex                              # tethered, interactive
$ codex resume 01930f2a-...          # tethered: it starts a session
$ codex exec "run the tests"         # passes through: prints and exits
$ codex -p myprofile                 # tethered: -p is --profile here, not --print
```

That last one is the trap. On `claude`, `-p` means print; on `codex` it selects
a profile. A tool that assumed `-p` always meant headless would untether real
work — so the rules are per-vendor data, not a global guess.

### Antigravity (`agy`)

```console
$ agy                                # tethered
$ agy --conversation 7f3a1c2b        # tethered, resumes that conversation
$ agy -p "explain this file"         # passes through
$ agy -i --prompt "start here"       # tethered: -i is interactive, and wins
```

`-i` / `--prompt-interactive` reads like a headless prompt flag and is not.

### Verify pass-through is invisible

```console
$ claude -p "say hello"
```

Output identical to running the real binary, and `$?` (or `$LASTEXITCODE`) is
the real exit code. If a script of yours behaves differently under the shim,
that is a bug — please report it.

---

## 5. Spawn one from a script

The other half of the design: something that is not a person.

```console
$ id=$(tether new claude --cwd ~/src/demo)
$ echo "$id"
tether-claude-a41c9e2b

$ tether read "$id"        # what is on its screen
$ tether send "$id" "list the files here"
$ tether attach "$id"      # take over by hand, Ctrl+q to hand it back
```

`tether new` prints exactly one line so a program can parse it with no
heuristics. Full API: [API.md](API.md).

---

## 6. Clean up

```console
$ tether ls
$ tether kill tether-claude-demo-1a2b3c4d
$ tether reap --dry-run          # see what else would go
$ tether reap
```

---

## Undo everything

```console
$ tether reap --all              # end every tethered session
$ tether uninstall --all         # remove the shims
$ uv tool uninstall agent-tether
```

Then remove the shim directory from your `PATH`, and delete
`~/.config/agent-tether`, `~/.local/share/agent-tether` and
`~/.local/state/agent-tether` if you want nothing left behind.

Your own zellij sessions from step 0 are untouched throughout — nothing here
ever writes to your personal zellij config or namespace.

---

## If a step failed

| Symptom | Likely cause | Fix |
|---|---|---|
| `tether doctor` says `NOT shadowing` | shim dir missing from `PATH`, or after the real binary | run the `export`/`setx` line from `tether install`, open a new terminal |
| No `[tether]` banner when you run `claude` | the shim is not being reached | `tether doctor`; check `which claude` |
| `Ctrl+q` does nothing | your terminal is intercepting it | just close the window, or run `zellij action detach` inside the session |
| `restore` says `terminal_only` | that vendor never told us its session id | expected — see [AGENTS.md](AGENTS.md) |
| Session missing after reboot | it was reaped, or its `cwd` no longer exists | `tether ls --state any`; `restore` reports `cwd is gone` |
| A script broke under the shim | pass-through bug | set `TETHER_DISABLE=1` to unblock, then please file an issue |

More: [TROUBLESHOOTING.md](TROUBLESHOOTING.md).
