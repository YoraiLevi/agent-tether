# Configuration

Everything `agent-tether` knows about a vendor CLI is data. Adding support for
a new agent, or changing how an existing one is routed, means writing a TOML
file — never editing source, never sending a pull request, never waiting for a
release.

For the list of agents that ship in the wheel and the per-vendor traps you are
inheriting when you override one, see [AGENTS.md](AGENTS.md).

---

## 1. Where configuration lives

`agent-tether` uses XDG-style paths on **every** operating system, including
Windows and macOS. There is no `%APPDATA%`, no
`~/Library/Application Support`.

**Why.** The tools this wraps already do it — Claude Code installs itself to
`~/.local/bin` on Windows. One layout across all three OSes means the docs, the
support answers and your muscle memory are identical everywhere, and a config
file can be copied between machines unchanged. See the module docstring of
`src/tether/paths.py`.

| What | Default location | Override |
|---|---|---|
| config directory | `~/.config/agent-tether` | `XDG_CONFIG_HOME`, or `TETHER_HOME` |
| `config.toml` | `~/.config/agent-tether/config.toml` | follows the config directory |
| agent drop-ins | `~/.config/agent-tether/agents.d/*.toml` | follows the config directory |
| zellij config | `~/.config/agent-tether/zellij.kdl` if present, else the built-in `tether.kdl` | `TETHER_ZELLIJ_CONFIG` |
| data directory | `~/.local/share/agent-tether` | `XDG_DATA_HOME`, or `TETHER_HOME` |
| generated shims | `~/.local/share/agent-tether/shims` | `TETHER_BIN_DIR` |
| generated layouts | `~/.local/share/agent-tether/layouts` | follows the data directory |
| state directory | `~/.local/state/agent-tether` | `XDG_STATE_HOME`, or `TETHER_HOME` |
| session records | `~/.local/state/agent-tether/sessions/*.json` | follows the state directory |
| zellij sockets | `~/.local/state/agent-tether/run/sock` | `XDG_RUNTIME_DIR` (ignored when `TETHER_HOME` is set) |
| cache directory | `~/.cache/agent-tether` | `XDG_CACHE_HOME`, or `TETHER_HOME` |
| built-in agent data | inside the installed package, read-only | not overridable |

Ask the running installation rather than deriving it from this table:

```
tether paths
tether paths --json
tether doctor          # the same paths, plus everything that would break
```

Two rules worth knowing:

- An `XDG_*` variable is honoured **only if it holds an absolute path**. The
  XDG spec says relative paths are invalid and must be ignored; obeying that
  avoids creating a stray `.config` inside whatever directory you happened to
  be in.
- `TETHER_HOME` relocates everything at once, into `$TETHER_HOME/config`,
  `$TETHER_HOME/share`, `$TETHER_HOME/state` and `$TETHER_HOME/cache`. It wins
  over the `XDG_*` variables, and it also disables the `XDG_RUNTIME_DIR` branch
  so the socket directory stays inside the relocated tree.

### Why shims are not in `~/.local/bin`

Counter-intuitive but load-bearing: a shim cannot live in the same directory as
the binary it shadows. Claude Code installs `claude.exe` to `~/.local/bin` on
Windows; within a single directory Windows resolves extensions in `PATHEXT`
order and `.exe` comes before `.cmd`, so a `claude.cmd` shim sitting next to
`claude.exe` would never run. PATH order only breaks ties *between*
directories. Shims therefore get their own directory, which is then placed
ahead of the real ones on PATH.

---

## 2. Layering and precedence

Four layers, lowest precedence first:

| # | Layer | Location | `source` shown by `tether agents` |
|---|---|---|---|
| 1 | built-in | `src/tether/data/agents.d/*.toml` inside the wheel | `builtin` |
| 2 | user drop-in | `~/.config/agent-tether/agents.d/*.toml` | `builtin+user`, or `user` |
| 3 | inline | `[agents.<name>]` tables in `config.toml` | `...+config.toml` |
| 4 | environment | `TETHER_TARGET_<AGENT>` — the binary only | `...+env` |

This is the systemd drop-in / `conf.d` pattern, chosen because users already
know it and because it survives upgrades: a better built-in can ship without
clobbering your customisation.

Within layer 2, files are read in sorted filename order, and two files that
declare the same agent name are merged against each other in that order.

### Merging is per key

A drop-in overrides only the fields it sets. Everything else is inherited from
the layer below.

