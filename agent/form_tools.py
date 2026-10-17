"""Application-form tools for the text assistant.

The voice agent (voice-agent/app.py) has the same two tools. They differ in one
way that matters: the voice runtime holds one WebSocket for the whole
conversation, so it can remember the open form. This runtime builds a new
Agent for every chat turn, so nothing survives between turns on the server.
The browser sends the current form state with each message (`form` in the
payload), and the tools rebuild their state from it.

The tools never talk to the browser directly. They append typed events to a
list, and the entrypoint yields them into the same stream as the reply text:

    {"app_event": {"type": "form_open", "productType": ..., "schema": {...}}}
    {"app_event": {"type": "form_fill", "path": ..., "value": ..., ...}}

Schemas are read straight from the forms bucket (FORMS_BUCKET) instead of
going through the model as tool arguments, as the voice agent does.
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Callable

import boto3
from botocore.exceptions import ClientError
from strands import tool

logger = logging.getLogger(__name__)

FORMS_BUCKET = os.environ.get("FORMS_BUCKET", "")
_SAFE_PRODUCT_TYPE = re.compile(r"^[a-z0-9_]{1,64}$")
# Each product type is one file in the forms bucket, named after the product's
# portfolio file. The mapping is also the allowlist for the S3 key.
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
_SAFE_PATH = re.compile(r"^[a-z0-9_]+\.[a-z0-9_]+$")
_s3 = boto3.client("s3", region_name=os.environ.get("AWS_REGION")) if FORMS_BUCKET else None

# Bounds on what the browser may send back as form state.
_MAX_VALUES = 200
_MAX_VALUE_LEN = 500


def load_form_schema(product_type: str) -> dict[str, Any]:
    """Read one schema from the forms bucket. Raises FileNotFoundError if absent."""
    if not (_s3 and FORMS_BUCKET):
        raise RuntimeError("FORMS_BUCKET is not configured")
    key = FORM_FILES.get(product_type)
    if key is None:
        raise FileNotFoundError(product_type)
    try:
        obj = _s3.get_object(Bucket=FORMS_BUCKET, Key=key)
    except ClientError as e:
        if e.response["Error"]["Code"] in ("NoSuchKey", "404"):
            raise FileNotFoundError(product_type) from e
        raise
    return json.loads(obj["Body"].read().decode("utf-8"))


def parse_form_context(raw: Any) -> dict[str, Any] | None:
    """Validate the `form` object from the request payload.

    Returns {"product_type": str, "values": {path: str}} or None. Anything
    malformed is dropped: this is advisor-supplied display state, so it is
    never trusted for more than "which form is open and what does it hold".
    """
    if not isinstance(raw, dict):
        return None
    product = raw.get("productType")
    if not isinstance(product, str) or not _SAFE_PRODUCT_TYPE.match(product):
        return None
    values: dict[str, str] = {}
    raw_values = raw.get("values")
    if isinstance(raw_values, dict):
        for path, value in list(raw_values.items())[:_MAX_VALUES]:
            if not isinstance(path, str) or not _SAFE_PATH.match(path):
                continue
            if isinstance(value, (str, int, float, bool)) and str(value) != "":
                values[path] = str(value)[:_MAX_VALUE_LEN]
    return {"product_type": product, "values": values}


def _fields_of(schema: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        field
        for section in schema.get("sections", [])
        for field in section.get("fields", [])
        if isinstance(field, dict) and "path" in field
    ]


def _describe(field: dict[str, Any]) -> str:
    """One line per field: label the advisor sees, path to call with, accepted values."""
    bits = [f'"{field["label"]}" -> {field["path"]}']
    ftype = field.get("type")
    if ftype == "select":
        bits.append("one of [" + "|".join(o["value"] for o in field.get("options", [])) + "]")
    elif ftype == "boolean":
        bits.append("true|false")
    elif ftype in ("number", "currency"):
        bits.append("plain number")
    elif ftype == "date":
        bits.append("YYYY-MM-DD")
    return "  " + " — ".join(bits)


def coerce_value(field: dict[str, Any], raw: str) -> tuple[str | None, str]:
    """Map a typed value onto the exact value the form expects.

    A <select> silently ignores a value that is not one of its options, so a
    field would look filled while showing nothing. Match generously, then
    refuse rather than forward something the form cannot render.
    """
    ftype = field.get("type")
    text = (raw or "").strip()

    def norm(s: str) -> str:
        return re.sub(r"[^a-z0-9]", "", s.lower())

    if not text:
        return None, "The value is empty."
    if ftype == "boolean":
        if norm(text) in {"true", "yes", "y", "1", "correct", "affirmative"}:
            return "true", ""
        if norm(text) in {"false", "no", "n", "0", "negative", "none"}:
            return "false", ""
        return None, "Use 'true' or 'false'."
    if ftype == "select":
        options = field.get("options") or []
        target = norm(text)
        for opt in options:
            if norm(opt["value"]) == target:
                return opt["value"], ""
        for opt in options:
            if norm(opt["label"]) == target:
                return opt["value"], ""
        for opt in options:
            if target and (target in norm(opt["label"]) or norm(opt["label"]) in target):
                return opt["value"], ""
        allowed = ", ".join(f"{o['value']} ({o['label']})" for o in options)
        return None, f"Allowed values are: {allowed}."
    if ftype in ("number", "currency"):
        cleaned = re.sub(r"[^0-9.\-]", "", text)
        if not cleaned or cleaned in {"-", ".", "-."}:
            return None, "Provide a plain number, e.g. 500000."
        return cleaned, ""
    if ftype == "date":
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", text):
            return None, "Use the format YYYY-MM-DD."
        return text, ""
    return text, ""


def form_state_note(form_ctx: dict[str, Any] | None) -> str:
    """A short note for the model describing the form already on screen.

    Added to the advisor's message each turn, so the model knows a form is
    open and what it holds without having to ask or guess.
    """
    if not form_ctx:
        return ""
    try:
        schema = load_form_schema(form_ctx["product_type"])
    except Exception:  # noqa: BLE001
        logger.warning("form_state_note: schema %s unavailable", form_ctx["product_type"])
        return ""
    fields = _fields_of(schema)
    values = form_ctx["values"]
    by_path = {f["path"]: f for f in fields}
    filled = [
        f'{by_path[p]["label"]} = {v}' for p, v in sorted(values.items()) if p in by_path
    ]
    missing = [f["label"] for f in fields if f.get("required") and f["path"] not in values]
    lines = [
        f'[Application form open on the advisor\'s screen: {schema.get("title", form_ctx["product_type"])} '
        f'(product_type {form_ctx["product_type"]}).',
        "Already on the form: " + ("; ".join(filled) if filled else "nothing yet") + ".",
        "Required and still empty: " + (", ".join(missing) if missing else "none") + ".]",
    ]
    return "\n".join(lines)


def make_form_tools(
    emit: Callable[[dict[str, Any]], None],
    form_ctx: dict[str, Any] | None,
    prefill: dict[str, str],
) -> list:
    """Build the two tools for one request.

    emit:      appends a typed event for the entrypoint to stream to the browser
    form_ctx:  the form already open in the browser, if any (from parse_form_context)
    prefill:   field values known from the customer's record
    """
    session: dict[str, Any] = {"product_type": None, "fields": {}, "filled": {}}

    def _adopt(schema: dict[str, Any], product_type: str) -> list[dict[str, Any]]:
        fields = _fields_of(schema)
        session["product_type"] = product_type
        session["fields"] = {f["path"]: f for f in fields}
        return fields

    # A form opened on an earlier turn: rebuild what the tools need from it.
    if form_ctx:
        try:
            _adopt(load_form_schema(form_ctx["product_type"]), form_ctx["product_type"])
            session["filled"] = dict(form_ctx["values"])
        except Exception:  # noqa: BLE001
            logger.warning("Could not restore form %s", form_ctx["product_type"])

    @tool
    def open_application_form(product_type: str) -> str:
        """Open an application form on the advisor's screen and fill it from the chat.

        Call this when the advisor has asked to start an application and a
        specific product has been identified. It opens the form beside the
        chat and returns the fields to fill. It does not need get_form_schema.

        Args:
            product_type: One of term_life, whole_life, universal_life,
                variable_life, critical_illness, health, home, auto, travel,
                disability.
        """
        requested = (product_type or "").strip().lower()
        if not _SAFE_PRODUCT_TYPE.match(requested):
            return f"'{product_type}' is not a valid product identifier. Use snake_case, e.g. 'term_life'."
        try:
            schema = load_form_schema(requested)
        except FileNotFoundError:
            return (
                f"There is no application form published for '{requested}'. Tell the "
                "advisor which products have forms and ask which they want. Do not "
                "invent a product type."
            )
        except Exception:  # noqa: BLE001
            logger.exception("open_application_form: failed to load %s", requested)
            return "The application form service is temporarily unavailable. Tell the advisor."
        previous = session["product_type"]
        fields = _adopt(schema, requested)
        if not fields:
            return f"The '{requested}' form has no fields defined. Report this."
        emit({"type": "form_open", "productType": requested, "schema": schema})

        valid = set(session["fields"])
        on_file = {p: v for p, v in prefill.items() if p in valid and v not in (None, "")}
        for p, v in on_file.items():
            session["filled"].setdefault(p, str(v))
        carried = [
            session["fields"][p]["label"]
            for p in session["filled"]
            if p in valid and p not in on_file
        ]
        outstanding = [f for f in fields if f["path"] not in session["filled"]]
        required = [f for f in outstanding if f.get("required")]
        optional = [f for f in outstanding if not f.get("required")]

        # Descriptive data only. The rules for how to use it live in the system
        # prompt. Instructions to the model inside a tool result (two or more
        # imperative lines, such as "Do not ask for these" and "never by path")
        # make the guardrail's prompt-attack filter block the next turn.
        lines = [
            f"The {schema.get('title', requested)} form is open on the advisor's screen.",
            "",
            'Fields are listed as "field name" -> path used in fill_form_field — accepted values.',
        ]
        if on_file:
            already = ", ".join(
                f'{session["fields"][p]["label"]} = {v}' for p, v in sorted(on_file.items())
            )
            lines += ["", f"Already filled from the customer's record: {already}."]
        if previous and previous != requested:
            lines += ["", f"Switched from {previous}. Already answered: {', '.join(sorted(carried)) or 'none'}."]
        lines += ["", "Required fields not yet answered:"]
        lines += [_describe(f) for f in required] if required else ["  none"]
        lines += ["Optional fields not yet answered:"]
        lines += [_describe(f) for f in optional] if optional else ["  none"]
        return "\n".join(lines)

    @tool
    def fill_form_field(path: str, value: str, confidence: float, source: str) -> str:
        """Fill one field on the open application form.

        Call this as soon as the advisor's messages establish a value. One call
        per field.

        Args:
            path: Field path from the list returned by open_application_form.
            value: The value, as a string. For choice fields use the machine
                value shown in the list; for yes/no use true or false.
            confidence: 0.0 to 1.0. Use 0.85 or more only when the advisor
                stated the value plainly. Use lower when inferring; the field
                is then flagged for the advisor to confirm.
            source: The advisor's words the value came from, quoted briefly.
        """
        if not session["fields"]:
            return "No application form is open. Call open_application_form first."
        field = session["fields"].get(path)
        if field is None:
            return f"'{path}' is not a field on this form. Use one of the exact paths from the list."
        coerced, why = coerce_value(field, str(value))
        if coerced is None:
            return f"Could not set {field['label']} to '{value}'. {why} Call fill_form_field again with a valid value."
        try:
            score = min(1.0, max(0.0, float(confidence)))
        except (TypeError, ValueError):
            score = 0.6
        emit({
            "type": "form_fill",
            "path": path,
            "value": coerced,
            "confidence": score,
            "source": (source or "")[:200],
        })
        session["filled"][path] = coerced
        return f"{field['label']} set to '{coerced}'."

    return [open_application_form, fill_form_field]
