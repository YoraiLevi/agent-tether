# Design

Each section states a decision, the failure it prevents, and the evidence. If a
choice here looks over-cautious, it is usually because the failure it avoids is
*silent*.

---

## The governing principle: asymmetric failure

Almost every judgement call resolves the same way, because the two ways of
being wrong do not cost the same:

| Mistake | Cost |
|---|---|
| Wrongly **pass through** | the user loses durability for one call. Obvious, recoverable. |
| Wrongly **tether** | a caller parsing stdout gets a terminal UI, or blocks forever. **Silent**, and it corrupts whatever consumed that output. |

So: **when unsure, pass through.** `router.py` encodes this, and it is why
`_has_tty()` requires *both* stdin and stdout to be terminals — the stricter
test lands on the safe side of the ambiguity.

The same principle governs error handling. `shim.py` catches everything and
falls back to executing the real binary. A bug in this tool must not be able to
take `claude` off someone's machine.

---

## 1. A router, not a wrapper

Three lanes: pass-through, human, agent.

`claude -p "summarize this"` must return text on stdout. Wrapping it returns a
TUI instead, and nothing errors.

**Why not just look for `-p`:**

| Vendor | `-p` means |
|---|---|
| claude, agy, devin, pi, omp | `--print` — headless |
| **codex** | `--profile` — *not* headless |
| **grok** | `--single`, and it **takes a value** |
| **mimo run** | `--password` |

A global rule misfires on three vendors, so classification is per-vendor *data*
(`data/agents.d/*.toml`), not code. `agy -i/--prompt-interactive` also looks
headless and is not, which is why `interactive_flags` override
`headless_flags`.

`tether explain` exists because these rules are non-obvious: the user can ask
what will happen instead of guessing.

---

## 2. One id, two layers

There are two independent layers of state:

```
zellij session ─── panes, layout, cwd, command line   ← zellij serializes this
   └── agent    ─── the conversation                   ← zellij knows nothing about it
```

Restore the outer layer alone and you get a perfectly restored terminal running
a brand-new empty agent. It *looks* like success. **This is the failure mode the
whole tool is shaped around.**

Where a vendor exposes a set-id flag, we mint one UUID and give it to both
layers. Only some vendors allow it (`can_choose_id` in the registry); the rest
are reported as `terminal_only` rather than implied to have worked.

---

## 3. Restore rebuilds, it does not replay

`tether restore` reconstructs the launch command in the vendor's *resume* form
from the registry, instead of letting zellij replay the serialized command.

Evidence from the vendors' own help text:

- **grok** `--session-id`: *"Does not resume existing sessions — use --resume /
  --continue instead."* Replaying the original launch line does the opposite of
  what is wanted.
- **gemini**: exits fatally on a duplicate `--session-id`.
- **mimo**: passes an unknown id straight through with no create-if-missing —
  completely silent.

**The cost, stated plainly:** a rebuilt session does not carry the old
scrollback. A correct transcript beats a pretty terminal. Sessions we cannot
rebuild fall back to zellij resurrection, which keeps scrollback but starts a
fresh agent — and the output says so.

---

## 4. Configuration is data, and layered

Adding an agent is dropping a TOML file. Never a code change, never a release.

```
builtin (in the wheel)  <  ~/.config/agent-tether/agents.d/*.toml
                        <  [agents.<name>] in config.toml
                        <  TETHER_TARGET_<AGENT>
```

Merging is **per key**, so a drop-in can override one field and inherit the
rest — the systemd drop-in / `conf.d` pattern. It survives upgrades: we can ship
a better builtin without clobbering a customisation.

**A malformed file is skipped and reported, never raised.** The shim path runs
on every agent invocation on the machine; a config parse error that propagated
would break all of them at once, which is far worse than one unrecognised
agent. `tether doctor` surfaces what was refused.

`schema = N` gates the format: a file from a future version is skipped rather
than misinterpreted.

---

## 5. XDG paths on every OS

`~/.config`, `~/.local/share`, `~/.local/state`, `~/.cache` — on Windows and
macOS too, not `%APPDATA%` or `~/Library/Application Support`.

The agent CLIs already do this (Claude Code installs to `~/.local/bin` on
Windows). One layout everywhere means one set of docs, one support answer, and
config files that can be copied between machines unchanged. `XDG_*` variables
are honoured on all platforms.

