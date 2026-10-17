"""
Pricing Lambda: indicative premium estimates and the affordability check.

Two MCP tools on the AgentCore Gateway, backed by two handlers in this file:

  get_pricing_table(product_type?)  ->  handler_table
      No product_type: the list of priced products, what each needs, and the
      affordability rule. With a product_type: that product's full rate file.

  estimate_premium(product_type, options?, customer_id?, advisor_id?)
      ->  handler_estimate
      Prices one configuration from the rate file and, when the customer is
      known, checks the total annual premium against 10% of their income
     .

All arithmetic happens here, in code. The model only chooses the inputs and
reads the result back; it never multiplies a rate itself.

Rate files live in the pricing bucket (s3-data/pricing/<product-slug>-pricing.json)
and describe their own inputs, tables and factors, so adding a product means
adding a file. The files hold indicative demo rates. Every result is labelled
as an estimate; none is a quote.

SECURITY NOTES
- product_type is checked against an allowlist before it becomes an S3 key.
- The customer's profile and policies are read through the advisor-id-index
  with the advisor_id the gateway sets. The gateway's request interceptor
  overwrites that argument with the advisor verified from the sign-in token,
  and this function is invoked only by the gateway's role. So one advisor cannot price
  against another advisor's customer.
- Option values are validated against the rate file's own definitions; free
  text never reaches S3 or DynamoDB.
"""
from __future__ import annotations

import datetime
import json
import os
import re
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

# Each product type is one rate file in the pricing bucket, named after the
# product's portfolio file: <product-slug>-pricing.json. The affordability rule
# is the one other file, affordability.json.
PRICING_FILES = {
    "term_life": "awesome-term-life-pricing.json",
    "whole_life": "awesome-whole-life-pricing.json",
    "universal_life": "awesome-universal-life-pricing.json",
    "variable_life": "awesome-variable-life-pricing.json",
    "critical_illness": "awesome-critical-illness-pricing.json",
    "health": "awesome-health-pricing.json",
    "home": "awesome-home-pricing.json",
    "auto": "awesome-motor-pricing.json",
    "travel": "awesome-travel-pricing.json",
    "disability": "awesome-disability-pricing.json",
}
AFFORDABILITY_FILE = "affordability.json"
KNOWN_PRODUCT_TYPES = list(PRICING_FILES)
SAFE_KEY_RE = re.compile(r"^[a-z0-9_]+$")

DISCLAIMER = (
    "Indicative estimate from Awesome's demo rate tables, effective {date}. "
    "It is not a quote or an offer of cover. The final premium is set by Awesome "
    "after the application is assessed."
)

PERIODS_PER_YEAR = {
    "monthly": 12, "quarterly": 4, "semi_annual": 2, "semi-annual": 2, "semiannual": 2,
    "biannual": 2, "annual": 1, "annually": 1, "yearly": 1,
}

_s3 = None
_dynamo = None
_cache: dict[str, dict] = {}


def _s3_client():
    global _s3
    if _s3 is None:
        _s3 = boto3.client("s3")
    return _s3


def _tables():
    global _dynamo
    if _dynamo is None:
        _dynamo = boto3.resource("dynamodb")
    return (_dynamo.Table(os.environ["PROFILES_TABLE"]),
            _dynamo.Table(os.environ["POLICIES_TABLE"]))


def _response(status: int, body: dict) -> dict:
    return {
        "statusCode": status,
        "headers": {
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "Content-Type,Authorization",
            "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
        },
        "body": json.dumps(body, default=_json_default),
    }


def _json_default(o):
    if isinstance(o, Decimal):
        return int(o) if o == o.to_integral_value() else float(o)
    raise TypeError(type(o))


def _num(v):
    return float(v) if isinstance(v, Decimal) else v


