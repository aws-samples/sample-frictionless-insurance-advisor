"""
Applications Lambda: "Send for Signature" saves a whole application form.

POST /applications
    Body: {
      "product_type": "universal_life",
      "customer_id":  "9" | null,              # null for a prospect with no record yet
      "ref":          "APP-1A2B3C4D",          # optional: the reference of an earlier save to overwrite
      "fields": { "<path>": { "value": "...", "origin": "profile"|"voice"|"manual", "visible": true|false } }
    }
    -> 201 { "ref": "APP-1A2B3C4D", "saved_at": "...Z", "updated_at": "...Z", "revision": 1,
             "replaced": false, "omitted": ["insured.ssn"] }
    -> 200 same shape with "replaced": true when `ref` named one of this advisor's saved
       applications for the same customer and product: that object is overwritten in place
       (same key, same ref and saved_at, revision + 1). If the ref is not found (it expired,
       or it is not this advisor's) a new application is created and a new ref returned.

The application is written as one JSON object to the applications bucket under
    <advisor>/<customer id | _prospect>/<product type>/<timestamp>-<ref>.json
The Data Explorer's "applications" folder (lambda/data) lists and reads them back,
for the signed-in advisor only.

Rules enforced here, not in the browser:
- The advisor is whoever the verified sign-in token says. A machine token (the
  AgentCore gateway's) has no user and is refused.
- A customer must belong to that advisor.
- The product type must be one of the published forms, and every path in the
  request must be a field of that form's definition. Anything else is a 400.
- A field marked `sensitive: "secret"` in the form definition (the Social
  Security number) is never stored, whatever the request says. The response
  lists what was left out.
- Hidden fields are kept (marked "visible": false) in case they are wanted.
- An overwrite can only touch an object under the advisor's own prefix, in the folder of
  the customer and product named in the request. The key is found by listing that folder
  for the ref, never built from anything the client supplies.
Values are never written to the logs.
"""
import datetime
import json
import os
import re
import secrets
from urllib.parse import quote

import boto3

USER_SCOPE = "aws.cognito.signin.user.admin"
FORMS_BUCKET = os.environ.get("FORMS_BUCKET", "")
APPLICATIONS_BUCKET = os.environ.get("APPLICATIONS_BUCKET", "")
PROFILES_TABLE = os.environ.get("PROFILES_TABLE", "")
USER_POOL_ID = os.environ.get("USER_POOL_ID", "")

# Must match lambda/formschema and the assistants' maps: this is also the S3 key allowlist.
FORM_FILES = {
    "term_life": "awesome-term-life-form.json",
    "whole_life": "awesome-whole-life-form.json",
    "universal_life": "awesome-universal-life-form.json",
    "variable_life": "awesome-variable-life-form.json",
    "critical_illness": "awesome-critical-illness-form.json",
    "health": "awesome-health-form.json",
    "home": "awesome-home-form.json",
    "auto": "awesome-motor-form.json",
    "travel": "awesome-travel-form.json",
    "disability": "awesome-disability-form.json",
}
ORIGINS = {"profile", "voice", "manual"}
CUSTOMER_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
REF_RE = re.compile(r"^APP-[0-9A-F]{8}$")
PROSPECT_FOLDER = "_prospect"
MAX_FIELDS = 300
MAX_VALUE_LEN = 2000
MAX_BODY_BYTES = 61_440  # the most the WAF lets through on this route (see cdk/tools_stack.py)

_clients = {}


def _client(name):
    if name not in _clients:
        _clients[name] = boto3.client(name)
    return _clients[name]


def _response(status, body):
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "Content-Type,Authorization",
            "Access-Control-Allow-Methods": "POST,OPTIONS",
        },
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


def advisor_from_event(event):
    """The signed-in advisor's email, from the verified token only (None for a machine token)."""
    try:
        claims = event["requestContext"]["authorizer"]["claims"]
        username = claims["username"]
        scopes = str(claims.get("scope", "")).split()
    except (KeyError, TypeError):
        return None
    if USER_SCOPE not in scopes or not USER_POOL_ID:
        return None
    try:
        user = _client("cognito-idp").admin_get_user(UserPoolId=USER_POOL_ID, Username=username)
        return _verified_email(user)
    except Exception as exc:  # noqa: BLE001
        print(f"Cognito lookup failed: {type(exc).__name__}")
    return None


def advisor_prefix(advisor_id):
    """Must match lambda/data (and voice-agent/transcripts.py safe_advisor_path)."""
    return advisor_id.replace("@", "_at_").replace("/", "_").replace(":", "_") + "/"


def fields_of(schema):
    return [f for s in schema.get("sections", []) for f in s.get("fields", [])]


def load_schema(product_type):
    obj = _client("s3").get_object(Bucket=FORMS_BUCKET, Key=FORM_FILES[product_type])
    return json.loads(obj["Body"].read().decode("utf-8"))


