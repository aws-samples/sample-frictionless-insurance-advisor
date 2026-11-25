"""
Insurance Policies Lambda Function

Supported operations:
  GET    - Read policies (Awesome + third-party)
  POST   - Create a third-party policy
  PUT    - Update a third-party policy
  DELETE - Delete a third-party policy

Awesome-issued policies are read-only — write operations refuse to touch any
row where ``third_party`` is missing or false. Real Awesome policy issuance
goes through the formal sales channel; the agents are scoped to managing
external coverage the customer has bought elsewhere.
"""
import json
import os
import time
import traceback
import uuid
from datetime import date
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

# Initialize DynamoDB connection
dynamodb = boto3.resource('dynamodb')
table = dynamodb.Table(os.environ['POLICIES_TABLE'])
# Only read for `include_totals` (the customer's income, for the affordability share).
profiles_table = dynamodb.Table(os.environ['PROFILES_TABLE']) if os.environ.get('PROFILES_TABLE') else None

# Yearly premium maths lives here, in code, so the assistants never add or
# multiply premiums themselves. Same table as lambda/pricing (annualise).
PERIODS_PER_YEAR = {
    'monthly': 12, 'quarterly': 4, 'semi_annual': 2, 'semi-annual': 2, 'semiannual': 2,
    'biannual': 2, 'annual': 1, 'annually': 1, 'yearly': 1,
}
# Affordability guide. Keep in sync with s3-data/pricing/affordability.json
# (cap_percent_of_annual_income); lambda/pricing reads it from there.
AFFORDABILITY_CAP_PERCENT = 10

# Demo hygiene: agent-created third-party policies (from the upload/extract
# flow) are ephemeral. We stamp an `expires_at` epoch on create so DynamoDB
# TTL (configured on the table in tools_stack.py) auto-deletes them ~2h
# later. Seed/mock rows never get this attribute, so they persist.
DEMO_POLICY_TTL_SECONDS = 2 * 60 * 60


# Fields the agent is allowed to write on a third-party policy. Anything not
# in this set is silently ignored on create/update.
WRITABLE_TOP_LEVEL_FIELDS = {
    'customer_id', 'type', 'product_name', 'insurer',
    'premium_amount', 'premium_frequency', 'coverage_amount',
    'status', 'start_date', 'renewal_date',
}

# Optional type-specific detail blocks. Whichever one the caller provides is
# stored verbatim — these are loosely structured by design (see the existing
# mock data for shape examples).
DETAIL_BLOCK_FIELDS = {
    'vehicle', 'property', 'health_details',
    'disability_details', 'life_details',
}


# The names the whole app uses for a policy's type: the product lines, the recommender, the
# translations and the seed data all key on these. A policy written with a short or loosely
# worded type ("Critical Illness", "life", "Motor") would match none of them and show as an
# unknown type, so the write paths map those onto the canonical name. Anything that is not a
# known alias (including "Other") is kept as given.
CANONICAL_POLICY_TYPES = (
    'Life Insurance', 'Health Insurance', 'Critical Illness Insurance', 'Disability Insurance',
    'Auto Insurance', 'Home Insurance', 'Travel Insurance',
)
_POLICY_TYPE_ALIASES = {
    'life': 'Life Insurance', 'term life': 'Life Insurance', 'whole life': 'Life Insurance',
    'universal life': 'Life Insurance', 'variable life': 'Life Insurance',
    'health': 'Health Insurance', 'medical': 'Health Insurance',
    'critical illness': 'Critical Illness Insurance', 'critical': 'Critical Illness Insurance',
    'dread disease': 'Critical Illness Insurance',
    'disability': 'Disability Insurance', 'income protection': 'Disability Insurance',
    'auto': 'Auto Insurance', 'motor': 'Auto Insurance', 'car': 'Auto Insurance', 'vehicle': 'Auto Insurance',
    'home': 'Home Insurance', 'homeowners': 'Home Insurance', 'property': 'Home Insurance',
    'travel': 'Travel Insurance',
}
def normalise_policy_type(value):
    """Map a policy type onto the canonical name; unknown values are returned unchanged."""
    if not isinstance(value, str):
        return value
    text = ' '.join(value.strip().split())
    key = text.lower()
    for canonical in CANONICAL_POLICY_TYPES:
        if key == canonical.lower():
            return canonical
    if key.endswith(' insurance'):
        key = key[: -len(' insurance')].strip()
    return _POLICY_TYPE_ALIASES.get(key, text or value)
