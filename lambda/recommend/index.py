"""
Insurance Recommendation Lambda

Pulls a single customer's full context (profile + Unicorn policies + third-party
policies + catalog + promotions) and asks Bedrock Claude Sonnet 4.5 (via the
Converse API) to identify coverage gaps and recommend Unicorn products that
close them. Returns a structured JSON envelope so the React frontend can
render the result without prose-parsing.

Route:
  POST /recommend  with body { "customer_id": "...", "locale": "en|ja|ko|es" }

Design notes mirroring the comparator lambda:
- Direct Bedrock Converse call, no AgentCore runtime — this is a one-shot
  structured generation; no tools, no streaming, no memory.
- `toolConfig` with `toolChoice` = a specific tool forces a JSON payload that
  matches the schema. Output goes straight to React.
- Cross-region inference profile: us.anthropic.claude-sonnet-4-5-...
- We reuse the existing DynamoDB tables and Promotion S3 bucket directly
  rather than calling the sibling lambdas, so we keep the call to a single
  hop and minimize cold-start cost. (HTTP-out from a Lambda costs ~50-100ms
  per hop; for 2 sibling calls on a cold Lambda that adds up fast.)
"""
from __future__ import annotations

import json
import os
import traceback
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key
from botocore.config import Config


SUPPORTED_LOCALES = {"en", "ja", "ko", "es", "fr"}

BEDROCK_MODEL_ID = os.environ.get(
    "BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
)

# Bedrock Guardrail wired by the agentcore stack at deploy time. The
# guardrail VERSION isn't an env var any more — we resolve it at cold start
# from an SSM parameter so guardrail policy updates can roll out without a
# Lambda redeploy. The parameter NAME is in the env; the VALUE is fetched
# once per cold start and cached for the life of the container.
GUARDRAIL_ID = os.environ.get("BEDROCK_GUARDRAIL_ID")
GUARDRAIL_VERSION_PARAM_NAME = os.environ.get("BEDROCK_GUARDRAIL_VERSION_PARAM_NAME")

_ssm_client = boto3.client("ssm")
_GUARDRAIL_VERSION_CACHE: str | None = None


def _resolve_guardrail_version() -> str | None:
    """Read the current guardrail version from SSM, cached per container.

    Falls back to the legacy `BEDROCK_GUARDRAIL_VERSION` env var if the
    SSM parameter name isn't configured (so this Lambda works under both
    the legacy CFN-export deploy path AND the SSM-based one). Returns
    None if neither is configured."""
    global _GUARDRAIL_VERSION_CACHE
    if _GUARDRAIL_VERSION_CACHE is not None:
        return _GUARDRAIL_VERSION_CACHE
    if GUARDRAIL_VERSION_PARAM_NAME:
        try:
            resp = _ssm_client.get_parameter(Name=GUARDRAIL_VERSION_PARAM_NAME)
            value = (resp.get("Parameter") or {}).get("Value", "").strip()
            if value and value != "DRAFT":
                _GUARDRAIL_VERSION_CACHE = value
                return value
        except Exception as exc:  # noqa: BLE001
            print(f"could not resolve guardrail version from SSM: {exc}")
    legacy = (os.environ.get("BEDROCK_GUARDRAIL_VERSION") or "").strip()
    if legacy:
        _GUARDRAIL_VERSION_CACHE = legacy
        return legacy
    return None


def _guardrail_config() -> dict | None:
    """Build the Converse `guardrailConfig` block when guardrail env vars
    are set, otherwise return None so the call proceeds unguarded.

    Trace mode is "enabled" so blocked requests show up in CloudWatch with
    the policy name that triggered.
    """
    if not GUARDRAIL_ID:
        return None
    version = _resolve_guardrail_version()
    if not version:
        return None
    return {
        "guardrailIdentifier": GUARDRAIL_ID,
        "guardrailVersion": version,
        "trace": "enabled",
    }


# --- AWS client wiring -----------------------------------------------------
dynamodb = boto3.resource("dynamodb")
profiles_table = dynamodb.Table(os.environ["PROFILES_TABLE"])
policies_table = dynamodb.Table(os.environ["POLICIES_TABLE"])
catalog_table = dynamodb.Table(os.environ["CATALOG_TABLE"])
s3_client = boto3.client("s3")

bedrock_runtime = boto3.client(
    "bedrock-runtime",
    config=Config(read_timeout=120, retries={"max_attempts": 2, "mode": "standard"}),
)


