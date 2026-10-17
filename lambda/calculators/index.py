"""
Calculators Lambda: education and retirement savings-gap scenarios.

One gateway tool, `calculate_savings_gap`, with a `scenario` of "education" or
"retirement". All the arithmetic is here, in code, so the assistants never
compute compound growth themselves (same rule as the premium estimator).

What it answers
    education   How much will a child's studies cost, how much of that will the
                current savings and monthly saving cover, and what is the
                shortfall (as a monthly amount, or a lump sum today)?
    retirement  What fund is needed at retirement to pay the wanted income for
                the retirement years, how much will current saving provide, and
                what is the shortfall?

Assumptions (inflation, return, ages) default to assumptions.json and can be
overridden per call. Every answer lists the assumptions it used. Results are
illustrations: they are not forecasts, not a quote, and not a statement about
any Awesome product's return.

Conventions
    - Contributions are made monthly, at the end of each month.
    - Education costs are paid at the start of each study year; the fund keeps
      earning the investment return while it is being spent.
    - Retirement income is paid at the start of each retirement year and rises
      with inflation; the fund earns the in-retirement return.
    - Percentages are annual and given as plain numbers (5 means 5%).
"""
import json
import os
from decimal import Decimal

_HERE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(_HERE, "assumptions.json"), "r", encoding="utf-8") as _f:
    DEFAULTS = json.load(_f)

DISCLAIMER = (
    "This is an illustration based on the assumptions shown. It is not a forecast, a quote or a "
    "guarantee, and it says nothing about the return of any Awesome product."
)

# (name, min, max) limits for the numbers the advisor or the model may pass.
LIMITS = {
    "child_age": (0, 25), "start_age": (15, 30), "years_of_study": (1, 10),
    "annual_cost_today": (1, 1_000_000),
    "current_age": (18, 80), "retirement_age": (40, 80), "life_expectancy": (60, 110),
    "annual_income_today": (1, 10_000_000), "other_annual_income_today": (0, 10_000_000),
    "income_replacement_percent": (1, 200),
    "current_savings": (0, 1_000_000_000), "monthly_saving": (0, 10_000_000),
    "education_cost_inflation_percent": (-5, 20), "investment_return_percent": (-5, 20),
    "inflation_percent": (-5, 20), "return_before_retirement_percent": (-5, 20),
    "return_in_retirement_percent": (-5, 20),
}
REQUIRED = {
    "education": ["child_age", "annual_cost_today"],
    "retirement": ["current_age", "annual_income_today"],
}
ASSUMPTION_KEYS = {
    "education": ["start_age", "years_of_study", "education_cost_inflation_percent", "investment_return_percent"],
    "retirement": ["retirement_age", "life_expectancy", "income_replacement_percent", "inflation_percent",
                   "return_before_retirement_percent", "return_in_retirement_percent"],
}
OPTIONAL = {  # inputs that are not assumptions and default to zero
    "education": ["current_savings", "monthly_saving"],
    "retirement": ["other_annual_income_today", "current_savings", "monthly_saving"],
}


# --------------------------------------------------------------------------
# Maths (pure)
# --------------------------------------------------------------------------
def monthly_rate(annual_percent: float) -> float:
    """Monthly rate equivalent to an annual percentage (compounded annually)."""
    return (1 + annual_percent / 100.0) ** (1 / 12) - 1


def fv_lump(amount: float, annual_percent: float, years: float) -> float:
    return amount * (1 + annual_percent / 100.0) ** years


def fv_monthly(payment: float, annual_percent: float, months: int) -> float:
    """Future value of equal end-of-month payments."""
    if months <= 0 or payment <= 0:
        return 0.0
    i = monthly_rate(annual_percent)
    if abs(i) < 1e-12:
        return payment * months
    return payment * (((1 + i) ** months - 1) / i)


def monthly_needed(shortfall: float, annual_percent: float, months: int) -> float | None:
    """Level monthly saving that grows to `shortfall` in `months`; None if there is no time left."""
    if shortfall <= 0:
        return 0.0
    if months <= 0:
        return None
    i = monthly_rate(annual_percent)
    if abs(i) < 1e-12:
        return shortfall / months
    return shortfall * i / ((1 + i) ** months - 1)


