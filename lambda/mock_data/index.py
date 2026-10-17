"""
Mock Data Populator Lambda Function

Resets the demo to its seed. Every run:

1. Shifts the dated seed fields so the demo looks current (see "Dates").
2. Upserts every seed row into the profiles, policies and catalog tables.
3. Deletes every row that is not in the seed files (prospects, policies and
   notes added during earlier demos).
4. Clears the AgentCore long-term memory, so the assistants do not remember
   facts from earlier demos about customers that were just reset.

It is started three ways, all with the same result:
- the CloudFormation custom resource in `cdk/tools_stack.py`, on every deploy;
- an EventBridge schedule in the same stack, so the demo is back to the seed
  at least every 48 hours;
- `scripts/refresh-demo-data.sh`, by hand.

Dates
-----
The seed files were written against two reference days, recorded in
`seed_anchors.json` (profiles and policies were authored at different times).
On each run the dated fields are moved forward by (today - anchor) days, so a
policy that renews 12 days after its anchor renews 12 days from today, and a
note written 3 days before the anchor is still 3 days old. `date_of_birth` is
never moved. Note timestamps are not allowed to land in the future.

Payload (all optional)
----------------------
    {"mode": "scheduled"}   raise on failure so the Lambda Errors metric (and
                            its alarm) sees it. Any other value, or none, logs
                            the error and returns a 500 body, as the
                            CloudFormation custom resource expects.
    {"keep_notes": true}    carry notes added after seeding onto the re-seeded
                            profiles (default: they are dropped, it is a reset).
    {"skip_memory": true}   leave AgentCore memory alone.
    {"today": "YYYY-MM-DD"} pretend it is another day (tests).

Note: this model is for demo workloads only. A real system would never
reconcile user-owned rows from a static JSON seed.
"""
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import boto3

PROFILES_PRIMARY_KEY = "customer_id"
POLICIES_PRIMARY_KEY = "id"
CATALOG_PRIMARY_KEY = "product_id"

# Dated fields that move with the anchor. date_of_birth is deliberately absent.
PROFILE_DATE_FIELDS = ("join_date", "last_reviewed")
NOTE_DATE_FIELDS = ("created_at", "event_date")
POLICY_DATE_FIELDS = ("start_date", "last_updated", "renewal_date")

SEED_NOTE_PREFIX = "seed-"
NOTES_PER_PROFILE_MAX = 100


def _here(filename):
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)


def load_json_data(filename):
    """Load a seed file and convert numbers to Decimal for DynamoDB."""
    with open(filename, "r", encoding="utf-8") as f:
        data = json.load(f)

    def convert_to_decimal(obj):
        if isinstance(obj, list):
            return [convert_to_decimal(item) for item in obj]
        elif isinstance(obj, dict):
            return {key: convert_to_decimal(value) for key, value in obj.items()}
        elif isinstance(obj, float) or (isinstance(obj, int) and not isinstance(obj, bool)):
            return Decimal(str(obj))
        return obj

    return convert_to_decimal(data)


def load_anchors():
    with open(_here("seed_anchors.json"), "r", encoding="utf-8") as f:
        raw = json.load(f)
    return {k: date.fromisoformat(v) for k, v in raw.items() if not k.startswith("_")}


# --------------------------------------------------------------------------
# Date shifting
# --------------------------------------------------------------------------
def shift_value(value, delta_days, now=None):
    """Move one ISO date (YYYY-MM-DD) or ISO timestamp forward by delta_days.

    A timestamp is never moved past `now`: shifting "15:10 on the anchor day"
    to today could otherwise land hours in the future.
    """
    if not isinstance(value, str) or not value:
        return value
    delta = timedelta(days=delta_days)
    if len(value) == 10:
        return (date.fromisoformat(value) + delta).isoformat()
    moved = datetime.fromisoformat(value) + delta
    if now is not None and moved.tzinfo is not None and moved > now:
        moved = now.astimezone(moved.tzinfo)
    return moved.isoformat(timespec="seconds")


def shift_profiles(profiles, delta_days, now=None):
    out = []
    for p in profiles:
        p = dict(p)
        for f in PROFILE_DATE_FIELDS:
            if f in p:
                p[f] = shift_value(p[f], delta_days)
        notes = []
        for n in p.get("notes", []) or []:
            n = dict(n)
            for f in NOTE_DATE_FIELDS:
                if f in n:
                    n[f] = shift_value(n[f], delta_days, now)
            notes.append(n)
        if "notes" in p:
            p["notes"] = notes
        out.append(p)
    return out


def shift_policies(policies, delta_days):
    out = []
    for p in policies:
        p = dict(p)
        for f in POLICY_DATE_FIELDS:
            if f in p:
                p[f] = shift_value(p[f], delta_days)
        out.append(p)
    return out