def create_response(status_code, body):
    """Create standardized API response with CORS headers."""
    return {
        'statusCode': status_code,
        'headers': {
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Headers': 'Content-Type,Authorization',
            'Access-Control-Allow-Methods': 'GET,POST,PUT,DELETE,OPTIONS'
        },
        'body': json.dumps(body, default=str)
    }


# The advisor's email comes from Cognito (access tokens do not carry it), one
# AdminGetUser call per request. Remember the answer per container for a short
# time. The key is the `username` claim API Gateway's authorizer verified, never
# anything the caller sent. 60 seconds matches the gateway interceptor's cache.
# Failures are not cached.
ADVISOR_CACHE_SECONDS = 60
ADVISOR_CACHE_MAX = 256
_advisor_cache = {}
_cognito = None


def _cognito_client():
    """One Cognito client per container instead of one per request."""
    global _cognito
    if _cognito is None:
        _cognito = boto3.client('cognito-idp')
    return _cognito


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


def extract_advisor_id(event):
    """The signed-in advisor's email, taken ONLY from the verified sign-in token.

    API Gateway's Cognito authorizer has already checked the token's signature,
    expiry and scope; `requestContext.authorizer.claims` is what it vouches for.
    Nothing the caller writes (query string, body, headers) is ever used to decide
    who the advisor is. A request with no such claim, such as one made with a
    machine token, gets no advisor and the handlers answer 401.
    """
    claims = ((event.get('requestContext') or {}).get('authorizer') or {}).get('claims') or {}
    username = claims.get('username')
    if not username:
        return None
    # Reuse an answer Cognito gave for this authorizer-verified username a
    # moment ago (see ADVISOR_CACHE_SECONDS); the token itself is still checked
    # by API Gateway on every request.
    now = time.time()
    hit = _advisor_cache.get(username) if isinstance(username, str) else None
    if hit and hit[1] > now:
        return hit[0]
    try:
        response = _cognito_client().admin_get_user(
            UserPoolId=os.environ.get('USER_POOL_ID'),
            Username=username
        )
        email = _verified_email(response)
        if email:
            print("Resolved advisor_id from Cognito user email")
            if isinstance(username, str):
                if len(_advisor_cache) >= ADVISOR_CACHE_MAX:
                    _advisor_cache.clear()
                _advisor_cache[username] = (email, now + ADVISOR_CACHE_SECONDS)
            return email
    except Exception as e:
        print(f"Error fetching user from Cognito: {e}")
    return None
def _to_dynamodb_safe(obj):
    """Recursively convert floats to Decimal so DynamoDB will accept the item.

    Booleans pass through (isinstance(True, int) is True so guard explicitly).
    Mock data is seeded with Decimal already; agent-supplied JSON arrives with
    floats and ints, hence this normalization on every write path.
    """
    if isinstance(obj, bool):
        return obj
    if isinstance(obj, float):
        return Decimal(str(obj))
    if isinstance(obj, int):
        # Keep ints as ints; DynamoDB accepts Number type for both.
        return obj
    if isinstance(obj, dict):
        return {k: _to_dynamodb_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_to_dynamodb_safe(v) for v in obj]
    return obj


# Longest text accepted per field. API Gateway's request model (cdk/api_models.py) bounds the same
# fields a little wider.
FIELD_MAX_CHARS = {
    'id': 64, 'customer_id': 64, 'type': 100, 'insurer': 200, 'product_name': 200,
    'premium_frequency': 40, 'status': 40, 'start_date': 40, 'renewal_date': 40,
}
NUMBER_FIELDS = ('premium_amount', 'coverage_amount')
MAX_DETAIL_KEYS = 30


def _parse_body(event):
    """The JSON object in the request body, with floats as Decimal, or None if it is not a JSON object."""
    try:
        body = json.loads(event.get('body') or '{}')
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(body, dict):
        return None
    return _to_dynamodb_safe(body)