def pv_growing_annuity_due(first_payment: float, growth_percent: float, discount_percent: float, n: int) -> float:
    """Present value, at the first payment date, of n yearly payments (paid at the start of each
    year) that start at `first_payment` and grow with `growth_percent`, discounted at `discount_percent`."""
    if n <= 0:
        return 0.0
    g = (1 + growth_percent / 100.0) / (1 + discount_percent / 100.0)
    if abs(g - 1) < 1e-12:
        return first_payment * n
    return first_payment * (1 - g ** n) / (1 - g)


def _r0(x):
    return None if x is None else int(round(x))


def education_gap(p: dict) -> dict:
    years_to_start = p["start_age"] - p["child_age"]
    if years_to_start < 0:
        raise ValueError(f"child_age ({p['child_age']:g}) is already past start_age ({p['start_age']:g}); give the age studies start")
    infl, ret = p["education_cost_inflation_percent"], p["investment_return_percent"]
    n_years = int(p["years_of_study"])
    schedule, total_nominal, required = [], 0.0, 0.0
    for k in range(n_years):
        cost = p["annual_cost_today"] * (1 + infl / 100.0) ** (years_to_start + k)
        total_nominal += cost
        required += cost / (1 + ret / 100.0) ** k
        schedule.append({"study_year": k + 1, "age": p["start_age"] + k, "cost": _r0(cost)})
    months = int(round(years_to_start * 12))
    projected = fv_lump(p["current_savings"], ret, years_to_start) + fv_monthly(p["monthly_saving"], ret, months)
    shortfall = max(0.0, required - projected)
    return {
        "years_until_studies_start": years_to_start,
        "total_cost_in_future_money": _r0(total_nominal),
        "fund_needed_when_studies_start": _r0(required),
        "projected_fund_when_studies_start": _r0(projected),
        "shortfall": _r0(shortfall),
        "on_track": shortfall <= 0.5,
        "extra_monthly_saving_needed": _r0(monthly_needed(shortfall, ret, months)),
        "or_lump_sum_today": _r0(shortfall / (1 + ret / 100.0) ** years_to_start) if shortfall > 0 else 0,
        "cost_by_year": schedule,
    }


def retirement_gap(p: dict) -> dict:
    ra, ca, le = p["retirement_age"], p["current_age"], p["life_expectancy"]
    if ra <= ca:
        raise ValueError(f"current_age ({ca:g}) is not before retirement_age ({ra:g})")
    if le <= ra:
        raise ValueError(f"life_expectancy ({le:g}) must be after retirement_age ({ra:g})")
    years_to_ret, years_in_ret = ra - ca, int(round(le - ra))
    infl = p["inflation_percent"]
    r_before, r_after = p["return_before_retirement_percent"], p["return_in_retirement_percent"]
    grow = (1 + infl / 100.0) ** years_to_ret
    wanted = p["annual_income_today"] * p["income_replacement_percent"] / 100.0 * grow
    other = p["other_annual_income_today"] * grow
    needed_income = max(0.0, wanted - other)
    required = pv_growing_annuity_due(needed_income, infl, r_after, years_in_ret)
    months = int(round(years_to_ret * 12))
    projected = fv_lump(p["current_savings"], r_before, years_to_ret) + fv_monthly(p["monthly_saving"], r_before, months)
    shortfall = max(0.0, required - projected)
    return {
        "years_until_retirement": years_to_ret,
        "years_in_retirement": years_in_ret,
        "wanted_first_year_income": _r0(wanted),
        "other_income_first_year": _r0(other),
        "income_to_fund_first_year": _r0(needed_income),
        "fund_needed_at_retirement": _r0(required),
        "projected_fund_at_retirement": _r0(projected),
        "shortfall": _r0(shortfall),
        "on_track": shortfall <= 0.5,
        "extra_monthly_saving_needed": _r0(monthly_needed(shortfall, r_before, months)),
        "or_lump_sum_today": _r0(shortfall / (1 + r_before / 100.0) ** years_to_ret) if shortfall > 0 else 0,
    }


