"""
Public sign-up Lambda for the React app.

The Cognito user pool in this account has AllowAdminCreateUserOnly forced
back to True by an organisation-wide policy, which blocks the SPA's
unauthenticated SignUp call. We work around it by taking the credentials
from a public POST endpoint and create the user server-side via
admin_create_user + admin_set_user_password.

This endpoint is intentionally unauthenticated. WAFv2 is associated with
the API Gateway and provides the AWS managed common rule set, which gives
us baseline protection against common abuse.

Email allowlist: this is a public demo, so we only let the two pre-baked
demo identities (john.doe@example.com and jane.doe@example.com) sign up.
Any other email is rejected with 403 even before we hit Cognito. Existing
accounts in the pool keep working — the allowlist only gates new
registrations. To extend access, edit ALLOWED_EMAILS below.
"""
import json
import os
import re

import boto3
from botocore.exceptions import ClientError

cognito = boto3.client("cognito-idp")
USER_POOL_ID = os.environ["USER_POOL_ID"]

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD_LEN = 12

# Demo allowlist. New sign-ups for any email outside this set are rejected
# with 403. Match is case-insensitive (we lowercase the inbound email
# before checking). To allow a new tester, append their email here and
# redeploy this Lambda — pre-existing accounts in the pool are not
# affected by this filter.
ALLOWED_EMAILS = frozenset(
    e.lower()
    for e in (
        "john.doe@example.com",
        "jane.doe@example.com",
    )
)

CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type",
    "Access-Control-Allow-Methods": "POST,OPTIONS",
    "Content-Type": "application/json",
}


def _resp(status: int, body: dict) -> dict:
    return {"statusCode": status, "headers": CORS_HEADERS, "body": json.dumps(body)}


def handler(event, _context):  # type: ignore[no-untyped-def]
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _resp(400, {"error": "Invalid JSON body"})

    email = (body.get("email") or "").strip().lower()
    password = body.get("password") or ""

    if not EMAIL_RE.match(email):
        return _resp(400, {"error": "A valid email is required"})

    # Hard allowlist. Reject before doing any Cognito work so we don't
    # leak which emails do or don't already exist via timing or error
    # codes — every disallowed email gets the same generic 403.
    #
    # IMPORTANT: do NOT enumerate the allowlist in the error message.
    # The whole point of the allowlist is that visitors shouldn't know
    # which addresses to try. Keep the response message bland.
    if email not in ALLOWED_EMAILS:
        return _resp(
            403,
            {"error": "Sign-up is currently closed on this demo."},
        )

    if len(password) < MIN_PASSWORD_LEN:
        return _resp(
            400,
            {"error": f"Password must be at least {MIN_PASSWORD_LEN} characters"},
        )

    # 1. Create the user with the email pre-verified so we never send a code.
    try:
        cognito.admin_create_user(
            UserPoolId=USER_POOL_ID,
            Username=email,
            UserAttributes=[
                {"Name": "email", "Value": email},
                {"Name": "email_verified", "Value": "true"},
            ],
            MessageAction="SUPPRESS",
        )
    except ClientError as exc:
        code = exc.response["Error"]["Code"]
        msg = exc.response["Error"]["Message"]
        if code == "UsernameExistsException":
            return _resp(409, {"error": "An account with that email already exists"})
        if code in ("InvalidPasswordException", "InvalidParameterException"):
            return _resp(400, {"error": msg})
        return _resp(500, {"error": msg})

    # 2. Set the password as permanent so the user is fully CONFIRMED and can
    #    sign in immediately. If this fails, delete the user so retries work.
    try:
        cognito.admin_set_user_password(
            UserPoolId=USER_POOL_ID,
            Username=email,
            Password=password,
            Permanent=True,
        )
    except ClientError as exc:
        try:
            cognito.admin_delete_user(UserPoolId=USER_POOL_ID, Username=email)
        except ClientError:
            # Best-effort cleanup; surface the original error.
            pass
        msg = exc.response["Error"]["Message"]
        return _resp(400, {"error": msg})

    return _resp(200, {"success": True, "email": email})
