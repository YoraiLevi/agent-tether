# agent-tether

**Durable, individually-addressable terminals for agent CLIs.**

Your coding agent dies when the thing that launched it dies. Close the IDE, quit the orchestrator, lose the SSH connection, reboot the host — the agent goes with it. `agent-tether` puts a zellij session between the launcher and the agent, so the agent outlives whatever started it.

It is a **transparent shim**. You do not learn a new command. You keep typing `claude`, `codex`, `agy`; your orchestrator keeps spawning them exactly as before. The shim decides what each invocation actually needs.

> **Status: v0.1, Windows only.** Verified against zellij 0.44.3 and Claude Code 2.1.237 on Windows 11. A POSIX port is on the roadmap and not written yet. Read [Limitations](#limitations) before adopting — one of them is significant.

---

## Why

```
without tether                        with tether
--------------                        -----------
orchestrator                          orchestrator
  └── shell                             └── shell
        └── claude   <- dies with it          └── zellij client  <- only this dies
                                                    ⇢ zellij server (detached)
                                                          └── claude  <- survives
```

The agent is no longer a child of the thing that launched it. Quit the orchestrator and the work keeps running; reattach from any terminal, later, from anywhere.

## What it is not

It is **not** a wrapper around everything. `claude -p "summarize this"` must return text on stdout. Putting a terminal multiplexer in the middle of that returns a TUI instead of an answer — and it fails *silently*, which is the worst way to fail. So the shim is a **router**:

| Invocation | Lane | What happens |
|---|---|---|
| `claude -p "..."`, `codex exec`, `mimo run` | pass-through | untouched, byte-identical |
| `claude mcp`, `codex login`, `--help`, `--version` | pass-through | untouched |
| already inside a tethered session | pass-through | no nesting |
| `claude` at a terminal | **human** | create-or-attach, then attach |
| `claude` spawned with no TTY, or `claude --bg` | **agent** | create **detached**, print the session id, exit 0 |

The TTY test is the load-bearing signal: a human at a terminal has a console, a spawned process does not.

---

## Requirements

- Windows 10/11
- [zellij](https://zellij.dev) **0.44.0 or newer** — native Windows support landed in 0.44.0
  ```powershell
  cargo install --locked zellij
  ```
- PowerShell 5.1+ (PowerShell 7 is used automatically when present)

## Install

```powershell
git clone https://github.com/YoraiLevi/agent-tether.git
cd agent-tether
.\install.ps1
```

That writes one `.cmd` shim per agent CLI it finds, plus a `tether` management command, into `%LOCALAPPDATA%\agent-tether\bin`, and prepends that directory to your **user** PATH. Nothing else on your system is modified — the real agent binaries are never touched, moved, or renamed. The shims work purely by PATH precedence.

Open a **new** terminal, then:

```powershell
tether doctor
```

```
shims and what they chain to:
  claude       tethered     -> C:\Users\you\.local\bin\claude.exe
  codex        tethered     -> C:\Users\you\AppData\Local\Programs\OpenAI\Codex\bin\codex.exe
```

### Install options

```powershell
.\install.ps1 -ShimDir D:\tools\shims       # you choose where
.\install.ps1 -Agents claude,codex          # only these
.\install.ps1 -PathPosition none            # write shims, do not touch PATH
.\install.ps1 -PathPosition last            # only used if nothing else provides the name
```

`-PathPosition none` is the honest choice if you already manage PATH yourself, or if another tool also shadows these executables and you want to decide the order.

### Chaining with other shims

The shims do **not** hardcode a vendor path. At run time each one walks PATH, skips any file carrying the `agent-tether-shim` marker, and calls the next match.

So if other software also shadows `claude`, both survive. Whichever is earlier on PATH runs first and reaches the other *through* us. Override a specific target explicitly at any time:

```powershell
$env:TETHER_TARGET_CLAUDE = "C:\some\other\claude.exe"
```

## Uninstall

```powershell
.\uninstall.ps1              # remove shims + PATH entry, keep running sessions
.\uninstall.ps1 -PurgeState  # also kill every tethered session and delete state
```

Uninstalling deletes only files carrying our marker; anything else in the shim directory is left alone and reported. Running sessions are ordinary zellij sessions and keep running unless you pass `-PurgeState`.

---

## Use

### As a human

```powershell
cd ~/source/myproject
claude
```

You get your agent, in a bare terminal — no tab bar, no status bar, no modes. Press <kbd>Ctrl</kbd>+<kbd>q</kbd> to detach, or just close the window. The agent keeps running.

Run `claude` in that directory again and you land back in the same session. Naming is per-directory for the human lane, so it is idempotent per project.

### As an orchestrator

```powershell
claude --bg
# -> tether-claude-a41c9e2b     (one line on stdout, nothing else)
```

Exit code 0 means the session is live and addressable. Later:

```powershell
tether attach tether-claude-a41c9e2b
```

You can take over an agent a robot started, work by hand, and detach again — leaving it running.

### Managing

```powershell
tether ls                # every tethered session and its state
tether attach <session>  # attach, resurrecting first if needed
tether restore           # after a reboot: bring everything back, detached
tether reap              # delete finished sessions so listings stay useful
tether kill <session>    # end one and forget it
tether doctor            # install state + what each shim chains to
```

## Crash recovery

The host crashes. On reboot:

```powershell
tether restore
```

```
restored  tether-claude-myproject-1a2b3c4d  [claude]  conversation + terminal
restored  tether-codex-9f2e1a               [codex]   TERMINAL ONLY - agent starts fresh
2 with conversation, 1 terminal-only, 0 already live, 0 skipped
```

**Read that output carefully — the two results are not the same thing.**

There are two independent layers of state:

```
zellij session ─── panes, layout, cwd, the command line   ← zellij serializes this
   └── agent    ─── the actual conversation                ← zellij knows nothing about it
```

Restore the first alone and you get a beautifully restored terminal running a **brand-new, empty** agent. It looks like it worked. It didn't.

`agent-tether` collapses the two onto one identifier where it can: when a vendor supports choosing a session id up front, the shim mints one UUID and gives it to both zellij and the agent. Then restore rebuilds the launch command in the vendor's **resume** form and the transcript comes back with the terminal.

**Only 4 of 12 vendors support this**: `claude`, `grok`, `gemini`, `pi`. For the rest the session id is generated by the vendor after launch and we never learn it, so restore is honestly labelled `TERMINAL ONLY`.

Restore deliberately **rebuilds** rather than replaying the original command. Replaying is the silent-empty-restore trap — grok's own help says `--session-id` *"Does not resume existing sessions"*, and gemini exits fatally on a duplicate. The cost is that a rebuilt session does not carry the old scrollback. A correct transcript beats a pretty terminal.

---

## Limitations

**1. Orchestrator live-status may go blank.** This is the significant one. Some orchestrators (Orca among them) identify the running agent by walking the pane's process-tree descendants and filtering by the pane's ConPTY console process list. A tethered agent runs under `zellij-server` — neither a descendant of the pane shell nor attached to its ConPTY — so such a tool may conclude no agent is running, even though it is. Durability and reattachment still work. See [docs/ORCA.md](docs/ORCA.md) for the full analysis and mitigations.

**2. Windows only.** The router is PowerShell and the shims are `.cmd`.

**3. 8 of 12 vendors cannot restore their conversation.** See above. `tether restore` tells you which is which rather than pretending.

**4. Per-invocation startup cost.** Every shimmed call starts a PowerShell process (~150–400 ms). Irrelevant for an interactive session, noticeable if you call `claude -p` in a tight loop. Set `TETHER_DISABLE=1` to bypass the shim entirely for such a run.

**5. Argument fidelity.** Pass-through recovers the caller's raw command-line text and hands it to the child verbatim, so quoting survives. The tethered lanes re-quote arguments through a generated layout; exotic quoting there is less well tested.

**6. `droid` and `devin` restore behaviour is unverified.** Neither documents what happens when a session id does not exist. Probe before trusting restore for those.

## Environment variables

| Variable | Effect |
|---|---|
| `TETHER_DISABLE=1` | bypass the shim completely for this process |
| `TETHER_HOME` | state directory (default `%LOCALAPPDATA%\agent-tether`) |
| `TETHER_TARGET_<AGENT>` | force the binary a given shim chains to |
| `TETHER_QUIET=1` | suppress the `[tether]` banner line |

Orchestrator identity variables (`ORCA_*`, `CONDUCTOR_*`, `GHOSTX_*`, and anything prefixed `TETHER_CARRY_`) are captured at spawn and replanted on restore, so hook-based telemetry keeps its pane attribution. Vendor session variables such as `CLAUDE_CODE_*` are deliberately **not** carried — they describe the launcher's conversation, and inheriting them would make a new agent believe it is part of its parent's session.

## Layout

```
src/tether.ps1     the router: lanes, naming, registry, zellij plumbing
src/agents.json    per-vendor flags, each row marked verified or docs-only
zellij/tether.kdl  keybinds + serialization (keybinds CANNOT live in a layout)
zellij/layouts/    the bare layout: one pane, no UI plugins
install.ps1        generate shims, manage PATH
uninstall.ps1      exact reverse
docs/DESIGN.md     why the lanes and the two-layer id design are shaped this way
docs/ORCA.md       orchestrator compatibility, and the process-tree problem
```

## License

MIT