# --------------------------------------------------------------------------
# Rate files
# --------------------------------------------------------------------------
def read_pricing_object(name: str) -> dict:
    """Read one JSON object from the pricing bucket. Raises FileNotFoundError."""
    bucket = os.environ.get("PRICING_BUCKET")
    if not bucket:
        raise RuntimeError("PRICING_BUCKET is not set")
    # `name` is a product type or "affordability". Anything else is not found, so
    # a caller-supplied value never becomes a raw S3 key.
    key = AFFORDABILITY_FILE if name == "affordability" else PRICING_FILES.get(name)
    if key is None:
        raise FileNotFoundError(name)
    try:
        obj = _s3_client().get_object(Bucket=bucket, Key=key)
    except ClientError as e:
        if e.response["Error"]["Code"] in ("NoSuchKey", "404"):
            raise FileNotFoundError(name) from e
        raise
    return json.loads(obj["Body"].read().decode("utf-8"))


def load_pricing(product_type: str) -> dict:
    if product_type not in _cache:
        _cache[product_type] = read_pricing_object(product_type)
    return _cache[product_type]


def _event_args(event) -> dict:
    """Tool arguments arrive as top-level keys from the gateway; fall back to a body."""
    args = {k: v for k, v in (event or {}).items() if k not in ("body", "headers", "requestContext")}
    body = (event or {}).get("body")
    if isinstance(body, str) and body:
        try:
            parsed = json.loads(body)
            if isinstance(parsed, dict):
                args = {**parsed, **args}
        except ValueError:
            pass
    return args


# --------------------------------------------------------------------------
# get_pricing_table
# --------------------------------------------------------------------------
def handler_table(event, context):
    args = _event_args(event)
    product_type = args.get("product_type")
    if isinstance(product_type, str) and product_type.strip():
        pt = product_type.strip().lower()
        if pt not in KNOWN_PRODUCT_TYPES or not SAFE_KEY_RE.match(pt):
            return _response(404, {"message": f"No pricing table for '{pt}'",
                                   "available_product_types": KNOWN_PRODUCT_TYPES})
        try:
            return _response(200, load_pricing(pt))
        except FileNotFoundError:
            return _response(500, {"message": "Pricing table unavailable"})
    catalog = []
    for pt in KNOWN_PRODUCT_TYPES:
        try:
            p = load_pricing(pt)
        except FileNotFoundError:
            continue
        needs = [k for k, d in p["inputs"].items() if d.get("required") or d.get("required_when")]
        catalog.append({
            "product_type": pt,
            "product_name": p["product_name"],
            "effective_date": p["effective_date"],
            "premium_basis": p.get("premium_basis"),
            "required_options": needs,
        })
    try:
        affordability = read_pricing_object("affordability")
    except FileNotFoundError:
        affordability = None
    return _response(200, {"products": catalog, "affordability_rule": affordability})


# --------------------------------------------------------------------------
# Input handling
# --------------------------------------------------------------------------
TRUE_WORDS = {"true", "yes", "y", "1"}
FALSE_WORDS = {"false", "no", "n", "0"}

# Options accepted for every product on top of the rate file's own inputs.
GLOBAL_OPTIONS = {
    "annual_income": "Customer's annual income in USD (for the affordability check, when not on the profile).",
    "other_annual_premiums": "Total yearly premium of the customer's other active policies in USD (only when no customer_id is given).",
}


def _age_from_dob(dob: str, today: datetime.date | None = None) -> int | None:
    try:
        d = datetime.date.fromisoformat(str(dob)[:10])
    except ValueError:
        return None
    t = today or datetime.date.today()
    return t.year - d.year - ((t.month, t.day) < (d.month, d.day))


