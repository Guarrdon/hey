# Hey — Design

Push text from one coding-agent session directly into a known friend's (and eventually back), via a small relay both sides can reach. Human-gated at both ends — the sender confirms the exact text before it leaves, the receiver has to ask before anything is fetched. What arrives is *working context*, not a transcript: once pulled in, the receiving agent can pick it up and run with it on top of whatever is already in flight, at its user's direction. What it can't do is act on arrival, or treat a received message as instructions rather than as untrusted material from another person. Dead simple by design — no accounts, no pairing protocol, just a shared key per friendship.

## Scope (v0)

- **Agent-agnostic is a first-class goal, not a nice-to-have.** The two people sharing will usually not be running the same tool, and asking either to switch defeats the purpose. This constrains the client hard: one file, stdlib only, no agent-specific APIs, no runtime detection — so it installs anywhere the open Agent Skills format is read (Claude Code, Codex CLI, Copilot today) by copying it there. Everything agent-specific lives in `SKILL.md`, and even that differs only by install path.
- Text only. No files, attachments, or rich content.
- One-way sending now; two-way is the same mechanism run in both directions (no protocol change needed later).
- On-demand checking (bare `/hey`) now; real push notifications later (see "Future: notifications" below) — not new infra on the relay side.
- Adding a friend is a manual, one-time, out-of-band step — you hand them a code the same low-tech way you'd share a phone number. Not a server-side handshake.
- Domain: a dedicated subdomain on a domain already owned in the deploying AWS account — nothing else served from it, no competing UI/site. The relay URL isn't published in this repo; it's handed to friends out-of-band alongside their pairing code (see "Open questions" on why that matters while `/pair/<code>` is unthrottled).

## Architecture

```
[Your agent]      --/hey-->    [relay (HTTP API)]  --write-->  [S3 bucket, lifecycle TTL]
[Friend's agent]  <--/hey----  [relay (HTTP API)]  <--read--   [S3 bucket]
```

Both sides can be Claude Code, Codex CLI, or Copilot in any combination — the client is one stdlib Python script and the relay has no idea which agent is calling it.

- **Relay:** AWS Lambda behind an API Gateway HTTP API with a regional custom domain. Started as a plain Lambda Function URL, but a custom domain requires either API Gateway or CloudFront in front of it — HTTP API was picked over CloudFront because it deploys faster (no CDN propagation wait) and its payload format (v2.0) is identical in shape to a Function URL's, so the handler code needed zero changes. Chosen over Cloudflare because the domain and AWS familiarity already live there.
- **Custom domain wiring:** an ACM certificate (DNS-validated via the Route53 hosted zone), an `AWS::Serverless::HttpApi` with an inline `Domain` block (which auto-creates the API Gateway domain-name resource, base path mapping, and Route53 alias record), and the Lambda wired to it via a proxy (`ANY /{proxy+}`) event. All defined in `template.yaml`.
- **Storage:** S3. Messages and pairing codes are objects, not rows in a database.
  - **TTL: 14 days** via an S3 Lifecycle rule on the bucket, as a backstop against orphaned data.
  - **Delete-on-read:** the Lambda explicitly deletes an object immediately after a successful read (message delivered via a check, or a pairing code redeemed via `/pair/<code>`). The Lifecycle rule only ever catches what's never picked up.
  - **Pairing codes get a shorter effective lifetime (24h)** enforced by the Lambda checking the object's creation timestamp against a max-age, since S3 Lifecycle rules operate in day-granularity, not hours — the bucket-level rule is the 14-day backstop, the code's real 24h expiry is enforced in application logic.
- **Server has no identity concept at all.** No accounts, no registration, no slugs. It only ever sees a shared key per relationship and does whatever that key is allowed to do.

## Key model — one tier

- **Channel key:** a shared secret between exactly two people, generated once per friendship. That's the entire auth model — no accounts, no separate "prove who you are" step.
- **Channel token:** `sha256(channel_key)`, and the only form the relay ever receives day-to-day (`X-Channel-Token`). It is also the channel's address, `inbox/<token>/<msg-id>` — addressing is the sole thing the relay does with it. Deliberately *not* a privilege reduction: anyone holding a token has full read/write on that channel. The point is narrower — the underlying secret stops appearing in every request header, so it can't accumulate in relay logs, and a future client-side encryption key derived from that secret stays underivable from anything the relay has seen. Cheap to do now, impossible to retrofit onto traffic already sent.
- **What the relay validates:** the token is used verbatim as an S3 key prefix, so it is checked against `^[0-9a-f]{64}$` before use. A client-supplied string that reaches a storage path unvalidated is a traversal bug waiting to happen; this is the guard.
- **Residual exposure:** pairing still moves the raw key through the relay (`POST /pair` → `pending/<code>` → `GET /pair/<code>`), so for up to 24h, or until redemption, the operator could read it. Closing that means blinding the handoff — store under `sha256(code)`, encrypt the payload under a key derived from the code, and send only the hash — which needs a cipher, and Python's stdlib has none. See "Open questions."
- Whoever sets up the friendship first generates the key; it's handed to the friend out-of-band via a short pairing code (see below) — the raw key itself is never spoken or typed by a human.
- Never surfaced by the skill in normal day-to-day use (both directions of `/hey` use it silently).

