# Supported agents

Every row below is read from a TOML file shipped inside the wheel at
`src/tether/data/agents.d/`. Nothing here is hardcoded in Python: the router
(`src/tether/router.py`) and the restore logic (`tether restore` in
`src/tether/cli.py`) consume this data and nothing else.

To override one of these rows, or to add a CLI that is not listed, drop a TOML
file into `~/.config/agent-tether/agents.d/`. You never edit source and you
never send a pull request. See [CONFIG.md](CONFIG.md).

To see what your machine actually resolved — after your drop-ins, your
`config.toml` and your environment have been layered on top — run:

```
tether agents                    # table
tether agents --json             # every field, machine-readable
tether explain claude -p "hi"    # which lane a specific invocation would take
```

---

## The shipped agents

11 agents ship in v0.2.0.

| agent | display name | verified | headless (pass-through) trigger | resume form | can choose id up front | restore recovers |
|---|---|---|---|---|---|---|
| `agy` | Antigravity | **yes** | flags `-p`, `--print`, `--prompt` | `--conversation <id>` | no | terminal only |
| `claude` | Claude Code | **yes** | flags `-p`, `--print` | `--resume <id>` (alias `-r`) | **yes** — `--session-id <uuid>` | conversation + terminal |
| `codex` | OpenAI Codex CLI | **yes** | subcommands `exec`, `e`, `review` | `codex resume <id>` (subcommand) | no | terminal only |
| `devin` | Devin CLI | no (docs only) | flags `-p`, `--print` | `--resume <id>` (alias `-r`) | no | terminal only |
| `droid` | Factory Droid | no (docs only) | subcommand `exec` | `--resume <id>` | no | terminal only |
| `gemini` | Gemini CLI | no (docs only) | flags `-p`, `--prompt` | `--resume <id>` (alias `-r`) | **yes** — `--session-id <uuid>` | conversation + terminal |
| `grok` | Grok CLI | **yes** | flags `-p`, `--single`, `--prompt-file`, `--prompt-json` | `--resume <id>` (alias `-r`) | **yes** — `-s` / `--session-id`, **launch only** | conversation + terminal |
| `mimo` | Mimo Code | no (docs only) | subcommand `run` | `--session <id>` (alias `-s`) | no | terminal only |
| `omp` | oh-my-pi | no (docs only) | flags `-p`, `--print` | `--resume <id>` (aliases `-r`, `--session`) | no | terminal only |
| `opencode` | OpenCode | no (docs only) | subcommand `run` | `--session <id>` (alias `-s`) | no | terminal only |
| `pi` | Pi Coding Agent | no (docs only) | flags `-p`, `--print` | `--session <id>` | **yes** — `--session-id <uuid>` | conversation + terminal |

**verified = yes** means someone ran `<bin> --help` on a real installation and
transcribed the flags from that output. It corresponds to `verified = true` in
the TOML and to the `verified` column of `tether agents`.

**verified = no (docs only)** means the row was written from vendor
documentation or from reports, and has not been confirmed against a running
binary. The row still works — the router uses it either way — but treat the
restore column as a claim, not a guarantee. `devin` and `droid` both carry an
explicit `notes` warning that their behaviour on a *missing* session id is
unknown. A vendor that silently starts a fresh conversation instead of erroring
is exactly the silent failure this project exists to prevent. Probe before you
rely on it.

Note that `verified` is display metadata only. It appears in `tether agents`
and in `tether agents --json`; no routing or restore decision reads it.

### Secondary classification data

These columns matter when you are debugging why an invocation took an
unexpected lane.

| agent | info flags | management subcommands | interactive override | vendor background flag | value flags (consume the next token) |
|---|---|---|---|---|---|
| `agy` | `-h` `--help` `--version` | — | `-i`, `--prompt-interactive` | — | `--prompt` `--conversation` `--model` |
| `claude` | `-h` `--help` `-v` `--version` | `agents` `auth` `doctor` `gateway` `import` `install` `mcp` `project` `ultrareview` | — | `--bg`, `--background` | `--model` `--agent` `--settings` `--add-dir` `--session-id` `--resume` `-r` `--append-system-prompt` `--permission-mode` |
| `codex` | `-h` `--help` `-V` `--version` | `login` `logout` `mcp` `review` | — | — | `-p` `--profile` `-m` `--model` `-c` `--config` |
| `devin` | `-h` `--help` `--version` | — | — | — | `-r` `--resume` `-p` `--print` |
| `droid` | `-h` `--help` `--version` | — | — | — | `--resume` `-s` `--session-id` `--model` |
| `gemini` | `-h` `--help` `-v` `--version` | — | — | — | `-p` `--prompt` `-m` `--model` `--session-id` `-r` `--resume` |
| `grok` | `-h` `--help` `--version` | — | — | — | `-p` `--single` `--prompt-file` `--prompt-json` `-s` `--session-id` `-r` `--resume` `--model` |
| `mimo` | `-h` `--help` `--version` | — | — | — | `-s` `--session` `--model` |
| `omp` | `-h` `--help` `--version` | — | — | — | `-r` `--resume` `--session` |
| `opencode` | `-h` `--help` `--version` | — | — | — | `-s` `--session` `--model` |
| `pi` | `-h` `--help` `--version` | — | — | — | `--session` `--session-id` `--model` |