def create_sample_data(today=None, now=None):
    """Load the seed files and move their dates so they are relative to `today`."""
    anchors = load_anchors()
    today = today or datetime.now(timezone.utc).date()
    now = now or datetime.now(timezone.utc)
    profiles = load_json_data(_here("profiles.json"))
    policies = load_json_data(_here("policies.json"))
    catalog = load_json_data(_here("catalog.json"))
    profile_delta = (today - anchors["profiles"]).days
    policy_delta = (today - anchors["policies"]).days
    return (
        shift_profiles(profiles, profile_delta, now),
        shift_policies(policies, policy_delta),
        catalog,
        {"profiles": profile_delta, "policies": policy_delta},
    )


# --------------------------------------------------------------------------
# Tables
# --------------------------------------------------------------------------
def _scan_primary_keys(table, pk_attr):
    """The set of primary-key values currently in the table (key-only scan)."""
    existing = set()
    kwargs = {"ProjectionExpression": "#pk", "ExpressionAttributeNames": {"#pk": pk_attr}}
    while True:
        response = table.scan(**kwargs)
        for item in response.get("Items", []):
            existing.add(item[pk_attr])
        if "LastEvaluatedKey" not in response:
            break
        kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]
    return existing


def _scan_user_notes(table, pk_attr):
    """pk -> notes that were added after seeding (anything not named seed-*)."""
    found = {}
    kwargs = {
        "ProjectionExpression": "#pk, #n",
        "ExpressionAttributeNames": {"#pk": pk_attr, "#n": "notes"},
    }
    while True:
        response = table.scan(**kwargs)
        for item in response.get("Items", []):
            kept = [
                n for n in item.get("notes", []) or []
                if isinstance(n, dict) and not str(n.get("note_id", "")).startswith(SEED_NOTE_PREFIX)
            ]
            if kept:
                found[item[pk_attr]] = kept
        if "LastEvaluatedKey" not in response:
            break
        kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]
    return found


def _sync_table(table, items, pk_attr, label, keep_user_notes=False):
    """Upsert every item and delete any row whose key is not in the seed set.
    Returns (upserts, deletes)."""
    expected = {item[pk_attr] for item in items}
    existing = _scan_primary_keys(table, pk_attr)
    stale = existing - expected
    carried = _scan_user_notes(table, pk_attr) if keep_user_notes else {}

    with table.batch_writer() as batch:
        for item in items:
            if item[pk_attr] in carried:
                item = {**item, "notes": (list(item.get("notes", [])) + carried[item[pk_attr]])[:NOTES_PER_PROFILE_MAX]}
            batch.put_item(Item=item)
    if carried:
        print(f"[{label}] kept notes added after seeding on {len(carried)} profile(s)")

    if stale:
        print(f"[{label}] deleting {len(stale)} row(s) that are not in the seed")
        with table.batch_writer() as batch:
            for pk_value in stale:
                batch.delete_item(Key={pk_attr: pk_value})
    print(f"[{label}] upserts={len(items)} deletes={len(stale)}")
    return len(items), len(stale)


# --------------------------------------------------------------------------
# AgentCore memory
# --------------------------------------------------------------------------
def _memory_id():
    """The long-term memory id, published by the agentcore stack. None when
    that stack is not deployed yet (first deploy) or the parameter is gone."""
    name = os.environ.get("MEMORY_ID_PARAM")
    if not name:
        return None
    ssm = boto3.client("ssm")
    try:
        return ssm.get_parameter(Name=name)["Parameter"]["Value"]
    except ssm.exceptions.ParameterNotFound:
        return None


# Time allowed for clearing memory. The deploy-time call goes through a CDK
# wrapper function that gives up after 120 seconds and leaves the stack waiting,
# so the clear must finish well inside that. What is not cleared in time is
# cleared by the next reset (the log says how many actors were left).
MEMORY_BUDGET_SECONDS = float(os.environ.get("MEMORY_CLEAR_BUDGET_SECONDS", "80"))
MEMORY_WORKERS = 8