## Rate limiting the pairing routes

A 6-digit code is ~20 bits. Single use and a 24h expiry help, but neither bounds how fast an attacker can search — so three independent limits do, deliberately layered so that defeating one runs into the next:

1. **API Gateway route throttling** (`template.yaml`): `GET /pair/{code}` and `POST /pair` are each capped at 2 req/s, burst 5, with a 20 req/s default for everything else. This runs before Lambda, so a flood costs nothing and never touches S3. It is also why those two routes are declared explicitly rather than left to the `ANY /{proxy+}` catch-all — `RouteSettings` can only target a route that exists.
2. **Per-address counter** (`handler.py`): 10 attempts per source address per 5-minute window, counted in S3 under `ratelimit/`. The address is hashed, never stored raw.
3. **Global failure breaker**: 50 *failed* redemptions across all addresses in a window stalls redemption entirely until the window rolls. This is the answer to address rotation, which defeats limit 2 by construction. Only failures count, so ordinary pairing never trips it.

The counters are read-modify-write against S3 and can therefore under-count under concurrency. That is acceptable: this bounds a search, it is not accounting, and limit 1 caps concurrency ahead of it. A dedicated 1-day lifecycle rule on `ratelimit/` sweeps the counters.

The tradeoff in limit 3 is that an attacker can stall legitimate pairing for a few minutes. That is the right way round — a delay costs a user nothing they cannot wait out, where the alternative is handing a channel key to whoever guesses first.

## Trust boundary on received text

The value of this tool is that a delivered message lands in the recipient's session as ordinary context — hand a friend the problem you're stuck on and their agent can work it immediately, on top of whatever it was already doing. No copy-paste, no re-explaining. That is the feature, and the design should lean into it.

The cost is that `hey` is, structurally, a way for someone else to put text directly into your agent's context window. Two things hold the boundary:

1. **Fetch is human-initiated.** Nothing arrives unprompted, so there's no path where a message lands and something happens without a person present. (This is also the honest reason the notification idea below stays a notification and not an auto-`check`.)
2. **Received text is data, not instructions.** The skill instructs the agent to treat anything from `check` as untrusted material from a third party — reason about it, offer to act on it, act when the *user* says to; never follow directives embedded in the message itself, and never `send` a reply without the same explicit confirmation an outbound share requires.

Point 2 is enforced by prompt, not by code, which is worth stating plainly rather than dressing up — it's a soft boundary. The hard control is who you pair with: a channel key is a standing capability to write into a friend's agent context, which is the real reason pairing is deliberate, manual, and out-of-band. Anyone worried about the soft boundary should read what arrives before directing action on it.

## Local client config

`~/.hey/config.json`, written atomically (temp file + rename) and chmod `600`:

```json
{
  "friends": {
    "bob": { "channel_key": "..." }
  }
}
```

A friend's local name maps straight to the channel key you share with them. No account key, no slug, no status field — there's nothing to be pending on.

## Adding a friend

No global setup step, because there's no account to register. The handoff goes through a short-lived **pairing code**: 6 digits, expires after 24 hours, single-use.

1. `/friend-new bob` — generates the real channel key locally, stores it under "bob," then `POST`s it to the relay stashed behind a random 6-digit code (24h effective TTL, enforced in Lambda). Prints just the code — easy to say over the phone or text.
2. Bob runs `/friend-add alice <code>` — client redeems the code against the relay (`GET /pair/<code>`), which returns the channel key and deletes the pairing object immediately (single-use, same delete-on-read philosophy as messages). Bob's client stores the channel key locally under "alice."
3. If the code isn't redeemed within 24 hours, the Lambda treats it as expired even though the object may still physically exist until the 14-day Lifecycle rule sweeps it — expired codes are rejected the same as missing ones.

The relay never learns who "bob" or "alice" are — only that someone holding a valid code once gets to redeem one channel key.

## API (Lambda routes, served from the relay's custom domain)

