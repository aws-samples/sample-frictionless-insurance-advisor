"""
Data Explorer Lambda

Read-only browser over the reference documents that ground the assistant's
answers. Backs the React frontend's "Data" tab.

Routes:
  GET /data/samples
    -> { "samples": [ { "key", "product_type", "product_name", "carrier_name" } ] }
       Sample competitor brochures offered as downloads in the Comparator.
       They are not part of the browsable corpus and not reachable by any agent.
  GET /data/files
    -> { "folders": [ { "folder": "portfolio", "files": [ {...}, ... ] }, ... ],
         "total": 60 }

  GET /data/file?folder=<folder>&key=<key>
    -> { "folder", "key", "name", "content_type", "content",
         "metadata": { "last_updated": "YYYY-MM-DD" } | {}, "size_bytes" }

  The `transcripts` folder is different from the rest: it holds the signed-in
  advisor's own voice transcripts (kept one day) and nobody else's. The caller
  is identified from the verified sign-in token only (claims.username looked up
  in Cognito), the listing covers only that advisor's prefix, and a request for
  anything outside it is a 404. A machine token (the AgentCore gateway's) has
  no user, so it never sees the folder.

Design notes:
- The knowledge corpus is spread across SEVEN separate S3 buckets (one per
  s3-data/ subfolder), so this Lambda resolves a caller-supplied *folder
  alias* to a bucket env var — the same alias->bucket pattern used by
  lambda/comparator/index.py. Callers never see or supply bucket names.
- SECURITY: this endpoint takes a caller-supplied S3 key, so it would be an
  arbitrary-object reader across seven buckets if unguarded. Three layers:
    1. `folder` must be in FOLDER_ENV_VARS (allowlist -> bucket).
    2. `key` must match SAFE_KEY_RE and contain no '..' segment.
    3. the key must actually appear in that folder's object listing before
       we GET it (membership check). Cheap: <= 20 objects per bucket.
- Only text formats are served: .md and .json. Generated .pdf artefacts and
  anything else are filtered out of listings and rejected on fetch, so this
  never streams binary through API Gateway.
- Front-matter (`---\\nlast_updated: ...\\n---`) is split off the body and
  returned as `metadata`, so the frontend can render a clean document and
  show the date as a proper field instead of stray text. Mirrors the parser
  in lambda/comparator/index.py.
"""
import json
import os
import re
import traceback
from typing import Any

import boto3
from urllib.parse import unquote
from botocore.config import Config
from botocore.exceptions import ClientError

# SigV4 so the presigned brochure links work with bucket-default encryption
# and in every region.
s3_client = boto3.client("s3", config=Config(signature_version="s3v4"))

# Folder alias -> environment variable holding that folder's bucket name.
# Keys of this dict double as the caller-facing allowlist. `mock-policies` is
# deliberately absent: it is not deployed to any bucket (the SPA already
# serves those files statically from /mock-policies/ via
# react-frontend/scripts/sync-mock-policies.sh).
FOLDER_ENV_VARS: dict[str, str] = {
    "portfolio": "PORTFOLIO_BUCKET",
    "company": "COMPANY_BUCKET",
    "competitive": "COMPETITIVE_BUCKET",
    "competitors": "COMPETITORS_BUCKET",
    "promotion": "PROMOTION_BUCKET",
    "forms": "FORMS_BUCKET",
    "pricing": "PRICING_BUCKET",
    # One brochures bucket, two prefixes (see FOLDER_PREFIX). `brochures` is the
    # Awesome set shown in Data Explorer; `samples` holds the fictitious
    # competitor's sample brochures for the Comparator and is never listed.
    "brochures": "BROCHURES_BUCKET",
    "samples": "BROCHURES_BUCKET",
    # Per-advisor; never listed or read without a signed-in user (see
    # _advisor_from_event). Not in FOLDER_ORDER on purpose.
    "transcripts": "TRANSCRIPTS_BUCKET",
}
FOLDER_PREFIX: dict[str, str] = {"brochures": "awesome/", "samples": "samples/"}
# Folders whose objects are PDFs. These are not read through the Lambda: the
# caller gets short-lived presigned S3 links, so no binary passes through
# API Gateway.
PDF_FOLDERS = {"brochures", "samples"}
PRESIGN_SECONDS = 120
USER_POOL_ID = os.environ.get("USER_POOL_ID", "")
MAX_TRANSCRIPTS_LISTED = 50
# <start time>-<session id>.json, as written by the voice runtime.
TRANSCRIPT_KEY_RE = re.compile(r"^\d{8}T\d{6}Z-[A-Za-z0-9_-]{1,48}\.json$")
USER_SCOPE = "aws.cognito.signin.user.admin"

