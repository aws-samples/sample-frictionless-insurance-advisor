"""AgentCore Gateway REQUEST interceptor: every tool call runs as the signed-in advisor.

Why this exists
---------------
The assistants reach the gateway with a machine token (client credentials). That
token proves "this is our runtime"; it says nothing about which advisor is being
served. If a tool's Lambda took the advisor from an argument, anyone holding the
machine token, or any text that steers the model, could ask for another advisor's
customers. The fix is to make the advisor's own sign-in token the only thing the
services believe, and to have the gateway check it before anything runs.

What it does
------------
For every `tools/call` request:

1. Reads the advisor's Cognito access token from the `X-Advisor-Token` header
   (the runtimes add it next to the machine token).
2. Verifies it with Cognito `GetUser` (which also catches signed-out and
   disabled users), and checks that it was issued by THIS user pool to THIS app
   client as an access token. GetUser alone accepts any pool's token, so the
   issuer check is required.
3. Without a verified advisor the call is refused. The machine token alone can
   no longer run a single tool.
4. With one, applies the target's mode (see TARGET_MODES):
   * forward_token: the REST API target. The advisor's token replaces the
     machine token on the outbound call, so API Gateway's Cognito authorizer
     and the Lambdas see the real signed-in user. `advisor_id` is removed from
     the arguments (the services do not read it), and a marker header tells the
     Lambdas the call came through the assistant.
   * set_argument: Lambda targets that take `advisor_id`. It is overwritten with
     the verified advisor's email, whatever the model sent.
   * anything else: allowed once the advisor is verified; arguments untouched.

Other MCP methods (initialize, tools/list, ping, notifications) carry no data
and pass through unchanged.

Handling of secrets: the token is never logged. Log lines carry the tool name and
the decision only.
"""
import base64
import hashlib
import json
import os
import time

import boto3
from botocore import UNSIGNED
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

REGION = os.environ.get("AWS_REGION", "us-east-1")
USER_POOL_ID = os.environ.get("USER_POOL_ID", "")
APP_CLIENT_ID = os.environ.get("APP_CLIENT_ID", "")
ISSUER = f"https://cognito-idp.{REGION}.amazonaws.com/{USER_POOL_ID}"

# Header the runtimes use to carry the advisor's access token to the gateway.
TOKEN_HEADER = "x-advisor-token"
# Marker the REST Lambdas read to tell an assistant-originated call from the app's own.
VIA_GATEWAY_HEADER = "X-Advisor-Via-Gateway"

# {gateway target name: mode}, written by the CDK stack from the target definitions.
TARGET_MODES = json.loads(os.environ.get("TARGET_MODES") or "{}")
MODE_FORWARD_TOKEN = "forward_token"
MODE_SET_ARGUMENT = "set_argument"

# The gateway names a tool "<target>___<tool>".
TARGET_SEPARATOR = "___"
ARG = "advisor_id"

# A verified token is remembered briefly. Short on purpose: a sign-out should
# take effect within a minute.
CACHE_SECONDS = 60
CACHE_MAX = 256
_cache: dict[str, tuple[str, float]] = {}

# GetUser is authenticated by the token itself, so the client needs no AWS
# credentials and this function's role has no Cognito permissions.
_cognito = boto3.client("cognito-idp", region_name=REGION, config=Config(signature_version=UNSIGNED))

NOT_SIGNED_IN = (
    "This request is not tied to a signed-in advisor, so the tool did not run. "
    "Ask the advisor to sign in again."
)


# --------------------------------------------------------------------------
# Verifying the advisor
# --------------------------------------------------------------------------
def _unverified_claims(token: str) -> dict | None:
    """The token's payload, decoded WITHOUT checking the signature.

    Only ever used together with a successful Cognito GetUser: Cognito has then
    proved the token is genuine and current, so the claims can be trusted for
    the issuer and client checks.
    """
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
        return claims if isinstance(claims, dict) else None
    except (IndexError, ValueError, TypeError):
        return None


