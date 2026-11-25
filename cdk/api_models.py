"""JSON request models for the POST and PUT routes of the REST API.

API Gateway checks the body of each request against its model before the Lambda runs (a failure is a
400 with a fixed message, see tools_stack.py). The models bound the shape and the size of a request:
the body is an object, each known field has the right kind of value and a maximum length, and lists
have a maximum number of items. The Lambdas stay the place for the exact rules and their messages
(allowed values, formats, the 1,000-character note limit), so every limit below is deliberately a
little wider than the Lambda's own.

Two kinds of route:

* Routes only the web app calls (documents, comparator, recommend, meeting summary, applications):
  `additionalProperties` is false, so a field the app does not send is refused.
* Routes the assistants also call through the AgentCore Gateway (profile, notes, policy): the
  model chooses the arguments, so a stray extra argument is tolerated (the Lambdas ignore fields
  they do not write) and optional fields may be null. Types and lengths are still enforced.

The dictionaries use JSON Schema draft 4 keywords as API Gateway reads them.
"""
from typing import Any

# Kinds of value
STR = "string"
NUM = "number"
BOOL = "boolean"
NULL = "null"


def _text(max_length: int, nullable: bool = True, numbers: bool = False) -> dict[str, Any]:
    kinds = [STR] + ([NUM] if numbers else []) + ([NULL] if nullable else [])
    return {"type": kinds if len(kinds) > 1 else kinds[0], "maxLength": max_length}


def _number(nullable: bool = True) -> dict[str, Any]:
    # Numbers may arrive as text ("85000"); the Lambda reads both.
    return {"type": [NUM, STR] + ([NULL] if nullable else []), "maxLength": 40}


def _object(max_properties: int = 30, nullable: bool = True) -> dict[str, Any]:
    return {"type": ["object", NULL] if nullable else "object", "maxProperties": max_properties}


# Fields lambda/profile writes (WRITABLE_FIELDS), with a maximum length each.
PROFILE_FIELDS: dict[str, dict[str, Any]] = {
    "name": _text(200),
    "first_name": _text(100),
    "middle_name": _text(100),
    "last_name": _text(100),
    "email": _text(254),
    "phone": _text(40, numbers=True),
    "address": _text(300),
    "address_line1": _text(200),
    "city": _text(100),
    "state": _text(40),
    "zip": _text(20, numbers=True),
    "date_of_birth": _text(40),
    "marital_status": _text(40),
    "dependents": _number(),
    "occupation": _text(100),
    "employment_status": _text(50),
    "annual_income": _number(),
    "gender": _text(40),
    "home_owner": {"type": [BOOL, STR, NULL], "maxLength": 10},
    "medical_conditions": _text(2000),
    "citizenship": _text(40),
    "tobacco_status": _text(40),
    "net_worth_band": _text(40),
    "financial_objective": _text(500),
    "time_horizon": _text(100),
    "risk_tolerance": _text(50),
    "liquidity_needs": _text(50),
}

PROFILE_CREATE = {
    "type": "object",
    "maxProperties": 60,
    "properties": dict(PROFILE_FIELDS),
}

PROFILE_UPDATE = {
    "type": "object",
    "maxProperties": 60,
    "required": ["customer_id"],
    "properties": {"customer_id": {"type": STR, "maxLength": 64}, **PROFILE_FIELDS},
}

PROFILE_NOTE = {
    "type": "object",
    "maxProperties": 20,
    "required": ["customer_id", "type", "text"],
    "properties": {
        "customer_id": {"type": STR, "maxLength": 64},
        "type": {"type": STR, "maxLength": 40},
        "text": {"type": STR, "maxLength": 2000},
        "event_date": _text(40),
    },
}

# Fields lambda/policies writes (WRITABLE_TOP_LEVEL_FIELDS and DETAIL_BLOCK_FIELDS).
POLICY_FIELDS: dict[str, dict[str, Any]] = {
    "customer_id": _text(64),
    "type": _text(100),
    "insurer": _text(200),
    "product_name": _text(200),
    "premium_amount": _number(),
    "premium_frequency": _text(40),
    "coverage_amount": _number(),
    "status": _text(40),
    "start_date": _text(40),
    "renewal_date": _text(40),
    "vehicle": _object(),
    "property": _object(),
    "health_details": _object(),
    "disability_details": _object(),
    "life_details": _object(),
}

POLICY_CREATE = {
    "type": "object",
    "maxProperties": 40,
    "properties": dict(POLICY_FIELDS),
}

POLICY_UPDATE = {
    "type": "object",
    "maxProperties": 40,
    "required": ["id"],
    "properties": {"id": {"type": STR, "maxLength": 64}, **POLICY_FIELDS},
}

# Sent by the web app only.
DOCUMENT_INITIATE = {
    "type": "object",
    "additionalProperties": False,
    "required": ["filename", "content_type"],
    "properties": {
        "filename": {"type": STR, "maxLength": 300},
        "content_type": {"type": STR, "maxLength": 100},
        "customer_id": _text(64),
    },
}

COMPARE = {
    "type": "object",
    "additionalProperties": False,
    "required": ["product_ids"],
    "properties": {
        "product_ids": {"type": "array", "maxItems": 8, "items": {"type": STR, "maxLength": 200}},
        "temp_ids": {"type": "array", "maxItems": 4, "items": {"type": STR, "maxLength": 64}},
        "locale": {"type": STR, "maxLength": 20},
    },
}

COMPARE_INGEST = {
    "type": "object",
    "additionalProperties": False,
    "required": ["document_id", "product_type"],
    "properties": {
        "document_id": {"type": STR, "maxLength": 64},
        "product_type": {"type": STR, "maxLength": 100},
    },
}

RECOMMEND = {
    "type": "object",
    "additionalProperties": False,
    "required": ["customer_id"],
    "properties": {
        "customer_id": {"type": STR, "maxLength": 64},
        "locale": {"type": STR, "maxLength": 20},
        "line": {"type": STR, "maxLength": 20},
    },
}

# lambda/meeting_summary keeps the last 400 turns of 2,000 characters; the web app sends no more.
MEETING_SUMMARY = {
    "type": "object",
    "additionalProperties": False,
    "required": ["customer_id", "turns"],
    "properties": {
        "customer_id": {"type": STR, "maxLength": 64},
        "locale": {"type": STR, "maxLength": 20},
        "turns": {
            "type": "array",
            "maxItems": 400,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["role", "text"],
                "properties": {
                    "role": {"type": STR, "maxLength": 20},
                    "text": {"type": STR, "maxLength": 2000},
                },
            },
        },
    },
}

# lambda/applications checks every answer against the form definition; this bounds the request.
APPLICATION = {
    "type": "object",
    "additionalProperties": False,
    "required": ["product_type", "fields"],
    "properties": {
        "product_type": {"type": STR, "maxLength": 64},
        "customer_id": _text(64),
        "ref": {"type": STR, "maxLength": 20},
        "fields": {"type": "object", "maxProperties": 300},
    },
}

# Only deployed when the open sign-up route is switched on.
SIGNUP = {
    "type": "object",
    "additionalProperties": False,
    "required": ["email", "password"],
    "properties": {
        "email": {"type": STR, "maxLength": 254},
        "password": {"type": STR, "maxLength": 256},
    },
}
