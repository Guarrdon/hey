# Hey — Design

Claude-to-Claude sharing: push text from one Claude Code session directly into a known friend's session (and eventually back), via a small relay both sides can reach. Dead simple by design — no accounts, no pairing protocol, just a shared key per friendship.

## Scope (v0)

- Text only. No files, attachments, or rich content.
- One-way sending now; two-way is the same mechanism run in both directions (no protocol change needed later).
- On-demand checking (`/check`) now; periodic auto-checking later via Claude Code's `loop`/`schedule` skills polling the same endpoint — not new infra.
- Adding a friend is a manual, one-time, out-of-band step — you hand them a code the same low-tech way you'd share a phone number. Not a server-side handshake.
- Domain: deployed and live at `https://api.sendingwith.com` (a domain already owned in this AWS account, dedicated to this — no competing UI/site).

## Architecture

```
[Your Claude Code]  --/hey-->  [api.sendingwith.com (HTTP API)]  --write-->  [S3 bucket, lifecycle TTL]
[Friend's Claude Code] <--/check--  [api.sendingwith.com (HTTP API)]  <--read--  [S3 bucket]
```

- **Relay:** AWS Lambda behind an API Gateway HTTP API with a regional custom domain (`api.sendingwith.com`). Started as a plain Lambda Function URL, but a custom domain requires either API Gateway or CloudFront in front of it — HTTP API was picked over CloudFront because it deploys faster (no CDN propagation wait) and its payload format (v2.0) is identical in shape to a Function URL's, so the handler code needed zero changes. Chosen over Cloudflare because the domain and AWS familiarity already live there.
- **Custom domain wiring:** an ACM certificate (DNS-validated via the Route53 hosted zone), an `AWS::Serverless::HttpApi` with an inline `Domain` block (which auto-creates the API Gateway domain-name resource, base path mapping, and Route53 alias record), and the Lambda wired to it via a proxy (`ANY /{proxy+}`) event. All defined in `template.yaml`.
- **Storage:** S3. Messages and pairing codes are objects, not rows in a database.
  - **TTL: 14 days** via an S3 Lifecycle rule on the bucket, as a backstop against orphaned data.
  - **Delete-on-read:** the Lambda explicitly deletes an object immediately after a successful read (message delivered via `/check`, or a pairing code redeemed via `/pair/<code>`). The Lifecycle rule only ever catches what's never picked up.
  - **Pairing codes get a shorter effective lifetime (24h)** enforced by the Lambda checking the object's creation timestamp against a max-age, since S3 Lifecycle rules operate in day-granularity, not hours — the bucket-level rule is the 14-day backstop, the code's real 24h expiry is enforced in application logic.
- **Server has no identity concept at all.** No accounts, no registration, no slugs. It only ever sees a shared key per relationship and does whatever that key is allowed to do.

## Key model — one tier

- **Channel key:** a shared secret between exactly two people, generated once per friendship. It directly authorizes both read and write on that relationship's message channel: `inbox/<hash(channel_key)>/<msg-id>`. That's the entire auth model — no accounts, no separate "prove who you are" step.
- Whoever sets up the friendship first generates the key; it's handed to the friend out-of-band via a short pairing code (see below) — the raw key itself is never spoken or typed by a human.
- Never surfaced by the skill in normal day-to-day use (`/hey`, `/check` use it silently).

## Local client config

`~/.hey/config.json` (portwarden-style):

```json
{
  "friends": {
    "sneaksoft": { "channel_key": "..." }
  }
}
```

A friend's local name maps straight to the channel key you share with them. No account key, no slug, no status field — there's nothing to be pending on.

## Adding a friend

No global setup step, because there's no account to register. The handoff goes through a short-lived **pairing code**: 6 digits, expires after 24 hours, single-use.

1. `/friend-new sneaksoft` — generates the real channel key locally, stores it under "sneaksoft," then `POST`s it to the relay stashed behind a random 6-digit code (24h effective TTL, enforced in Lambda). Prints just the code — easy to say over the phone or text.
2. Sneaksoft runs `/friend-add matt <code>` — client redeems the code against the relay (`GET /pair/<code>`), which returns the channel key and deletes the pairing object immediately (single-use, same delete-on-read philosophy as messages). Sneaksoft's client stores the channel key locally under "matt."
3. If the code isn't redeemed within 24 hours, the Lambda treats it as expired even though the object may still physically exist until the 14-day Lifecycle rule sweeps it — expired codes are rejected the same as missing ones.

The relay never learns who "sneaksoft" or "matt" are — only that someone holding a valid code once gets to redeem one channel key.

## API (Lambda routes, via `https://api.sendingwith.com`)

- `POST /pair` — body `{ channel_key }`, no auth needed (this is the bootstrap step). Generates a 6-digit code, stores `pending/<code> → { channel_key, created_at }` in S3. Returns the code.
- `GET /pair/<code>` — no auth beyond the code itself. Rejects if `created_at` is older than 24h; otherwise returns the channel key and deletes the object immediately (single redemption).
- `POST /send` — body `{ text }`, auth via channel key (header). Writes to `inbox/<hash(channel_key)>/<msg-id>`.
- `GET /inbox` — auth via channel key. Returns all unread objects for that channel and deletes each one immediately after it's returned (delete-on-read).

Four routes total, no persistent identity of any kind.

## Client skill

- `/friend-new <name>` — generate a channel key, store it locally under `<name>`, print the one-time 6-digit pairing code for out-of-band handoff.
- `/friend-add <name> <code>` — redeem a pairing code you were given, storing the resulting channel key locally under `<name>`.
- **`/hey <friend> <what>`** — the share command. `<what>` selects one of two content-resolution modes:
  - **Recent context** — a bounded window of the current session (roughly the last ~50 lines), taken close to verbatim. This is the default when no specific topic is named.
  - **Last concept** — a natural-language pointer (e.g. `/hey sneaksoft concept of this project`) that Claude resolves against the session, distilling the relevant idea into shareable text rather than dumping raw lines.
  - Either way: resolved text is always shown back to you in full, exact form, with the recipient named, before anything sends. `POST /send` only fires on explicit confirmation.
- `/check` — `GET /inbox` for each known friend, display anything waiting.

## Open questions

- **6-digit codes are low-entropy (1-in-a-million) and guessable if unrate-limited** — worth a basic rate limit on `/pair/<code>` (e.g. via a small counter in S3 or a Lambda-level throttle) before this goes beyond a small circle of known friends. Not a v0 blocker, but flagging it honestly — the code's short lifetime and single-use are what actually carry the security weight here, not its length.
- Key rotation/revocation if a channel key is compromised — probably just "generate a new one and re-share it," no protocol needed since the whole scheme is already manual.
- Whether `/check` iterates every known friend's key on each call, or takes an optional `<friend>` argument to check just one.
- Exact boundary of "recent context" (~50 lines is a starting guess — may want to tune once it's actually used).

## Prior art

- portwarden's `~/.portwarden/` registry-file pattern — model for the local contacts/key store here.
