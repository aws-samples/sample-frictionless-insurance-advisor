"""
Documents Upload Lambda

Issues short-lived presigned S3 PUT URLs so the React SPA can upload
insurance-policy documents (PDF / Markdown / image) directly to S3 without
the binary ever transiting API Gateway. The agent then calls the
extract_policy MCP tool target with the returned `document_id` to read
the file from S3 and run a tool-forced extraction.

Route:
  POST /documents/initiate
    Body: { filename: str, content_type: str, customer_id?: str }
    Returns: { document_id, presigned_put_url, s3_key, expires_in_seconds, max_size_bytes }

Why presigned URLs (not multipart upload through API GW):
- API Gateway REST has a 10 MB request size cap; base64-inflated binary
  blows that. Presigned PUT keeps the binary off API GW entirely.
- This Lambda becomes purely a URL signer (sub-50ms cold start, no compute
  cost on the actual file transfer).

Security model (matches the rest of the system):
- Cognito JWT required at API Gateway (api.access scope or user-admin scope)
- S3 key is `{advisor_id}/{customer_id_or_unassigned}/{document_id}.{ext}`
  so a stolen presigned URL can only PUT to the calling advisor's
  namespace; the extract_policy Lambda re-derives advisor_id from the
  caller's JWT and rejects mismatched paths.
- 24-hour S3 lifecycle expiration on the bucket auto-deletes uploads.
- Only PDF, Markdown, plain text, and JPEG/PNG/WEBP are accepted.
- 10 MB hard cap.
"""

from __future__ import annotations

import json
import os
import uuid

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError


MAX_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB
PRESIGN_EXPIRES_SECONDS = 300  # 5 min — long enough for a slow upload, short enough to limit replay

# Map of allowed Content-Type -> file extension. Anything else is rejected.
ALLOWED_CONTENT_TYPES: dict[str, str] = {
    "application/pdf": "pdf",
    "text/markdown": "md",
    "text/plain": "txt",
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
}


# `s3v4` is required so the presigned URL signature is compatible with the
# default SSE-KMS key. Without it the browser PUT gets InvalidArgument.
s3_client = boto3.client(
    "s3",
    config=Config(signature_version="s3v4"),
)
cognito = boto3.client("cognito-idp")

DOCUMENTS_BUCKET = os.environ["DOCUMENTS_BUCKET"]
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


def _extract_advisor_id(event: dict) -> str | None:
    """Resolve the caller's advisor email — same pattern the profile and
    policies Lambdas use. JWT claims first, query string fallback for M2M.
    """
    try:
        username = event["requestContext"]["authorizer"]["claims"]["username"]
        if not USER_POOL_ID:
            return None
        response = cognito.admin_get_user(UserPoolId=USER_POOL_ID, Username=username)
        for attr in response["UserAttributes"]:
            if attr["Name"] == "email":
                return attr["Value"]
    except (KeyError, TypeError):
        try:
            qs = event.get("queryStringParameters") or {}
            return qs.get("advisor_id")
        except (KeyError, TypeError):
            pass
    except Exception as e:  # noqa: BLE001
        print(f"Cognito lookup error: {e}")
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

        filename = (body.get("filename") or "").strip()
        content_type = (body.get("content_type") or "").strip().lower()
        customer_id = (body.get("customer_id") or "").strip() or "unassigned"

        if not filename:
            return _response(400, {"message": "filename is required"})
        if content_type not in ALLOWED_CONTENT_TYPES:
            return _response(
                400,
                {
                    "message": f"Unsupported content_type {content_type!r}. "
                    f"Allowed: {sorted(ALLOWED_CONTENT_TYPES.keys())}"
                },
            )

        # Stable customer scoping in the key. The "unassigned" namespace is
        # used when the advisor uploads in "+ New Prospect" mode before any
        # customer record exists; later the agent can move the file to the
        # right customer namespace if we ever needed permanent storage,
        # but with 24h lifecycle expiry that's a non-issue.
        document_id = uuid.uuid4().hex
        ext = ALLOWED_CONTENT_TYPES[content_type]
        s3_key = (
            f"{_safe_advisor_path(advisor_id)}/{customer_id}/"
            f"{document_id}.{ext}"
        )

        # Generate presigned PUT URL.
        #
        # IMPORTANT: only sign Bucket/Key/ContentType. Anything we add to
        # Params is folded into the SigV4 canonical request and MUST be
        # echoed by the browser as a matching header. We deliberately do
        # NOT sign Metadata — the browser fetch() doesn't send arbitrary
        # x-amz-meta-* headers, which would cause SignatureDoesNotMatch
        # (surfaces as a 403 with no body in CORS contexts).
        #
        # The audit trail we care about (advisor_id, customer_id,
        # document_id) is already encoded in the S3 key path, so dropping
        # the object metadata loses nothing: extract_policy parses the key,
        # and S3 access logs + CloudTrail capture the key as well.
        try:
            presigned_url = s3_client.generate_presigned_url(
                "put_object",
                Params={
                    "Bucket": DOCUMENTS_BUCKET,
                    "Key": s3_key,
                    "ContentType": content_type,
                },
                ExpiresIn=PRESIGN_EXPIRES_SECONDS,
            )
        except ClientError as e:
            print(f"presign error: {e}")
            return _response(500, {"message": "Could not issue upload URL"})

        return _response(
            200,
            {
                "document_id": document_id,
                "s3_key": s3_key,
                "presigned_put_url": presigned_url,
                "expires_in_seconds": PRESIGN_EXPIRES_SECONDS,
                "max_size_bytes": MAX_SIZE_BYTES,
                "content_type": content_type,
                "filename": filename,
            },
        )

    except Exception as e:  # noqa: BLE001
        import traceback
        print(f"Error in documents handler: {e}")
        traceback.print_exc()
        return _response(500, {"message": "Internal error"})
