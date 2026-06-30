"""
Insurance Product Comparator Lambda

Fetches selected products' markdown from S3 and asks Bedrock (Claude Sonnet
4.5 via the Converse API) to produce a fixed-shape JSON comparison suitable
for table rendering in the React frontend.

Route:
  POST /comparator/compare
    Body: { "product_ids": ["...", "..."], "locale": "en|ja|ko|es" }
    Returns: the LLM's JSON payload (schema documented below).

Design notes:
- Direct Bedrock Converse call, not routed through the AgentCore runtime.
  This is a one-shot structured generation; the runtime would add latency
  without giving us tools, streaming, or memory that we need here.
- Product count is bounded (2..4) at the Lambda boundary so the context
  window stays small and the table stays readable.
- `toolConfig` with `toolChoice` = specific tool forces a JSON payload matching
  the schema. No prose parsing, no regex tricks.
- Markdown for each product is fetched from one of two buckets (portfolio
  or competitors) determined by a catalog lookup, same mapping as
  `catalog/index.py`.
"""
import json
import os
import traceback
from typing import Any

import boto3
from botocore.config import Config

MIN_PRODUCTS = 2
MAX_PRODUCTS = 4

# Supported frontend locales. Anything else falls through to English.
SUPPORTED_LOCALES = {"en", "ja", "ko", "es"}

# Claude Sonnet 4.5 on us-east-1 Bedrock runtime.
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

bedrock_runtime = boto3.client(
    "bedrock-runtime",
    config=Config(read_timeout=120, retries={"max_attempts": 2, "mode": "standard"}),
)
dynamodb = boto3.resource("dynamodb")
catalog_table = dynamodb.Table(os.environ["CATALOG_TABLE"])
s3 = boto3.client("s3")

PORTFOLIO_BUCKET = os.environ["PORTFOLIO_BUCKET"]
COMPETITORS_BUCKET = os.environ["COMPETITORS_BUCKET"]


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


COMPARISON_TOOL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "title": {
            "type": "string",
            "description": "Short comparison title, e.g. 'Comparison: Rainbow Life vs CenturyGuard Life Term vs StarBasic Life'.",
        },
        "summary": {
            "type": "string",
            "description": (
                "2-3 sentence prose overview of the comparison, highlighting the most "
                "important differences and trade-offs. Do not recommend a specific product."
            ),
        },
        "products": {
            "type": "array",
            "description": "Products being compared, in the same order as the `values` arrays below.",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "name": {"type": "string"},
                    "carrier": {"type": "string"},
                    "pricing_tier": {"type": "string"},
                },
                "required": ["id", "name", "carrier"],
                "additionalProperties": False,
            },
        },
        "sections": {
            "type": "array",
            "description": (
                "Grouped rows of comparison attributes. Use these sections when "
                "applicable: Coverage, Pricing & Premium Structure, Underwriting & "
                "Issue, Riders & Options, Best Suited For. Include AT MOST 6 sections "
                "total, and AT MOST 8 rows per section — pick the most decision-"
                "relevant attributes rather than listing everything. Include a section "
                "only if it has at least one row."
            ),
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "rows": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "attribute": {"type": "string"},
                                "values": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "description": (
                                        "Length must equal the products array length, in "
                                        "the same order. Use 'Not offered' for absent "
                                        "coverage. Keep each value under 120 characters."
                                    ),
                                },
                            },
                            "required": ["attribute", "values"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["title", "rows"],
                "additionalProperties": False,
            },
        },
        "disclaimer": {
            "type": "string",
            "description": "One-sentence note that the comparison is AI-generated from product documentation.",
        },
    },
    "required": ["title", "summary", "products", "sections", "disclaimer"],
    "additionalProperties": False,
}


def _cors_headers() -> dict:
    return {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Headers": "Content-Type,Authorization",
        "Access-Control-Allow-Methods": "POST,OPTIONS",
    }


def _response(status: int, body: Any):
    return {
        "statusCode": status,
        "headers": _cors_headers(),
        "body": json.dumps(body),
    }


def _fetch_catalog_entries(product_ids: list[str]) -> list[dict]:
    """BatchGet catalog rows for the requested product IDs. Order preserved."""
    keys = [{"product_id": pid} for pid in product_ids]
    response = dynamodb.batch_get_item(RequestItems={catalog_table.name: {"Keys": keys}})
    items_by_id = {
        item["product_id"]: item
        for item in response.get("Responses", {}).get(catalog_table.name, [])
    }
    missing = [pid for pid in product_ids if pid not in items_by_id]
    if missing:
        raise LookupError(f"product_id(s) not found in catalog: {missing}")
    return [items_by_id[pid] for pid in product_ids]


