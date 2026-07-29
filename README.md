# hey

Your coding agent can see your session and your machine, and nothing else. It
has no way to hand something to another person.

**`hey` adds exactly that one capability:** push a block of text out of your
session to a friend you've paired with, and pull back anything they've sent
you. One command in both directions — `/hey` — and one shared secret per
friendship. No accounts.

**And it does not care which agent either of you runs.** You live in Claude
Code, your friend swears by Codex, someone else won't leave Copilot. Nobody
switches. `/hey` reaches across all three.

```
alice › /hey bob the gist of this refactor           ← in Claude Code
        └ drafts it, shows the exact text, sends only on an OK

                       ·   ·   ·

bob   › /hey                                         ← in Codex
        └ prints what alice sent — and can pick the work up from there
```

(`alice` and `bob` are placeholders throughout — friend names are purely local
labels you pick; the relay never sees them.)

## It crosses agents — that's the point

Everyone has a favorite, and they're rarely the same one. Normally that means
whoever wants to share something is the one who switches tools, or you both
fall back to pasting through Slack and re-explaining the context on the far
side.

`hey` treats mixed tooling as the ordinary case. `hey.py` is a plain stdlib
Python script, and Claude Code, OpenAI Codex CLI, and GitHub Copilot all read
the same open [Agent Skills](https://code.claude.com/docs/en/skills) format —
so the identical file drops into all three, unchanged. Only the install path
differs. Neither side knows what the other is running, and the relay doesn't
either; it sees a channel token and some text.

- `/hey` from **Claude Code** → your friend reads it in **Codex**.
- `/hey` from **Copilot** in VS Code → your friend reads it in **Claude Code**.
- Install it into several agents on your own machine and they share one friend
  list (`~/.hey/config.json`). Pair once; `/hey` works from whichever you have
  open.

The same holds for whatever comes next — anything that reads Agent Skills
picks this up with a `cp`.

## What else this is — and isn't

- **What arrives is working context, not a transcript.** This is the point of
  the tool. Send someone the problem you're stuck on, and once they pull it in,
  it's in their session — their agent can pick it up and run with it on top of
  whatever they were already doing. Same as if they'd pasted it in themselves,
  minus the copy-paste and the app switch.
- **Human-gated at both ends.** You confirm the exact text before it leaves
  your machine; they have to ask before anything is fetched. Nothing sends,
  polls, or arrives on its own, and nothing *acts* on arrival — a received
  message becomes context for its recipient to direct, never instructions the
  agent follows on its own. (See [Key considerations](#key-considerations) —
  this boundary is load-bearing.)
- **Not a messenger.** Text only, one friend at a time, no history, no inbox,
  no read receipts. Delivered messages are deleted from the relay immediately.

## Install the skill

Clone this repo and, from its root, copy two files into whichever agent(s) you
want `/hey` from — install as many as you like, they share one friend list. No
build step, no dependencies.

**Claude Code** — per machine:

```
mkdir -p ~/.claude/skills/hey
cp SKILL.md hey.py ~/.claude/skills/hey/
```

**OpenAI Codex CLI** — per machine:

```
mkdir -p ~/.codex/skills/hey
cp codex/SKILL.md hey.py ~/.codex/skills/hey/
```

**GitHub Copilot (VS Code)** — per repo, so it version-controls with your
project:

```
mkdir -p .github/skills/hey
cp copilot/SKILL.md hey.py .github/skills/hey/
```

The `SKILL.md` files differ only in the install path they tell the agent to
call. `hey.py` is byte-identical everywhere.

## Setup

**You talk; your agent does it.** There is nothing to memorize and no CLI to
learn — every step below is just the sentence you type into whichever agent you
installed the skill in.

Setup is per machine, not per agent — do it once and every agent you installed
into is covered.

### 1. Point at a relay — once per machine

The relay's code is right here — [`lambda/handler.py`](./lambda/handler.py) and
[`template.yaml`](./template.yaml), about 170 lines and four routes. What a repo
can't ship is a *running* one, since that's a live URL in somebody's AWS
account. So you either borrow an instance or stand one up:

- **Use someone's existing instance.** Whoever runs it hands you the URL
  alongside your pairing code — same phone call or text, one message. The
  maintainer runs one and shares it with friends; the URL isn't printed in this
  repo on purpose, for reasons in [Key considerations](#key-considerations).
- **[Run your own](#run-your-own-relay).** One `sam deploy`, a few minutes,
  cents a month at this traffic level. A private instance isn't a lesser
  version — it's the same four routes, and it's the only way to be the sole
  operator of your own data.

Once you have a URL, hand it over in a sentence — no slash needed, just keep
the word *hey* in it so your agent knows which tool you mean:

> "point hey at https://relay.example.com"

Or don't bother remembering that either — just type `/hey`. If no relay is
configured yet, your agent will say it needs a URL, take the one you paste, and
carry on with whatever you actually asked for.

Both sides of a friendship must be pointed at the *same* relay. Different
relays can't see each other, so a private instance means a private circle.

**How private are messages, either way?** In transit, HTTPS. On the relay, each
friendship is addressed by its own token — no other pair sharing that relay
can read or even name your channel — and a message is deleted the moment it's
delivered. Nothing is public, indexed, logged as content, or retained after
pickup. What a *shared* relay doesn't give you is privacy from the person
operating it: for the seconds or hours before you collect it, your message sits
in their S3 bucket in plaintext, and they have access to that bucket. That is
the single thing running your own instance changes, and the reason the option
is here.

### 2. Pair with a friend — once per friendship

Pairing exists so the real secret — a 64-character channel key — is never
spoken, typed, or pasted by a human. Instead one side generates it and stashes
it behind a throwaway 6-digit code.

**Alice** starts it, filing the friendship under a local label for Bob:

> "add bob as a friend"
>
> → *pairing code for bob: **123456***

Alice reads that code out-of-band — voice, text, anything but this tool, since
the two aren't paired yet. **Bob** redeems it, filing it under a local label
for Alice (single use, expires in 24h):

> "pair me with alice, the code is 123456"
>
> → *paired with alice*

Done. Each side now holds the channel key for this one friendship in
`~/.hey/config.json`, and never sees it again.

### 3. Share something

In your agent session:

> `/hey bob concept of this project`

Name a topic and the agent distills it from the conversation into a
self-contained block. Name nothing and it takes roughly the last 50 lines,
close to verbatim. Either way **it shows you the exact text and the recipient
and waits for you to confirm.** Nothing leaves your machine until you say so.

### 4. Check what's waiting

Same command, no arguments:

> `/hey`

That's the whole thing to remember: **`/hey <friend> …` sends, bare `/hey`
checks.** Anything waiting gets printed and is then live context — tell your
agent to run with it and it will, on top of whatever it was already doing.
"Anything from bob?" narrows it to one friend.

There's no push — you have to ask. (Why, and what a real notification would
take, is in [`DESIGN.md`](./DESIGN.md#future-notifications-not-implemented).)

## Everything you can say

The whole interface. `/hey` is the only piece of syntax, and even it has a
plain-English equivalent.

| Say this | And you get |
|---|---|
| `/hey` | Anything friends have sent you, pulled into the session |
| `/hey <friend>` | The recent conversation, shared with them near-verbatim |
| `/hey <friend> <topic>` | That one idea, distilled out of the session and shared |
| "anything from bob?" | Same as `/hey`, narrowed to one friend |
| "add bob as a friend" | A pairing code to read them out loud |
| "pair me with alice, code 123456" | The other side of that handoff |
| "who are my hey friends?" | Your friend list — names only, never keys |
| "point hey at `<url>`" | The relay set, once per machine |

Sends always show you the exact text first and wait. Nothing else needs
confirming, because nothing else leaves your machine.

## Key considerations

Read this part before you send anything you'd mind seeing elsewhere. The design
trades security surface for simplicity, deliberately and in specific places.

**Messages are readable by whoever runs the relay.** They sit in that person's
S3 bucket until someone reads them. The bucket is encrypted at rest, blocks
public access, and refuses non-TLS requests — but none of that hides anything
from the bucket's owner, who can read plaintext. This is **not** end-to-end
encryption; nothing here encrypts payloads. Treat a relay you don't operate the
way you'd treat any third-party server: fine for ideas, snippets, and context;
wrong for credentials, keys, customer data, or anything under compliance.

**The relay never sees your channel key after pairing.** Day-to-day requests
carry `sha256(channel_key)` — enough to address the channel, which is all the
relay does with it. Be clear on what this does and doesn't buy: that token
still grants full read and write on the channel, so it is not a weaker
credential. What it avoids is putting the underlying secret in every request
header, where it would accumulate in any logs the relay keeps. The one moment
the raw key does transit the relay is pairing, where it sits in the bucket until
redeemed or 24 hours pass.

**Received text lands in your agent's context — that cuts both ways.** It's
what makes the tool useful (your friend's problem statement becomes something
their agent can actually work on) and it's the main thing to be careful about.
Anything that arrives is untrusted input written by someone else, and the skill
tells the agent to treat it as material to reason about, never as instructions
to follow. Do read what arrives before telling your agent to act on it. Pair
only with people you'd hand a terminal to.

**The channel key is the entire auth model.** Anyone holding a copy can read
and write that friendship's channel, from anywhere. It lives in
`~/.hey/config.json` (mode `600`, not encrypted). A compromised machine or a
leaked backup is a compromised channel — and given the point above, whoever
holds it can put text directly into your agent's context.

**There is no revocation.** Deleting a friend locally doesn't invalidate
anything server-side — the old key keeps working for anyone who has it. To
rotate, both sides re-pair with `friend-new`/`friend-add` and, if it matters,
the relay operator empties the bucket.

**Pairing codes are short, so the relay rate-limits them.** A code is six
digits, single-use, and expires in 24 hours. Six digits is a small space, so
guessing is bounded in three places: API Gateway throttles the redemption route
to 2 requests a second before Lambda is even invoked; the relay allows 10
attempts per address per five minutes; and 50 failed guesses from any source
combined stall all redemptions until the window rolls over. Rotating addresses
gets you past the second limit and straight into the third. A real pairing —
one code, one attempt — never comes close to any of them.

**`POST /pair` is unauthenticated** — it has to be, it's the bootstrap. It's
throttled the same way, so a stranger with the URL can't flood the bucket. Still
treat the relay URL as something you hand to friends deliberately rather than
post publicly.

**Delivery isn't guaranteed.** The relay deletes each message the moment it
hands it over, so there's no replay: if `check` dies between the fetch and
printing to your terminal, that message is gone. Unread messages are swept by a
14-day S3 lifecycle rule. Two machines sharing one config race — whoever checks
first gets it.

**Scope limits.** Text only. One friend per message, no groups, no files, no
threading. There's no chunking, so a very large paste will hit API Gateway's
payload limit rather than split.

## Run your own relay

Worth doing if you want to be the only operator with access to your own
messages, or you want a closed circle — a relay only ever connects the people
pointed at it.

Needs the AWS SAM CLI (`brew install aws-sam-cli`), AWS credentials with
Route53 / ACM / API Gateway / Lambda / S3 access, and a domain already in a
Route53 hosted zone.

```
sam build
sam deploy --stack-name hey \
  --resolve-s3 \
  --capabilities CAPABILITY_IAM \
  --region us-east-1 \
  --parameter-overrides DomainName=hey.example.com HostedZoneId=<your-zone-id>
```

First deploy takes a few minutes — ACM's DNS validation and the HTTP API custom
domain are the slow parts; everything else is seconds. The stack outputs both
your custom domain and the raw `execute-api` URL, which works immediately if
you don't want to wait on DNS. Then point yourself at it the same way as
always — "point hey at https://hey.example.com" — and share that URL with the
friends you want on it.

What you get: an HTTP API, one 128 MB Lambda, and an S3 bucket with a 14-day
lifecycle rule. Idle cost is effectively zero.

## Handling multiple profiles

One machine, several separate identities. Each profile is its own friend list,
its own keys, and its own relay — nothing is shared between them.

> "use my work profile for hey"

Reach for it when:

- **You keep circles apart.** Work friends on the work relay, everyone else on
  a personal one, with no chance of sending to the wrong list.
- **You're on someone else's machine** and don't want to touch their setup.
- **You want to test both sides yourself.** A single friend list can't hold both
  ends of a friendship — pairing yourself under two names just points both at
  one key, which isn't two parties. Two profiles are two parties:

  > "make two hey profiles, a and b, pair them with each other on
  > https://relay.example.com, then send a test from a and check it as b"

Profiles live under `~/.hey/profiles/<name>/`, leaving your default
`~/.hey/config.json` untouched. Setup is per profile, so a new one starts empty:
point it at a relay and pair it before use.

## Repo layout

```
hey.py             does the actual work — stdlib only, identical for every agent
SKILL.md           teaches Claude Code when and how to call it
codex/SKILL.md     same, for OpenAI Codex CLI
copilot/SKILL.md   same, for GitHub Copilot
lambda/handler.py  the relay: four routes, S3-backed, no identity concept
template.yaml      SAM template (HTTP API + ACM cert + Lambda + S3 + Route53)
```

Full design and rationale, including what was rejected and why:
[`DESIGN.md`](./DESIGN.md).
