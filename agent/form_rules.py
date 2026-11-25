"""Rules for application-form definitions: when a field is shown, when it is required, and how
sensitive it is.

A form definition (s3-data/forms/<product>-form.json) lists fields. Besides `path`, `label`,
`type`, `required`, `hint` and `options`, a field may carry three optional keys:

  visible_when   A condition. The field is shown only while it holds. No key means always shown.
  required_when  A condition. The field is required while it holds (and it is shown).
                 `required: true` still means always required.
  sensitive      "secret": the assistants never fill, read back or log it (the SSN). The advisor
                 types it on screen, where it is masked.
                 "health": health information. The assistants may fill and read it, but its value
                 is never written to a log.

A condition is a map of field path -> expected value. Every entry must match. An expected value may
be a list, which means "any of these". A list of such maps means "any of these maps holds". Values
are compared as text, so a boolean is "true" or "false". A field with no answer matches nothing.
This is the shape the pricing files already use for `required_when`.

A hidden field is never required. Answers typed into a field that later becomes hidden are kept
(the customer may change their mind) but do not count toward progress and are left off the PDF.

The same rules are implemented in react-frontend/src/lib/formRules.ts, and the voice agent carries
an identical copy of this file. Keep the three in step: they implement the same rules.
"""
from __future__ import annotations

from typing import Any

SENSITIVE_LEVELS = ("secret", "health")
FIELD_KEYS = {"path", "label", "type", "required", "hint", "options", "visible_when", "required_when", "sensitive"}
FIELD_TYPES = {"text", "date", "number", "currency", "select", "boolean"}
# What a log line shows instead of the value of a sensitive field.
HIDDEN = "[hidden]"


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def has_value(values: dict[str, Any], path: str) -> bool:
    return _text(values.get(path)) != ""


def condition_holds(condition: Any, values: dict[str, Any]) -> bool:
    """True when `condition` holds for the answers in `values`. No condition always holds."""
    if not condition:
        return True
    if isinstance(condition, list):
        return any(condition_holds(c, values) for c in condition)
    if not isinstance(condition, dict):
        return False
    for path, expected in condition.items():
        actual = _text(values.get(path))
        if actual == "":
            return False
        options = expected if isinstance(expected, list) else [expected]
        if actual not in [_text(o) for o in options]:
            return False
    return True


def is_visible(field: dict[str, Any], values: dict[str, Any]) -> bool:
    return condition_holds(field.get("visible_when"), values)


def is_required(field: dict[str, Any], values: dict[str, Any]) -> bool:
    if not is_visible(field, values):
        return False
    if field.get("required") is True:
        return True
    return bool(field.get("required_when")) and condition_holds(field["required_when"], values)


def fields_of(schema: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        f
        for section in schema.get("sections", [])
        for f in section.get("fields", [])
        if isinstance(f, dict) and "path" in f
    ]


def visible_fields(schema: dict[str, Any], values: dict[str, Any]) -> list[dict[str, Any]]:
    return [f for f in fields_of(schema) if is_visible(f, values)]


def required_fields(schema: dict[str, Any], values: dict[str, Any]) -> list[dict[str, Any]]:
    return [f for f in fields_of(schema) if is_required(f, values)]


def missing_required(schema: dict[str, Any], values: dict[str, Any]) -> list[dict[str, Any]]:
    """Fields that are shown, required, and still empty."""
    return [f for f in required_fields(schema, values) if not has_value(values, f["path"])]


def sensitivity(field: dict[str, Any]) -> str | None:
    level = field.get("sensitive")
    return level if level in SENSITIVE_LEVELS else None


def assistant_may_handle(field: dict[str, Any]) -> bool:
    """False for "secret" fields: the assistants never fill or read them."""
    return sensitivity(field) != "secret"


def loggable_value(field: dict[str, Any], value: Any) -> str:
    """The value as it may appear in a log: hidden for any sensitive field."""
    return HIDDEN if sensitivity(field) else _text(value)


def without_secret_values(schema: dict[str, Any], values: dict[str, Any]) -> dict[str, Any]:
    """`values` minus every secret field, for anything that is sent to a model."""
    secret = {f["path"] for f in fields_of(schema) if sensitivity(f) == "secret"}
    return {p: v for p, v in values.items() if p not in secret}


def describe_condition(condition: Any, labels: dict[str, str]) -> str:
    """A condition in plain words for the model: '"Heart disease" is true'. `labels` maps path to label."""
    if isinstance(condition, list):
        return " or ".join(describe_condition(c, labels) for c in condition)
    if not isinstance(condition, dict):
        return ""
    parts = []
    for path, expected in condition.items():
        options = expected if isinstance(expected, list) else [expected]
        parts.append(f'"{labels.get(path, path)}" is ' + " or ".join(_text(o) for o in options))
    return " and ".join(parts)


def newly_visible(schema: dict[str, Any], before: dict[str, Any], after: dict[str, Any]) -> list[dict[str, Any]]:
    """Fields that the change from `before` to `after` made visible."""
    return [f for f in fields_of(schema) if is_visible(f, after) and not is_visible(f, before)]


def _condition_problems(owner: str, key: str, condition: Any, paths: set[str]) -> list[str]:
    where = f"{owner}.{key}"
    if condition in (None, {}):
        return []
    maps = condition if isinstance(condition, list) else [condition]
    if not maps:
        return [f"{where}: an empty list is the same as no condition; leave the key out"]
    out: list[str] = []
    for m in maps:
        if not isinstance(m, dict) or not m:
            out.append(f"{where}: each condition must be a non-empty map of path to value")
            continue
        for path, expected in m.items():
            if path not in paths:
                out.append(f"{where}: refers to unknown field '{path}'")
            if path == owner:
                out.append(f"{where}: a field cannot depend on itself")
            options = expected if isinstance(expected, list) else [expected]
            if not options or any(isinstance(o, (dict, list)) or o is None for o in options):
                out.append(f"{where}: value for '{path}' must be a text, number or boolean (or a list of them)")
    return out


def validate_schema(schema: dict[str, Any]) -> list[str]:
    """Problems with a form definition. An empty list means it is usable."""
    problems: list[str] = []
    fields = fields_of(schema)
    paths = [f["path"] for f in fields]
    pathset = set(paths)
    for dup in sorted({p for p in paths if paths.count(p) > 1}):
        problems.append(f"{dup}: path appears more than once")
    for f in fields:
        p = f["path"]
        for k in f:
            if k not in FIELD_KEYS:
                problems.append(f"{p}: unknown key '{k}'")
        if f.get("type") not in FIELD_TYPES:
            problems.append(f"{p}: unknown type '{f.get('type')}'")
        if f.get("type") == "select" and not f.get("options"):
            problems.append(f"{p}: a select needs options")
        if "sensitive" in f and f["sensitive"] not in SENSITIVE_LEVELS:
            problems.append(f"{p}: sensitive must be one of {', '.join(SENSITIVE_LEVELS)}")
        problems += _condition_problems(p, "visible_when", f.get("visible_when"), pathset)
        problems += _condition_problems(p, "required_when", f.get("required_when"), pathset)
        if f.get("required") is True and f.get("required_when"):
            problems.append(f"{p}: required_when is redundant when required is true")
    return problems