```toml
# ~/.config/agent-tether/agents.d/claude.toml
[agent]
name   = "claude"
binary = "/opt/custom/claude"
```

That agent still has claude's `headless_flags`, its nine management
subcommands, its `--session-id` set-id flag, its `--bg` interception and its
`value_flags` — every one of them from the built-in row. Only `binary` changed.
`tether agents` will show its source as `builtin+user`.

### The one merge rule that will surprise you

`_merge()` in `registry.py` decides "was this field specified?" by comparing the
override's value against the **dataclass default**:

```python
setattr(merged, f, over_val if over_val != default else base_val)
```

So a field explicitly set to its default value is indistinguishable from a field
that was never set, and the base value survives. Consequences:

| You write in a drop-in | Effect |
|---|---|
| `headless_flags = []` | **No effect.** The built-in list survives. There is no supported way to clear a list back to empty. |
| `verified = false` over a built-in `verified = true` | **No effect.** `false` is the default. |
| `set_id_launch_only = false` over `true` | **No effect.** |
| `enabled = false` | **Works.** The default is `true`, so `false` differs from it. |
| `binary = ""` | **No effect** (and `binary` is backfilled to the agent name anyway). |

If you need a shipped agent to stop being classified at all, use
`enabled = false` and, if you still want it tethered, define your own agent
under a different name — or replace the flag list with a non-default value that
cannot match, rather than trying to empty it.

### The environment layer

`TETHER_TARGET_<AGENT>` overrides only the binary. `<AGENT>` is the agent name
uppercased with `-` replaced by `_`, so agent `my-agent` reads
`TETHER_TARGET_MY_AGENT`.

Two independent code paths read it, and the difference matters:

- `registry.load()` applies it to agents **already in the registry**, updating
  `binary` and appending `+env` to the source. An agent with no TOML row is not
  touched here.
- `resolve.next_binary()` checks the same variable **first, unconditionally**,
  before any configured path and before the PATH walk — and raises
  `BinaryNotFound` if it points at a file that does not exist. So the override
  works even for an agent that has no TOML row at all.

---

## 3. File format

A drop-in is a TOML file with an optional top-level `schema` key and one or
more `[agent]` tables.

```toml
schema = 1          # top level, a SIBLING of [agent] - not inside it

[agent]
name = "myagent"
# ... fields ...
```

Multiple agents can live in one file using TOML's array-of-tables form:

```toml
schema = 1

[[agent]]
name = "myagent"

[[agent]]
name = "myagent-beta"
```

If `name` is omitted, the file's stem is used — `agents.d/myagent.toml` yields
an agent called `myagent`.

In `config.toml` the same fields go under an `[agents.<name>]` table:

```toml
[agents.claude]
binary = "/opt/custom/claude"
```

`config.toml` currently has **no other meaningful keys**. Only the `agents`
table is read (`registry.load()`); nothing else in the file is consumed by any
code path today.

---

## 4. Field reference

Authoritative source: the `Agent` dataclass in `src/tether/registry.py`.

Types are enforced at load time. A field of the wrong type does not fail
silently and does not fail loudly either — it causes the **entire agent entry**
to be skipped, with the reason recorded for `tether doctor`.

### Identity

| Field | Type | Default | What it does | What breaks if you get it wrong |
|---|---|---|---|---|
| `name` | string | file stem, or the `[agents.<name>]` key | The registry key. Also the shim filename, the PATH search term in `resolve.next_binary()`, and the `<AGENT>` half of `TETHER_TARGET_<AGENT>`. | If it does not match the executable name you type, the shim never fires and the row is inert. |
| `binary` | string | the agent `name` | Explicit path to the real executable. **Only honoured when it contains a path separator** — a bare name is ignored and the normal PATH walk runs. | A wrong path is not an error: the file simply is not a file, and resolution falls through to the PATH walk. You get the wrong binary silently. |
| `display_name` | string | `""` | Human label. | Nothing. Surfaced only in `tether agents --json`. |
| `enabled` | bool | `true` | `false` removes the agent from `tether agents` and makes `Registry.get()` return the untyped fallback agent instead of your row. | With `enabled = false` the CLI is still shimmed and still tethered, but nothing about its flags is known, so every invocation is decided purely by the TTY test. |
| `verified` | bool | `false` | Records that someone ran `<bin> --help` on a real install. | Nothing functional. It is a trust marker for humans reading `tether agents`. |

`source` is set by the loader (`builtin`, `user`, `config.toml`, `fallback`,
with `+env` appended) and cannot be set from a file.

