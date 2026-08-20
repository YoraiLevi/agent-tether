# API for orchestrators and agents

Every command below takes `--json`. That output is a contract: it carries a
`schema` integer, and fields are added but not removed or repurposed within a
schema version.

Human output is for humans and may change freely. **If you are a program, always
pass `--json`.**

```json
{ "schema": 1, "...": "..." }
```

---

## Exit codes

| Code | Meaning |
|---|---|
| `0` | success |
| `1` | a problem was found (`doctor`), or the operation failed |
| `2` | bad usage — missing or invalid arguments |
| `4` | the named session does not exist |
| `130` | interrupted |

Anything non-zero from `tether new` means **no session was created**. Do not
parse stdout on a non-zero exit.

---

## Discovering sessions

### `tether ls`

```console
$ tether ls --json
$ tether ls --under ~/src --state live --json
$ tether ls --agent claude --json
$ tether ls --id 9602334f-75fb-45bc-991b-868b958a7951 --json
```

| Filter | Matches |
|---|---|
| `--agent NAME` | one agent |
| `--cwd PATH` | sessions whose project directory is exactly `PATH` |
| `--under PATH` | sessions anywhere beneath `PATH` — "every agent in this repo" |
| `--name TEXT` | substring of the session name |
| `--id ID` | a tether session name **or** a vendor session id |
| `--state live\|recoverable\|gone\|any` | lifecycle state |

`--cwd` and `--under` are resolved through symlinks and case-folded, so the
same project always matches however it was spelled.

```json
{
  "schema": 1,
  "count": 1,
  "sessions": [
    {
      "session": "tether-claude-payments-1a2b3c4d",
      "agent": "claude",
      "state": "live",
      "lane": "human",
      "cwd": "/home/you/src/payments",
      "project": "payments",
      "provider_session_id": "9602334f-75fb-45bc-991b-868b958a7951",
      "created_at": 1755689110.7,
      "can_restore_conversation": true
    }
  ]
}
```

**`can_restore_conversation` is the field to check** before assuming a restore
will bring back history. When it is `false`, a restore recovers the terminal and
the agent starts fresh.

### States

| State | Meaning |
|---|---|
| `live` | running now; attach or drive it |
| `recoverable` | not running, but zellij can resurrect it |
| `gone` | we have a record, nothing to resurrect |

`live` is determined from zellij's socket markers, never from `zellij ls` —
that listing includes dead sessions from other namespaces.

### `tether get <session>`

Accepts a tether session name **or** a vendor session id. Exits `4` if unknown.
Adds `argv` and `binary` to the record above.

---

## Creating

### `tether new <agent> [args…]`

```console
$ tether new claude --cwd ~/src/api
tether-claude-a41c9e2b
```

Creates a **detached** session and prints **exactly one line**: its id.
Everything else goes to stderr. Exit `0` means the session is live and
addressable.

Pass vendor arguments after the agent name:

```console
$ tether new claude --cwd ~/src/api -- --model opus
```

Spawning the agent CLI directly works too — with no TTY it takes the same lane
and prints the same single line:

```console
$ claude --bg          # or any spawn without a terminal
tether-claude-a41c9e2b
```

---

## Driving

```console
$ tether read <session> --json        # current screen contents
$ tether send <session> "run tests"   # type into it, submits with Enter
$ tether send <session> "text" --no-enter
$ tether attach <session>             # hand it to a human (blocks)
```

`send` writes to the pane's stdin. It **bypasses zellij's keybinding layer**, so
it cannot trigger key bindings — only send input to whatever is reading.

`read` returns what is on screen now, not the full scrollback.

---

## Lifecycle

```console
$ tether restore --json           # after a reboot; add --dry-run to preview
$ tether reap --dry-run           # preview; --all also drops recoverable ones
$ tether kill <session>
```

`restore` reports one object per session:

```json
{
  "schema": 1,
  "restored": [
    { "session": "tether-claude-payments-1a2b3c4d", "result": "conversation_and_terminal" },
    { "session": "tether-codex-api-4b5c6d7e", "result": "terminal_only",
      "reason": "codex never told us its session id" },
    { "session": "tether-agy-old-1122", "result": "skipped", "reason": "cwd is gone" }
  ]
}
```

| `result` | Meaning |
|---|---|
| `conversation_and_terminal` | fully restored, history intact |
| `terminal_only` | **the agent starts fresh** — treat as a new conversation |
| `already_live` | nothing to do |
| `skipped` | see `reason` |

Restore **rebuilds** the launch command in the vendor's resume form rather than
replaying the original. Replaying is the silent-empty-restore trap: grok's
`--session-id` explicitly does not resume, and gemini exits fatally on a
duplicate. The trade-off is that a rebuilt session does not carry the old
scrollback.

---

## Introspection

```console
$ tether agents --json      # known CLIs, what restore can do for each, config errors
$ tether doctor --json      # install state; exits 1 if anything is wrong
$ tether paths --json       # every resolved location
$ tether explain codex -p x # which lane an invocation takes, and why
```

`doctor --json` is the right pre-flight for an automated setup: check
`problems` is empty.

---

## Recipes

**Find or create an agent for a repo** — idempotent, no id bookkeeping:

```bash
id=$(tether ls --cwd "$repo" --agent claude --state live --json \
     | jq -r '.sessions[0].session // empty')
[ -z "$id" ] && id=$(tether new claude --cwd "$repo")
```

**Wait for output to settle:**

```bash
prev=""
while :; do
  cur=$(tether read "$id" --json | jq -r .screen)
  [ "$cur" = "$prev" ] && break
  prev=$cur; sleep 5
done
```

**Reap everything finished under a tree:**

```bash
tether ls --under ~/src --state gone --json \
  | jq -r '.sessions[].session' \
  | xargs -rn1 tether kill
```

**Refuse to trust a restore that lost history:**

```bash
tether restore --json \
  | jq -e '[.restored[] | select(.result=="terminal_only")] | length == 0' \
  || echo "some agents came back empty - re-brief them"
```

---

## Environment

| Variable | Use |
|---|---|
| `TETHER_DISABLE=1` | bypass the shim entirely for this process |
| `TETHER_FORCE_LANE=agent` | force a detached session even with a TTY |
| `TETHER_QUIET=1` | suppress the `[tether]` banner |
| `TETHER_HOME` | relocate all state (useful for isolated test runs) |
| `TETHER_TARGET_<AGENT>` | force which binary a shim chains to |

Full list: [CONFIG.md](CONFIG.md).

---

## Stability

`schema: 1` is current. Within a version: fields may be **added**; existing
fields will not be removed or change meaning. Ignore unknown fields.
