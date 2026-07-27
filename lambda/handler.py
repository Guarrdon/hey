"""hey relay — Lambda behind a Function URL, backed by S3.

No identity concept: the only credential is a "channel key" (a shared secret
per friendship). Routes:

  POST /pair          {channel_key}  -> {code}            bootstrap a friendship
  GET  /pair/{code}                  -> {channel_key}      redeem it (single-use)
  POST /send          {text}         -> {ok, id}           write to a channel
  GET  /inbox                        -> {messages: [...]}  drain a channel

S3 layout:
  pending/<code>.json         -> {channel_key, created_at}   pairing handoff
  inbox/<hash(key)>/<id>.json -> {text, created_at}           messages

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


def channel_key_of(event):
    return event["headers"].get("x-channel-key")


# ----------------------------------------------------------------- handlers
def handle_pair_create(event):
    channel_key = body_of(event).get("channel_key")
    if not channel_key:
        return error(400, "channel_key required")
    code = gen_pairing_code()
    put_json(f"pending/{code}.json", {"channel_key": channel_key, "created_at": time.time()})
    return response(200, {"code": code})


def handle_pair_redeem(code):
    key = f"pending/{code}.json"
    obj = get_json(key)
    if obj is None:
        return error(404, "invalid or expired code")
    if time.time() - obj["created_at"] > PAIR_CODE_MAX_AGE_SECONDS:
        delete(key)
        return error(404, "invalid or expired code")
    delete(key)
    return response(200, {"channel_key": obj["channel_key"]})


def handle_send(event):
    channel_key = channel_key_of(event)
    if not channel_key:
        return error(401, "missing channel key")
    text = body_of(event).get("text")
    if not text:
        return error(400, "text required")
    msg_id = f"{int(time.time() * 1000):013d}-{secrets.token_hex(4)}"
    key = f"inbox/{channel_hash(channel_key)}/{msg_id}.json"
    put_json(key, {"text": text, "created_at": time.time()})
    return response(200, {"ok": True, "id": msg_id})


def handle_inbox(event):
    channel_key = channel_key_of(event)
    if not channel_key:
        return error(401, "missing channel key")
    prefix = f"inbox/{channel_hash(channel_key)}/"
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
            return handle_pair_redeem(m.group(1))
        if method == "POST" and path == "/send":
            return handle_send(event)
        if method == "GET" and path == "/inbox":
            return handle_inbox(event)
        return error(404, "not found")
    except Exception:
        return error(500, "internal error")