### Lane classification

These decide whether an invocation is passed through untouched, given a human
terminal, or tethered detached. Read `src/tether/router.py` for the exact order.
The router's fail-safe direction is **toward pass-through**, because wrongly
passing through costs one call's durability, while wrongly tethering hands a
TUI to a caller that expected text on stdout.

| Field | Type | Default | What it does | What breaks if you get it wrong |
|---|---|---|---|---|
| `headless_flags` | list of strings | `[]` | Any of these tokens in argv means "print and exit" — the invocation is passed through. Matched after stripping an `=` suffix, so `--print=x` matches `--print`. | **The most dangerous field.** Omit a real headless flag and a scripted `agent -p "..."` gets a terminal UI or blocks forever, corrupting whatever pipeline read that output. Add a flag that is not headless and interactive sessions are never tethered, so they die with their launcher. Never assume `-p`: it is `--profile` on codex, takes a value on grok, and is `--password` inside `mimo run`. |
| `headless_subcommands` | list of strings | `[]` | First positional token match means headless — `codex exec`, `opencode run`, `mimo run`. Checked before `subcommands`. | Same asymmetry as above. |
| `interactive_flags` | list of strings | `[]` | Flags that *look* headless but open a session. Their presence suppresses the whole `headless_flags` check for that invocation. Matched on the exact token (no `=` handling). | Omit one and an interactive invocation is passed through untethered. Note these do not force a lane — they only disable the headless check; the TTY test still chooses human vs agent. |
| `info_flags` | list of strings | `["-h", "--help", "--version"]` | `--help` / `--version` traffic is passed through. Matched with `=` stripping. | Supplying this key **replaces** the default list rather than adding to it; forgetting `-h` means `agent -h` opens a session. |
| `subcommands` | list of strings | `[]` | Management subcommands that must not be tethered — `claude mcp`, `codex login`. | A missing entry means `codex login` opens inside a zellij pane. A wrong entry means a real session is passed through. |
| `session_start_subcommands` | list of strings | `[]` | Exempts a subcommand from the `subcommands` pass-through so it *is* tethered — `codex resume <id>` looks like management but starts a session. | Without it, `codex resume` runs untethered and dies with its launcher. |
| `background_flags` | list of strings | `[]` | The vendor's own background flag. Intercepted and turned into the AGENT lane, producing a detached session you can `tether attach` to. Exact token match. | Nothing breaks if omitted; you just lose the interception and the vendor's own background mode runs instead. |
| `value_flags` | list of strings | `[]` | Flags that consume the following token. Used by `_first_positional()` so a flag's VALUE is never mistaken for a subcommand. The `--flag=value` form is not treated as consuming. | Omit `--model` and `claude --model exec` classifies `exec` as a subcommand and passes the whole session through. |

### Session identity

These decide what `tether restore` can bring back. See
[AGENTS.md](AGENTS.md), "What restore can and cannot recover".

| Field | Type | Default | What it does | What breaks if you get it wrong |
|---|---|---|---|---|
| `set_id_flag` | string | `""` | **The valuable one.** Its presence makes `can_choose_id` true, which makes `run.py` mint a UUID and prepend `[set_id_flag, uuid]` to every new launch, and name the zellij session after that id. One identifier then names both the terminal and the conversation. | Declare a flag the vendor does not accept and **every new session fails to start** — the pane opens, the vendor rejects the argument, the agent exits. Declare nothing when the vendor does support it and you silently forfeit conversation restore forever. |
| `set_id_aliases` | list of strings | `[]` | Extra spellings, scanned when capturing a user-supplied id from argv. Not used when launching — only `set_id_flag` is prepended. | A missing alias means a user-supplied id is not captured, so that session forks a second tether session and restores terminal-only. |
| `resume_flag` | string | `""` | The vendor's resume flag. Restore rebuilds `[resume_flag, id]`. Also scanned to capture a user-supplied id. | Wrong flag means restore launches with a bogus argument. Depending on the vendor that is a visible error or a **brand-new empty conversation that looks like a successful restore**. |
| `resume_aliases` | list of strings | `[]` | Extra spellings, scanned for id capture. Never used to build the restore argv. | Same capture loss as `set_id_aliases`. |
| `resume_subcommand` | string | `""` | Resume expressed as a subcommand — `codex resume <id>`. Restore builds `[subcommand, id]`. **Takes precedence over `resume_flag`** in `resume_argv()`. | Setting both, when only the flag is real, makes every restore use the subcommand form. |
| `continue_flag` | string | `""` | Records the vendor's "continue the last conversation" flag. | Nothing. Parsed and exposed in JSON; no code path uses it. |

