#!/usr/bin/env python3
"""hey — Claude-to-Claude text sharing between known friends.

Bundled with the `hey` Claude skill. Keeps a JSON friend list (name -> shared
"channel key") under ~/.hey/ and talks to a small relay (see ../DESIGN.md and
../template.yaml) over plain HTTPS. The relay itself has no identity concept:
whoever holds a channel key can read/write that one relationship's message
channel. New friendships are bootstrapped through a short-lived 6-digit
pairing code so the raw channel key is never spoken or typed by a human.

No third-party dependencies — stdlib only (urllib for HTTP).
"""
import argparse
import json
import os
import secrets
import sys
import urllib.error
import urllib.request

HOME = os.path.expanduser("~")
DIR = os.path.join(HOME, ".hey")
CONFIG = os.path.join(DIR, "config.json")


def use_profile(profile):
    """Point DIR/CONFIG at an isolated profile directory instead of the default.

    Lets one machine hold multiple independent local identities (e.g. to test
    a full pairing + send/check cycle solo) without them sharing a config
    file — two names in the same config would share a channel key, not be
    two separate parties.
    """
    global DIR, CONFIG
    DIR = os.path.join(HOME, ".hey", "profiles", profile)
    CONFIG = os.path.join(DIR, "config.json")


# ------------------------------------------------------------------ config io
def load():
    if not os.path.exists(CONFIG):
        return {"endpoint": None, "friends": {}}
    with open(CONFIG) as f:
        return json.load(f)


def save(data):
    os.makedirs(DIR, exist_ok=True)
    tmp = CONFIG + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    os.replace(tmp, CONFIG)
    os.chmod(CONFIG, 0o600)


# --------------------------------------------------------------------- http
def request(data, method, path, body=None, channel_key=None):
    endpoint = data.get("endpoint")
    if not endpoint:
        print("error: no relay endpoint configured — run `hey.py endpoint <url>`",
              file=sys.stderr)
        sys.exit(2)
    url = endpoint.rstrip("/") + path
    headers = {"Content-Type": "application/json"}
    if channel_key:
        headers["X-Channel-Key"] = channel_key
    payload = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=payload, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read().decode()).get("error", e.reason)
        except Exception:
            detail = e.reason
        print(f"error: relay returned {e.code}: {detail}", file=sys.stderr)
        sys.exit(2)
    except urllib.error.URLError as e:
        print(f"error: could not reach relay at {endpoint}: {e.reason}", file=sys.stderr)
        sys.exit(2)


# ----------------------------------------------------------------- commands
def cmd_endpoint(args, data):
    if args.url:
        data["endpoint"] = args.url
        save(data)
        print(f"endpoint set to {args.url}")
    else:
        ep = data.get("endpoint")
        print(ep if ep else "no endpoint configured")


def cmd_friend_new(args, data):
    channel_key = secrets.token_hex(32)
    result = request(data, "POST", "/pair", body={"channel_key": channel_key})
    data["friends"][args.name] = {"channel_key": channel_key}
    save(data)
    code = result["code"]
    print(f"pairing code for {args.name}: {code}")
    print("give this to them out-of-band (say it, text it) — it expires in 24h "
          "and works once. they should run: hey.py friend-add <your name> " + code)


def cmd_friend_add(args, data):
    result = request(data, "GET", f"/pair/{args.code}")
    data["friends"][args.name] = {"channel_key": result["channel_key"]}
    save(data)
    print(f"paired with {args.name}")


def cmd_list(args, data):
    friends = data.get("friends", {})
    if not friends:
        print("no friends yet — use friend-new or friend-add")
        return
    for name in sorted(friends):
        print(name)


def require_friend(data, name):
    friend = data.get("friends", {}).get(name)
    if not friend:
        print(f"error: no friend named '{name}' — use friend-new or friend-add first",
              file=sys.stderr)
        sys.exit(2)
    return friend


def cmd_send(args, data):
    friend = require_friend(data, args.name)
    if args.file:
        with open(args.file) as f:
            text = f.read()
    else:
        text = sys.stdin.read()
    text = text.strip()
    if not text:
        print("error: nothing to send (empty text)", file=sys.stderr)
        sys.exit(2)
    request(data, "POST", "/send", body={"text": text}, channel_key=friend["channel_key"])
    print(f"sent to {args.name}")


def cmd_check(args, data):
    friends = data.get("friends", {})
    if args.name:
        require_friend(data, args.name)
        names = [args.name]
    else:
        names = sorted(friends)
    if not names:
        print("no friends yet — use friend-new or friend-add")
        return
    all_messages = []
    for name in names:
        result = request(data, "GET", "/inbox", channel_key=friends[name]["channel_key"])
        for msg in result.get("messages", []):
            all_messages.append({"from": name, **msg})
    if args.json:
        print(json.dumps(all_messages, indent=2))
        return
    if not all_messages:
        print("nothing waiting")
        return
    for msg in all_messages:
        print(f"--- from {msg['from']} ---")
        print(msg["text"])


def main():
    p = argparse.ArgumentParser(prog="hey", description=__doc__.splitlines()[0])
    p.add_argument("--profile", help="use an isolated local identity (or set HEY_PROFILE); "
                                      "for testing two parties solo on one machine")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("endpoint", help="get or set the relay's Function URL")
    s.add_argument("url", nargs="?")
    s.set_defaults(func=cmd_endpoint)

    s = sub.add_parser("friend-new", help="start a new friendship, print a pairing code")
    s.add_argument("name")
    s.set_defaults(func=cmd_friend_new)

    s = sub.add_parser("friend-add", help="redeem a pairing code you were given")
    s.add_argument("name")
    s.add_argument("code")
    s.set_defaults(func=cmd_friend_add)

    s = sub.add_parser("list", help="known friend names (never prints keys)")
    s.set_defaults(func=cmd_list)

    s = sub.add_parser("send", help="send text (from stdin, or --file) to a friend")
    s.add_argument("name")
    s.add_argument("--file", help="read text from this file instead of stdin")
    s.set_defaults(func=cmd_send)

    s = sub.add_parser("check", help="pull anything waiting, for one friend or all")
    s.add_argument("name", nargs="?")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_check)

    args = p.parse_args()
    profile = args.profile or os.environ.get("HEY_PROFILE")
    if profile:
        use_profile(profile)
    data = load()
    sys.exit(args.func(args, data) or 0)


if __name__ == "__main__":
    main()