`codex` additionally declares `resume` as a **session-start** subcommand. It
reads like management traffic but `codex resume <id>` opens a real interactive
session, so it must be tethered rather than passed through.

`claude`'s `--bg` / `--background` is intercepted rather than passed through.
The result is a background agent you can `tether attach` to, which is strictly
more capability than the vendor's own detached mode.

Only `claude`, `grok` and `pi` declare a `continue_flag` (`--continue`). It is
recorded for completeness; no code path currently uses it.

---

## What restore can and cannot recover

There are two independent layers of state, and only one of them belongs to
zellij:

```
zellij session ─── panes, layout, cwd, the command line   ← zellij serializes this
   └── agent    ─── the actual conversation                ← zellij knows nothing about it
```

Restore the outer layer alone and you get a perfectly reconstructed terminal
running a **brand-new, empty** agent. It looks like success. That is the exact
failure this table exists to prevent.

### How the two layers get collapsed onto one identifier

When a vendor exposes a flag that sets the conversation id *before the
conversation exists*, `agent-tether` mints one UUID
(`naming.new_session_id()`) and hands it to both layers: it seeds the zellij
session name and it is prepended to the vendor's argv as
`<set_id_flag> <uuid>` (see `run.py`). That UUID is then written into the
session record as `provider_session_id`.

On `tether restore`, the record's `provider_session_id` is fed to
`Agent.resume_argv()`, which **rebuilds** the command in the vendor's resume
form — `--resume <id>`, `--session <id>`, or `codex resume <id>`, whichever the
TOML declares. The original launch line is deliberately *not* replayed. The
result is reported as `conversation_and_terminal`.

Plain UUID4 is used because `claude` and `gemini` require a valid UUID and
`pi`'s charset is a superset of it, so one format satisfies every vendor that
can be told.

### Only 4 of the 11 shipped agents can do this

`claude`, `gemini`, `pi` and `grok` declare a `set_id_flag`. The other seven do
not, because the vendor mints the id itself, after launch, and never tells the
launching process what it chose.

| agent | why it cannot choose the id |
|---|---|
| `agy` | Its session key is spelled `conversationId`, not `session_id`. No set-id flag — confirmed absent from both the flag listing and the binary's strings. |
| `codex` | `codex --session-id x` errors with "unexpected argument". |
| `devin` | No set-id flag documented. Unverified. |
| `droid` | It accepts `--session-id` as a value-taking flag in some modes, but the row declares no `set_id_flag`. Unverified. |
| `mimo` | No set-id flag; ids come from the vendor. |
| `omp` | It is a fork of `pi` that **dropped** `pi`'s `--session-id`. |
| `opencode` | The server mints `ses_<26 chars>`. We cannot choose it. |

For those seven, `tether restore` reports `terminal_only` with the reason
"`<agent>` never told us its session id", and prints a yellow summary line
counting them. The pane, the layout, the cwd and the command line come back.
The conversation does not — the agent starts fresh, empty.

### The one way a non-set-id agent still gets its conversation back

If **you** supply the id, we capture it. `router.provided_session_id()` scans
argv for any resume flag, resume alias, set-id flag, set-id alias, or the
resume subcommand, and stores whatever token follows it. So:

```
codex resume 01JABC...          # id captured -> restore rebuilds `codex resume 01JABC...`
opencode --session ses_xxxx     # id captured -> restore rebuilds `opencode --session ses_xxxx`
```

Those sessions restore conversation + terminal, because at that point the
identifier is known. The `--flag=value` form is recognised too. The captured id
also seeds the zellij session name, so a vendor-issued `--resume <id>` lands
back on the *same* tether session instead of forking a second one for one
conversation.