### Declarative metadata

| Field | Type | Default | What it does |
|---|---|---|---|
| `set_id_launch_only` | bool | `false` | Records that the set-id flag works only for NEW sessions and must never be replayed on restore. grok says exactly this in its own `--help`. |
| `id_is_path` | bool | `false` | Records that the vendor's session id is a filesystem path, so identity comparisons must be path-aware. True for `pi`. |
| `transcript_dir` | string | `""` | Where the vendor keeps its transcripts, e.g. `~/.claude/projects`. |
| `install_hint` | string | `""` | How to install the CLI, e.g. `npm i -g @openai/codex`. |
| `notes` | string | `""` | Free text. This is where the per-vendor traps are recorded, and it is the field to read before overriding a shipped row. |

### Fields that are parsed but not yet acted on

Be precise about this when reasoning about behaviour. The following fields are
validated, merged, stored on the `Agent`, and emitted by `tether agents --json`
— but **no code reads them to make a decision**:

`display_name`, `continue_flag`, `set_id_launch_only`, `id_is_path`,
`transcript_dir`, `install_hint`, `notes`.

`set_id_launch_only` deserves a specific note: the invariant it describes is
enforced structurally rather than by reading the flag. `tether restore` always
rebuilds the command from `resume_argv()` and never replays the recorded launch
line, so a launch-only set-id flag can never reach a restore for any agent,
whether or not the flag is set.

`verified` is read only for display in `tether agents`.

---

## 5. Worked example 1 — override just the binary path of a shipped agent

You have two Claude Code installs and want the shim to chain to a specific one.

```toml
# ~/.config/agent-tether/agents.d/claude.toml
schema = 1

[agent]
name   = "claude"
binary = "C:/Users/you/.local/bin/claude.exe"
```

Verify by effect, not by presence — the file existing proves nothing:

```
tether agents
# claude should now show source: builtin+user

tether doctor
# the "chains_to" column for claude must be the path you just set
```

`tether doctor` resolves the binary through `next_binary()`, the same function
the shim uses at run time, so its `chains_to` value is the value that will
actually be executed.

Equivalent inline form, if you prefer one file:

```toml
# ~/.config/agent-tether/config.toml
[agents.claude]
binary = "C:/Users/you/.local/bin/claude.exe"
```

Equivalent throwaway form, for one shell only:

```powershell
$env:TETHER_TARGET_CLAUDE = "C:\Users\you\.local\bin\claude.exe"
```

All three change only the binary. Every other claude field still comes from the
built-in row.

---

## 6. Worked example 2 — add a brand-new agent from scratch

Suppose a CLI called `newbot` with this help output:

```
newbot [OPTIONS] [COMMAND]
  -q, --quiet-run <PROMPT>   run once and print the answer
  -m, --model <NAME>         model to use
      --sid <UUID>           set the session id for a NEW session
      --continue-session <UUID>   resume an existing session
  Commands: batch, login
```

The drop-in:

```toml
# ~/.config/agent-tether/agents.d/newbot.toml
schema = 1

[agent]
name         = "newbot"
display_name = "NewBot CLI"
verified     = true          # you ran `newbot --help` yourself
install_hint = "npm i -g newbot"

# Lane classification -------------------------------------------------------
headless_flags       = ["-q", "--quiet-run"]
headless_subcommands = ["batch"]
info_flags           = ["-h", "--help", "--version"]
subcommands          = ["login"]
# Every flag that eats the next token, or `newbot --model batch` would be
# classified as the `batch` subcommand and passed through untethered.
value_flags = ["-q", "--quiet-run", "-m", "--model", "--sid", "--continue-session"]

# Session identity ----------------------------------------------------------
set_id_flag = "--sid"
resume_flag = "--continue-session"

transcript_dir = "~/.newbot/sessions"
notes = "--sid accepts any UUID and creates the session. Verified against 1.4.0."
```

Install and prove it works:

```
tether agents
# newbot appears, source "user", verified true, restore "conversation"

tether explain newbot -q "hello"
# lane passthrough / reason "headless flag -q"

tether explain newbot --model batch
# lane human or agent - NOT "management subcommand 'batch'".
# If it says the latter, value_flags is missing --model.

tether install newbot
tether doctor
# newbot shows "shadowing", chaining to the real newbot executable
```