def _key(value) -> str:
    """Canonical string for matching a value against a rate file's choices/maps."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value == int(value):
        return str(int(value))
    return str(value)


def _fmt(n) -> str:
    """Whole numbers with thousands separators, never scientific notation."""
    return f"{int(n):,}" if float(n) == int(n) else f"{n:,}"


def _coerce(name: str, spec: dict, raw):
    """Return (value, error). value is normalised; error is a short sentence."""
    t = spec["type"]
    if t == "boolean":
        if isinstance(raw, bool):
            return raw, None
        s = str(raw).strip().lower()
        if s in TRUE_WORDS:
            return True, None
        if s in FALSE_WORDS:
            return False, None
        return None, f"{name} must be true or false"
    if t == "number":
        try:
            n = float(str(raw).replace(",", "").replace("$", "").strip())
        except ValueError:
            return None, f"{name} must be a number"
        lo, hi = spec.get("min"), spec.get("max")
        if lo is not None and n < lo:
            return None, f"{name} must be at least {_fmt(lo)}"
        if hi is not None and n > hi:
            return None, f"{name} must be at most {_fmt(hi)}"
        return (int(n) if n == int(n) else n), None
    if t == "choice":
        s = _key(raw).strip().lower().replace(" ", "_").replace("-", "_")
        for c in spec["choices"]:
            if c.lower() == s:
                return c, None
        return None, f"{name} must be one of: {', '.join(spec['choices'])}"
    return None, f"{name} has an unsupported type"


def _applies(rule_when: dict | None, values: dict) -> bool:
    if not rule_when:
        return True
    return all(_key(values.get(k)) == str(v) for k, v in rule_when.items())


def _lookup_map(mapping: dict, value) -> float | None:
    k = _key(value)
    if k in mapping:
        return mapping[k]
    # "2+" style keys: use the highest threshold not above the value.
    try:
        n = float(k)
    except ValueError:
        return None
    best = None
    for mk, mv in mapping.items():
        if mk.endswith("+"):
            try:
                thr = float(mk[:-1])
            except ValueError:
                continue
            if n >= thr and (best is None or thr > best[0]):
                best = (thr, mv)
    return best[1] if best else None


def _band_index(age_bands: list[dict], age: float) -> int | None:
    for i, b in enumerate(age_bands):
        if b["min"] <= age <= b["max"]:
            return i
    return None


# --------------------------------------------------------------------------
# Pricing engine
# --------------------------------------------------------------------------
class NotAvailable(Exception):
    pass


def _base_premium(pricing: dict, v: dict, steps: list[dict]) -> float:
    base = pricing["base"]
    kind = base["type"]
    if kind in ("age_rate", "age_column", "age_amount"):
        age = v["age"]
        idx = _band_index(base["age_bands"], age)
        if idx is None:
            lo, hi = base["age_bands"][0]["min"], base["age_bands"][-1]["max"]
            raise NotAvailable(f"There is no published rate for age {age:g}. {pricing['product_name']} is rated for ages {lo} to {hi}.")
        band = base["age_bands"][idx]
        band_txt = f"age {band['min']} to {band['max']}"
    if kind == "age_rate":
        amount = float(v[base["amount_input"]])
        rate = base["rates"][idx]
        prem = amount / base["unit"] * rate
        steps.append({"step": "Base", "detail": f"${amount:,.0f} of cover at ${rate:g} per ${base['unit']:,} ({band_txt})", "amount": round(prem)})
        return prem
    if kind == "age_column":
        col = v[base["column_input"]]
        table_value = base["columns"][col][idx]
        prem = table_value
        detail = f"{col} rate for {band_txt}: ${table_value:,}"
        if base.get("amount_input"):
            amount = float(v[base["amount_input"]])
            prem = table_value * amount / base["per"]
            detail += f", scaled to ${amount:,.0f} of cover (table is for ${base['per']:,})"
        steps.append({"step": "Base", "detail": detail, "amount": round(prem)})
        return prem
    if kind == "age_amount":
        amount = float(v[base["amount_input"]])
        rows = sorted((float(k), vals[idx]) for k, vals in base["rows"].items())
        if amount <= rows[0][0]:
            prem = rows[0][1] * amount / rows[0][0]
        elif amount >= rows[-1][0]:
            prem = rows[-1][1] * (amount / rows[-1][0]) if amount > rows[-1][0] else rows[-1][1]
        else:
            prem = rows[-1][1]
            for (a0, p0), (a1, p1) in zip(rows, rows[1:]):
                if a0 <= amount <= a1:
                    prem = p0 + (p1 - p0) * (amount - a0) / (a1 - a0)
                    break
        steps.append({"step": "Base", "detail": f"${amount:,.0f} a month at {band_txt}", "amount": round(prem)})
        return prem
    if kind == "rate_by_choice":
        amount = float(v[base["amount_input"]])
        choice = v[base["choice_input"]]
        rate = base["rates"][choice]
        prem = amount / base["unit"] * rate
        steps.append({"step": "Base", "detail": f"${amount:,.0f} at ${rate:g} per ${base['unit']:,} ({choice})", "amount": round(prem)})
        return prem
    if kind == "flat_by_choice":
        choice = v[base["choice_input"]]
        prem = float(base["values"][choice])
        steps.append({"step": "Base", "detail": f"{choice} base premium", "amount": round(prem)})
        return prem
    if kind == "trip":
        geo = v[base["geographic_input"]]
        travellers = v.get(base["travellers_input"], 1)
        if v[base["duration_input"]] == "single_trip":
            days = v[base["days_input"]]
            prem = base["per_day"][geo] * days * travellers
            steps.append({"step": "Base", "detail": f"{days:g} days x {travellers:g} traveller(s) at ${base['per_day'][geo]:g} a day ({geo})", "amount": round(prem)})
        else:
            prem = base["annual"][geo] * travellers
            steps.append({"step": "Base", "detail": f"annual multi-trip for {travellers:g} traveller(s) ({geo})", "amount": round(prem)})
        return prem
    raise ValueError(f"Unknown base type {kind}")


def compute_premium(pricing: dict, v: dict) -> dict:
    """Price one fully-validated configuration. Returns annual premium and steps."""
    steps: list[dict] = []
    amount = _base_premium(pricing, v, steps)

    for f in pricing.get("factors", []):
        if not _applies(f.get("when"), v):
            continue
        name = f["input"]
        if name not in v:
            continue
        if "map" in f:
            mult = _lookup_map(f["map"], v[name])
        else:
            mult = None
            val = float(v[name])
            for b in f["bands"]:
                if b.get("min", float("-inf")) <= val <= b.get("max", float("inf")):
                    mult = b["mult"]
                    break
        if mult is None:
            continue
        if mult != 1.0:
            amount *= mult
            steps.append({"step": f["label"], "detail": f"{_key(v[name])}: x{mult:g}", "amount": round(amount)})

    subtotal = amount
    for a in pricing.get("addons", []):
        raw = v.get(a["input"])
        if not raw:
            continue
        if "pct" in a:
            add = subtotal * a["pct"]
        elif "flat" in a:
            add = a["flat"]
        elif "flat_per_unit" in a:
            u = a["flat_per_unit"]
            add = v.get(u["input"], 0) / u["per"] * u["amount"]
        elif "rate_on_input" in a:
            add = float(raw) * a["rate_on_input"]
        else:
            continue
        amount += add
        steps.append({"step": a["label"], "detail": "add-on", "amount": round(amount)})

    return {"annual_premium": round(amount), "steps": steps}


def _instalments(annual: int, frequency: str, loading: dict) -> dict:
    n = PERIODS_PER_YEAR[frequency]
    load = loading.get(frequency, 1.0)
    year_cost = round(annual * load)
    return {
        "premium_frequency": frequency,
        "instalment_amount": round(year_cost / n, 2),
        "instalments_per_year": n,
        "annual_cost_when_paid_this_way": year_cost,
        "instalment_loading": load,
    }


# --------------------------------------------------------------------------
# Customer data and affordability
# --------------------------------------------------------------------------
def _fetch(table, advisor_id: str, customer_id: str) -> list[dict]:
    resp = table.query(
        IndexName="advisor-id-index",
        KeyConditionExpression=Key("advisor_id").eq(advisor_id) & Key("customer_id").eq(customer_id),
    )
    return resp.get("Items", [])


def annualise(policy: dict) -> tuple[float | None, str | None]:
    """Yearly premium of one stored policy, or (None, reason) if it cannot be worked out."""
    try:
        amount = float(policy.get("premium_amount"))
    except (TypeError, ValueError):
        return None, "no premium amount"
    freq = str(policy.get("premium_frequency") or "").strip().lower().replace(" ", "_")
    periods = PERIODS_PER_YEAR.get(freq)
    if periods is None:
        return None, f"unknown premium frequency '{policy.get('premium_frequency')}'"
    return amount * periods, None


def affordability_check(
    cap_percent: float,
    annual_income,
    existing: list[dict],
    proposed_annual: float,
    other_override=None,
) -> dict:
    """Pure arithmetic for the affordability guide. `existing` are stored policy items."""
    result: dict = {"rule": "affordability_cap", "cap_percent_of_income": cap_percent}
    try:
        income = float(annual_income) if annual_income not in (None, "") else None
    except (TypeError, ValueError):
        income = None
    if not income or income <= 0:
        result.update({"status": "income_unknown",
                       "message": "Annual income is not on file. Ask the advisor for it; do not guess."})
        return result

    lines, warnings, total_existing = [], [], 0.0
    if other_override is not None:
        total_existing = float(other_override)
    else:
        for p in existing:
            if str(p.get("status", "Active")).lower() != "active":
                continue
            yearly, why = annualise(p)
            name = p.get("product_name") or p.get("type") or p.get("id")
            if yearly is None:
                warnings.append(f"{name}: {why}, left out of the total")
                continue
            total_existing += yearly
            lines.append({"policy": name, "insurer": p.get("insurer") or "Awesome", "annual_premium": round(yearly)})
    total = total_existing + proposed_annual
    cap_amount = income * cap_percent / 100.0
    result.update({
        "status": "within_cap" if total <= cap_amount else "over_cap",
        "annual_income": round(income),
        "cap_amount": round(cap_amount),
        "existing_annual_premium": round(total_existing),
        "proposed_annual_premium": round(proposed_annual),
        "total_annual_premium": round(total),
        "total_percent_of_income": round(total / income * 100, 1),
        "headroom": round(cap_amount - total),
        "existing_policies": lines,
    })
    if warnings:
        result["warnings"] = warnings
    if result["status"] == "over_cap":
        result["message"] = (
            f"Total premiums would be {result['total_percent_of_income']}% of income, above the "
            f"{cap_percent:g}% guide. Raise this with the advisor before recommending the cover."
        )
    return result


# --------------------------------------------------------------------------
# estimate_premium
# --------------------------------------------------------------------------
def _parse_options(raw) -> tuple[dict | None, str | None]:
    if raw in (None, "", {}):
        return {}, None
    if isinstance(raw, dict):
        return raw, None
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except ValueError:
            return None, "options must be a JSON object, for example {\"sum_assured\": 500000, \"term_years\": \"20\"}"
        if isinstance(parsed, dict):
            return parsed, None
    return None, "options must be a JSON object"


def _input_hint(name: str, spec: dict) -> dict:
    hint = {"option": name, "description": spec.get("description", "")}
    if spec["type"] == "choice":
        hint["choices"] = spec["choices"]
    if spec["type"] == "number":
        hint["range"] = [spec.get("min"), spec.get("max")]
    # Anything the record could have supplied but did not is asked of the advisor.
    if spec.get("source") in ("advisor", "profile"):
        hint["ask"] = "advisor"
    return hint


def estimate(args: dict, profile: dict | None, policies: list[dict], pricing: dict, affordability_rule: dict | None) -> tuple[int, dict]:
    options, err = _parse_options(args.get("options"))
    if err:
        return 400, {"status": "invalid_options", "message": err}
    inputs = pricing["inputs"]
    unknown = [k for k in options if k not in inputs and k not in GLOBAL_OPTIONS]
    if unknown:
        return 400, {"status": "invalid_options",
                     "message": f"Unknown option(s): {', '.join(unknown)}.",
                     "valid_options": [*inputs.keys(), *GLOBAL_OPTIONS.keys()]}

    raw: dict = {}
    assumptions: list[str] = []
    # Profile-sourced values first, so explicit options can override them.
    if profile:
        if "age" in inputs and profile.get("date_of_birth"):
            age = _age_from_dob(profile["date_of_birth"])
            if age is not None:
                raw["age"] = age
        if "smoker" in inputs and profile.get("smoking") is not None:
            raw["smoker"] = bool(profile["smoking"])
        if "gender" in inputs and profile.get("gender") in ("male", "female"):
            raw["gender"] = profile["gender"]
    for k, val in options.items():
        if k in inputs:
            raw[k] = val

    values: dict = {}
    problems: list[str] = []
    needs: list[dict] = []
    for name, spec in inputs.items():
        if name in raw:
            val, e = _coerce(name, spec, raw[name])
            if e:
                problems.append(e)
            else:
                values[name] = val
        elif "default" in spec:
            values[name] = spec["default"]
            assumptions.append(f"{name} not given, assumed {_key(spec['default'])}")
    # Required checks use the values gathered so far (for required_when).
    for name, spec in inputs.items():
        if name in values:
            continue
        needed = spec.get("required") or (spec.get("required_when") and _applies(spec["required_when"], values))
        if needed and name not in raw:
            needs.append(_input_hint(name, spec))
    if problems or needs:
        body: dict = {"status": "needs_input"}
        if problems:
            body["problems"] = problems
        if needs:
            body["missing"] = needs
        body["message"] = ("Ask the advisor for the missing details, or correct the invalid ones, "
                           "then call estimate_premium again. Do not guess values.")
        return 200, body

    try:
        priced = compute_premium(pricing, values)
    except NotAvailable as e:
        return 200, {"status": "not_available", "message": str(e)}

    annual = priced["annual_premium"]
    freq = values.get("premium_frequency", "annual")
    result = {
        "status": "ok",
        "indicative": True,
        "product_type": pricing["product_type"],
        "product_name": pricing["product_name"],
        "currency": pricing["currency"],
        "effective_date": pricing["effective_date"],
        "premium_basis": pricing.get("premium_basis"),
        "annual_premium": annual,
    }
    if "premium_frequency" in inputs:
        result.update(_instalments(annual, freq, pricing.get("frequency_loading", {})))
    result["inputs_used"] = values
    if assumptions:
        result["assumptions"] = assumptions
    result["breakdown"] = priced["steps"]
    if pricing.get("notes"):
        result["notes"] = pricing["notes"]

    # Affordability.
    cap = float((affordability_rule or {}).get("cap_percent_of_income", 10))
    income = options.get("annual_income", (profile or {}).get("annual_income"))
    yearly_cost = result.get("annual_cost_when_paid_this_way", annual)
    if profile is not None or "annual_income" in options or "other_annual_premiums" in options:
        other = options.get("other_annual_premiums") if profile is None else None
        result["affordability"] = affordability_check(cap, _num(income), policies, yearly_cost, other)
    else:
        result["affordability"] = {
            "status": "not_checked",
            "message": "No customer_id was given. Pass customer_id and advisor_id, or annual_income and other_annual_premiums, to check the 10% affordability guide.",
        }
    result["disclaimer"] = DISCLAIMER.format(date=pricing["effective_date"])
    return 200, result


def handler_estimate(event, context):
    try:
        args = _event_args(event)
        product_type = args.get("product_type")
        if not isinstance(product_type, str) or not product_type.strip():
            return _response(400, {"status": "invalid_options", "message": "product_type is required",
                                   "available_product_types": KNOWN_PRODUCT_TYPES})
        pt = product_type.strip().lower()
        if pt not in KNOWN_PRODUCT_TYPES or not SAFE_KEY_RE.match(pt):
            return _response(404, {"status": "unknown_product", "message": f"No pricing table for '{pt}'",
                                   "available_product_types": KNOWN_PRODUCT_TYPES})
        try:
            pricing = load_pricing(pt)
        except FileNotFoundError:
            return _response(500, {"message": "Pricing table unavailable"})
        try:
            rule = read_pricing_object("affordability")
        except FileNotFoundError:
            rule = None

        profile, policies = None, []
        customer_id, advisor_id = args.get("customer_id"), args.get("advisor_id")
        if customer_id:
            if not advisor_id:
                return _response(400, {"status": "invalid_options", "message": "advisor_id is required with customer_id"})
            profiles_table, policies_table = _tables()
            items = _fetch(profiles_table, str(advisor_id), str(customer_id))
            if not items:
                return _response(404, {"status": "customer_not_found", "message": "Customer not found for this advisor"})
            profile = items[0]
            policies = _fetch(policies_table, str(advisor_id), str(customer_id))

        status, body = estimate(args, profile, policies, pricing, rule)
        return _response(status, body)
    except Exception as e:  # noqa: BLE001
        print(f"estimate_premium failed: {type(e).__name__}: {e}")
        return _response(500, {"message": "Pricing is temporarily unavailable"})