# --- Tool schema for structured output -------------------------------------
RECOMMENDATION_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {
            "type": "string",
            "description": (
                "1-2 sentence overview of the customer's coverage state. "
                "If there are no meaningful gaps, say so plainly."
            ),
        },
        "gaps": {
            "type": "array",
            "description": (
                "Up to 3 coverage gaps the advisor should consider. May be "
                "empty if the customer is well covered. Order by priority "
                "(most material first)."
            ),
            "maxItems": 3,
            "items": {
                "type": "object",
                "properties": {
                    "gap": {
                        "type": "string",
                        "description": "Short label for the gap, e.g. 'No life insurance' or 'Underinsured for home'.",
                    },
                    "why": {
                        "type": "string",
                        "description": "1 short sentence on why this matters for THIS customer (cite suitability fields).",
                    },
                    "recommendations": {
                        "type": "array",
                        "description": "1-2 Unicorn products that close the gap. Pick from the catalog.",
                        "maxItems": 2,
                        "items": {
                            "type": "object",
                            "properties": {
                                "product_name": {"type": "string"},
                                "product_type": {"type": "string"},
                                "why_helps": {
                                    "type": "string",
                                    "description": "1 short sentence connecting product features to the gap.",
                                },
                            },
                            "required": ["product_name", "product_type", "why_helps"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["gap", "why", "recommendations"],
                "additionalProperties": False,
            },
        },
        "disclaimer": {
            "type": "string",
            "description": "1 short sentence stating the recommendation is AI-generated and not a quote.",
        },
    },
    "required": ["summary", "gaps", "disclaimer"],
    "additionalProperties": False,
}


# --- Auth + CORS helpers ---------------------------------------------------
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
        "body": json.dumps(body, default=_default_json),
    }


def _default_json(value):
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def extract_advisor_id(event) -> str | None:
    """Resolve advisor email from JWT (browser path) or query string (M2M)."""
    try:
        username = event["requestContext"]["authorizer"]["claims"]["username"]
        cognito_client = boto3.client("cognito-idp")
        user_pool_id = os.environ.get("USER_POOL_ID")
        response = cognito_client.admin_get_user(
            UserPoolId=user_pool_id, Username=username
        )
        for attr in response["UserAttributes"]:
            if attr["Name"] == "email":
                return attr["Value"]
    except (KeyError, TypeError):
        qs = event.get("queryStringParameters") or {}
        body_dict = _safe_body(event)
        return qs.get("advisor_id") or body_dict.get("advisor_id")
    except Exception as e:
        print(f"Cognito lookup error: {e}")
    return None


def _safe_body(event) -> dict:
    try:
        return json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return {}


# --- Data fetchers ---------------------------------------------------------
def _fetch_profile(advisor_id: str, customer_id: str) -> dict | None:
    """Get the customer's profile, scoped to the calling advisor."""
    response = profiles_table.query(
        IndexName="advisor-id-index",
        KeyConditionExpression=Key("advisor_id").eq(advisor_id)
        & Key("customer_id").eq(customer_id),
    )
    items = response.get("Items", [])
    return items[0] if items else None


def _fetch_policies(advisor_id: str, customer_id: str) -> list[dict]:
    response = policies_table.query(
        IndexName="advisor-id-index",
        KeyConditionExpression=Key("advisor_id").eq(advisor_id)
        & Key("customer_id").eq(customer_id),
    )
    return response.get("Items", [])


def _fetch_catalog() -> list[dict]:
    """Return the full Unicorn product catalog (small, no pagination)."""
    items = []
    response = catalog_table.scan()
    items.extend(response.get("Items", []))
    while "LastEvaluatedKey" in response:
        response = catalog_table.scan(
            ExclusiveStartKey=response["LastEvaluatedKey"]
        )
        items.extend(response.get("Items", []))
    # Only keep Unicorn-issued products (catalog also stores competitor refs).
    unicorn = [p for p in items if (p.get("carrier_name") or "").lower().startswith("unicorn")]
    if unicorn:
        return unicorn
    # If carrier_name doesn't follow the convention we expected, fall back to
    # all items to avoid an empty recommendation. The system prompt still
    # tells the model to recommend only Unicorn products.
    return items


def _fetch_promotions_text() -> str:
    """Concatenate every object in the promotions S3 bucket into one string.

    Mirrors `lambda/promotions/index.py`. We read directly here to avoid a
    Lambda-to-Lambda hop on every recommendation request.
    """
    bucket = os.environ.get("PROMOTION_BUCKET")
    if not bucket:
        return ""
    try:
        listing = s3_client.list_objects_v2(Bucket=bucket)
        if "Contents" not in listing:
            return ""
        out = []
        for obj in listing["Contents"]:
            file_response = s3_client.get_object(Bucket=bucket, Key=obj["Key"])
            raw = file_response["Body"].read()
            # Tolerate cp1252-only bytes (em dashes, smart quotes) that
            # sometimes sneak in via Word/.docx authoring. Final fallback
            # replaces bad bytes so we never lose a recommendation over
            # a single character.
            try:
                content = raw.decode("utf-8")
            except UnicodeDecodeError:
                try:
                    content = raw.decode("cp1252")
                except UnicodeDecodeError:
                    content = raw.decode("utf-8", errors="replace")
            out.append(f"=== {obj['Key']} ===\n{content}")
        return "\n\n".join(out)
    except Exception as e:  # noqa: BLE001
        print(f"Failed to read promotions: {e}")
        return ""


# --- Bedrock Converse builder ---------------------------------------------
_LANG_NAMES = {"en": "English", "ja": "Japanese", "ko": "Korean", "es": "Spanish", "fr": "French"}


def _summarize_policies(policies: list[dict]) -> str:
    """Produce a compact textual policy summary for the LLM."""
    if not policies:
        return "(none)"
    lines = []
    for p in policies:
        kind = "third-party" if p.get("third_party") else "unicorn"
        insurer = p.get("insurer") or "Unicorn Insurance"
        product = p.get("product_name") or p.get("type") or "policy"
        coverage = p.get("coverage_amount", "n/a")
        renewal = p.get("renewal_date", "n/a")
        lines.append(
            f"- [{kind}] {p.get('type', 'unknown')} | {insurer} | {product} | "
            f"coverage={coverage} | renews {renewal}"
        )
    return "\n".join(lines)


def _summarize_profile(profile: dict) -> str:
    fields = [
        "name", "date_of_birth", "marital_status", "dependents",
        "occupation", "employment_status", "annual_income",
        "home_owner", "smoking", "medical_conditions",
        "financial_objective", "time_horizon", "risk_tolerance", "liquidity_needs",
    ]
    out = []
    for k in fields:
        v = profile.get(k)
        if v not in (None, ""):
            out.append(f"- {k}: {v}")
    return "\n".join(out) or "(no profile fields populated)"


def _summarize_catalog(catalog: list[dict]) -> str:
    """One bullet per Unicorn product — name, type, pricing tier."""
    if not catalog:
        return "(catalog unavailable)"
    lines = []
    for p in catalog:
        lines.append(
            f"- {p.get('product_name', '?')} ({p.get('product_type', '?')}, "
            f"tier={p.get('pricing_tier', 'n/a')})"
        )
    return "\n".join(lines)


def _build_converse_request(
    profile: dict,
    policies: list[dict],
    catalog: list[dict],
    promotions: str,
    locale: str,
) -> dict:
    language_name = _LANG_NAMES.get(locale, "English")

    system_prompt = (
        "You are an insurance advisor's coverage-gap analyst. Given a "
        "customer's profile, current policies (both Unicorn-issued and "
        "third-party held with other carriers), the Unicorn product "
        "catalog, and current promotions, identify up to 3 meaningful "
        "coverage gaps and recommend Unicorn products that would close "
        "them. Recommendations MUST come from the supplied catalog only.\n\n"
        "Rules:\n"
        "- A line of business is COVERED if the customer already holds "
        "any policy of that type (Unicorn or third-party). Don't recommend "
        "a duplicate Unicorn product on top of an existing third-party "
        "policy unless the third-party policy is clearly inadequate, and "
        "in that case state the inadequacy in your reasoning.\n"
        "- If the customer is well covered, return zero gaps and say so in "
        "the summary. Do NOT manufacture gaps.\n"
        "- Stay short and concrete. Each `why` and `why_helps` is a single "
        "short sentence.\n"
        "- Use suitability cues from the profile (dependents, "
        "home_owner, occupation, financial_objective, etc.) to justify "
        "recommendations.\n"
        "- Mention a current promotion in `why_helps` ONLY if it directly "
        "applies to the recommended product.\n\n"
        f"Respond entirely in {language_name}. Proper nouns (product "
        "names, carrier names) stay in English.\n\n"
        "You MUST call the emit_recommendation tool with your structured "
        "output. Do not produce free-form text outside the tool call."
    )

    user_message = (
        "Identify up to 3 meaningful coverage gaps for this customer using "
        "the customer profile, current policies, Unicorn catalog, and "
        "promotions provided as grounding sources. Recommend Unicorn "
        "products from the catalog that would close each gap."
    )

    # Tag each context section as a grounding source so the contextual
    # grounding policy (GROUNDING + RELEVANCE) on the guardrail has source
    # content to evaluate the model's recommendations against. Without these
    # guardContent blocks the policy is inert. Each block is a separate
    # qualified text segment so the grounding scorer can attribute the
    # answer to the most relevant source.
    grounding_sources = [
        {
            "guardContent": {
                "text": {
                    "text": f"Customer profile:\n{_summarize_profile(profile)}",
                    "qualifiers": ["grounding_source"],
                }
            }
        },
        {
            "guardContent": {
                "text": {
                    "text": f"Current policies:\n{_summarize_policies(policies)}",
                    "qualifiers": ["grounding_source"],
                }
            }
        },
        {
            "guardContent": {
                "text": {
                    "text": f"Unicorn product catalog:\n{_summarize_catalog(catalog)}",
                    "qualifiers": ["grounding_source"],
                }
            }
        },
    ]
    if promotions:
        grounding_sources.append(
            {
                "guardContent": {
                    "text": {
                        "text": f"Promotions:\n{promotions}",
                        "qualifiers": ["grounding_source"],
                    }
                }
            }
        )

    # Bedrock's contextual grounding policy requires both a "query" qualifier
    # (the question being asked) and at least one "grounding_source"
    # qualifier (the reference content). Emitting the question as plain
    # {"text": ...} causes Bedrock to reject the call with "request does not
    # contain the query".
    user_content = [
        {
            "guardContent": {
                "text": {
                    "text": user_message,
                    "qualifiers": ["query"],
                }
            }
        },
        *grounding_sources,
    ]

    request: dict = {
        "modelId": BEDROCK_MODEL_ID,
        "system": [{"text": system_prompt}],
        "messages": [
            {"role": "user", "content": user_content},
        ],
        "inferenceConfig": {
            "temperature": 0.2,
            # Doubled from 1500 to avoid the same output-token truncation the
            # comparator hit: a customer with many policies / coverage gaps
            # produces a longer structured payload, and truncation would drop
            # trailing gaps and silently render an incomplete analysis.
            "maxTokens": 3000,
        },
        "toolConfig": {
            "tools": [
                {
                    "toolSpec": {
                        "name": "emit_recommendation",
                        "description": "Emit a structured coverage-gap analysis and Unicorn product recommendations.",
                        "inputSchema": {"json": RECOMMENDATION_TOOL_SCHEMA},
                    }
                }
            ],
            "toolChoice": {"tool": {"name": "emit_recommendation"}},
        },
    }
    guardrail = _guardrail_config()
    if guardrail is not None:
        request["guardrailConfig"] = guardrail
    return request


def _extract_tool_result(response: dict) -> dict:
    """Pluck the JSON arguments out of the model's tool-use turn."""
    output = response.get("output", {})
    message = output.get("message", {})
    for block in message.get("content", []):
        if "toolUse" in block:
            return block["toolUse"].get("input") or {}
    raise RuntimeError("Model response did not contain a toolUse block")


def _first_text_block(response: dict) -> str | None:
    """Return the first text content block from a Converse response, if any.

    Used when a guardrail intervention substitutes a safe message in place
    of the model output - the safe message lives in a regular text block
    instead of toolUse.
    """
    message = response.get("output", {}).get("message", {})
    for block in message.get("content", []):
        text = block.get("text")
        if text:
            return text
    return None


# --- Handler ---------------------------------------------------------------
def handler(event, context):
    try:
        if event.get("httpMethod", "POST") not in ("POST",):
            return _response(405, {"message": "Only POST supported"})

        advisor_id = extract_advisor_id(event)
        if not advisor_id:
            return _response(401, {"message": "Unauthorized - no advisor ID found"})

        body = _safe_body(event)
        customer_id = body.get("customer_id")
        if not customer_id:
            return _response(400, {"message": "customer_id is required"})

        locale = (body.get("locale") or "en").lower()
        if locale not in SUPPORTED_LOCALES:
            locale = "en"

        profile = _fetch_profile(advisor_id, customer_id)
        if not profile:
            return _response(404, {"message": "Customer not found for this advisor"})

        policies = _fetch_policies(advisor_id, customer_id)
        catalog = _fetch_catalog()
        promotions = _fetch_promotions_text()

        request = _build_converse_request(profile, policies, catalog, promotions, locale)
        bedrock_response = bedrock_runtime.converse(**request)

        # Guardrail intervention: Bedrock substitutes the blocked content
        # with the configured `blockedOutputsMessaging` and sets stopReason
        # to "guardrail_intervened". The response then has no toolUse block,
        # so _extract_tool_result would raise. Catch it here and return a
        # clean 400 with the safe message.
        if bedrock_response.get("stopReason") == "guardrail_intervened":
            blocked_text = _first_text_block(bedrock_response) or (
                "The request was blocked by a content safety policy."
            )
            print(f"Guardrail intervened on recommend request: {blocked_text}")
            return _response(400, {"message": blocked_text})

        payload = _extract_tool_result(bedrock_response)

        # Light validation: gaps array must exist (may be empty).
        if not isinstance(payload.get("gaps"), list):
            payload["gaps"] = []

        return _response(200, payload)

    except Exception as e:
        print(f"Error in recommend handler: {e}")
        traceback.print_exc()
        return _response(500, {"message": "An internal error occurred"})