Then start one real session and confirm restore end to end:

```
tether new newbot
tether get <session>            # provider_session_id must be a UUID, not empty
tether restore --dry-run        # must say conversation_and_terminal, not terminal_only
```

`provider_session_id` being non-empty is the check that matters. Empty means
`set_id_flag` did not take effect, and every restore of that agent will be
terminal-only no matter what the table in [AGENTS.md](AGENTS.md) claims.

---

## 7. Error behaviour

**A malformed file is skipped, never raised.**

The shim runs on every invocation of every agent CLI on the machine. If a
broken TOML could raise, one typo in one drop-in would make `claude`, `codex`
and everything else fail at once — a far worse failure than one unrecognised
agent. So `_load_dir()` catches everything, records `(path, message)`, and
carries on with the layers it could read.

What gets skipped, and why:

| Condition | Message recorded | Scope skipped |
|---|---|---|
| TOML does not parse | `could not parse: <exception>` | the whole file |
| `schema` is not an integer, or is greater than the supported version | `schema N is newer than supported 1; skipped` | the whole file |
| no `[agent]` table | `missing an [agent] table` | the whole file |
| an `[agent]` entry is not a table | `[agent] must be a table` | that entry |
| `name` missing or not a non-empty string | `agent name must be a non-empty string` | that entry |
| a string field holds a non-string | `'<field>' must be a string` | **that whole agent entry**, not just the field |
| a bool field holds a non-bool | `'<field>' must be true or false` | that whole agent entry |
| a list field is not a list of strings | `'<field>' must be a list of strings` | that whole agent entry |
| `config.toml` does not parse | `could not parse: <exception>` | all inline `[agents.*]` overrides; the rest of the tool still runs on defaults |
| `agents.<name>` is not a table | `agents.<name> must be a table` | that entry |

Every one of these surfaces in three places and nowhere else:

```
tether doctor          # listed under "problem:", and the command exits 1
tether agents          # red "config problem:" lines under the table
tether agents --json    # in the "errors" array
```

`tether doctor` also reports unreadable session records
(`store.corrupt_records()`), a shim directory that is not on PATH, a missing or
too-old zellij, and any agent whose binary cannot be resolved.

**Because failure is silent by design, `tether doctor` is the only thing that
tells you your drop-in did not load.** Run it after every config change.

### Schema version gating

`SCHEMA_VERSION` is `1`. The rule in `_load_dir()`:

- A file with **no** `schema` key is assumed to be the current version.
- `schema = 1` loads.
- `schema = 2` or higher is skipped, with a message — an older `agent-tether`
  refuses to guess at a format it does not understand rather than
  misinterpreting it.
- A non-integer `schema` is skipped the same way.
- Values below `1` are not rejected; only "greater than supported" is gated.

The gate applies to drop-in **files** only. Inline `[agents.<name>]` tables in
`config.toml` and the `TETHER_TARGET_*` environment layer carry no version
marker and are never gated.

---

## 8. Environment variables

### Ones you would set