def _field_problem(body):
    """An error message if a field holds the wrong kind of value or too much text, else None.

    Text fields take a string, the two amounts a finite number or numeric text, and the detail
    blocks an object of at most MAX_DETAIL_KEYS keys. Null means "not given".
    """
    for field, limit in FIELD_MAX_CHARS.items():
        value = body.get(field)
        if value is None:
            continue
        if not isinstance(value, str) or len(value) > limit:
            return f"Field '{field}' must be text of at most {limit} characters"
    for field in NUMBER_FIELDS:
        value = body.get(field)
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, Decimal, str)):
            return f"Field '{field}' must be a number"
        if isinstance(value, str) and len(value) > 40:
            return f"Field '{field}' must be a number"
        if isinstance(value, Decimal) and not value.is_finite():
            return f"Field '{field}' must be a number"
    for block in DETAIL_BLOCK_FIELDS:
        value = body.get(block)
        if value is None:
            continue
        if not isinstance(value, dict) or len(value) > MAX_DETAIL_KEYS:
            return f"Field '{block}' must be an object of at most {MAX_DETAIL_KEYS} entries"
    return None


def _get_policy(policy_id):
    """Read a single policy row by partition key."""
    response = table.get_item(Key={'id': policy_id})
    return response.get('Item')


def _customer_belongs_to(advisor_id, customer_id):
    """True when the customer's profile row names this advisor as its owner."""
    if profiles_table is None:
        return False
    item = profiles_table.get_item(Key={'customer_id': customer_id}).get('Item')
    return bool(item) and item.get('advisor_id') == advisor_id


def handler(event, context):
    """Main Lambda handler for policy operations."""
    try:
        method = event.get('httpMethod', 'GET')
        print(f"Received event: httpMethod={method}, "
              f"path={event.get('path')}, "
              f"resource={event.get('resource')}")

        advisor_id = extract_advisor_id(event)
        if not advisor_id:
            return create_response(401, {'message': 'Unauthorized - no advisor ID found'})

        if method == 'GET':
            return handle_get(event, advisor_id)
        if method == 'POST':
            return handle_create(event, advisor_id)
        if method == 'PUT':
            return handle_update(event, advisor_id)
        if method == 'DELETE':
            return handle_delete(event, advisor_id)
        return create_response(405, {'message': f'Method {method} not supported'})
    except Exception as e:
        print(f"Error in policy handler: {str(e)}")
        traceback.print_exc()
        return create_response(500, {'message': 'An internal error occurred while processing your request'})


def _annual_premium(policy):
    """Yearly premium of one stored policy as a number, or None if it cannot be worked out."""
    try:
        amount = float(policy.get('premium_amount'))
    except (TypeError, ValueError):
        return None
    freq = str(policy.get('premium_frequency') or '').strip().lower().replace(' ', '_')
    periods = PERIODS_PER_YEAR.get(freq)
    return None if periods is None else amount * periods


def _days_until(iso_date, today):
    """Whole days from today to an ISO date; negative when it has passed; None if unparseable."""
    try:
        return (date.fromisoformat(str(iso_date)[:10]) - today).days
    except ValueError:
        return None


def _decorate(policies, today):
    """Add derived fields to each policy (additive; stored fields are untouched):
    `annual_premium` and `days_until_renewal`."""
    out = []
    for pol in policies:
        pol = dict(pol)
        yearly = _annual_premium(pol)
        if yearly is not None:
            pol['annual_premium'] = round(yearly)
        days = _days_until(pol.get('renewal_date'), today)
        if days is not None:
            pol['days_until_renewal'] = days
        out.append(pol)
    return out