- `POST /pair` — body `{ channel_key }`, no auth needed (this is the bootstrap step). Generates a 6-digit code, stores `pending/<code> → { channel_key, created_at }` in S3. Returns the code.
- `GET /pair/<code>` — no auth beyond the code itself. Rejects if `created_at` is older than 24h; otherwise returns the channel key and deletes the object immediately (single redemption).
- `POST /send` — body `{ text }`, auth via channel key (header). Writes to `inbox/<hash(channel_key)>/<msg-id>`.
- `GET /inbox` — auth via channel key. Returns all unread objects for that channel and deletes each one immediately after it's returned (delete-on-read).

Four routes total, no persistent identity of any kind.

## Client skill

- `/friend-new <name>` — generate a channel key, store it locally under `<name>`, print the one-time 6-digit pairing code for out-of-band handoff.
- `/friend-add <name> <code>` — redeem a pairing code you were given, storing the resulting channel key locally under `<name>`.
- **`/hey <friend> [<what>]`** — the share command. `<what>` selects one of two content-resolution modes:
  - **Recent context** — a bounded window of the current session (roughly the last ~50 lines), taken close to verbatim. This is the default when no specific topic is named.
  - **Last concept** — a natural-language pointer (e.g. `/hey bob concept of this project`) that the agent resolves against the session, distilling the relevant idea into shareable text rather than dumping raw lines.
  - Either way: resolved text is always shown back to you in full, exact form, with the recipient named, before anything sends. `POST /send` only fires on explicit confirmation.
- **`/hey` with no arguments** — the check side of the same verb: `GET /inbox` for each known friend, display anything waiting, and offer to act on it. Delivered text becomes ordinary session context, so the receiving agent can work the problem it describes — that's the intended flow, not an edge case. The boundary: it acts when its user says to, and treats the message as untrusted content from a third party rather than as instructions. See "Trust boundary on received text" above.

## Future: notifications (not implemented)

Right now getting a share means remembering to run `/hey`. Real, unprompted
notifications are possible without adding anything to the relay — Claude Code
already has the two pieces needed (this one is Claude Code-specific; Codex and
Copilot users stay on manual `check` until they grow equivalents):

- **`/schedule`** — a genuine cloud-based cron routine, independent of any
  open session or the machine being on (unlike `/loop`, which only "watches"
  while that specific session stays running).
- **`PushNotification`** — the actual attention-getting primitive: a desktop
  notification, plus a phone push if Remote Control is connected.

The plan, when built: a `/schedule`d routine on some cadence (candidate:
every 15–30 min) runs `hey.py check --json`; if it returns anything, fire
`PushNotification` with something like "bob sent you something via
hey."

**Honest caveat:** this doesn't eliminate polling, it just relocates who does
it. There's no webhook from the relay back into Claude Code — no mechanism
for S3/Lambda to call out the moment a message lands — so a scheduled check
is still a scheduled check under the hood. What changes is that Hey doesn't
need to build or run its own watcher process; it rides Claude Code's native
scheduling instead, and the payoff is a real notification instead of "go
remember to check."

**Open before building it:** the cadence tradeoff (tighter = faster notice,
more scheduled runs; looser = fewer wake-ups, more delay) and whether it's
`/schedule`d per-friend or once globally covering all friends via `check`
with no argument.

## Open questions

- Whether 6 digits stays the right call as the circle widens. It is ~20 bits, which is only safe because it is fenced on three sides (see "Rate limiting" above); more entropy would let the fences come down, at the cost of a code you can no longer read down a phone line. The UX is the reason to keep it, not the security.
- **End-to-end encryption is the one real gap, and it costs the zero-dependency property.** Server-side encryption on the bucket does nothing here, since the threat is the bucket's owner. Encrypting payloads client-side needs an AEAD, and the stdlib ships no cipher at all — only `hashlib`/`hmac`. So it means adding `cryptography` or PyNaCl, which turns a `cp` install into a `pip` install and breaks the agent-agnostic goal above, or hand-rolling crypto, which is not on the table. Deferred deliberately: the honest documentation plus "run your own relay" covers the actual threat model (friends sharing dev context, told not to send secrets). Revisit if the audience widens. Note E2E would still leak message sizes, timing, and which channel is active.
- Key rotation/revocation if a channel key is compromised — probably just "generate a new one and re-share it," no protocol needed since the whole scheme is already manual.
- Settled: bare `/hey` iterates every known friend's key; `check <friend>` narrows it. One verb for both directions (`/hey <friend> …` sends, `/hey` checks) beat a second command on memorability alone.
- Exact boundary of "recent context" (~50 lines is a starting guess — may want to tune once it's actually used).

## Prior art

- The "single JSON registry under a dotted home directory, written atomically via temp-file rename, mode `600`" pattern used by small local-state CLIs — the model for `~/.hey/config.json`. No database, no daemon, trivially inspectable and hand-editable.