# Display order in the UI: Awesome's own material first, then competitive
# intel, then operational schemas.
FOLDER_ORDER = [
    "portfolio",
    "company",
    "competitive",
    "competitors",
    "promotion",
    "forms",
    "pricing",
    "brochures",
]

# Only text documents are browsable.
ALLOWED_EXTENSIONS = (".md", ".json")
PDF_EXTENSIONS = (".pdf",)

# Permits nested keys like `products/bigrival/primecare-health.md` while
# excluding anything that could escape the bucket prefix or smuggle a scheme.
SAFE_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")

CONTENT_TYPE_BY_EXT = {".md": "markdown", ".json": "json", ".pdf": "pdf"}


def _cors_headers() -> dict:
    return {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Headers": "Content-Type,Authorization",
        "Access-Control-Allow-Methods": "GET,OPTIONS",
    }


def _response(status: int, body: Any) -> dict:
    return {
        "statusCode": status,
        "headers": _cors_headers(),
        "body": json.dumps(body, default=str),
    }


def _bucket_for(folder: str) -> str:
    """Resolve a folder alias to its bucket name, or raise LookupError."""
    env_var = FOLDER_ENV_VARS.get(folder)
    if not env_var:
        raise LookupError(f"Unknown data folder: {folder}")
    bucket = os.environ.get(env_var)
    if not bucket:
        # Misconfiguration, not a caller error.
        raise ValueError(f"{env_var} environment variable not set")
    return bucket


def _is_browsable(key: str, folder: str = "") -> bool:
    """True for text documents we're willing to serve.

    Excludes editor/OS/build cruft that can end up inside a BucketDeployment
    asset. Every path SEGMENT is checked, not just the filename, so a file
    nested under a hidden or private directory (`.git/`, `__pycache__/`) is
    filtered even when its own name looks clean.
    """
    lowered = key.lower()
    allowed = PDF_EXTENSIONS if folder in PDF_FOLDERS else ALLOWED_EXTENSIONS
    if not lowered.endswith(allowed):
        return False
    return not any(seg.startswith((".", "_")) for seg in lowered.split("/") if seg)


def _decode(raw: bytes) -> str:
    """UTF-8, falling back to cp1252 then lossy UTF-8.

    Same ladder as the other S3-reading Lambdas: source docs are authored as
    UTF-8 but Windows-1252-only bytes (em dashes, smart quotes) occasionally
    arrive via copy/paste from Word, and we would rather serve a document
    with one replaced character than 502 the whole request.
    """
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        try:
            return raw.decode("cp1252")
        except UnicodeDecodeError:
            return raw.decode("utf-8", errors="replace")


def _parse_front_matter(text: str) -> tuple[dict, str]:
    """Split a leading `--- ... ---` block off a document.

    Returns (metadata, body). Only simple `key: value` lines are parsed —
    enough for `last_updated` without taking a YAML dependency. Documents
    without front-matter come back as ({}, original).
    """
    if not text.startswith("---"):
        return {}, text
    lines = text.splitlines()
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        return {}, text
    meta: dict[str, str] = {}
    for line in lines[1:end]:
        if ":" in line:
            key, _, value = line.partition(":")
            meta[key.strip()] = value.strip()
    return meta, "\n".join(lines[end + 1 :]).lstrip("\n")


def _list_folder_objects(folder: str) -> list[dict]:
    """Paginated listing of one folder's browsable objects."""
    bucket = _bucket_for(folder)
    paginator = s3_client.get_paginator("list_objects_v2")
    out: list[dict] = []
    for page in paginator.paginate(Bucket=bucket, Prefix=FOLDER_PREFIX.get(folder, "")):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if not _is_browsable(key, folder):
                continue
            ext = "." + key.rsplit(".", 1)[-1].lower()
            out.append(
                {
                    "folder": folder,
                    "key": key,
                    # Filename without directories — the tree shows the
                    # nesting separately via `path`.
                    "name": key.rsplit("/", 1)[-1],
                    # Directory portion ('' for root-level files), so the UI
                    # can group competitors/products/<carrier>/ sensibly.
                    "path": key.rsplit("/", 1)[0] if "/" in key else "",
                    "content_type": CONTENT_TYPE_BY_EXT.get(ext, "text"),
                    "size_bytes": obj.get("Size", 0),
                    "last_modified": obj.get("LastModified"),
                }
            )
    out.sort(key=lambda f: (f["path"], f["name"]))
    return out


