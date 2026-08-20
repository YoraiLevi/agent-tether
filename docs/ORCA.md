# Orchestrator compatibility (Orca)

Orca is the first real consumer of `agent-tether`, and the reason it exists: today Orca must stay running, because restoring a session is something you do *through its UI*. Tethering the agent removes that constraint.

This document records what was found by reading Orca 1.4.177's shipped code on Windows. Claims are marked **confirmed** (read in code) or **unconfirmed** (inferred, needs an empirical check). Do not treat the unconfirmed ones as facts.

---

## 1. The problem you must decide about before adopting

**Orca resolves "which agent is running in this pane" by walking the pane's process-tree descendants and intersecting them with the pane's ConPTY console process list.** *(confirmed: `out/main/daemon-entry.js` ~1500; `queryWindowsProcessDescendants`; `readWindowsConptyProcessIds`)*

A tethered agent fails **both** filters:

```
pane shell (ConPTY A)
  └── zellij client          <- descendant, in ConPTY A
        ⇢ (IPC, not a child)
zellij server                <- NOT a descendant, NOT in ConPTY A
  └── claude                 <- the agent Orca is looking for
```

The agent is deliberately outside the pane's process tree — that is the entire feature — so a process-tree scan cannot see it.

**Expected symptom:** the pane looks correct and works normally, but Orca's sidebar shows no running agent for it after a short grace period. There is a `STARTUP_AGENT_FOREGROUND_BOOTSTRAP_MS = 5000` window seeded from the startup command string, so the **first five seconds look right, then the status collapses**. *(confirmed in code; the exact user-visible end state is **unconfirmed**.)*

**What still works regardless:** the agent keeps running, survives Orca quitting, and can be reattached from any terminal. Durability is unaffected. What degrades is *Orca's live status display*.

### Mitigations, in order of preference

1. **Hook-based status is independent of the process scan and should survive** — it is env-driven, not tree-driven (see 2). If your sidebar rows come from hooks, they may well keep working. **Unconfirmed**; this is the first thing to test.
2. **Keep the process name recognizable.** Orca maps a process to an agent by basename with `.exe/.cmd/.bat/.ps1` stripped *(confirmed: `out/shared/agent-process-recognition.js` ~53)*. A shim named `claude.cmd` normalizes to `claude` and **is** recognized. Never rename the shims.
3. **Use `-PathPosition none`** and shim only the agents you actually want tethered, so unshimmed ones keep Orca's status intact.

---

## 2. The environment contract

Orca identifies a pane **entirely by environment variables** planted at PTY spawn and echoed back by each hook POST. *(confirmed: `buildTerminalWorkspaceEnv`, `agentHookServer.buildPtyEnv`)*

The hook script **silently `exit /b 0`** if `ORCA_AGENT_HOOK_PORT`, `ORCA_AGENT_HOOK_TOKEN`, or `ORCA_PANE_KEY` is empty. Drop one and there is no error — just a permanently idle-looking pane. This is why `agent-tether` captures and replants them.

Minimum set that must survive into the session:

```
ORCA_PANE_KEY            "<tabId>:<leafId>", both UUIDs
ORCA_TAB_ID
ORCA_WORKTREE_ID         "<repoId>::<path>"
ORCA_AGENT_LAUNCH_TOKEN  per-launch UUID; SHA-256'd and compared on ingest
ORCA_AGENT_HOOK_PORT
ORCA_AGENT_HOOK_TOKEN
ORCA_AGENT_HOOK_ENV
ORCA_AGENT_HOOK_VERSION
ORCA_AGENT_HOOK_ENDPOINT   <- propagate this above all others, see below
ORCA_TERMINAL_HANDLE       <- orca-cli run INSIDE a pane needs this
```

`agent-tether` carries every `ORCA_*` variable wholesale, which covers these and the per-vendor routing vars (`ORCA_CODEX_HOME`, `ORCA_OPENCODE_CONFIG_DIR`, `ORCA_GROK_HOME`, …).

**Verified on a live Orca pane:** the registry record written by the shim captured `ORCA_PANE_KEY`, `ORCA_TAB_ID`, `ORCA_WORKTREE_ID`, `ORCA_AGENT_LAUNCH_TOKEN`, all five hook vars, and `ORCA_TERMINAL_HANDLE`.

### Token rotation is self-healing — if one variable survives

`ORCA_AGENT_HOOK_PORT` and `ORCA_AGENT_HOOK_TOKEN` are re-minted **every Orca boot**, and written to an endpoint file:

```
%APPDATA%\orca\agent-hooks\endpoint.cmd
    set ORCA_AGENT_HOOK_PORT=52583
    set ORCA_AGENT_HOOK_TOKEN=9213c6c9-...
    set ORCA_AGENT_HOOK_ENV=production
    set ORCA_AGENT_HOOK_VERSION=1
```