def verify_advisor(token: str | None) -> str | None:
    """The advisor's email if `token` is a valid access token for this app, else None."""
    if not token or not isinstance(token, str):
        return None
    token = token.strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    if not token:
        return None

    now = time.time()
    key = hashlib.sha256(token.encode()).hexdigest()
    hit = _cache.get(key)
    if hit and hit[1] > now:
        return hit[0]

    # Cheap refusals first, so tokens that cannot be ours never reach Cognito.
    claims = _unverified_claims(token)
    if not claims:
        return None
    if claims.get("token_use") != "access":
        return None
    if claims.get("iss") != ISSUER or claims.get("client_id") != APP_CLIENT_ID:
        return None
    exp = claims.get("exp")
    if not isinstance(exp, (int, float)) or exp <= now:
        return None

    try:
        user = _cognito.get_user(AccessToken=token)
    except (ClientError, BotoCoreError):
        return None
    email = next(
        (a.get("Value") for a in user.get("UserAttributes", []) if a.get("Name") == "email"),
        None,
    )
    if not email:
        return None

    if len(_cache) >= CACHE_MAX:
        for stale in [k for k, (_, until) in _cache.items() if until <= now] or list(_cache)[:CACHE_MAX // 2]:
            _cache.pop(stale, None)
    _cache[key] = (email, min(now + CACHE_SECONDS, float(exp)))
    return email


# --------------------------------------------------------------------------
# Response builders
# --------------------------------------------------------------------------
def _pass_through(body, headers=None) -> dict:
    transformed = {"body": body}
    if headers:
        transformed["headers"] = headers
    return {"interceptorOutputVersion": "1.0", "mcp": {"transformedGatewayRequest": transformed}}


def _refuse(rpc_id, message: str) -> dict:
    """Answer the caller directly. The target is never called."""
    return {
        "interceptorOutputVersion": "1.0",
        "mcp": {
            "transformedGatewayResponse": {
                "statusCode": 200,
                "body": {
                    "jsonrpc": "2.0",
                    "id": rpc_id,
                    "result": {"isError": True, "content": [{"type": "text", "text": message}]},
                },
            }
        },
    }


def _header(headers: dict, name: str) -> str | None:
    wanted = name.lower()
    for key, value in (headers or {}).items():
        if isinstance(key, str) and key.lower() == wanted:
            return value
    return None


# --------------------------------------------------------------------------
# Handler
# --------------------------------------------------------------------------
def handler(event, context):
    rpc_id = None
    try:
        request = ((event or {}).get("mcp") or {}).get("gatewayRequest") or {}
        body = request.get("body")
        if not isinstance(body, dict):
            # A batch or anything unparseable: cannot tell what it would run.
            return _refuse(None, NOT_SIGNED_IN)
        rpc_id = body.get("id")

        method = str(body.get("method") or "").strip().lower()
        if method != "tools/call":
            return _pass_through(body)

        params = body.get("params")
        if not isinstance(params, dict):
            return _refuse(rpc_id, NOT_SIGNED_IN)
        tool = str(params.get("name") or "")

        advisor = verify_advisor(_header(request.get("headers") or {}, TOKEN_HEADER))
        if not advisor:
            print(json.dumps({"decision": "refused", "reason": "no verified advisor", "tool": tool}))
            return _refuse(rpc_id, NOT_SIGNED_IN)

        target = tool.split(TARGET_SEPARATOR, 1)[0] if TARGET_SEPARATOR in tool else ""
        mode = TARGET_MODES.get(target)

        arguments = params.get("arguments")
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            return _refuse(rpc_id, "The tool arguments were not understood.")

        if mode == MODE_FORWARD_TOKEN:
            arguments.pop(ARG, None)
            params["arguments"] = arguments
            token = _header(request.get("headers") or {}, TOKEN_HEADER).strip()
            if token.lower().startswith("bearer "):
                token = token[7:].strip()
            print(json.dumps({"decision": "allowed", "mode": mode, "tool": tool}))
            return _pass_through(
                body,
                headers={"Authorization": f"Bearer {token}", VIA_GATEWAY_HEADER: "1"},
            )

        if mode == MODE_SET_ARGUMENT:
            arguments[ARG] = advisor
            params["arguments"] = arguments
            print(json.dumps({"decision": "allowed", "mode": mode, "tool": tool}))
            return _pass_through(body)

        print(json.dumps({"decision": "allowed", "mode": mode or "verified-only", "tool": tool}))
        return _pass_through(body)
    except Exception as exc:  # noqa: BLE001 - never let an error turn into "allowed"
        print(json.dumps({"decision": "refused", "reason": "interceptor error", "error": type(exc).__name__}))
        return _refuse(rpc_id, NOT_SIGNED_IN)