def _fetch_markdown(entry: dict) -> str:
    bucket_alias = entry["s3_bucket"]
    if bucket_alias == "portfolio":
        bucket = PORTFOLIO_BUCKET
    elif bucket_alias == "competitors":
        bucket = COMPETITORS_BUCKET
    else:
        raise ValueError(f"Unknown bucket alias in catalog: {bucket_alias}")
    obj = s3.get_object(Bucket=bucket, Key=entry["s3_key"])
    body = obj["Body"].read()
    # Be tolerant of mixed-encoding inputs in S3. Source markdown is
    # authored as UTF-8 but content occasionally arrives via copy/paste
    # from Word documents, where Windows-1252-only bytes (em dashes,
    # smart quotes, etc.) sneak in and would otherwise raise
    # UnicodeDecodeError and 502 the whole comparison. Try strict
    # UTF-8 first; on failure, fall back to cp1252 which is a strict
    # superset for these byte values. Last resort: replace bad bytes
    # so we never lose a whole comparison over a single character.
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError:
        try:
            return body.decode("cp1252")
        except UnicodeDecodeError:
            return body.decode("utf-8", errors="replace")


def _locale_language_name(locale: str) -> str:
    return {
        "en": "English",
        "ja": "Japanese",
        "ko": "Korean",
        "es": "Spanish",
    }.get(locale, "English")


def _build_converse_request(entries: list[dict], markdowns: list[str], locale: str) -> dict:
    language_name = _locale_language_name(locale)
    # Each product's markdown is emitted as a grounding-source block so
    # the contextual grounding policy on the guardrail (GROUNDING + RELEVANCE)
    # has source content to evaluate the model's structured comparison
    # against. Without these guardContent tags the policy is inert.
    grounding_sources: list[dict] = []
    for i, (entry, markdown) in enumerate(zip(entries, markdowns), start=1):
        header = (
            f"Product {i} of {len(entries)}\n"
            f"ID: {entry['product_id']}\n"
            f"Carrier: {entry['carrier_name']}\n"
            f"Name: {entry['product_name']}\n"
            f"Type: {entry['product_type']}\n"
            f"Pricing tier: {entry.get('pricing_tier', 'n/a')}"
        )
        # guardContent block: text + qualifier "grounding_source" tells the
        # guardrail this is the source-of-truth for the GROUNDING filter.
        grounding_sources.append(
            {
                "guardContent": {
                    "text": {
                        "text": f"{header}\n\n{markdown}",
                        "qualifiers": ["grounding_source"],
                    }
                }
            }
        )

    system_prompt = (
        "You are an insurance analyst producing a side-by-side comparison of "
        "insurance products for an advisor. Use ONLY the content in the product "
        "documents supplied by the user. Do not invent features, prices, or riders "
        "that are not in the documents. If a product does not describe a specific "
        "attribute that another product does, write 'Not specified' or 'Not offered' "
        "in that product's cell rather than leaving the cell blank or guessing.\n\n"
        "Stay factual and neutral. Do not recommend a winner.\n\n"
        f"Respond entirely in {language_name}. Translate both the prose and the "
        "attribute names/values into that language. Proper nouns (product names, "
        "carrier names) must remain in the original English.\n\n"
        "You MUST call the emit_comparison tool with your structured output. Do "
        "not respond in plain prose."
    )

    user_message = (
        f"Compare the {len(entries)} insurance products provided as "
        "grounding sources. For each comparison row, the `values` array "
        "must have exactly one entry per product, in the same order the "
        "products are listed in the grounding sources."
    )

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

    request: dict[str, Any] = {
        "modelId": BEDROCK_MODEL_ID,
        "system": [{"text": system_prompt}],
        "messages": [
            {
                "role": "user",
                "content": user_content,
            }
        ],
        "inferenceConfig": {
            "temperature": 0.2,
            # The structured comparison needs enough output budget to emit
            # every section + the trailing disclaimer. Health products have
            # large source markdown and produce richer tables: a complete
            # 2-product health comparison measured at ~3.4k output tokens,
            # so the previous 2000 cap truncated mid-output (stopReason=
            # max_tokens), dropping `sections` and `disclaimer` entirely and
            # rendering an empty table. 4096 covers the observed worst case
            # with headroom; the schema below also bounds section/row counts
            # so generation stays well under API Gateway's 29s timeout on
            # Haiku 4.5.
            "maxTokens": 4096,
        },
        "toolConfig": {
            "tools": [
                {
                    "toolSpec": {
                        "name": "emit_comparison",
                        "description": "Emit a structured side-by-side comparison of the provided products.",
                        "inputSchema": {"json": COMPARISON_TOOL_SCHEMA},
                    }
                }
            ],
            "toolChoice": {"tool": {"name": "emit_comparison"}},
        },
    }
    guardrail = _guardrail_config()
    if guardrail is not None:
        request["guardrailConfig"] = guardrail
    return request