The Windows hook script re-reads it on **every** invocation (`call "%ORCA_AGENT_HOOK_ENDPOINT%"`), so a session that outlives Orca self-heals its port and token — **provided `ORCA_AGENT_HOOK_ENDPOINT` (a stable path) survives.** That makes it the single most important variable to propagate.

> **POSIX caveat for the future port** *(confirmed in code)*: the POSIX helper refuses to overwrite a non-empty existing value and bails if the file's port disagrees with a non-empty `ORCA_AGENT_HOOK_PORT`. A stale non-empty port therefore **blocks** self-healing. A POSIX port of this tool should *unset* PORT/TOKEN before exec so the file always wins.

---

## 3. Why a name-based shim works here at all

Orca does **not** exec the agent binary. It spawns a shell PTY and delivers the agent command as **text typed into the pane** *(confirmed: `out/main/daemon-entry.js` ~273)*:

```
cd /d "C:\Users\you\source\myrepo" && claude --resume 9602334f-...
```

So the shim is found by ordinary name resolution inside the pane's shell. No `spawn()` interception is needed — which is what makes this approach viable.

**Corollary:** `cwd` arrives via `cd`, not via the spawn call, so the shim must read it from the process working directory. It does.

## 4. Do not break command position

Orca rewrites Claude's argv on resume: it finds the `claude` token, strips any existing `--resume/-r/--continue/-c` selector, and splices in exactly one authoritative selector *(confirmed: `out/shared/agent-resume-launch-command.js` ~75)*.

Two consequences:

- `isClaudeExecutableToken` is `/^claude(\.(exe|cmd|bat|ps1))?$/i` on the basename — a shim named `claude.cmd` **matches**.
- The token is only accepted in **command position**. If a wrapper rewrote the line into `zellij ... -- claude`, the guard fails *open*: the base command is left alone and the selector is appended to the end, landing on `zellij`'s argv instead of the agent's.

`agent-tether` never rewrites the launch line. The shim *is* the command, in command position, and all zellij invocation happens inside it. Keep it that way.

## 5. Two premise-level findings worth your own check

**(a) Orca already runs a detached terminal daemon on Windows.** `%LOCALAPPDATA%\Orca\daemon-host\<version>\orca-terminal-daemon.exe` owns the PTYs; the Electron app attaches and detaches as a client, with tombstones and PTY recovery. *(confirmed the daemon exists; **unconfirmed** whether it survives app quit and for how long.)*

If it does survive, part of the "Orca must stay running" premise is already false on Windows, and the value of tethering shifts from *durability* toward *addressability from outside Orca*. **Worth thirty minutes of empirical checking before building further on the premise.**

**(b) Orca already installs PATH shims of its own** — `ORCA_AGENT_TEAMS_SHIM_DIR` (`~/.orca/claude-agent-teams-bin`, prepended to PATH) and `ORCA_ATTRIBUTION_SHIM_DIR` (`git`/`gh` wrappers using `ORCA_REAL_GIT`/`ORCA_REAL_GH`). The shim *concept* is therefore already compatible with how Orca works — and `ORCA_REAL_<COMMAND>` is a naming convention worth mirroring.

**PATH ordering hazard** *(confirmed)*: Orca rewrites PATH-like keys and on Windows collapses multiple case-variant `PATH`/`Path` keys into one. A shim directory injected via a differently-cased key can be silently dropped. Install through the normal user PATH, as `install.ps1` does.

## 6. Other behaviour changes to expect

| Area | Change |
|---|---|
| Closing a tab | Orca kills the pane process tree (`taskkill /T /F`). A tethered agent is outside that tree and **survives**. That is the feature, but it silently changes Orca's "close tab = stop agent" contract. |
| Terminal titles | Orca parses OSC 0/1/2 and already understands multiplexer `outer \| inner` titles *(confirmed: `out/shared/terminal-title-wrapper-segments.js`)*. Whether zellij's title output matches that convention is **unconfirmed**. |
| Codex preflight | `ORCA_CODEX_LAUNCH_PREFLIGHT` runs in the shell's argv **before** the shim; it and `ORCA_CODEX_LAUNCH_PREFLIGHT_CMD_QUOTE` must survive. Carrying all `ORCA_*` covers this. |
| Fire-and-forget | Orca treats a returning command as "the agent exited" and retires the pane identity within ~1 s. The human lane therefore **blocks** for the session's lifetime, by design. Only the no-TTY agent lane returns immediately. |
| zellij awareness | Orca has none — one unrelated comment mentions it. There is no zellij process recognition or title handling to rely on. |

## 7. Suggested first empirical test

Run this inside a live Orca pane. It dumps the real contract as Orca actually plants it, which beats any static reading:

```cmd
set ORCA_ | sort
```

Then tether one agent, and watch whether the Orca sidebar keeps its live status. That single observation decides how broadly you roll this out.