def _advisor_from_event(event: dict) -> str | None:
    """The signed-in advisor's email, from the verified token only.

    Returns None for the gateway's machine token (no username, no user scope)
    and for anything unverifiable. There is no request-supplied fallback.
    """
    try:
        claims = event["requestContext"]["authorizer"]["claims"]
        username = claims["username"]
        scopes = str(claims.get("scope", "")).split()
    except (KeyError, TypeError):
        return None
    if USER_SCOPE not in scopes or not USER_POOL_ID:
        return None
    try:
        user = boto3.client("cognito-idp").admin_get_user(UserPoolId=USER_POOL_ID, Username=username)
        for attr in user["UserAttributes"]:
            if attr["Name"] == "email" and attr["Value"]:
                return attr["Value"]
    except Exception as exc:  # noqa: BLE001
        print(f"Cognito lookup failed: {type(exc).__name__}")
    return None


def _advisor_prefix(advisor_id: str) -> str:
    """Must match voice-agent/transcripts.py safe_advisor_path."""
    return advisor_id.replace("@", "_at_").replace("/", "_").replace(":", "_") + "/"


def _truthy_meta(value: Any) -> bool:
    return str(value) == "1"


def _list_transcripts(advisor_id: str) -> list[dict]:
    """The advisor's own transcripts, newest first. Never another advisor's."""
    bucket = _bucket_for("transcripts")
    prefix = _advisor_prefix(advisor_id)
    paginator = s3_client.get_paginator("list_objects_v2")
    found: list[dict] = []
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            name = obj["Key"][len(prefix):]
            if TRANSCRIPT_KEY_RE.match(name):
                found.append({"name": name, "obj": obj})
    found.sort(key=lambda f: f["name"], reverse=True)
    out: list[dict] = []
    for item in found[:MAX_TRANSCRIPTS_LISTED]:
        obj = item["obj"]
        meta: dict = {}
        try:
            head = s3_client.head_object(Bucket=bucket, Key=obj["Key"]).get("Metadata", {})
            meta = {
                "started_at": head.get("started"),
                "updated_at": head.get("updated"),
                "turns": int(head.get("turns", "0") or 0),
                "customer": unquote(head.get("customer", "")) or None,
                "ended": _truthy_meta(head.get("ended")),
            }
        except (ClientError, ValueError):
            pass
        out.append(
            {
                "folder": "transcripts",
                "key": item["name"],
                "name": item["name"],
                "path": "",
                "content_type": "transcript",
                "size_bytes": obj.get("Size", 0),
                "last_modified": obj.get("LastModified"),
                "meta": meta,
            }
        )
    return out


def _get_transcript(advisor_id: str, key: str) -> dict:
    """One of the advisor's transcripts. LookupError for anything else."""
    if not TRANSCRIPT_KEY_RE.match(key):
        raise LookupError(f"Invalid transcript key: {key}")
    bucket = _bucket_for("transcripts")
    try:
        obj = s3_client.get_object(Bucket=bucket, Key=_advisor_prefix(advisor_id) + key)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in ("NoSuchKey", "404", "AccessDenied"):
            raise LookupError(f"No such transcript: {key}") from exc
        raise
    raw = obj["Body"].read()
    text = _decode(raw)
    updated = None
    ended = False
    try:
        doc = json.loads(text)
        updated = doc.get("updated_at")
        ended = bool(doc.get("ended"))
        text = json.dumps(doc, indent=2, ensure_ascii=False)
    except ValueError:
        pass
    return {
        "folder": "transcripts",
        "key": key,
        "name": key,
        "content_type": "transcript",
        "content": text,
        "metadata": {"updated_at": updated, "ended": ended},
        "size_bytes": len(raw),
    }


def _list_all(advisor_id: str | None = None) -> dict:
    """Listing for every configured folder, in display order.

    A single folder failing (e.g. a bucket that hasn't finished deploying)
    degrades to an empty list for that folder rather than failing the whole
    request — the rest of the corpus stays browsable.
    """
    folders = []
    total = 0
    for folder in FOLDER_ORDER:
        try:
            files = _list_folder_objects(folder)
        except (ClientError, LookupError, ValueError) as exc:
            print(f"could not list folder {folder}: {exc}")
            files = []
        total += len(files)
        folders.append({"folder": folder, "files": files})
    # The advisor's own voice transcripts, only for a signed-in user.
    if advisor_id:
        try:
            files = _list_transcripts(advisor_id)
        except (ClientError, LookupError, ValueError) as exc:
            print(f"could not list transcripts: {exc}")
            files = []
        total += len(files)
        folders.append({"folder": "transcripts", "files": files})
    return {"folders": folders, "total": total}


