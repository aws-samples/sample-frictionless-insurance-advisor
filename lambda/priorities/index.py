"""
Priorities Lambda: "what should I do today?" for one advisor.

GET /priorities
    Returns the advisor's customers that most need attention, with the reason
    for each. No model is involved: five rules over the profiles, notes and
    policies, scored in code, so the answer is the same every time and fast.

Used by the Cockpit (the welcome area of the React app) and, through the
AgentCore Gateway, by the text and voice assistants as `get_priorities`.

Rules (thresholds are the constants below)
    review_overdue        no profile review for more than 90 days
    life_event_upcoming   a life-event note dated within the next 60 days
    life_event_passed     a life event up to 14 days ago and no interaction
                          note written since
    renewal_due           an active policy (any insurer) renewing within 60 days
    renewal_passed        an active policy whose renewal date passed up to 30
                          days ago
    over_cap              yearly premiums above 10% of income
    prospect_quiet        no Awesome policy and no contact for 30 days

A customer that trips several rules appears once, with every hit listed in
`reasons`, strongest first (three renewals due soon are three reasons). The first
reason decides the ranking and the suggested action.
The button offers "Draft email" when the strongest reason is a date to reach out about (life event,
renewal, quiet prospect) and "Annual review" when it is a stale review or premiums above the guide,
or a renewal on a file that is also due for a review. A prospect with no Awesome cover gets "Prepare
meeting" instead of a review.

Not covered, on purpose: lapse prediction (there is no payment data),
notifications, scheduling.
"""
import json
import os
from datetime import date, datetime
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key

REVIEW_STALE_DAYS = 90
LIFE_EVENT_AHEAD_DAYS = 60
LIFE_EVENT_PAST_DAYS = 14
RENEWAL_AHEAD_DAYS = 60
RENEWAL_PAST_DAYS = 30
PROSPECT_QUIET_DAYS = 30
# Affordability guide. Keep in sync with s3-data/pricing/affordability.json.
AFFORDABILITY_CAP_PERCENT = 10
DEFAULT_LIMIT = 8
MAX_LIMIT = 25

# Rule weights (higher = more urgent). Date-based rules add an urgency bonus.
WEIGHT = {
    "life_event_upcoming": 60,
    "life_event_passed": 60,
    "renewal_due": 55,
    "renewal_passed": 55,
    "over_cap": 45,
    "review_overdue": 35,
    "prospect_quiet": 30,
}
ACTION = {  # what the Cockpit offers for the strongest rule
    # A date to reach out about (life event, renewal, a quiet prospect): draft an email.
    "life_event_upcoming": "outreach",
    "life_event_passed": "outreach",
    "renewal_due": "outreach",
    "renewal_passed": "outreach",
    "prospect_quiet": "outreach",
    # The file itself needs attention (stale review, premiums too high): review it.
    "over_cap": "annual_review",
    "review_overdue": "annual_review",
}
# A renewal is a reason to write, unless the file is also due for a review: then one annual
# review covers both, so that is what the button offers.
REVIEW_RULES = ("review_overdue", "over_cap")


def suggested_action(top_rule, all_rules, prospect):
    action = ACTION[top_rule]
    if top_rule in ("renewal_due", "renewal_passed") and any(r in REVIEW_RULES for r in all_rules):
        action = "annual_review"
    if prospect and action == "annual_review":
        action = "meeting_prep"  # an annual review needs Awesome cover on file
    return action
PERIODS_PER_YEAR = {
    "monthly": 12, "quarterly": 4, "semi_annual": 2, "semi-annual": 2, "semiannual": 2,
    "biannual": 2, "annual": 1, "annually": 1, "yearly": 1,
}

_ddb = None


def _tables():
    global _ddb
    if _ddb is None:
        _ddb = boto3.resource("dynamodb")
    return _ddb.Table(os.environ["PROFILES_TABLE"]), _ddb.Table(os.environ["POLICIES_TABLE"])


# --------------------------------------------------------------------------
# Pure logic
# --------------------------------------------------------------------------
def _num(v):
    if isinstance(v, Decimal):
        return int(v) if v == v.to_integral_value() else float(v)
    return v


def _d(value):
    """ISO date or timestamp -> date, or None."""
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _annual(policy):
    try:
        amount = float(policy.get("premium_amount"))
    except (TypeError, ValueError):
        return None
    freq = str(policy.get("premium_frequency") or "").strip().lower().replace(" ", "_")
    periods = PERIODS_PER_YEAR.get(freq)
    return None if periods is None else amount * periods


