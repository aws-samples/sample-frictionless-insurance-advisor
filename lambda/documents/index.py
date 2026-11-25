"""
Documents Upload Lambda

Issues short-lived presigned S3 POST forms so the React SPA can upload
insurance-policy documents (PDF / Markdown / image) directly to S3 without
the binary ever transiting API Gateway. The agent then calls the
extract_policy MCP tool target with the returned `document_id` to read
the file from S3 and run a tool-forced extraction.

Route:
  POST /documents/initiate
    Body: { filename: str, content_type: str, customer_id?: str }
    Returns: { document_id, upload_url, upload_fields, s3_key, expires_in_seconds, max_size_bytes,
               content_type, filename }
  The browser sends `upload_fields` and then the file (as the last form field) to `upload_url`
  with a multipart/form-data POST.

Why a presigned POST (not a multipart upload through API GW):
- API Gateway REST has a 10 MB request size cap; base64-inflated binary
  blows that. A presigned POST keeps the binary off API GW entirely.
- Unlike a presigned PUT, a POST policy can carry a `content-length-range`
  condition, so S3 itself refuses a file that is empty or larger than the limit.
- This Lambda is purely a form signer (sub-50ms cold start, no compute
  cost on the actual file transfer).

Security model (matches the rest of the system):
- Cognito JWT required at API Gateway (the sign-in scope); the advisor is taken from its verified claims only
- S3 key is `{advisor_id}/{customer_id_or_unassigned}/{document_id}.{ext}`
  so a stolen presigned form can only write to the calling advisor's
  namespace; the extract_policy Lambda re-derives advisor_id from the
  caller's JWT and rejects mismatched paths.
- customer_id must match a strict pattern and be one of the calling advisor's
  own customers before it becomes a path segment of the key.
- 24-hour S3 lifecycle expiration on the bucket auto-deletes uploads.
- Only PDF, Markdown, plain text, and JPEG/PNG/WEBP are accepted.
- The size limit (10 MB by default, environment variable MAX_UPLOAD_BYTES) is part of the signed policy.
"""
from __future__ import annotations

import json
import os
import re
import uuid

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

DEFAULT_MAX_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB
MIN_UPLOAD_BYTES = 1


def _max_size_bytes() -> int:
    """MAX_UPLOAD_BYTES from the environment, kept between 1 KB and 50 MB; the default if unset or not a number."""
    try:
        value = int(str(os.environ.get("MAX_UPLOAD_BYTES", "")).strip())
    except ValueError:
        return DEFAULT_MAX_SIZE_BYTES
    return min(50 * 1024 * 1024, max(1024, value))


MAX_SIZE_BYTES = _max_size_bytes()
PRESIGN_EXPIRES_SECONDS = 300  # 5 min — long enough for a slow upload, short enough to limit replay
MAX_FILENAME_CHARS = 255
# Same pattern as lambda/applications.
CUSTOMER_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
UNASSIGNED = "unassigned"

# Map of allowed Content-Type -> file extension. Anything else is rejected.
ALLOWED_CONTENT_TYPES: dict[str, str] = {
    "application/pdf": "pdf",
    "text/markdown": "md",
    "text/plain": "txt",
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
}

# `s3v4` signs the presigned form with Signature Version 4, which S3 requires
# for buckets with default encryption.
s3_client = boto3.client(
    "s3",
    config=Config(signature_version="s3v4"),
)
cognito = boto3.client("cognito-idp")
dynamodb = boto3.resource("dynamodb")

DOCUMENTS_BUCKET = os.environ["DOCUMENTS_BUCKET"]
PROFILES_TABLE = os.environ["PROFILES_TABLE"]
USER_POOL_ID = os.environ.get("USER_POOL_ID")


def _cors_headers() -> dict:
    return {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Headers": "Content-Type,Authorization",
        "Access-Control-Allow-Methods": "POST,OPTIONS",
    }


def _response(status: int, body) -> dict:
    return {
        "statusCode": status,
        "headers": _cors_headers(),
        "body": json.dumps(body),
    }


def _verified_email(user):
    """The email address stored for a Cognito user, exactly as stored, or None.

    `user` is an AdminGetUser response. Only a user that is enabled, CONFIRMED and has a
    verified email address counts as an advisor: an address Cognito has not verified is
    text the user typed in, so it must never become an advisor id. The address is returned
    as stored, with no case folding or trimming.
    """
    if not isinstance(user, dict) or user.get("Enabled") is not True or user.get("UserStatus") != "CONFIRMED":
        print("The user is not enabled and confirmed")
        return None
    attributes = {a.get("Name"): a.get("Value") for a in user.get("UserAttributes") or []}
    email = attributes.get("email")
    if attributes.get("email_verified") != "true" or not isinstance(email, str) or not email:
        print("The user has no verified email address")
        return None
    return email