def _totals(policies, annual_income):
    """Portfolio figures for one customer, computed from the (already decorated) policies."""
    active = [p for p in policies if str(p.get('status', 'Active')).lower() == 'active']
    priced = [p for p in active if 'annual_premium' in p]
    own = sum(p['annual_premium'] for p in priced if not p.get('third_party'))
    other = sum(p['annual_premium'] for p in priced if p.get('third_party'))
    total = own + other
    out = {
        'active_policy_count': len(active),
        'awesome_policy_count': sum(1 for p in active if not p.get('third_party')),
        'third_party_policy_count': sum(1 for p in active if p.get('third_party')),
        'annual_premium_total': total,
        'awesome_annual_premium': own,
        'third_party_annual_premium': other,
        'policies_without_premium': len(active) - len(priced),
    }
    out['renewals'] = sorted(
        ({'policy': p.get('product_name') or p.get('type'), 'insurer': p.get('insurer') or 'Awesome',
          'renewal_date': p['renewal_date'], 'days_until_renewal': p['days_until_renewal']}
         for p in active if 'days_until_renewal' in p),
        key=lambda r: r['days_until_renewal'])
    try:
        income = float(annual_income) if annual_income not in (None, '') else None
    except (TypeError, ValueError):
        income = None
    if not income or income <= 0:
        out['affordability'] = {'rule': 'affordability_cap', 'status': 'income_unknown',
                                'message': 'Annual income is not on file. Ask the advisor for it; do not guess.'}
    else:
        cap = income * AFFORDABILITY_CAP_PERCENT / 100.0
        out['affordability'] = {
            'rule': 'affordability_cap',
            'cap_percent_of_income': AFFORDABILITY_CAP_PERCENT,
            'annual_income': round(income),
            'cap_amount': round(cap),
            'total_annual_premium': total,
            'total_percent_of_income': round(total / income * 100, 1),
            'headroom': round(cap - total),
            'status': 'within_cap' if total <= cap else 'over_cap',
        }
    return out


def handle_get(event, advisor_id):
    """Retrieve policies filtered by advisor_id and optional customer_id.

    Every policy carries two derived fields, `annual_premium` and
    `days_until_renewal`. With `include_totals=true` and a `customer_id` the
    answer is instead {"policies": [...], "totals": {...}} (used by the
    assistants for reviews); the advisor UI does not send the flag and keeps
    receiving a plain list.
    """
    qs = event.get('queryStringParameters') or {}
    customer_id = qs.get('customer_id') or event.get('customer_id')
    include_totals = str(qs.get('include_totals') or '').lower() in ('true', '1', 'yes')

    key_condition = Key('advisor_id').eq(advisor_id)
    if customer_id:
        key_condition = key_condition & Key('customer_id').eq(customer_id)

    response = table.query(
        IndexName='advisor-id-index',
        KeyConditionExpression=key_condition
    )
    print(f"Query returned {len(response['Items'])} items")
    policies = _decorate(response['Items'], date.today())
    if not (include_totals and customer_id):
        return create_response(200, policies)

    income = None
    if profiles_table is not None:
        item = profiles_table.get_item(Key={'customer_id': customer_id}).get('Item')
        # Only the owning advisor's profile is used.
        if item and item.get('advisor_id') == advisor_id:
            income = item.get('annual_income')
    return create_response(200, {'policies': policies, 'totals': _totals(policies, income)})


def handle_create(event, advisor_id):
    """Create a third-party policy.

    Required body fields: customer_id, type, insurer.
    The id is always generated here; an ``id`` in the body is ignored, and the
    write fails if a row with that id exists, so a create can never replace
    another row. The customer must belong to the calling advisor.
    Server-side enforces ``third_party=True``; the caller cannot create a
    Awesome-issued policy through this endpoint.
    """
    body = _parse_body(event)
    if body is None:
        return create_response(400, {'message': 'Invalid JSON body'})
    problem = _field_problem(body)
    if problem:
        return create_response(400, {'message': problem})

    customer_id = body.get('customer_id')
    policy_type = normalise_policy_type(body.get('type'))
    insurer = body.get('insurer')
    missing = [f for f, v in [('customer_id', customer_id),
                              ('type', policy_type),
                              ('insurer', insurer)] if not v]
    if missing:
        return create_response(400, {'message': f"Missing required fields: {', '.join(missing)}"})

    if not isinstance(customer_id, str):
        return create_response(400, {'message': 'customer_id is not valid'})
    if not _customer_belongs_to(advisor_id, customer_id):
        return create_response(404, {'message': 'Customer not found for this advisor'})

    policy_id = f"POL-3P-{uuid.uuid4().hex.upper()}"

    item = {
        'id': policy_id,
        'customer_id': customer_id,
        'advisor_id': advisor_id,
        'third_party': True,
        'insurer': insurer,
        'type': policy_type,
        'status': body.get('status', 'Active'),
        'start_date': body.get('start_date', date.today().isoformat()),
        'last_updated': date.today().isoformat(),
        # TTL: auto-expire this demo upload ~2h from now (epoch seconds).
        # DynamoDB TTL on the table deletes the row once this time passes.
        'expires_at': int(time.time()) + DEMO_POLICY_TTL_SECONDS,
    }
    # Copy in any remaining writable fields the caller provided.
    for field in WRITABLE_TOP_LEVEL_FIELDS:
        if field in body and field not in item:
            item[field] = body[field]
    # Copy in whichever detail block the caller provided, if any.
    for block in DETAIL_BLOCK_FIELDS:
        if isinstance(body.get(block), dict):
            item[block] = body[block]

    try:
        table.put_item(Item=item, ConditionExpression='attribute_not_exists(id)')
    except ClientError as e:
        if e.response.get('Error', {}).get('Code') == 'ConditionalCheckFailedException':
            return create_response(409, {'message': 'Policy id already exists'})
        raise
    print(f"Created third-party policy: id={policy_id}, customer={customer_id}, insurer={insurer}")
    return create_response(201, {
        'message': 'Third-party policy created',
        'id': policy_id,
        'policy': item,
    })