An agent with neither `resume_flag` nor `resume_subcommand` could never use
this escape hatch, but every shipped agent declares one.

### What restore never recovers

Scrollback. Because restore rebuilds the command instead of replaying the
serialized one, the resurrected pane starts from a clean screen with the
correct transcript loaded inside the agent. A correct transcript beats a pretty
terminal.

### What happens when the project directory is gone

`tether restore` skips any session whose recorded `cwd` no longer exists, with
reason "cwd is gone". Nothing is launched and nothing is deleted.

---

## Known traps

Each of these is recorded verbatim or near-verbatim in the `notes` field of the
corresponding TOML, and is visible in `tether agents --json`.

**`codex`: `-p` is `--profile`, not `--print`.** Listing `-p` as a headless flag
for codex would silently send interactive sessions down the pass-through lane —
they would run untethered and die with their launcher, with no error anywhere.
`codex` therefore declares `headless_flags = []` and routes on subcommands
(`exec`, `e`, `review`) instead.

**`grok`: `-p` takes a VALUE.** It is the short form of `--single`, unlike
claude's boolean `-p`. It appears in both `headless_flags` and `value_flags`.

**`grok`: `--session-id` is LAUNCH ONLY, and its own help says it does not
resume.** The string from `grok --help` is: *"Does not resume existing sessions
- use --resume"*. Replaying the original launch line on restore would therefore
create a brand-new empty session that looks like a successful restore. This is
why restore rebuilds in the `--resume` form and never replays. The TOML records
it as `set_id_launch_only = true`.

**`gemini`: fails loudly, which is the good kind of failure.** It is reported to
exit fatally on a duplicate `--session-id` and on a missing `--resume` target.
Contrast with `mimo`, which passes an unknown id through with no
create-if-missing, so a bad restore there is completely silent.

**`agy`: `-i` / `--prompt-interactive` looks headless and is not.** It reads
like a prompt flag but opens a real interactive session. It is declared in
`interactive_flags`, which `router.classify()` evaluates before `headless_flags`
and which wins outright.

**`mimo`: `-p` is `--password` inside `mimo run`.** Not print, not profile. This
is why `mimo` routes on the `run` subcommand and declares no headless flags at
all.

**`omp` is oh-my-pi (omp.sh), NOT Oh My Posh.** It is a fork of `pi`. It dropped
`pi`'s `--session-id`, and here `--session` is a plain alias of `--resume`
rather than a set-id flag.

**`pi`: the transcript path is its identity.** `pi`'s `--session-id` creates if
missing (it is idempotent), and `-r`/`--resume` takes no value at all — it opens
a picker. The TOML sets `id_is_path = true` to record that identity comparisons
must be path-aware. Be aware that no code currently reads `id_is_path`; it is
declarative metadata only, and restore still rebuilds `--session <uuid>` with
the plain UUID it minted. See [CONFIG.md](CONFIG.md), "Fields that are parsed
but not yet acted on".

**`droid`: `-r` is mode dependent.** It means `--resume` interactively and
`--reasoning-effort` under `droid exec`. Only the long form `--resume` is
declared, so the ambiguity can never be hit.

**`opencode`: prints "Session not found" on a miss.** Visible, but it is text on
the screen inside the pane, not a non-zero exit from the shim.

---

## Adding an agent we do not ship

Write one TOML file. Put it in `~/.config/agent-tether/agents.d/` — the same
path on Windows, macOS and Linux; see [CONFIG.md](CONFIG.md) for why.

```toml
schema = 1

[agent]
name           = "myagent"
display_name   = "My Agent CLI"
headless_flags = ["-p", "--print"]
info_flags     = ["-h", "--help", "--version"]
resume_flag    = "--resume"
value_flags    = ["--resume", "--model"]
```

Then:

```
tether agents             # confirm it appears, with source "user"
tether install myagent    # generate the shim
tether explain myagent    # confirm the lane it would take
tether doctor             # confirm the file parsed and the shim shadows
```

An agent with no TOML at all is still tetherable: `Registry.get()` falls back to
`Agent(name=name, binary=name, source="fallback")`. That fallback cannot
classify any of the vendor's flags and can never recover its conversation, so
every invocation is judged purely on the TTY test. The TOML is what buys you
correct lane routing and restore.

The full field reference, the layering rules, worked examples and the error
behaviour are in [CONFIG.md](CONFIG.md).
