# Hey

Claude-to-Claude text sharing between known friends. Push text from one
Claude Code session directly into a friend's session — no email, no SMS, no
manual copy-paste.

The relay is **already deployed and live** at `https://api.sendingwith.com`.
If you're a friend being added, skip straight to [Using it](#using-it) — you
do not need to deploy anything.

Full design/rationale: [`DESIGN.md`](./DESIGN.md).

## Layout

```
hey.py             the CLI itself (stdlib only) — shared by every agent below
SKILL.md           skill definition Claude Code loads to know when/how to call hey.py
codex/SKILL.md     same skill, pathed for OpenAI Codex CLI
copilot/SKILL.md   same skill, pathed for GitHub Copilot (VS Code)
lambda/handler.py  the relay itself: Lambda behind an HTTP API custom domain, backed by S3
template.yaml      AWS SAM template for the relay (HTTP API + ACM cert + Lambda + S3 + Route53 alias)
```

`hey.py` is agent-agnostic — it's the same script in every case, just
installed under whichever directory that agent scans for `SKILL.md` files.
All agents on a given machine share the same friend list and channel keys
(`~/.hey/config.json`), so pairing once makes `/hey` work from any of them.

## Install the skill

Pick whichever agent(s) you use `/hey` from — same repo, different install
directory. Codex and Copilot both speak the same open Agent Skills format
Claude Code does, so no code changes are needed per agent, just placement.

**Claude Code** (one-time, per machine):

```
mkdir -p ~/.claude/skills/hey
cp SKILL.md hey.py ~/.claude/skills/hey/
```

**OpenAI Codex CLI** (one-time, per machine — personal skill):

```
mkdir -p ~/.codex/skills/hey
cp codex/SKILL.md hey.py ~/.codex/skills/hey/
```

**GitHub Copilot in VS Code** (per repo — project skill, so it's shared with
your team via version control):

```
mkdir -p .github/skills/hey
cp copilot/SKILL.md hey.py .github/skills/hey/
```

## Using it

All commands below go through Claude Code (say the phrase, or run the
underlying `hey.py` command yourself — both work identically).

### 1. Point at the relay (once, per machine)

```
python3 ~/.claude/skills/hey/hey.py endpoint https://api.sendingwith.com
```

### 2. Add a friend (once, per friendship)

Whoever starts it generates a pairing code and reads/texts it to the other
person out-of-band — never through this tool, since you're not paired yet:

```
python3 ~/.claude/skills/hey/hey.py friend-new sneaksoft
# -> pairing code for sneaksoft: 123456
```

The other person redeems that code (it's single-use, expires in 24h):

```
python3 ~/.claude/skills/hey/hey.py friend-add matt 123456
# -> paired with matt
```

That's it — no accounts, no further setup. Each side now holds a private
channel key for this one friendship (never shown again after this point).

### 3. Share something

In a Claude Code session:

> `/hey sneaksoft concept of this project`

Claude resolves that into actual text (distilling the relevant idea from the
conversation, or — if you don't name a topic — grabbing the last ~50 lines of
context close to verbatim), **shows you the exact text and recipient, and
waits for your confirmation** before anything sends. Nothing goes out
silently. Under the hood, once confirmed, it's just:

```
printf '%s' "<the text>" | python3 ~/.claude/skills/hey/hey.py send sneaksoft
```

### 4. Check for anything sent to you

> "check for anything from sneaksoft" (or just "check for shares")

```
python3 ~/.claude/skills/hey/hey.py check           # everyone
python3 ~/.claude/skills/hey/hey.py check sneaksoft # just one friend
```

Messages are deleted from the relay the instant they're delivered — there's
no inbox to manage, nothing to mark as read.

## Testing solo (two identities, one machine)

A single config file can't hold two "sides" of the same friendship — if you
`friend-new`/`friend-add` yourself under two names in the *same* config,
both names end up sharing one channel key, not acting as two independent
parties. To actually test a full pairing + send/check cycle by yourself, give
each side its own isolated config with `--profile` (or `HEY_PROFILE`):

```
python3 hey.py --profile a endpoint https://api.sendingwith.com
python3 hey.py --profile b endpoint https://api.sendingwith.com
python3 hey.py --profile a friend-new b
python3 hey.py --profile b friend-add a <code from above>
echo "test" | python3 hey.py --profile a send b
python3 hey.py --profile b check
```

Profiles live under `~/.hey/profiles/<name>/` and never touch your real
`~/.hey/config.json`.

## Deploying your own instance instead

Only needed if you're standing up a *different* relay (your own domain, your
own AWS account) rather than using the shared one above.

Requires the AWS SAM CLI (`brew install aws-sam-cli`) and AWS credentials
with Route53/ACM/API Gateway/Lambda/S3 access, plus a domain already
registered in Route53.

```
sam build
sam deploy --stack-name hey \
  --resolve-s3 \
  --capabilities CAPABILITY_IAM \
  --region us-east-1 \
  --parameter-overrides DomainName=<your-subdomain> HostedZoneId=<your-zone-id>
```

First deploy takes a few minutes (ACM certificate DNS validation + HTTP API
custom domain propagation are the slow parts; everything else is fast). Then
point clients at your domain instead:

```
python3 hey.py endpoint https://<your-subdomain>
```