def handle_update(event, advisor_id):
    """Update fields on an existing third-party policy.

    Refuses to touch a policy that isn't third-party or that belongs to a
    different advisor.
    """
    body = _parse_body(event)
    if body is None:
        return create_response(400, {'message': 'Invalid JSON body'})
    problem = _field_problem(body)
    if problem:
        return create_response(400, {'message': problem})

    policy_id = body.get('id')
    if not policy_id:
        return create_response(400, {'message': "Field 'id' is required for update"})

    existing = _get_policy(policy_id)
    if existing is None:
        return create_response(404, {'message': 'Policy not found'})
    if not existing.get('third_party'):
        return create_response(403, {
            'message': 'This is an Awesome-issued policy and is read-only. '
                       'Only third-party policies can be updated.'
        })
    if existing.get('advisor_id') != advisor_id:
        return create_response(403, {'message': 'Policy belongs to a different advisor'})

    expr_names = {}
    expr_values = {}
    set_clauses = []

    for field in WRITABLE_TOP_LEVEL_FIELDS:
        if field in body:
            expr_names[f'#{field}'] = field
            expr_values[f':{field}'] = normalise_policy_type(body[field]) if field == 'type' else body[field]
            set_clauses.append(f'#{field} = :{field}')
    for block in DETAIL_BLOCK_FIELDS:
        if isinstance(body.get(block), dict):
            expr_names[f'#{block}'] = block
            expr_values[f':{block}'] = body[block]
            set_clauses.append(f'#{block} = :{block}')

    if not set_clauses:
        return create_response(400, {'message': 'No valid fields to update'})

    # Always bump last_updated.
    expr_names['#last_updated'] = 'last_updated'
    expr_values[':last_updated'] = date.today().isoformat()
    set_clauses.append('#last_updated = :last_updated')

    table.update_item(
        Key={'id': policy_id},
        UpdateExpression='SET ' + ', '.join(set_clauses),
        ExpressionAttributeNames=expr_names,
        ExpressionAttributeValues=expr_values,
    )
    updated = list(set(WRITABLE_TOP_LEVEL_FIELDS | DETAIL_BLOCK_FIELDS) & set(body.keys()))
    print(f"Updated third-party policy: id={policy_id}, fields={updated}")
    return create_response(200, {
        'message': 'Third-party policy updated',
        'id': policy_id,
        'updated_fields': updated,
    })


def handle_delete(event, advisor_id):
    """Delete an existing third-party policy.

    Same guards as update: must be third-party, must belong to this advisor.
    """
    qs = event.get('queryStringParameters') or {}
    body = _parse_body(event) or {}
    policy_id = qs.get('id') or body.get('id')
    if not policy_id:
        return create_response(400, {'message': "Field 'id' is required for delete"})

    existing = _get_policy(policy_id)
    if existing is None:
        return create_response(404, {'message': 'Policy not found'})
    if not existing.get('third_party'):
        return create_response(403, {
            'message': 'This is an Awesome-issued policy and is read-only. '
                       'Only third-party policies can be deleted.'
        })
    if existing.get('advisor_id') != advisor_id:
        return create_response(403, {'message': 'Policy belongs to a different advisor'})

    table.delete_item(Key={'id': policy_id})
    print(f"Deleted third-party policy: id={policy_id}")
    return create_response(200, {'message': 'Third-party policy deleted', 'id': policy_id})