def _extract_tool_result(response: dict) -> dict:
    """Find the tool-use block and return its input payload.

    Raises if the model returned plain text instead of using the tool.
    """
    message = response.get("output", {}).get("message", {})
    for block in message.get("content", []):
        if "toolUse" in block:
            return block["toolUse"]["input"]
    raise RuntimeError(
        "Model did not invoke emit_comparison tool. stop_reason="
        f"{response.get('stopReason')}"
    )


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


def handler(event, context):
    try:
        method = event.get("httpMethod", "POST")
        if method != "POST":
            return _response(405, {"message": "Only POST supported"})

        body_raw = event.get("body") or "{}"
        body = json.loads(body_raw)

        product_ids = body.get("product_ids", [])
        if not isinstance(product_ids, list):
            return _response(400, {"message": "product_ids must be an array"})
        # De-dupe while preserving order.
        seen: set[str] = set()
        product_ids = [pid for pid in product_ids if not (pid in seen or seen.add(pid))]
        if not MIN_PRODUCTS <= len(product_ids) <= MAX_PRODUCTS:
            return _response(
                400,
                {
                    "message": f"product_ids must contain between {MIN_PRODUCTS} and "
                    f"{MAX_PRODUCTS} unique values"
                },
            )

        locale = body.get("locale", "en")
        if locale not in SUPPORTED_LOCALES:
            locale = "en"

        # Load catalog rows in the same order the client requested.
        entries = _fetch_catalog_entries(product_ids)

        # Pull markdown from S3 in parallel? For 4 files it's not worth it.
        markdowns = [_fetch_markdown(e) for e in entries]

        request_kwargs = _build_converse_request(entries, markdowns, locale)
        bedrock_response = bedrock_runtime.converse(**request_kwargs)

        # Guardrail intervention: Bedrock substitutes the blocked content
        # with the configured `blockedOutputsMessaging` and sets stopReason
        # to "guardrail_intervened". The response then has no toolUse block,
        # so _extract_tool_result would raise a confusing error. Catch it
        # here and return a clean 400 with the safe message.
        if bedrock_response.get("stopReason") == "guardrail_intervened":
            blocked_text = _first_text_block(bedrock_response) or (
                "The request was blocked by a content safety policy."
            )
            print(f"Guardrail intervened on comparator request: {blocked_text}")
            return _response(400, {"message": blocked_text})

        # Output-token truncation: if the model hit the maxTokens ceiling the
        # tool payload is cut off mid-JSON (sections/disclaimer dropped),
        # which previously surfaced as a silently empty comparison table.
        # Treat it as a handled error so the frontend shows a retry affordance
        # instead of a blank result, and log it so a recurrence is visible.
        if bedrock_response.get("stopReason") == "max_tokens":
            usage = bedrock_response.get("usage", {})
            print(f"Comparator output truncated at maxTokens. usage={usage}")
            return _response(
                502,
                {"message": "The comparison was too long to generate. Please try again "
                            "or compare fewer products."},
            )

        payload = _extract_tool_result(bedrock_response)

        # Light server-side validation that the model respected the contract.
        if not isinstance(payload.get("products"), list):
            raise RuntimeError("Model output missing 'products' array")
        for section in payload.get("sections", []):
            for row in section.get("rows", []):
                if len(row.get("values", [])) != len(product_ids):
                    raise RuntimeError(
                        f"Row '{row.get('attribute')}' has "
                        f"{len(row.get('values', []))} values; expected {len(product_ids)}"
                    )

        return _response(200, payload)

    except LookupError as e:
        return _response(404, {"message": str(e)})
    except json.JSONDecodeError:
        return _response(400, {"message": "Request body is not valid JSON"})
    except Exception as e:
        print(f"Error in comparator handler: {e}")
        traceback.print_exc()
        return _response(502, {"message": "Unable to generate comparison"})