def _urgency(days):
    """Extra points for how close a date is. days < 0 means it has passed."""
    if days < 0:
        return 25
    if days <= 14:
        return 30
    if days <= 30:
        return 15
    return 5


def _short(text, n=90):
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[: n - 1].rstrip() + "\u2026"


def is_prospect(policies):
    """No active Awesome policy (none at all, or only other insurers')."""
    return not any(
        not p.get("third_party") and str(p.get("status", "Active")).lower() == "active" for p in policies
    )


def rules_for_customer(profile, policies, today):
    """All rules this customer trips, strongest first."""
    out = []
    notes = [n for n in (profile.get("notes") or []) if isinstance(n, dict)]
    active = [p for p in policies if str(p.get("status", "Active")).lower() == "active"]

    # 1. profile review overdue
    reviewed = _d(profile.get("last_reviewed")) or _d(profile.get("join_date"))
    if reviewed:
        since = (today - reviewed).days
        if since > REVIEW_STALE_DAYS:
            bonus = min(20, ((since - REVIEW_STALE_DAYS) // 30) * 5)
            out.append({
                "rule": "review_overdue", "score": WEIGHT["review_overdue"] + bonus,
                "due_date": (reviewed).isoformat(), "days": since, "params": {"days": since},
                "reason": f"Profile last reviewed {since} days ago (guide: every {REVIEW_STALE_DAYS} days).",
            })

    # 2. life events
    interactions = [d for d in (_d(n.get("created_at")) for n in notes if n.get("type") == "interaction") if d]
    for n in notes:
        if n.get("type") != "life_event":
            continue
        ev = _d(n.get("event_date"))
        if not ev:
            continue
        days = (ev - today).days
        text = _short(n.get("text"))
        if 0 <= days <= LIFE_EVENT_AHEAD_DAYS:
            out.append({
                "rule": "life_event_upcoming", "score": WEIGHT["life_event_upcoming"] + _urgency(days),
                "due_date": ev.isoformat(), "days": days, "params": {"days": days, "text": text},
                "reason": f"Life event in {days} days ({ev.isoformat()}): {text}",
            })
        elif -LIFE_EVENT_PAST_DAYS <= days < 0 and not any(i > ev for i in interactions):
            out.append({
                "rule": "life_event_passed", "score": WEIGHT["life_event_passed"] + _urgency(days),
                "due_date": ev.isoformat(), "days": days, "params": {"days": -days, "text": text},
                "reason": f"Life event {-days} days ago with no follow-up since: {text}",
            })

    # 3. renewals (own and other insurers')
    for p in active:
        ren = _d(p.get("renewal_date"))
        if not ren:
            continue
        days = (ren - today).days
        name = p.get("product_name") or p.get("type") or "Policy"
        insurer = p.get("insurer") or "Awesome"
        own = not p.get("third_party")
        who = name if own else f"{name} ({insurer})"  # our own policies need no insurer in brackets
        if 0 <= days <= RENEWAL_AHEAD_DAYS:
            out.append({
                "rule": "renewal_due", "score": WEIGHT["renewal_due"] + _urgency(days),
                "due_date": ren.isoformat(), "days": days, "params": {"days": days, "policy": name, "insurer": insurer, "own": int(own)},
                "reason": f"{who} renews in {days} days ({ren.isoformat()}).",
            })
        elif -RENEWAL_PAST_DAYS <= days < 0:
            out.append({
                "rule": "renewal_passed", "score": WEIGHT["renewal_passed"] + _urgency(days),
                "due_date": ren.isoformat(), "days": days, "params": {"days": -days, "policy": name, "insurer": insurer, "own": int(own)},
                "reason": f"{who} passed its renewal date {-days} days ago.",
            })

    # 4. premiums above 10% of income
    try:
        income = float(profile.get("annual_income"))
    except (TypeError, ValueError):
        income = None
    if income and income > 0:
        total = sum(a for a in (_annual(p) for p in active) if a is not None)
        pct = total / income * 100
        if pct > AFFORDABILITY_CAP_PERCENT:
            bonus = min(20, round(pct - AFFORDABILITY_CAP_PERCENT))
            out.append({
                "rule": "over_cap", "score": WEIGHT["over_cap"] + bonus, "due_date": None, "days": None,
                "params": {"percent": round(pct, 1), "cap": AFFORDABILITY_CAP_PERCENT},
                "reason": f"Yearly premiums are {round(pct, 1)}% of income, above the {AFFORDABILITY_CAP_PERCENT}% guide.",
            })

    # 5. prospect nobody has contacted lately
    if is_prospect(policies):
        contact = max(interactions) if interactions else _d(profile.get("join_date"))
        if contact:
            quiet = (today - contact).days
            if quiet > PROSPECT_QUIET_DAYS:
                bonus = min(20, quiet - PROSPECT_QUIET_DAYS)
                out.append({
                    "rule": "prospect_quiet", "score": WEIGHT["prospect_quiet"] + bonus,
                    "due_date": contact.isoformat(), "days": quiet, "params": {"days": quiet},
                    "reason": f"No Awesome policy yet and no contact for {quiet} days.",
                })

    out.sort(key=lambda r: (-r["score"], r["due_date"] or "9999"))
    return out


def build_priorities(profiles, policies, today, limit=DEFAULT_LIMIT):
    """The ranked list for one advisor. Pure: no I/O."""
    by_customer = {}
    for p in policies:
        by_customer.setdefault(p.get("customer_id"), []).append(p)
    items = []
    for prof in profiles:
        cid = prof.get("customer_id")
        mine = by_customer.get(cid, [])
        rules = rules_for_customer(prof, mine, today)
        if not rules:
            continue
        # Only the strongest hit of each rule counts toward the score (four
        # overdue renewals are not four times as urgent), but every hit is
        # returned in `reasons` so none of them is hidden from the advisor.
        best, seen = [], set()
        for r in rules:  # already strongest first
            if r["rule"] not in seen:
                seen.add(r["rule"])
                best.append(r)
        top, rest = best[0], best[1:]
        prospect = is_prospect(mine)
        action = suggested_action(top["rule"], [r["rule"] for r in rules], prospect)
        reasons = [{k: r[k] for k in ("rule", "reason", "due_date", "days", "params")} for r in rules]
        items.append({
            "customer_id": cid,
            "customer_name": prof.get("name"),
            "is_prospect": prospect,
            "score": round(top["score"] + 0.25 * sum(r["score"] for r in rest), 1),
            "reasons": reasons,  # all of them, strongest first; the first is why the customer is ranked here
            "suggested_action": action,
        })
    items.sort(key=lambda i: (-i["score"], i["reasons"][0]["due_date"] or "9999", i["customer_name"] or ""))
    return {
        "generated_on": today.isoformat(),
        "total_customers": len(profiles),
        "customers_needing_attention": len(items),
        "items": items[: max(1, min(int(limit), MAX_LIMIT))],
    }


# --------------------------------------------------------------------------
# Lambda plumbing
# --------------------------------------------------------------------------
def _response(status, body):
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "Content-Type,Authorization",
            "Access-Control-Allow-Methods": "GET,OPTIONS",
        },
        "body": json.dumps(body, default=_num),
    }