def _extract_advisor_id(event: dict) -> str | None:
    """The signed-in advisor's email, taken ONLY from the verified sign-in token.

    API Gateway's Cognito authorizer has already checked the token; its claims are
    all this trusts. The query string and body are never used to pick the advisor,
    because the advisor's id is also the folder the upload lands in.
    """
    claims = ((event.get("requestContext") or {}).get("authorizer") or {}).get("claims") or {}
    username = claims.get("username")
    if not username or not USER_POOL_ID:
        return None
    try:
        response = cognito.admin_get_user(UserPoolId=USER_POOL_ID, Username=username)
        return _verified_email(response)
    except Exception as e:  # noqa: BLE001
        print(f"Cognito lookup error: {type(e).__name__}")
    return None


def _safe_advisor_path(advisor_id: str) -> str:
    """The advisor_id is the email address. Use a slugged form for the S3
    key so we don't inject characters that break S3 prefix listings.
    """
    return advisor_id.replace("@", "_at_").replace("/", "_").replace(":", "_")


def handler(event, context):
    try:
        method = event.get("httpMethod", "POST")
        if method != "POST":
            return _response(405, {"message": "Only POST supported"})

        advisor_id = _extract_advisor_id(event)
        if not advisor_id:
            return _response(401, {"message": "Unauthorized — no advisor ID found"})

        try:
            body = json.loads(event.get("body") or "{}")
        except json.JSONDecodeError:
            return _response(400, {"message": "Body is not valid JSON"})
        if not isinstance(body, dict):
            return _response(400, {"message": "Body must be a JSON object"})

        filename = body.get("filename")
        content_type = body.get("content_type")
        customer_id = body.get("customer_id")

        if not isinstance(filename, str) or not filename.strip():
            return _response(400, {"message": "filename is required"})
        filename = filename.strip()
        if len(filename) > MAX_FILENAME_CHARS:
            return _response(400, {"message": "filename is too long"})

        content_type = content_type.strip().lower() if isinstance(content_type, str) else ""
        if content_type not in ALLOWED_CONTENT_TYPES:
            return _response(
                400,
                {
                    "message": f"Unsupported content_type {content_type[:60]!r}. "
                    f"Allowed: {sorted(ALLOWED_CONTENT_TYPES.keys())}"
                },
            )

        if customer_id is None or customer_id == "":
            customer_id = UNASSIGNED
        elif not isinstance(customer_id, str) or not CUSTOMER_ID_RE.match(customer_id):
            return _response(400, {"message": "customer_id is not valid"})
        if customer_id != UNASSIGNED:
            # The customer becomes a folder in the key, so it must be one of this advisor's own.
            profile = dynamodb.Table(PROFILES_TABLE).get_item(Key={"customer_id": customer_id}).get("Item")
            if not profile or profile.get("advisor_id") != advisor_id:
                return _response(404, {"message": "Customer not found for this advisor"})

        # Stable customer scoping in the key. The "unassigned" namespace is
        # used when the advisor uploads in "+ New Prospect" mode before any
        # customer record exists. Uploads expire after 24 hours, so they are
        # never moved to a customer namespace.
        document_id = uuid.uuid4().hex
        ext = ALLOWED_CONTENT_TYPES[content_type]
        s3_key = (
            f"{_safe_advisor_path(advisor_id)}/{customer_id}/"
            f"{document_id}.{ext}"
        )

        # Generate the presigned POST form.
        #
        # The signed policy pins the object key and Content-Type and bounds the size with
        # a content-length-range condition, so S3 rejects an empty or oversized file even
        # though this Lambda never sees the bytes. Metadata is deliberately not signed: the
        # audit trail (advisor_id, customer_id, document_id) is already encoded in the S3
        # key path, and S3 access logs and CloudTrail capture the key as well.
        try:
            post = s3_client.generate_presigned_post(
                Bucket=DOCUMENTS_BUCKET,
                Key=s3_key,
                Fields={"Content-Type": content_type},
                Conditions=[
                    {"Content-Type": content_type},
                    ["content-length-range", MIN_UPLOAD_BYTES, MAX_SIZE_BYTES],
                ],
                ExpiresIn=PRESIGN_EXPIRES_SECONDS,
            )
        except ClientError as e:
            print(f"presign error: {type(e).__name__}")
            return _response(500, {"message": "Could not issue upload URL"})

        return _response(
            200,
            {
                "document_id": document_id,
                "s3_key": s3_key,
                "upload_url": post["url"],
                "upload_fields": post["fields"],
                "expires_in_seconds": PRESIGN_EXPIRES_SECONDS,
                "max_size_bytes": MAX_SIZE_BYTES,
                "content_type": content_type,
                "filename": filename,
            },
        )
    except Exception as e:  # noqa: BLE001
        print(f"Error in documents handler: {type(e).__name__}")
        return _response(500, {"message": "Internal error"})
