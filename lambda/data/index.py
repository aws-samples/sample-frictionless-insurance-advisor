"""
Data Explorer Lambda

Read-only browser over the reference documents that ground the assistant's
answers. Backs the React frontend's "Data" tab.

Routes:
  GET /data/files
    -> { "folders": [ { "folder": "portfolio", "files": [ {...}, ... ] }, ... ],
         "total": 60 }

  GET /data/file?folder=<folder>&key=<key>
    -> { "folder", "key", "name", "content_type", "content",
         "metadata": { "last_updated": "YYYY-MM-DD" } | {}, "size_bytes" }

Design notes:
- The knowledge corpus is spread across SIX separate S3 buckets (one per
  s3-data/ subfolder), so this Lambda resolves a caller-supplied *folder
  alias* to a bucket env var — the same alias->bucket pattern used by
  lambda/comparator/index.py. Callers never see or supply bucket names.
- SECURITY: this endpoint takes a caller-supplied S3 key, so it would be an
  arbitrary-object reader across six buckets if unguarded. Three layers:
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
from botocore.exceptions import ClientError

s3_client = boto3.client("s3")

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
}

# Display order in the UI: Unicorn's own material first, then competitive
# intel, then operational schemas.
FOLDER_ORDER = [
    "portfolio",
    "company",
    "competitive",
    "competitors",
    "promotion",
    "forms",
]

# Only text documents are browsable.
ALLOWED_EXTENSIONS = (".md", ".json")

# Permits nested keys like `products/bigrival/primecare-health.md` while
# excluding anything that could escape the bucket prefix or smuggle a scheme.
SAFE_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")

CONTENT_TYPE_BY_EXT = {".md": "markdown", ".json": "json"}


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


def _is_browsable(key: str) -> bool:
    """True for text documents we're willing to serve.

    Excludes editor/OS/build cruft that can end up inside a BucketDeployment
    asset. Every path SEGMENT is checked, not just the filename, so a file
    nested under a hidden or private directory (`.git/`, `__pycache__/`) is
    filtered even when its own name looks clean.
    """
    lowered = key.lower()
    if not lowered.endswith(ALLOWED_EXTENSIONS):
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
    for page in paginator.paginate(Bucket=bucket):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if not _is_browsable(key):
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


def _list_all() -> dict:
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
    return {"folders": folders, "total": total}


def _get_file(folder: str, key: str) -> dict:
    """Fetch one document after validating folder + key.

    Raises LookupError for anything the caller shouldn't reach; the handler
    maps that to a 404 so we never distinguish "blocked" from "absent".
    """
    bucket = _bucket_for(folder)

    if not SAFE_KEY_RE.match(key) or ".." in key.split("/"):
        raise LookupError(f"Invalid key: {key}")
    if not _is_browsable(key):
        raise LookupError(f"Not a browsable document: {key}")

    # Membership check: only serve keys this folder actually advertises, so a
    # crafted-but-regex-clean key can't reach an unlisted object.
    known = {f["key"] for f in _list_folder_objects(folder)}
    if key not in known:
        raise LookupError(f"No such document: {folder}/{key}")

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
            return _response(200, _list_all())

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
