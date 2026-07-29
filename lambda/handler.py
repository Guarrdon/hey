"""hey relay — Lambda behind an API Gateway HTTP API, backed by S3.

No identity concept: the only credential is a channel token — sha256 of a
shared secret held by exactly two people, sent as X-Channel-Token. The relay
never sees the underlying key except once, in transit, during pairing. Routes:

  POST /pair          {channel_key}  -> {code}            bootstrap a friendship
  GET  /pair/{code}                  -> {channel_key}      redeem it (single-use)
  POST /send          {text}         -> {ok, id}           write to a channel
  GET  /inbox                        -> {messages: [...]}  drain a channel

S3 layout:
  pending/<code>.json         -> {channel_key, created_at}   pairing handoff
  inbox/<token>/<id>.json     -> {text, created_at}           messages
  ratelimit/<scope>-<win>.json-> {n}                          brute-force counters

Delete-on-read everywhere; a 14-day S3 Lifecycle rule (see ../template.yaml)
is only the backstop for objects nobody ever reads. Pairing codes additionally
expire after 24h, enforced here since Lifecycle rules are day-granularity.
"""
import hashlib
import json
import os
import re
import secrets
import time

import boto3
from botocore.exceptions import ClientError

s3 = boto3.client("s3")
BUCKET = os.environ["BUCKET_NAME"]
PAIR_CODE_MAX_AGE_SECONDS = 24 * 60 * 60
PAIR_CODE_RE = re.compile(r"^/pair/(\d{6})$")
# A channel token is used verbatim as an S3 key prefix, so it is validated
# strictly rather than trusted: anything but 64 lowercase hex is rejected
# before it can reach a bucket path.
CHANNEL_TOKEN_RE = re.compile(r"^[0-9a-f]{64}$")

# Brute-force limits on the pairing routes. A 6-digit code is only ~20 bits, so
# the code's short life and single use cannot carry the weight alone — these
# bound how fast the space can be searched. API Gateway throttles these routes
# ahead of Lambda too (see ../template.yaml); this is the second layer, and the
# only one that can distinguish a failed guess from a successful redemption.
RATE_WINDOW_SECONDS = 300
RATE_MAX_PER_IP = 10          # attempts from one address per window
RATE_MAX_FAILURES = 50        # failed redemptions across all addresses per window


# --------------------------------------------------------------------- s3 io
def put_json(key, obj):
    s3.put_object(Bucket=BUCKET, Key=key, Body=json.dumps(obj).encode(),
                  ContentType="application/json")


def get_json(key):
    try:
        resp = s3.get_object(Bucket=BUCKET, Key=key)
    except ClientError as e:
        if e.response["Error"]["Code"] in ("NoSuchKey", "404"):
            return None
        raise
    return json.loads(resp["Body"].read())


def delete(key):
    s3.delete_object(Bucket=BUCKET, Key=key)


def list_keys(prefix):
    keys = []
    token = None
    while True:
        kwargs = {"Bucket": BUCKET, "Prefix": prefix}
        if token:
            kwargs["ContinuationToken"] = token
        resp = s3.list_objects_v2(**kwargs)
        keys.extend(o["Key"] for o in resp.get("Contents", []))
        if not resp.get("IsTruncated"):
            break
        token = resp["NextContinuationToken"]
    return sorted(keys)


# ------------------------------------------------------------------- helpers
def channel_hash(channel_key):
    return hashlib.sha256(channel_key.encode()).hexdigest()


# --------------------------------------------------------------- rate limits
def rate_key(scope):
    return f"ratelimit/{scope}-{int(time.time() // RATE_WINDOW_SECONDS)}.json"


def bump(scope):
    """Increment this window's counter for `scope` and return the new value.

    Read-modify-write against S3, so simultaneous requests can under-count.
    That is fine: this bounds a search, it is not accounting. API Gateway caps
    concurrency on these routes before Lambda sees them, and the global failure
    breaker catches whatever slips past a per-address count.
    """
    key = rate_key(scope)
    n = (get_json(key) or {"n": 0})["n"] + 1
    put_json(key, {"n": n})
    return n


def client_scope(event, prefix):
    """Per-caller counter name. The address is hashed, never stored raw."""
    ip = event.get("requestContext", {}).get("http", {}).get("sourceIp", "unknown")
    return f"{prefix}-{hashlib.sha256(ip.encode()).hexdigest()[:32]}"


