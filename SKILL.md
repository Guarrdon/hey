---
name: hey
description: >-
  Share text directly from this Claude Code session into a known friend's
  Claude Code session, and check for anything they've shared back. Use when
  the user types "/hey <friend> ...", asks to share/send something to a
  friend, wants to add a friend via a pairing code, or wants to check for
  incoming shares. Backed by a small AWS relay (Lambda + S3) and a local key
  store at ~/.hey/config.json.
---

# hey

Claude-to-Claude text sharing between known friends, via a tiny relay. No
accounts, no server-side identity — each friendship is just a shared secret
("channel key") bootstrapped through a short-lived 6-digit pairing code, never
the raw key itself.

## The tool

A single self-contained script ships with this skill. Always call it by its
absolute path:

```
python3 ~/.claude/skills/hey/hey.py <command> [args]
```

### Commands

| Command | What it does |
|---|---|
| `endpoint [<url>]` | Get, or set, the relay's Function URL |
| `friend-new <name>` | Generate a channel key for a new friendship; prints a one-time 6-digit pairing code to relay out-of-band |
| `friend-add <name> <code>` | Redeem a pairing code you were given; stores the resulting channel key under `<name>` |
| `list` | Known friend names (never prints keys) |
| `send <name>` | Send text (piped via stdin) to a friend |
| `check [<name>]` | Pull anything waiting — all friends, or just one; `--json` for raw output |

## How to use it

- **`/hey <friend> <what>`** — the share command. Resolve `<what>` yourself
  before calling the script:
  - If it names a topic or idea (e.g. "concept of this project"), distill the
    relevant idea from the conversation into a short, self-contained block of
    text — don't just grep for the literal phrase.
  - Otherwise (no specific topic named), default to roughly the last ~50 lines
    of session context, close to verbatim.
  - **Always show the exact resolved text and the recipient's name back to the
    user and wait for explicit confirmation before sending — never send
    silently, no matter which mode resolved the content.** Once confirmed:
    `printf '%s' "<text>" | python3 ~/.claude/skills/hey/hey.py send <friend>`.
  - If `<friend>` isn't in `list`'s output, say so and suggest `friend-new` or
    `friend-add` first — don't guess a name.
- **"add sneaksoft as a friend" / "pair with X"** — if the user is initiating
  the friendship: `friend-new <name>`, then tell them to relay the printed
  code to their friend out-of-band (voice call, text message — anything but
  through this tool, since the relay isn't set up between you yet). If the
  user was given a code by a friend instead: `friend-add <name> <code>`.
- **"check for anything from X" / "did anyone send me something?"** — `check`
  (or `check <name>` for just one friend). Display each returned message's
  text plainly, labeled by who it came from.
- **Cold start** — if `endpoint` prints "no endpoint configured," nothing else
  will work. Ask the user for the relay's Function URL (from deploying
  `template.yaml` in the repo) and run `endpoint <url>` once before continuing.

## Notes

- Channel keys and pairing codes are never displayed except at the moment
  `friend-new` prints a pairing code — that display *is* the handoff
  mechanism. Don't echo keys or codes back to the user in any other context.
- Messages are deleted from the relay the instant `check` retrieves them —
  there's no "mark as read" step, and nothing to manage locally beyond names.
- Text only, one friend at a time, per message. No files, link-attachments, or
  group sharing yet.
