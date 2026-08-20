# Design

Why the pieces are shaped the way they are. Each section states the decision, the failure it prevents, and the evidence.

---

## 1. A router, not a wrapper

**Decision.** The shim classifies every invocation into one of three lanes and only tethers one and a half of them.

**Failure it prevents.** `claude -p "summarize this"` returns text on stdout. Wrapping it in a terminal multiplexer returns a TUI. The caller gets garbage, exit codes stop meaning anything, and *nothing errors* — it just quietly produces the wrong thing.

**Why the TTY test.** A human at a terminal has a console; a spawned process does not. `[Console]::IsOutputRedirected` is the cheapest reliable discriminator, and it needs no cooperation from the caller.

**Why not just look for `-p`.** Because `-p` does not universally mean "print":

| Vendor | `-p` means |
|---|---|
| claude, agy, devin, pi, omp | `--print` — headless |
| **codex** | `--profile` — **not** headless |
| **grok** | `--single`, and it **takes a value** |
| **mimo run** | `--password` |

A generic rule misfires on three vendors, so every row in `agents.json` is per-vendor. `agy -i/--prompt-interactive` also *looks* headless and is not, which is why `interactiveFlags` overrides `headlessFlags`.

---

## 2. One id, two layers

**Decision.** When a vendor allows it, mint one UUID and give it to both zellij (as the session name seed) and the agent (via its set-id flag).

**Failure it prevents.** There are two independent layers of state:

```
zellij session ─── panes, layout, cwd, command line   ← zellij serializes this
   └── agent    ─── the conversation                   ← zellij knows nothing about it
```

Restore the outer layer alone and you get a perfectly restored terminal running a brand-new empty agent. It *looks* like success. That is the failure mode this whole design exists to avoid.

**Coverage is the hard limit.** Only 4 of 12 vendors let you choose the id up front:

| Can choose id | Cannot — vendor mints it after launch |
|---|---|
| claude `--session-id` | codex, agy, opencode, droid, devin, mimo, omp |
| gemini `--session-id` | |
| pi `--session-id` (idempotent, creates if missing) | |
| grok `-s/--session-id` — **launch only** | |

For the other eight, the id would have to be *captured after launch* by reading the vendor's hook output or transcript store — the approach Orca takes. `agent-tether` does not do this yet, and reports those restores as `TERMINAL ONLY` rather than implying success.

---

## 3. Restore rebuilds, it does not replay

**Decision.** `tether restore` reconstructs the launch command in the vendor's **resume** form from the registry, instead of letting zellij replay the original serialized command.

**Failure it prevents.** Replay is the silent-empty-restore trap. Evidence, from the vendors' own help text:

- **grok** `--session-id`: *"Does not resume existing sessions — use --resume / --continue instead."* Replaying the original launch line does exactly the wrong thing.
- **gemini**: exits fatally on a duplicate `--session-id`.
- **mimo**: passes an unknown id straight through with no create-if-missing — the failure is completely silent.

**The cost, stated plainly.** A rebuilt session does not carry the old scrollback. That is a deliberate trade: a correct transcript beats a pretty terminal. Sessions we cannot rebuild fall back to zellij resurrection, which preserves scrollback but starts a fresh agent — and says so in the output.

---

## 4. Keybinds live in a config file, because they cannot live anywhere else

**Decision.** `zellij/tether.kdl` is passed with `zellij --config`, and holds exactly one thing that could not be a CLI argument.

**Evidence.** In zellij 0.44.3, a `keybinds` block inside a *layout* file is parsed, validated, and then **discarded**. `Keybinds::from_string` has zero callers, and `input/layout.rs` contains no reference to keybinds at all. Only `Config::from_kdl` — the config-file path — merges them.

This is worth stating because it fails deceptively: an invalid action inside a layout's keybinds block *does* abort startup, which makes the block look live when it is not. Parsing is not application.

**Why `shared` and not `locked`.** A tethered session has no status bar, so you cannot see which input mode you are in, so any mode-*dependent* binding is unusable — you cannot know whether it will fire. `shared` covers every mode. It also overrides zellij's stock `Ctrl q = Quit`, which sits in `shared_except "locked"`: harmless while locked, then kills the session the instant you unlock.

---

## 5. Naming by lane

| Lane | Name | Why |
|---|---|---|
| id present in argv | `tether-<agent>-<hash(id)>` | a vendor-issued `--resume <id>` lands back on the **same** tether session instead of forking a new one |
| human | `tether-<agent>-<cwd-leaf>-<hash(cwd)>` | `claude` in a project is idempotent — you always return to that project's agent |
| agent | `tether-<agent>-<uuid>` | an orchestrator may want six in one repo |

## 6. Arguments are never bound by PowerShell

The shim passes the agent name in an environment variable and lets the caller's arguments land in `$args` untouched.

**Failure it prevents.** PowerShell binds and *prefix-matches* script parameters. `claude --version` would have `--version` matched against `-Verbose`/`-Version` and rejected as ambiguous before any of our code ran. Since the tool shims arbitrary third-party CLIs, any vendor flag could collide with a PowerShell parameter name. Out-of-band is the only safe channel.

For pass-through the shim goes further and recovers the caller's **raw command-line text**, handing it to the child verbatim via `ProcessStartInfo.Arguments`, so quoting is never re-derived.

## 7. Liveness comes from socket markers, not `zellij ls`

`zellij ls -s` lists resurrectable sessions too. Worse, on Windows the resurrection cache lives under `%LOCALAPPDATA%` and **cannot** be redirected — `ZELLIJ_SOCKET_DIR` isolates live sessions only — so sessions from other socket namespaces leak into the listing.

A session is live if and only if it has a socket marker in *our* socket directory. `tether ls` still shells out to `zellij ls` first, because that is what probes each socket and reaps the stale markers a crash leaves behind.

## 8. Roadmap

1. Capture-after-launch session ids for the eight vendors that mint their own, closing the `TERMINAL ONLY` gap.
2. POSIX port (`sh` shims + a portable router).
3. Verify `droid` and `devin` restore behaviour empirically.
4. Investigate whether hook-based orchestrator status survives tethering (see `ORCA.md`).
