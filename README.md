# Hey

Claude-to-Claude text sharing between known friends. Push text from one
Claude Code session directly into a friend's session — no email, no SMS, no
manual copy-paste.

Full design/rationale: [`DESIGN.md`](./DESIGN.md).

## Layout

```
hey.py           the Claude Code skill's CLI (stdlib only — no deploy needed to use its non-network commands)
SKILL.md         skill definition Claude Code loads to know when/how to call hey.py
lambda/handler.py  the relay itself: Lambda behind a Function URL, backed by S3
template.yaml    AWS SAM template for the relay (Lambda + Function URL + S3 bucket)
```

## Local install (skill only, no AWS involved)

```
mkdir -p ~/.claude/skills/hey
cp SKILL.md hey.py ~/.claude/skills/hey/
```

At that point `list`, `endpoint`, `friend-new`, etc. are usable from any
Claude Code session — anything that talks to the relay just won't work until
it's deployed and `hey.py endpoint <url>` is set.

## Deploy the relay (not done yet — run this when ready)

Requires the [AWS SAM CLI](https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/install-sam-cli.html)
and AWS credentials for the account you want this running in.

```
sam build
sam deploy --guided
```

`sam deploy --guided` prompts for a stack name/region once and writes
`samconfig.toml` (gitignored) so subsequent deploys are just `sam deploy`.
Take the `FunctionUrl` output and run:

```
python3 hey.py endpoint <FunctionUrl from stack output>
```

Every friend does the same against their own `~/.hey/config.json` — the
endpoint is shared infrastructure, the channel keys are not.