def over_rate_limit(event, prefix, check_failures=False):
    if bump(client_scope(event, prefix)) > RATE_MAX_PER_IP:
        return True
    # Per-address counting alone is defeated by rotating addresses, so failed
    # redemptions are also counted globally. Tripping this stalls pairing for
    # the rest of the window — a few minutes' delay for a real user, versus
    # handing out a channel key to a guesser.
    if check_failures:
        failures = (get_json(rate_key("pair-failures")) or {"n": 0})["n"]
        if failures >= RATE_MAX_FAILURES:
            return True
    return False


def gen_pairing_code():
    for _ in range(5):
        code = f"{secrets.randbelow(1_000_000):06d}"
        if get_json(f"pending/{code}.json") is None:
            return code
    raise RuntimeError("could not allocate a pairing code")


def response(status, body):
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }


def error(status, message):
    return response(status, {"error": message})


def body_of(event):
    raw = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        import base64
        raw = base64.b64decode(raw).decode()
    return json.loads(raw)


def channel_token_of(event):
    """The channel's address, or None if absent/malformed.

    Current clients send sha256(channel_key) directly. Clients predating that
    change sent the raw key in X-Channel-Key; hashing it here yields the same
    token, so both address the same channel and old installs keep working.
    Drop the fallback once no such client remains.
    """
    headers = event["headers"]
    token = headers.get("x-channel-token")
    if token is None:
        legacy = headers.get("x-channel-key")
        token = channel_hash(legacy) if legacy else None
    if token is None or not CHANNEL_TOKEN_RE.match(token):
        return None
    return token


# ----------------------------------------------------------------- handlers
def handle_pair_create(event):
    if over_rate_limit(event, "pair-new"):
        return error(429, "too many pairing requests, try again in a few minutes")
    channel_key = body_of(event).get("channel_key")
    if not channel_key:
        return error(400, "channel_key required")
    code = gen_pairing_code()
    put_json(f"pending/{code}.json", {"channel_key": channel_key, "created_at": time.time()})
    return response(200, {"code": code})


def handle_pair_redeem(code, event):
    if over_rate_limit(event, "pair-redeem", check_failures=True):
        return error(429, "too many attempts, try again in a few minutes")
    key = f"pending/{code}.json"
    obj = get_json(key)
    if obj is None:
        bump("pair-failures")
        return error(404, "invalid or expired code")
    if time.time() - obj["created_at"] > PAIR_CODE_MAX_AGE_SECONDS:
        delete(key)
        bump("pair-failures")
        return error(404, "invalid or expired code")
    delete(key)
    return response(200, {"channel_key": obj["channel_key"]})


def handle_send(event):
    token = channel_token_of(event)
    if not token:
        return error(401, "missing or malformed channel token")
    text = body_of(event).get("text")
    if not text:
        return error(400, "text required")
    msg_id = f"{int(time.time() * 1000):013d}-{secrets.token_hex(4)}"
    key = f"inbox/{token}/{msg_id}.json"
    put_json(key, {"text": text, "created_at": time.time()})
    return response(200, {"ok": True, "id": msg_id})


def handle_inbox(event):
    token = channel_token_of(event)
    if not token:
        return error(401, "missing or malformed channel token")
    prefix = f"inbox/{token}/"
    messages = []
    for key in list_keys(prefix):
        obj = get_json(key)
        if obj is None:
            continue
        delete(key)
        messages.append({"text": obj["text"], "created_at": obj["created_at"]})
    return response(200, {"messages": messages})


def lambda_handler(event, context):
    method = event.get("requestContext", {}).get("http", {}).get("method", "GET")
    path = event.get("rawPath", "/")
    event["headers"] = {k.lower(): v for k, v in (event.get("headers") or {}).items()}

    try:
        if method == "POST" and path == "/pair":
            return handle_pair_create(event)
        m = PAIR_CODE_RE.match(path)
        if method == "GET" and m:
            return handle_pair_redeem(m.group(1), event)
        if method == "POST" and path == "/send":
            return handle_send(event)
        if method == "GET" and path == "/inbox":
            return handle_inbox(event)
        return error(404, "not found")
    except Exception:
        return error(500, "internal error")