def extract_advisor_id(event):
    """The signed-in advisor's email, taken ONLY from the verified sign-in token.

    API Gateway's Cognito authorizer has already checked the token; its claims are
    all this trusts. The query string and body are never used to pick the advisor.
    """
    claims = ((event.get("requestContext") or {}).get("authorizer") or {}).get("claims") or {}
    username = claims.get("username")
    if not username:
        return None
    try:
        cognito = boto3.client("cognito-idp")
        user = cognito.admin_get_user(UserPoolId=os.environ.get("USER_POOL_ID"), Username=username)
        for attr in user["UserAttributes"]:
            if attr["Name"] == "email":
                return attr["Value"]
    except Exception as e:  # noqa: BLE001
        print(f"Error resolving the advisor: {type(e).__name__}")
    return None


def _query_all(table, advisor_id):
    items, kwargs = [], {
        "IndexName": "advisor-id-index",
        "KeyConditionExpression": Key("advisor_id").eq(advisor_id),
    }
    while True:
        resp = table.query(**kwargs)
        items += resp.get("Items", [])
        if "LastEvaluatedKey" not in resp:
            return items
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]


def handler(event, context):
    try:
        if (event.get("httpMethod") or "GET").upper() == "OPTIONS":
            return _response(200, {})
        advisor_id = extract_advisor_id(event)
        if not advisor_id:
            return _response(401, {"message": "Unauthorized - no advisor ID found"})
        qs = event.get("queryStringParameters") or {}
        try:
            limit = int(qs.get("limit") or DEFAULT_LIMIT)
        except ValueError:
            return _response(400, {"message": "limit must be a whole number"})
        profiles_table, policies_table = _tables()
        profiles = _query_all(profiles_table, advisor_id)
        policies = _query_all(policies_table, advisor_id)
        return _response(200, build_priorities(profiles, policies, datetime.utcnow().date(), limit))
    except Exception as e:  # noqa: BLE001
        print(f"priorities failed: {type(e).__name__}: {e}")
        return _response(500, {"message": "An internal error occurred while processing your request"})