def clear_memory(client=None, memory_id=None, budget_seconds=None, workers=MEMORY_WORKERS):
    """Delete every long-term memory record (summaries, preferences, facts) of
    every actor. Actors are customer ids, so the seed customers would
    otherwise 'remember' conversations from earlier demos.

    Actors are processed in parallel and the whole clear stops starting new work
    after `budget_seconds`. Never raises: a failure here must not undo the data
    reset. Returns a dict with the counts for the log; `actors_left` is the number
    of actors not reached because the budget ran out.
    """
    result = {"deleted": 0, "failed": 0, "skipped": None}
    deadline = time.monotonic() + (MEMORY_BUDGET_SECONDS if budget_seconds is None else budget_seconds)
    try:
        memory_id = memory_id or _memory_id()
        if not memory_id:
            result["skipped"] = "memory not deployed"
            return result
        client = client or boto3.client("bedrock-agentcore")

        def pages(call, key, **kw):
            token = None
            while True:
                resp = call(**kw, **({"nextToken": token} if token else {}))
                for x in resp.get(key, []):
                    yield x
                token = resp.get("nextToken")
                if not token:
                    return

        def clear_actor(actor_id):
            """Delete one actor's records. Returns (deleted, failed, first_error), or None if out of time."""
            if time.monotonic() > deadline:
                return None
            namespaces = [f"/preferences/{actor_id}", f"/facts/{actor_id}"]
            for sess in pages(client.list_sessions, "sessionSummaries", memoryId=memory_id, actorId=actor_id):
                namespaces.append(f"/summaries/{actor_id}/{sess['sessionId']}")
            # The same record can be listed under more than one namespace; delete
            # each id once (a second delete of the same id would count as failed).
            record_ids, seen = [], set()
            for ns in namespaces:
                for rec in pages(client.list_memory_records, "memoryRecordSummaries", memoryId=memory_id, namespace=ns):
                    rid = rec["memoryRecordId"]
                    if rid not in seen:
                        seen.add(rid)
                        record_ids.append(rid)
            deleted = failed = 0
            first_error = None
            for i in range(0, len(record_ids), 100):
                resp = client.batch_delete_memory_records(
                    memoryId=memory_id, records=[{"memoryRecordId": r} for r in record_ids[i:i + 100]]
                )
                deleted += len(resp.get("successfulRecords", []))
                # "Not found" means another worker (or an earlier reset) already
                # removed the record, which is the outcome we want: not a failure.
                bad = [f for f in resp.get("failedRecords", []) if str(f.get("errorCode")) not in ("404", "ResourceNotFoundException")]
                failed += len(bad)
                if bad and first_error is None:
                    first_error = f"{bad[0].get('errorCode')}: {bad[0].get('errorMessage')}"
            return deleted, failed, first_error

        actors = [a["actorId"] for a in pages(client.list_actors, "actorSummaries", memoryId=memory_id)]
        left = 0
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            for outcome in pool.map(clear_actor, actors):
                if outcome is None:
                    left += 1
                    continue
                result["deleted"] += outcome[0]
                result["failed"] += outcome[1]
                if outcome[2] and "first_error" not in result:
                    result["first_error"] = outcome[2]
        if left:
            result["actors_left"] = left
            print(f"[memory] time budget reached; {left} of {len(actors)} actor(s) will be cleared by the next reset")
    except Exception as e:  # noqa: BLE001 - see docstring
        print(f"[memory] could not clear memory: {type(e).__name__}: {e}")
        result["skipped"] = f"error: {type(e).__name__}"
    print(f"[memory] {result}")
    return result


# --------------------------------------------------------------------------
# Handler
# --------------------------------------------------------------------------
def lambda_handler(event, context):
    """Reset the demo tables (and memory) to the seed."""
    event = event if isinstance(event, dict) else {}
    scheduled = event.get("mode") == "scheduled"
    try:
        today = date.fromisoformat(event["today"]) if event.get("today") else None
        profiles_table = boto3.resource("dynamodb").Table(os.environ["PROFILES_TABLE"])
        policies_table = boto3.resource("dynamodb").Table(os.environ["POLICIES_TABLE"])
        catalog_table = boto3.resource("dynamodb").Table(os.environ["CATALOG_TABLE"])

        profiles, policies, catalog, deltas = create_sample_data(today=today)
        print(f"[dates] shifted profiles by {deltas['profiles']} day(s), policies by {deltas['policies']} day(s)")

        profiles_up, profiles_del = _sync_table(
            profiles_table, profiles, PROFILES_PRIMARY_KEY, "profiles",
            keep_user_notes=bool(event.get("keep_notes")),
        )
        policies_up, policies_del = _sync_table(policies_table, policies, POLICIES_PRIMARY_KEY, "policies")
        catalog_up, catalog_del = _sync_table(catalog_table, catalog, CATALOG_PRIMARY_KEY, "catalog")

        memory = {"skipped": "skip_memory"} if event.get("skip_memory") else clear_memory()

        return {
            "statusCode": 200,
            "body": json.dumps({
                "message": "Demo data reset to the seed",
                "date_shift_days": deltas,
                "profiles": {"upserts": profiles_up, "deletes": profiles_del},
                "policies": {"upserts": policies_up, "deletes": policies_del},
                "catalog": {"upserts": catalog_up, "deletes": catalog_del},
                "memory": memory,
            }),
        }
    except Exception as e:  # noqa: BLE001
        print(f"Error populating mock data: {type(e).__name__}: {e}")
        if scheduled:
            # Let the failure show up in the Lambda Errors metric and alarm.
            raise
        return {"statusCode": 500, "body": json.dumps({"error": "Error encountered during data loading"})}