**Shims are the exception, and it is instructive.** They go in
`~/.local/share/agent-tether/shims`, *not* `~/.local/bin`, because a shim
cannot live in the same directory as the binary it shadows: within one
directory Windows resolves `PATHEXT` before anything else, so `claude.exe`
beats `claude.cmd` regardless of intent. `PATH` order only breaks ties
*between* directories.

---

## 6. Shims, recursion, and argument fidelity

A shim is a two-line launcher that sets one environment variable and execs the
real entry point. All logic lives in Python, so upgrading the package upgrades
every shim without regenerating them.

**Recursion** — a file called `claude` must find the *other* `claude`. Two
independent defences, because either alone has a hole:

1. same-file detection (`os.path.samefile`, exact through symlinks and
   hardlinks)
2. a marker string in every generated shim, which also catches shims from older
   installs

Skipping only *our* shims is what makes chaining work: if another tool also
shadows `claude`, it stays reachable through us.

**Argument fidelity** differs by platform:

- POSIX: `"$@"` preserves argv exactly, and `exec` replaces the shell so no
  process sits between caller and agent.
- Windows: `%*` re-expands the command line and Python re-parses it with
  `CommandLineToArgvW` — one parse, by the same parser every native program
  uses.

`tests/smoke.py` verifies this end to end against a fake agent that echoes its
argv, covering empty strings, embedded quotes, trailing backslashes, shell
metacharacters and non-ASCII.

**`os.execv` is POSIX-only in effect.** There it replaces the process, so pid,
terminal, process group and signal handling are untouched. On Windows it
spawns and the parent exits immediately, which would lose the exit code and
confuse a waiting parent — so Windows spawns and waits instead.

---

## 7. Liveness comes from socket markers

`zellij ls` is not a liveness check. It lists resurrectable sessions too, and
zellij's resurrection cache lives in the OS cache directory and **cannot be
redirected on Windows** — so sessions from other socket namespaces appear in it.
Using it would report a dead session, or someone else's, as live.

A session is live iff it has a socket marker in *our* socket directory. We still
shell out to `zellij ls` first, because that is what probes each socket and
reaps the stale markers a crash leaves behind.

---

## 8. Keybinds must live in a config file

`zellij/tether.kdl` exists for exactly one thing.

In zellij 0.44.3 a `keybinds` block inside a *layout* is parsed, validated, and
then **discarded**: `Keybinds::from_string` has zero callers and
`input/layout.rs` never mentions keybinds. Only `Config::from_kdl` merges them.

This fails deceptively — an invalid action inside a layout's keybinds block
*does* abort startup, which makes the block look live when it is not. Parsing
is not application.

The binding is `shared` (all modes) rather than mode-specific, because a
tethered session has no status bar, so the user cannot see which mode they are
in and a mode-dependent binding is unusable. It also overrides zellij's stock
`Ctrl q = Quit`, which is harmless while locked and kills the session the
instant you unlock.

---

## 9. Naming

| Lane | Name | Why |
|---|---|---|
| id in argv | `tether-<agent>-<hash(id)>` | a vendor `--resume <id>` lands on the **same** session instead of forking one |
| human | `tether-<agent>-<leaf>-<hash(cwd)>` | `claude` in a project is idempotent |
| agent | `tether-<agent>-<uuid>` | an orchestrator may want six in one repo |

`cwd` is resolved through symlinks and case-folded first. Without that, `/proj`
and a symlink to it — or `C:\Proj` and `c:\proj` — would silently become two
sessions, giving the user two agents each holding half the context.

---

## 10. Testing strategy

CI runs on Linux, macOS and Windows because this project's entire risk surface
is platform-specific.

| Tier | Needs | Covers |
|---|---|---|
| unit | nothing | lane routing, recursion, naming, config merge, atomic writes |
| smoke | nothing | the real shim end to end against a fake agent |
| integration | zellij | session create/attach/restore |

The dangerous logic is deliberately in the tiers that need **no** zellij and no
vendor CLI, so it is covered identically on all three OSes. A test that can only
run on the maintainer's machine does not protect users.

---

## 11. Known gaps

1. **Capture-after-launch session ids** for vendors that mint their own. This is
   how orchestrators like Orca do it — read the id out of the vendor's hook
   output or transcript store. Closing this would turn most `terminal_only`
   restores into full ones.
2. **`droid` and `devin` restore behaviour** is unverified; neither documents
   what happens when a session id does not exist.
3. **Orchestrator process-tree visibility** — see [ORCA.md](ORCA.md).
4. **Concurrent `restore`** is not locked; running two at once is undefined.
