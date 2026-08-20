# agent-tether

**Your coding agent shouldn't die because you closed a window.**

`agent-tether` puts a durable [zellij](https://zellij.dev) session between an agent CLI and whatever launched it. Close the IDE, quit the orchestrator, lose the SSH link, reboot the host — the agent keeps working, and you reattach from anywhere.

It is a **transparent shim**. You keep typing `claude`, `codex`, `agy`. Your orchestrator keeps spawning them exactly as before. Nothing learns a new command.

```
without tether                        with tether
──────────────                        ───────────
your IDE                              your IDE
  └── shell                             └── shell
        └── claude   ← dies with it           └── zellij client   ← only this dies
                                                    ⇢ zellij server (detached)
                                                          └── claude   ← survives
```

> **v0.2 · Linux, macOS, Windows.** Unit-tested on all three; integration-tested on Linux. See [Limitations](#limitations) — one of them matters if you use an orchestrator like Orca.

---

## Install

Two dependencies. Neither is bundled.

```bash
# 1. uv — https://docs.astral.sh/uv/
curl -LsSf https://astral.sh/uv/install.sh | sh          # Linux / macOS
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"  # Windows

# 2. zellij >= 0.44 — https://zellij.dev
cargo install --locked zellij
```

Then:

```bash
uv tool install agent-tether
tether install --all
tether doctor
```

`tether install` writes one small shim per agent CLI and tells you if its directory isn't on `PATH` yet. `tether doctor` verifies the whole chain and names anything that would break it.

Full details, per-OS notes and troubleshooting: **[docs/INSTALL.md](docs/INSTALL.md)**.

---

## The day, end to end

This is the entire product. Everything else is detail.

### Morning — start work

```console
$ cd ~/src/payments
$ claude
[tether] tether-claude-payments-1a2b3c4d - Ctrl+q detaches, session keeps running
```

You get your agent in a plain terminal. No tab bar, no status bar, no modes.

### Midday — step away

Press **`Ctrl+q`**. Or just close the window. Or quit your IDE entirely.

```console
$ tether ls
* tether-claude-payments-1a2b3c4d  claude  live  payments
```

Still running. It kept working while you were in a meeting.

### Afternoon — pick it back up

```console
$ cd ~/src/payments
$ claude
```

Same command as this morning, and you land **back in the same session** with its history intact. Naming is per-directory, so `claude` in a project is idempotent — you never have to remember a session id.

### Evening — the machine reboots

```console
$ tether restore
conversation_and_terminal  tether-claude-payments-1a2b3c4d
conversation_and_terminal  tether-gemini-docs-9f8e7d6c
terminal_only              tether-codex-api-4b5c6d7e   codex never told us its session id

2 restored with conversation, 1 terminal-only
```

Read that output. The two results are **not** the same thing, and the difference is the single most important idea in this tool — see [What restore can actually recover](#what-restore-can-actually-recover).

---

## Exact usage, three agents

```console
# Claude Code — best supported: it can be told its session id up front,
# so restore brings back the conversation, not just the terminal.
$ cd ~/src/api && claude
$ claude --resume 9602334f-75fb-45bc-991b-868b958a7951   # lands on the SAME tether session
$ claude -p "summarize the diff"                          # headless: passes straight through

# Codex — resume is a bare subcommand, and `codex exec` is its headless mode.
$ cd ~/src/api && codex
$ codex resume 01930f2a-...        # tethered: it starts a session
$ codex exec "run the tests"       # passes through: it prints and exits
$ codex -p myprofile               # tethered: -p is --profile here, NOT --print

# Antigravity — resumes by conversation, and -i is interactive despite looking headless.
$ cd ~/src/api && agy
$ agy --conversation 7f3a1c2b      # tethered
$ agy -p "explain this file"       # passes through
$ agy -i --prompt "start here"     # tethered: -i wins over -p
```

Not sure which way a command will go? Ask:

```console
$ tether explain codex -p myprofile
lane    human
reason  interactive terminal
```

A human walkthrough with expected output at every step: **[docs/TUTORIAL.md](docs/TUTORIAL.md)**.

---

## For orchestrators and agents

Spawn detached, get one line back, drive it later:

```console
$ tether new claude --cwd ~/src/api
tether-claude-a41c9e2b

$ tether ls --under ~/src --state live --json
$ tether read tether-claude-a41c9e2b        # what is on its screen
$ tether send tether-claude-a41c9e2b "run the tests"
$ tether attach tether-claude-a41c9e2b      # a human takes over, then Ctrl+q
$ tether kill tether-claude-a41c9e2b
```

Every command takes `--json`, with a versioned schema. Filter by directory, subtree, agent, state, name or vendor session id.

Full surface, schemas and exit codes: **[docs/API.md](docs/API.md)**. Drop-in agent skills: **[skills/](skills/)**.

---

## What restore can actually recover

There are two independent layers of state:

```
zellij session ─── panes, layout, cwd, command line   ← zellij restores this
   └── agent    ─── the conversation                   ← only the vendor CLI can
```

Restore the outer layer alone and you get a perfectly restored terminal running a **brand-new, empty agent**. It looks like it worked. It didn't. Avoiding that is why this tool exists in the shape it does.

Where a vendor lets us choose its session id up front, `agent-tether` mints one id and gives it to both layers, so they come back together. **Only some vendors allow that**, so `tether restore` labels every result honestly rather than implying success.

Which agents fall on which side: **[docs/AGENTS.md](docs/AGENTS.md)**.

---

## Adding an agent we don't ship

Drop a file. No source change, no pull request.

```toml
# ~/.config/agent-tether/agents.d/mytool.toml
schema = 1

[agent]
name           = "mytool"
headless_flags = ["--oneshot"]
resume_flag    = "--resume"
set_id_flag    = "--session-id"
```

```console
$ tether agents          # confirm it loaded
$ tether install mytool
```

Layering, every field, and worked examples: **[docs/CONFIG.md](docs/CONFIG.md)**.

---

## Commands

| | |
|---|---|
| `tether ls` | sessions, with filters and `--json` |
| `tether get <session>` | one session in detail |
| `tether attach <session>` | attach, resurrecting first if needed |
| `tether new <agent>` | create detached, print the id |
| `tether read` / `send` | read a screen, type into a session |
| `tether restore` | after a reboot, bring everything back |
| `tether reap` | delete finished sessions |
| `tether kill <session>` | end one and forget it |
| `tether install` / `uninstall` | manage shims |
| `tether agents` | known CLIs and what restore can do for each |
| `tether explain <agent> …` | which lane an invocation takes, and why |
| `tether doctor` | check everything, name what's broken |
| `tether paths` | every location used |

---

## Limitations

1. **Orchestrator live-status may go blank.** Tools that identify the running agent by walking the pane's process tree (Orca does) won't see a tethered agent, because it deliberately runs outside that tree. Durability and reattach are unaffected; the sidebar is. See **[docs/ORCA.md](docs/ORCA.md)**.
2. **Not every agent can restore its conversation.** See [above](#what-restore-can-actually-recover).
3. **Per-call startup cost** of a Python process (~50–150 ms). Irrelevant interactively, noticeable in a tight `claude -p` loop — set `TETHER_DISABLE=1` for those.
4. **`droid` and `devin` restore behaviour is unverified** — neither documents what happens on a missing session id.

## Documentation

| | |
|---|---|
| [docs/INSTALL.md](docs/INSTALL.md) | dependencies, per-OS install, PATH, uninstall |
| [docs/TUTORIAL.md](docs/TUTORIAL.md) | step-by-step for a human, with expected output |
| [docs/AGENTS.md](docs/AGENTS.md) | every supported CLI and what restore can recover |
| [docs/CONFIG.md](docs/CONFIG.md) | the pluggable config system, every field and variable |
| [docs/API.md](docs/API.md) | the JSON API for orchestrators and agents |
| [docs/DESIGN.md](docs/DESIGN.md) | why it is built this way, and the failures each choice prevents |
| [docs/ORCA.md](docs/ORCA.md) | orchestrator compatibility and the process-tree problem |
| [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) | symptom → cause → fix |

## License

MIT