def _number_ok(text):
    try:
        float(str(text).replace(",", "").replace("$", "").strip())
        return True
    except ValueError:
        return False


def clean_fields(schema, raw):
    """Validate the request's fields against the form definition.

    Returns (answers, omitted, error). `answers` maps path to
    {"value", "origin", "visible"} for every non-secret field with a value, in
    the form's own order. `omitted` lists the paths left out because they are secret.
    """
    if not isinstance(raw, dict):
        return None, None, "fields must be an object"
    if len(raw) > MAX_FIELDS:
        return None, None, f"at most {MAX_FIELDS} fields"
    by_path = {f["path"]: f for f in fields_of(schema)}
    unknown = [p for p in raw if p not in by_path]
    if unknown:
        return None, None, f"not fields of this form: {', '.join(sorted(map(str, unknown))[:5])}"
    answers = {}
    # Every secret field of the form is listed as not stored, whether or not the request carried it.
    omitted = [path for path, field in by_path.items() if field.get("sensitive") == "secret"]
    for path, field in by_path.items():
        if path not in raw:
            continue
        entry = raw[path]
        if not isinstance(entry, dict):
            return None, None, f"{field['label']}: expected an object with a value"
        if field.get("sensitive") == "secret":
            continue
        value = entry.get("value")
        if value is None or (isinstance(value, str) and value == ""):
            continue
        if isinstance(value, bool):
            value = "true" if value else "false"
        if isinstance(value, (int, float)):
            value = str(value)
        if not isinstance(value, str):
            return None, None, f"{field['label']}: the value must be text"
        if len(value) > MAX_VALUE_LEN:
            return None, None, f"{field['label']}: too long"
        kind = field.get("type")
        if kind == "select" and value not in [o["value"] for o in field.get("options", [])]:
            return None, None, f"{field['label']}: not one of the choices"
        if kind == "boolean" and value not in ("true", "false"):
            return None, None, f"{field['label']}: must be yes or no"
        if kind in ("number", "currency") and not _number_ok(value):
            return None, None, f"{field['label']}: must be a number"
        origin = entry.get("origin")
        if origin not in ORIGINS:
            origin = "manual"
        answers[path] = {"value": value, "origin": origin, "visible": entry.get("visible") is not False}
    return answers, omitted, None


def display_value(field, value):
    """The answer as a person reads it: the option's label, Yes/No."""
    if field.get("type") == "select":
        for o in field.get("options", []):
            if o["value"] == value:
                return o["label"]
    if field.get("type") == "boolean":
        return "Yes" if value == "true" else "No"
    return value


def build_document(schema, answers, omitted, *, ref, saved_at, advisor_id, customer, updated_at=None, revision=1):
    labels = {}
    sections = []
    for s in schema.get("sections", []):
        rows = []
        for f in s.get("fields", []):
            labels[f["path"]] = f["label"]
            a = answers.get(f["path"])
            if a:
                rows.append({"path": f["path"], "label": f["label"], "value": a["value"], "display": display_value(f, a["value"]),
                             "origin": a["origin"], "visible": a["visible"],
                             **({"sensitive": f["sensitive"]} if f.get("sensitive") else {})})
        if rows:
            sections.append({"id": s["id"], "title": s["title"], "fields": rows})
    return {
        "ref": ref,
        "saved_at": saved_at,
        "updated_at": updated_at or saved_at,
        "revision": revision,
        "status": "sent_for_signature",
        "product_type": schema["product_type"],
        "product_name": schema.get("product_name"),
        "form_id": schema.get("form_id"),
        "form_title": schema.get("title"),
        "advisor": advisor_id,
        "customer": customer,
        "omitted": [{"path": p, "label": labels.get(p, p), "reason": "secret field, never stored"} for p in omitted],
        "answers": {p: a["value"] for p, a in answers.items()},
        "sections": sections,
    }


def object_key(advisor_id, customer_id, product_type, saved_at, ref):
    stamp = saved_at.replace("-", "").replace(":", "")
    stamp = stamp.split(".")[0].rstrip("Z") + "Z"
    return f"{advisor_prefix(advisor_id)}{customer_id or PROSPECT_FOLDER}/{product_type}/{stamp}-{ref}.json"


def find_existing(advisor_id, customer_id, product_type, ref):
    """The key of this advisor's saved application with this ref, or None.

    Only the folder for this advisor, customer (or prospect) and product is listed, and a key
    counts only if it has the exact shape lambda/applications writes, so nothing outside it
    can be matched whatever the ref says.
    """
    prefix = f"{advisor_prefix(advisor_id)}{customer_id or PROSPECT_FOLDER}/{product_type}/"
    shape = re.compile(r"^\d{8}T\d{6}Z-" + re.escape(ref) + r"\.json$")
    paginator = _client("s3").get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=APPLICATIONS_BUCKET, Prefix=prefix):
        for obj in page.get("Contents", []):
            if shape.match(obj["Key"][len(prefix):]):
                return obj["Key"]
    return None