def _pdf_links(folder: str, bucket: str, key: str, listing: dict) -> dict:
    """Short-lived links to view or download one PDF straight from S3."""
    name = key.rsplit("/", 1)[-1]
    base = {"Bucket": bucket, "Key": key, "ResponseContentType": "application/pdf"}
    view = s3_client.generate_presigned_url(
        "get_object", ExpiresIn=PRESIGN_SECONDS,
        Params={**base, "ResponseContentDisposition": f'inline; filename="{name}"'},
    )
    download = s3_client.generate_presigned_url(
        "get_object", ExpiresIn=PRESIGN_SECONDS,
        Params={**base, "ResponseContentDisposition": f'attachment; filename="{name}"'},
    )
    return {
        "folder": folder,
        "key": key,
        "name": name,
        "content_type": "pdf",
        "view_url": view,
        "download_url": download,
        "expires_in_seconds": PRESIGN_SECONDS,
        "metadata": {},
        "size_bytes": listing.get("size_bytes", 0),
    }


def _list_samples() -> dict:
    """Manifest of the sample competitor brochures, limited to PDFs that exist."""
    bucket = _bucket_for("samples")
    try:
        manifest = json.loads(s3_client.get_object(Bucket=bucket, Key="samples/index.json")["Body"].read())
    except ClientError as exc:
        print(f"sample manifest unavailable: {exc}")
        return {"samples": []}
    present = {f["key"] for f in _list_folder_objects("samples")}
    samples = [
        {k: item[k] for k in ("key", "product_type", "product_name", "carrier_name")}
        for item in manifest.get("samples", [])
        if item.get("key") in present
    ]
    return {"samples": samples}


def _get_file(folder: str, key: str) -> dict:
    """Fetch one document after validating folder + key.

    Raises LookupError for anything the caller shouldn't reach; the handler
    maps that to a 404 so we never distinguish "blocked" from "absent".
    """
    bucket = _bucket_for(folder)

    if not SAFE_KEY_RE.match(key) or ".." in key.split("/"):
        raise LookupError(f"Invalid key: {key}")
    if not _is_browsable(key, folder):
        raise LookupError(f"Not a browsable document: {key}")

    # Membership check: only serve keys this folder actually advertises, so a
    # crafted-but-regex-clean key can't reach an unlisted object.
    known_files = _list_folder_objects(folder)
    known = {f["key"] for f in known_files}
    if key not in known:
        raise LookupError(f"No such document: {folder}/{key}")

    if folder in PDF_FOLDERS:
        return _pdf_links(folder, bucket, key, next(f for f in known_files if f["key"] == key))
    obj = s3_client.get_object(Bucket=bucket, Key=key)
    raw = obj["Body"].read()
    text = _decode(raw)

    ext = "." + key.rsplit(".", 1)[-1].lower()
    content_type = CONTENT_TYPE_BY_EXT.get(ext, "text")

    metadata: dict = {}
    if content_type == "markdown":
        metadata, text = _parse_front_matter(text)
    elif content_type == "json":
        # Pretty-print so the raw schema is readable in the viewer. Fall back
        # to the original text if it isn't valid JSON.
        try:
            text = json.dumps(json.loads(text), indent=2, ensure_ascii=False)
        except ValueError:
            pass

    return {
        "folder": folder,
        "key": key,
        "name": key.rsplit("/", 1)[-1],
        "content_type": content_type,
        "content": text,
        "metadata": metadata,
        "size_bytes": len(raw),
    }


def handler(event, context):
    try:
        method = event.get("httpMethod", "GET")
        if method != "GET":
            return _response(405, {"message": "Only GET supported"})

        resource = event.get("resource", "")
        params = event.get("queryStringParameters") or {}

        if resource.endswith("/files"):
            return _response(200, _list_all(_advisor_from_event(event)))
        if resource.endswith("/samples"):
            return _response(200, _list_samples())

        if resource.endswith("/file"):
            folder = (params.get("folder") or "").strip()
            key = (params.get("key") or "").strip()
            if not folder or not key:
                return _response(
                    400,
                    {
                        "message": "query params 'folder' and 'key' are required",
                        "available_folders": FOLDER_ORDER,
                    },
                )
            if folder not in FOLDER_ENV_VARS:
                return _response(
                    404,
                    {
                        "message": f"Unknown data folder: {folder}",
                        "available_folders": FOLDER_ORDER,
                    },
                )
            if folder == "transcripts":
                advisor_id = _advisor_from_event(event)
                if not advisor_id:
                    raise LookupError("transcripts need a signed-in advisor")
                return _response(200, _get_transcript(advisor_id, key))
            return _response(200, _get_file(folder, key))

        return _response(404, {"message": f"Unknown route: {resource}"})

    except LookupError as exc:
        # Invalid/blocked/absent key — deliberately indistinguishable.
        print(f"data explorer lookup rejected: {exc}")
        return _response(404, {"message": "Document not found"})
    except ClientError as exc:
        print(f"S3 error in data handler: {exc}")
        return _response(502, {"message": "Unable to read the document"})
    except Exception as exc:  # noqa: BLE001
        print(f"Error in data handler: {exc}")
        traceback.print_exc()
        return _response(500, {"message": "An internal error occurred"})
