---
name: tether-sessions
description: Find, create, inspect and drive durable agent-CLI sessions through the `tether` command. Use whenever you need to run a coding agent (claude, codex, gemini, agy, grok...) so it OUTLIVES your own process, to discover agents already running in a repo or directory, to read or send input to a running agent session, to hand a session to a human, or to recover sessions after a host crash. Trigger on phrasings like "spawn an agent in that repo", "is anything already working on this", "what agents are running", "check on that session", "take over the agent", "restore my sessions", or any task where an agent must keep working after you exit.
---

# Driving tethered agent sessions

`tether` wraps agent CLIs in durable zellij sessions. A tethered agent keeps
running after the process that started it exits, and can be found and driven
later by id, by directory, or by agent name.

**Always pass `--json`.** Human output is not a contract; JSON is, and it
carries a `schema` integer.

## Before anything else

```bash
tether doctor --json
```

Exits non-zero if the install is broken. If it fails, say so and stop —
`tether` commands will not behave.

## Find before you create

Creating a second agent for a repo that already has one wastes work and splits
context. Look first:

```bash
tether ls --under "$REPO" --state live --json
tether ls --cwd "$DIR" --agent claude --state live --json
tether ls --id "$SESSION_OR_VENDOR_ID" --json
```

Find-or-create, idempotent:

```bash
id=$(tether ls --cwd "$repo" --agent claude --state live --json \
     | jq -r '.sessions[0].session // empty')
[ -z "$id" ] && id=$(tether new claude --cwd "$repo")
```

## Create

```bash
id=$(tether new claude --cwd /path/to/repo)
id=$(tether new claude --cwd /path/to/repo -- --model opus)
```

Prints **exactly one line**: the session id. A non-zero exit means nothing was
created — do not parse stdout in that case.

## Inspect and drive

```bash
tether get "$id" --json          # full record; exit 4 if unknown
tether read "$id" --json         # what is on screen right now
tether send "$id" "run the tests"
tether send "$id" "text" --no-enter
```

`read` returns the visible screen, not full scrollback. `send` writes to the
pane's stdin and **cannot** trigger key bindings.

To watch for completion, poll `read` until the screen stops changing rather
than sleeping a fixed time.

## Hand it to a human

```bash
tether attach "$id"     # blocks; the human presses Ctrl+q to detach
```

Tell the human the id and that `Ctrl+q` leaves it running.

## Recover after a crash

```bash
tether restore --json
```

**Check every result before trusting history:**

| `result` | Meaning |
|---|---|
| `conversation_and_terminal` | fully restored |
| `terminal_only` | **the agent is brand new and empty** — re-brief it |
| `already_live` | nothing to do |
| `skipped` | see `reason` |

`terminal_only` looks identical on screen to a real restore. Never assume a
restored agent remembers anything unless the result says
`conversation_and_terminal`, or the session record says
`can_restore_conversation: true`.

## Clean up

```bash
tether kill "$id"
tether reap --dry-run
tether reap
```

Reap sessions you created once their task is done; otherwise listings fill with
dead entries and the next agent cannot tell what is real.

## Rules

- Prefer `--under`/`--cwd` filters over listing everything and matching by hand;
  they resolve symlinks and case, so they match a project however it is spelled.
- Check `can_restore_conversation` before assuming a session has history.
- Never kill a session you did not create without asking.
- If `tether` is not installed, say so — do not fall back to running the agent
  CLI directly and calling it durable, because it will not be.

Full reference: `docs/API.md` in https://github.com/YoraiLevi/agent-tether