def previous_state(key):
    """(saved_at, revision) of the object at `key`. Falls back to the key's own time and 1."""
    stamp = key.rsplit("/", 1)[-1][:16]  # YYYYMMDDTHHMMSSZ
    saved_at = f"{stamp[0:4]}-{stamp[4:6]}-{stamp[6:8]}T{stamp[9:11]}:{stamp[11:13]}:{stamp[13:15]}Z"
    revision = 1
    try:
        old = json.loads(_client("s3").get_object(Bucket=APPLICATIONS_BUCKET, Key=key)["Body"].read().decode("utf-8"))
        if isinstance(old.get("saved_at"), str):
            saved_at = old["saved_at"]
        if isinstance(old.get("revision"), int) and old["revision"] >= 1:
            revision = old["revision"]
    except Exception as exc:  # noqa: BLE001
        print(f"previous revision unreadable: {type(exc).__name__}")
    return saved_at, revision


def handler(event, context):
    try:
        method = (event.get("httpMethod") or "POST").upper()
        if method == "OPTIONS":
            return _response(200, {})
        if method != "POST":
            return _response(405, {"message": "Only POST supported"})
        advisor_id = advisor_from_event(event)
        if not advisor_id:
            return _response(401, {"message": "Unauthorized - no advisor ID found"})
        raw_body = event.get("body") or "{}"
        if len(raw_body) > MAX_BODY_BYTES:
            return _response(413, {"message": "The application is too large"})
        try:
            body = json.loads(raw_body)
        except json.JSONDecodeError:
            return _response(400, {"message": "Body is not valid JSON"})
        if not isinstance(body, dict):
            return _response(400, {"message": "Body must be a JSON object"})

        product_type = body.get("product_type")
        if not isinstance(product_type, str) or product_type not in FORM_FILES:
            return _response(400, {"message": "product_type is not a published form"})

        earlier_ref = body.get("ref")
        if earlier_ref is not None and (not isinstance(earlier_ref, str) or not REF_RE.match(earlier_ref)):
            return _response(400, {"message": "ref is not valid"})

        customer_id = body.get("customer_id")
        customer = None
        if customer_id is not None:
            if not isinstance(customer_id, str) or not CUSTOMER_ID_RE.match(customer_id):
                return _response(400, {"message": "customer_id is not valid"})
            profile = boto3.resource("dynamodb").Table(PROFILES_TABLE).get_item(Key={"customer_id": customer_id}).get("Item")
            if not profile or profile.get("advisor_id") != advisor_id:
                return _response(404, {"message": "Customer not found for this advisor"})
            customer = {"customer_id": customer_id, "name": profile.get("name")}

        schema = load_schema(product_type)
        answers, omitted, err = clean_fields(schema, body.get("fields"))
        if err:
            return _response(400, {"message": err})
        if not answers:
            return _response(400, {"message": "The application has no answers to save"})

        now = datetime.datetime.now(datetime.timezone.utc)
        updated_at = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        existing_key = find_existing(advisor_id, customer_id, product_type, earlier_ref) if earlier_ref else None
        if existing_key:
            # Re-saving an application that was saved before: replace it, do not add a second one.
            ref = earlier_ref
            key = existing_key
            saved_at, previous = previous_state(key)
            revision = previous + 1
        else:
            ref = "APP-" + secrets.token_hex(4).upper()
            saved_at = updated_at
            revision = 1
            key = object_key(advisor_id, customer_id, product_type, saved_at, ref)
        document = build_document(schema, answers, omitted, ref=ref, saved_at=saved_at, advisor_id=advisor_id,
                                  customer=customer, updated_at=updated_at, revision=revision)
        _client("s3").put_object(
            Bucket=APPLICATIONS_BUCKET,
            Key=key,
            Body=json.dumps(document, ensure_ascii=False, indent=2).encode("utf-8"),
            ContentType="application/json",
            ServerSideEncryption="AES256",
            # Shown in the Data Explorer list without reading each object. ASCII only.
            Metadata={
                "ref": ref,
                "product": product_type,
                "saved": saved_at,
                "updated": updated_at,
                "revision": str(revision),
                "customer": quote(customer["name"] or "") if customer else "",
                "customer-id": customer_id or "",
                "fields": str(len(answers)),
            },
        )
        replaced = revision > 1
        print(f"application saved ref={ref} product={product_type} fields={len(answers)} omitted={len(omitted)} revision={revision}")
        return _response(200 if replaced else 201, {"ref": ref, "saved_at": saved_at, "updated_at": updated_at,
                                                     "revision": revision, "replaced": replaced, "omitted": omitted})
    except Exception as exc:  # noqa: BLE001
        # Type only: the message of a failed write could name a key built from the advisor's address.
        print(f"applications failed: {type(exc).__name__}")
        return _response(500, {"message": "An internal error occurred while saving the application"})