| Variable | Read by | Effect | When you would set it |
|---|---|---|---|
| `TETHER_HOME` | `paths.py` | Relocates the whole tree to `$TETHER_HOME/{config,share,state,cache}`. Wins over the `XDG_*` variables and disables the `XDG_RUNTIME_DIR` branch for sockets. Expanded and resolved to an absolute path. | Running a second isolated installation side by side, or in tests. |
| `TETHER_BIN_DIR` | `paths.bin_dir()` | Where shims are generated and looked for, instead of `<data>/shims`. | You keep all your tools in one directory that is already on PATH. |
| `TETHER_DISABLE=1` | `router.classify()` | Forces PASSTHROUGH for every invocation in this process. | A tight loop of headless calls where the per-invocation start-up cost matters, or bisecting whether the tether is the problem. |
| `TETHER_QUIET=1` | `run._banner()` | Suppresses the dim `[tether] <session> - Ctrl+q detaches` line written to stderr on the human lane. | Scripts, screen recordings, or once you already know. |
| `TETHER_DEBUG=1` | `shim.main()` | Re-raises internal errors instead of catching them and running the agent untethered. | The shim is misbehaving and you need the traceback instead of the graceful fallback. |
| `TETHER_FORCE_LANE` | `router.classify()` | Forces the lane to `agent`, `human` or `passthrough`. Highest-priority router signal — checked before `TETHER_DISABLE`, the recursion guard, the nesting guard and the TTY test. Any other value is ignored. | An orchestrator that happens to have a terminal attached but wants a detached session. `tether new` sets it to `agent` for exactly this reason. |
| `TETHER_TARGET_<AGENT>` | `resolve.next_binary()`, `registry.load()` | Forces which binary a shim chains to. `<AGENT>` is the agent name uppercased with `-` replaced by `_`. Checked before the configured `binary` and before the PATH walk; raises `BinaryNotFound` if it names a file that does not exist. | Several tools shadow the same name, the real binary is somewhere unusual, or you want to A/B two versions. |
| `TETHER_ZELLIJ` | `zellij.executable()` | Absolute path to the zellij binary, instead of finding it on PATH. | zellij is not on PATH, or you want a specific build. |
| `TETHER_ZELLIJ_CONFIG` | `paths.zellij_config_file()` | Path to the zellij `--config` file. Otherwise `<config>/zellij.kdl` is used if it exists, else the built-in `tether.kdl`. | You want different keybinds. Note this file exists only because keybinds cannot be passed as CLI arguments, and a `keybinds` block inside a zellij *layout* is parsed, validated, then silently discarded — see [DESIGN.md](DESIGN.md). |
| `TETHER_SHIM_ENTRY` | `shims.entry_point()` | The absolute launcher path baked into newly generated shims. Otherwise `tether-shim` is located on PATH, falling back to `"<python>" -m tether.shim` in a source checkout. | Installing from a checkout, or pinning a specific interpreter. Read at generation time, not at run time. |
| `TETHER_CARRY_<ANYTHING>` | `store.carried_env()` | Any variable with this prefix is captured when a session is created and replanted when it is restored. | Your own pane-identity or telemetry variables need to survive a reboot. |
| `XDG_CONFIG_HOME`, `XDG_DATA_HOME`, `XDG_STATE_HOME`, `XDG_CACHE_HOME` | `paths.py` | Relocate one root each. Honoured on Windows and macOS too. **Ignored unless the value is an absolute path.** | You already decided where things go and want that decision kept. |
| `XDG_RUNTIME_DIR` | `paths.runtime_dir()` | Puts the zellij socket directory under `$XDG_RUNTIME_DIR/agent-tether`. Honoured only when absolute **and** `TETHER_HOME` is unset. | Standard POSIX runtime-dir hygiene. |

Beyond `TETHER_CARRY_*`, the prefixes `ORCA_`, `CONDUCTOR_` and `GHOSTX_` are
also captured and replanted. These identify the pane to an orchestrator, and
Orca's hook scripts exit 0 silently when any of them is missing — dropping one
produces no error at all, just a pane that looks permanently idle. Vendor
session variables such as `CLAUDE_CODE_*` are deliberately **not** carried:
they describe the conversation of whoever launched us, and inheriting them
would make a new agent believe it is part of its parent's session.

### Ones the tool sets for itself

Listed so you recognise them in a process listing, and so you do not set them
by hand.

| Variable | Set by | Purpose |
|---|---|---|
| `TETHER_ACTIVE=1` | `shim.exec_passthrough()`, `zellij.base_env()` | Recursion guard. `router.classify()` sees it and passes through, so anything spawned inside a tethered session is never tethered again. |
| `TETHER_SHIM_AGENT` | the generated shim script | Tells `tether-shim` which agent it is standing in for. `shim.main()` **pops** it from the environment, so it is not inherited by the child. If it is absent, the first argv token is taken as the agent name instead. |
| `TETHER_SHIM_PATH` | the generated shim script | The shim's own path. `resolve._self_paths()` uses it for same-file detection, which is one of the two defences that stop a shim from calling itself forever. |
| `ZELLIJ_SOCKET_DIR` | `zellij.base_env()` | Points zellij at our isolated socket directory, which is what keeps tethered sessions out of your own `zellij ls`. |
| `ZELLIJ`, `ZELLIJ_SESSION_NAME` | zellij itself | Read by `router.classify()`. If either is present the invocation is passed through, refusing to nest a tether inside a zellij session. |

---

## See also

- [AGENTS.md](AGENTS.md) — the shipped agents, what each one's restore can
  recover, and the per-vendor traps.
- [DESIGN.md](DESIGN.md) — why the lanes and the two-layer id design are shaped
  this way.
- [ORCA.md](ORCA.md) — orchestrator compatibility and the process-tree problem.