# --------------------------------------------------------------------------
# Inputs
# --------------------------------------------------------------------------
def _parse_inputs(raw):
    if raw in (None, ""):
        return {}, None
    if isinstance(raw, dict):
        return dict(raw), None
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None, "inputs must be a JSON object, for example {\"child_age\": 6, \"annual_cost_today\": 25000}"
    if not isinstance(data, dict):
        return None, "inputs must be a JSON object"
    return data, None


def _number(name, value):
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a number")
    if isinstance(value, str):
        value = value.replace(",", "").replace("$", "").replace("%", "").strip()
    try:
        num = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a number") from None
    lo, hi = LIMITS[name]
    if not lo <= num <= hi:
        raise ValueError(f"{name} must be between {lo:g} and {hi:g}")
    return num


def calculate(scenario, raw_inputs):
    """Returns (http_status, body)."""
    scenario = str(scenario or "").strip().lower()
    if scenario not in REQUIRED:
        return 400, {"status": "invalid_scenario", "message": "scenario must be 'education' or 'retirement'",
                     "scenarios": sorted(REQUIRED)}
    data, err = _parse_inputs(raw_inputs)
    if err:
        return 400, {"status": "invalid_inputs", "message": err}
    allowed = set(REQUIRED[scenario]) | set(OPTIONAL[scenario]) | set(ASSUMPTION_KEYS[scenario])
    unknown = sorted(set(data) - allowed)
    if unknown:
        return 400, {"status": "invalid_inputs", "message": f"Unknown input(s): {', '.join(unknown)}.",
                     "valid_inputs": sorted(allowed)}
    missing = [k for k in REQUIRED[scenario] if data.get(k) in (None, "")]
    if missing:
        return 200, {"status": "needs_input", "missing": missing,
                     "message": "Ask the advisor for the missing details. Do not guess them.",
                     "describe": {k: DESCRIPTIONS[k] for k in missing}}
    values, assumptions, problems = {}, {}, []
    for name in REQUIRED[scenario] + OPTIONAL[scenario] + ASSUMPTION_KEYS[scenario]:
        given = data.get(name) not in (None, "")
        if given:
            try:
                values[name] = _number(name, data[name])
            except ValueError as e:
                problems.append(str(e))
                continue
        elif name in ASSUMPTION_KEYS[scenario]:
            values[name] = float(DEFAULTS[scenario][name])
        elif name in OPTIONAL[scenario]:
            values[name] = 0.0
        if name in ASSUMPTION_KEYS[scenario]:
            assumptions[name] = {"value": values[name], "source": "advisor" if given else "default"}
    if problems:
        return 200, {"status": "needs_input", "problems": problems,
                     "message": "Correct the invalid values, then call again."}
    try:
        results = education_gap(values) if scenario == "education" else retirement_gap(values)
    except ValueError as e:
        return 200, {"status": "needs_input", "problems": [str(e)], "message": "Correct the values, then call again."}
    return 200, {
        "status": "ok",
        "scenario": scenario,
        "indicative": True,
        "inputs_used": {k: values[k] for k in REQUIRED[scenario] + OPTIONAL[scenario]},
        "assumptions": assumptions,
        "results": results,
        "disclaimer": DISCLAIMER,
    }


DESCRIPTIONS = {
    "child_age": "The child's age today, in years.",
    "annual_cost_today": "Yearly cost of the studies at today's prices, in USD.",
    "current_age": "The customer's age today, in years.",
    "annual_income_today": "The customer's yearly income today, in USD.",
}


# --------------------------------------------------------------------------
# Lambda plumbing
# --------------------------------------------------------------------------
def _response(status, body):
    return {
        "statusCode": status,
        "headers": {
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "Content-Type,Authorization",
            "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
        },
        "body": json.dumps(body, default=lambda o: float(o) if isinstance(o, Decimal) else str(o)),
    }


def _event_args(event):
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


def handler(event, context):
    try:
        args = _event_args(event)
        status, body = calculate(args.get("scenario"), args.get("inputs"))
        return _response(status, body)
    except Exception as e:  # noqa: BLE001
        print(f"calculate_savings_gap failed: {type(e).__name__}: {e}")
        return _response(500, {"message": "The calculator is temporarily unavailable"})
