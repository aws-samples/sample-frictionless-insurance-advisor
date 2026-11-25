"""
Customer Profile Lambda Function
Handles read/write access to customer profiles with advisor-based filtering.

Supported operations:
  GET    /profile                   - Retrieve profiles
  POST   /profile                   - Create a new prospect profile
  PUT    /profile                   - Update fields on an existing profile
  POST   /profile/notes             - Add a note (advisor UI or AI assistant)
  DELETE /profile/notes/{note_id}   - Delete a note (advisor UI only)

Notes live in a `notes` list nested in the profile item. They are not in
WRITABLE_FIELDS, so `update_profile` can never overwrite or clear them; the
only write paths are the two notes routes above.
"""
import json
import re
import boto3
import os
import time
import uuid
from datetime import date, datetime, timezone
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

# Initialize DynamoDB connection
dynamodb = boto3.resource('dynamodb')
table = dynamodb.Table(os.environ['PROFILES_TABLE'])

# Demo hygiene: prospect profiles created through this Lambda are ephemeral.
# We stamp an `expires_at` epoch-seconds attribute so DynamoDB TTL (configured
# on the table in tools_stack.py) auto-deletes them ~2h later. Seed/mock
# customers loaded elsewhere do NOT set this attribute, so they persist.
PROSPECT_TTL_SECONDS = 2 * 60 * 60
# How many fresh ids a create tries when the generated one is already taken.
NEW_CUSTOMER_ID_ATTEMPTS = 5

# Fields that the agent is allowed to write
WRITABLE_FIELDS = {
    # `name` and `address` are kept for display and for every form. They are DERIVED from the
    # parts below whenever a part is written (see `normalise_person`), so they never drift.
    'name', 'first_name', 'middle_name', 'last_name',
    'email', 'phone',
    'address', 'address_line1', 'city', 'state', 'zip',
    'date_of_birth', 'marital_status', 'dependents',
    'occupation', 'employment_status', 'annual_income',
    'gender', 'home_owner', 'medical_conditions',
    'citizenship', 'tobacco_status', 'net_worth_band',
    'financial_objective', 'time_horizon', 'risk_tolerance', 'liquidity_needs',
}
# Used by the application forms and the life-product rate tables, which only
# have male and female.
GENDERS = {'male', 'female'}
# Tobacco use. "former" means quit more than 12 months ago, which is what the rate files
# mean by a non-smoker, so pricing treats only "current" as a smoker.
TOBACCO_STATUSES = ('never', 'former', 'current')
CITIZENSHIPS = ('us_citizen', 'permanent_resident', 'other')
MARITAL_STATUSES = ('Single', 'Married', 'Divorced', 'Widowed')
NET_WORTH_BANDS = ('under_100k', '100k_250k', '250k_500k', '500k_1m', '1m_2_5m', 'over_2_5m')
_ENUMS = {
    'tobacco_status': TOBACCO_STATUSES,
    'citizenship': CITIZENSHIPS,
    'net_worth_band': NET_WORTH_BANDS,
}
_ZIP_RE = re.compile(r'^\d{5}(-\d{4})?$')
_STATE_RE = re.compile(r'^[A-Za-z]{2}$')
# "street, city, ST 12345" (the format of the seeded addresses)
_ADDRESS_RE = re.compile(r'^\s*(?P<line1>[^,]+?)\s*,\s*(?P<city>[^,]+?)\s*,\s*(?P<state>[A-Za-z]{2})\s+(?P<zip>\d{5}(?:-\d{4})?)\s*$')
_ADDRESS_PARTS = ('address_line1', 'city', 'state', 'zip')
NOTE_TYPES = {'life_event', 'interaction'}
NOTE_TEXT_MAX = 1000
NOTES_PER_PROFILE_MAX = 100
_ISO_DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')
# note_id reaches us in the URL path; constrain it before use.
_NOTE_ID_RE = re.compile(r'^[A-Za-z0-9-]{1,64}$')


# The advisor's email comes from Cognito (access tokens do not carry it), one
# AdminGetUser call per request. Remember the answer per container for a short
# time. The key is the `username` claim API Gateway's authorizer verified, never
# anything the caller sent. 60 seconds matches the gateway interceptor's cache:
# a deleted or changed user takes effect within a minute. Failures are not cached.
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


def create_response(status_code, body):
    """Create standardized API response with CORS headers"""
    return {
        'statusCode': status_code,
        'headers': {
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Headers': 'Content-Type,Authorization',
            'Access-Control-Allow-Methods': 'GET,POST,PUT,DELETE,OPTIONS'
        },
        'body': json.dumps(body, default=str)
    }

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
        print("No signed-in user in the request claims")
        return None
    # Only an answer Cognito gave for this exact, authorizer-verified username
    # is reused, and only for a minute (see ADVISOR_CACHE_SECONDS). The token
    # itself is still checked by API Gateway on every request.
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

def handler(event, context):
    """Main Lambda handler for profile operations"""
    try:
        http_method = event.get('httpMethod', 'GET')
        print(f"Received event: httpMethod={http_method}, "
              f"path={event.get('path')}, "
              f"resource={event.get('resource')}, "
              f"queryParams={list(event.get('queryStringParameters', {}).keys()) if event.get('queryStringParameters') else 'None'}")

        resource = event.get('resource') or ''
        if resource == '/profile/notes' and http_method == 'POST':
            return handle_add_note(event)
        if resource == '/profile/notes/{note_id}' and http_method == 'DELETE':
            return handle_delete_note(event)
        if resource.startswith('/profile/notes'):
            return create_response(405, {'message': f'Method {http_method} not supported on {resource}'})

        if http_method == 'GET':
            return handle_get(event)
        elif http_method == 'POST':
            return handle_create(event)
        elif http_method == 'PUT':
            return handle_update(event)
        else:
            return create_response(405, {'message': f'Method {http_method} not supported'})

    except Exception as e:
        print(f"Error in profile handler: {str(e)}")
        return create_response(500, {'message': 'An internal error occurred while processing your request'})


def handle_get(event):
    """Retrieve profiles filtered by advisor_id and optional customer_id"""
    advisor_id = extract_advisor_id(event)

    if not advisor_id:
        print("No advisor ID found - returning 401")
        return create_response(401, {'message': 'Unauthorized - no advisor ID found'})

    # Get customer ID from query params or direct call
    customer_id = (
        event.get('queryStringParameters', {}).get('customer_id') if event.get('queryStringParameters')
        else event.get('customer_id')
    )

    if customer_id is not None and (not isinstance(customer_id, str) or len(customer_id) > MAX_ID_CHARS):
        return create_response(400, {'message': "Query parameter 'customer_id' is not valid"})
    # Query using GSI with advisor_id and optional customer_id
    print(f"Querying GSI with advisor_id: [REDACTED], customer_id: {customer_id}")
    key_condition = Key('advisor_id').eq(advisor_id)
    if customer_id:
        key_condition = key_condition & Key('customer_id').eq(customer_id)

    response = table.query(
        IndexName='advisor-id-index',
        KeyConditionExpression=key_condition
    )

    print(f"Query returned {len(response['Items'])} items")
    return create_response(200, response['Items'])


# Longest text accepted per field (200 for any not listed). API Gateway's request model
# (cdk/api_models.py) bounds the same fields a little wider.
FIELD_MAX_CHARS = {
    'name': 200, 'first_name': 100, 'middle_name': 100, 'last_name': 100, 'email': 254, 'phone': 40,
    'address': 300, 'address_line1': 200, 'city': 100, 'state': 40, 'zip': 20, 'date_of_birth': 40,
    'marital_status': 40, 'occupation': 100, 'employment_status': 50, 'gender': 40,
    'medical_conditions': 2000, 'citizenship': 40, 'tobacco_status': 40, 'net_worth_band': 40,
    'financial_objective': 500, 'time_horizon': 100, 'risk_tolerance': 50, 'liquidity_needs': 50,
}
DEFAULT_FIELD_MAX_CHARS = 200
NUMERIC_FIELDS = ('dependents', 'annual_income')
MAX_ID_CHARS = 64


def parse_body(event):
    """(body, error_response): the JSON object in the request body, or a 400 response."""
    try:
        body = json.loads(event.get('body') or '{}')
    except (json.JSONDecodeError, TypeError):
        return None, create_response(400, {'message': 'Body is not valid JSON'})
    if not isinstance(body, dict):
        return None, create_response(400, {'message': 'Body must be a JSON object'})
    return body, None


def validate_field_values(body):
    """An error message if a writable field holds the wrong kind of value or too much text, else None.

    Text fields take a string (a number is read as text for phone and zip), the numeric fields a
    number or numeric text, home_owner a boolean or text. Lists and objects are refused everywhere.
    """
    for field in WRITABLE_FIELDS:
        value = body.get(field)
        if value is None:
            continue
        if isinstance(value, (dict, list)):
            return f"Field '{field}' must be a single value"
        if field in NUMERIC_FIELDS:
            numeric = (isinstance(value, (int, float)) and not isinstance(value, bool)) or isinstance(value, str)
            if not numeric or (isinstance(value, str) and len(value) > 40):
                return f"Field '{field}' must be a number"
        elif field == 'home_owner':
            if not isinstance(value, (bool, str)) or (isinstance(value, str) and len(value) > 10):
                return "Field 'home_owner' must be true or false"
        elif isinstance(value, str):
            if len(value) > FIELD_MAX_CHARS.get(field, DEFAULT_FIELD_MAX_CHARS):
                return f"Field '{field}' is too long"
        elif field in ('phone', 'zip') and isinstance(value, (int, float)) and not isinstance(value, bool):
            continue
        else:
            return f"Field '{field}' must be text"
    return None


def validate_person(body):
    """An error message if a value is outside its allowed set, else None."""
    for field, allowed in _ENUMS.items():
        v = body.get(field)
        if v is not None and v not in allowed:
            return f"Field '{field}' must be one of: {', '.join(allowed)}"
    ms = body.get('marital_status')
    if ms is not None and str(ms).strip().title() not in MARITAL_STATUSES:
        return f"Field 'marital_status' must be one of: {', '.join(MARITAL_STATUSES)}"
    st = body.get('state')
    if st is not None and not _STATE_RE.match(str(st)):
        return "Field 'state' must be a two-letter state code"
    zp = body.get('zip')
    if zp is not None and not _ZIP_RE.match(str(zp)):
        return "Field 'zip' must be 5 digits (or ZIP+4)"
    return None


def _split_name(full):
    """Best effort: the last word is the family name. A single word is a first name."""
    tokens = str(full).split()
    if not tokens:
        return '', ''
    if len(tokens) == 1:
        return tokens[0], ''
    return ' '.join(tokens[:-1]), tokens[-1]


def normalise_person(fields, existing=None):
    """The fields to write, with the derived ones filled in.

    `fields` holds what the caller sent (already restricted to WRITABLE_FIELDS); `existing` is
    the stored profile on update. `name` is first + last, and `address` is "line1, city, ST zip",
    whenever one of their parts is written. If a caller sends only the combined `name` or
    `address` (voice onboarding does), the parts are derived from it, so both forms stay in step.
    """
    out = dict(fields)
    old = existing or {}

    if out.get('marital_status') is not None:
        out['marital_status'] = str(out['marital_status']).strip().title()
    if out.get('state') is not None:
        out['state'] = str(out['state']).upper()

    # --- name
    if 'first_name' in out or 'last_name' in out:
        first = out.get('first_name', old.get('first_name', ''))
        last = out.get('last_name', old.get('last_name', ''))
        out['name'] = ' '.join(p for p in (str(first).strip(), str(last).strip()) if p)
    elif 'name' in out:
        out['first_name'], out['last_name'] = _split_name(out['name'])

    # --- address
    if any(k in out for k in _ADDRESS_PARTS):
        parts = {k: str(out.get(k, old.get(k, '')) or '').strip() for k in _ADDRESS_PARTS}
        tail = ' '.join(p for p in (parts['state'], parts['zip']) if p)
        out['address'] = ', '.join(p for p in (parts['address_line1'], parts['city'], tail) if p)
    elif 'address' in out:
        m = _ADDRESS_RE.match(str(out['address']))
        if m:
            out['address_line1'], out['city'] = m.group('line1'), m.group('city')
            out['state'], out['zip'] = m.group('state').upper(), m.group('zip')
        else:  # free text we cannot split: keep it whole rather than leave stale parts behind
            out['address_line1'], out['city'], out['state'], out['zip'] = str(out['address']).strip(), '', '', ''
    return out


def _bad_gender(body):
    """An error response if `gender` is present but not male or female, else None."""
    if 'gender' in body and body['gender'] is not None and body['gender'] not in GENDERS:
        return create_response(400, {'message': "Field 'gender' must be 'male' or 'female'"})
    return None


def handle_create(event):
    """Create a new prospect profile. Requires at least 'name' in the body."""
    advisor_id = extract_advisor_id(event)
    if not advisor_id:
        return create_response(401, {'message': 'Unauthorized - no advisor ID found'})

    body, error = parse_body(event)
    if error:
        return error
    shape_problem = validate_field_values(body)
    if shape_problem:
        return create_response(400, {'message': shape_problem})
    name = body.get('name') or ' '.join(p for p in (body.get('first_name'), body.get('last_name')) if p)
    if not name:
        return create_response(400, {'message': "Field 'name' (or 'first_name' and 'last_name') is required to create a profile"})
    bad = _bad_gender(body)
    if bad:
        return bad
    problem = validate_person(body)
    if problem:
        return create_response(400, {'message': problem})

    item = {
        'name': name,
        'advisor_id': advisor_id,
        'status': 'Active',
        'join_date': date.today().isoformat(),
        # Data-freshness signal. Distinct from join_date
        # (enrollment): last_reviewed tracks when the advisor last confirmed
        # the profile is current, so the UI can nudge to re-verify stale data.
        'last_reviewed': date.today().isoformat(),
        # Ephemeral demo prospect — auto-expire ~2h from now (epoch seconds).
        # DynamoDB TTL on the table deletes the row once this time passes.
        'expires_at': int(time.time()) + PROSPECT_TTL_SECONDS,
    }

    # Add any additional writable fields provided in the request, then fill in the derived ones.
    sent = {f: body[f] for f in WRITABLE_FIELDS if f in body and body[f] is not None}
    sent.setdefault('name', name)
    item.update(normalise_person(sent))

    # Short ids (8 hex characters) are kept; a put that would replace an existing row is
    # refused by the condition and retried with a new id.
    customer_id = None
    for _ in range(NEW_CUSTOMER_ID_ATTEMPTS):
        candidate = str(uuid.uuid4())[:8]
        item['customer_id'] = candidate
        try:
            table.put_item(Item=item, ConditionExpression='attribute_not_exists(customer_id)')
        except ClientError as e:
            if e.response.get('Error', {}).get('Code') == 'ConditionalCheckFailedException':
                continue
            raise
        customer_id = candidate
        break
    if customer_id is None:
        return create_response(500, {'message': 'Could not allocate a customer id, please try again'})
    print(f"Created new prospect profile: customer_id={customer_id}")

    return create_response(201, {'message': 'Profile created', 'customer_id': customer_id, 'profile': item})


def handle_update(event):
    """Update fields on an existing profile."""
    advisor_id = extract_advisor_id(event)
    if not advisor_id:
        return create_response(401, {'message': 'Unauthorized - no advisor ID found'})

    body, error = parse_body(event)
    if error:
        return error
    customer_id = body.get('customer_id')
    if not customer_id:
        return create_response(400, {'message': "Field 'customer_id' is required for update"})
    if not isinstance(customer_id, str) or len(customer_id) > MAX_ID_CHARS:
        return create_response(400, {'message': "Field 'customer_id' is not valid"})
    shape_problem = validate_field_values(body)
    if shape_problem:
        return create_response(400, {'message': shape_problem})

    # Build update expression from writable fields present in the body
    bad = _bad_gender(body)
    if bad:
        return bad
    problem = validate_person(body)
    if problem:
        return create_response(400, {'message': problem})
    update_fields = {k: v for k, v in body.items() if k in WRITABLE_FIELDS and v is not None}
    if not update_fields:
        return create_response(400, {'message': 'No valid fields to update'})

    # Verify the profile belongs to this advisor
    existing = table.get_item(Key={'customer_id': customer_id})
    if 'Item' not in existing:
        return create_response(404, {'message': 'Profile not found'})
    if existing['Item'].get('advisor_id') != advisor_id:
        return create_response(403, {'message': 'Not authorized to update this profile'})

    # Derived values (name from the parts, address from the parts) use the stored profile.
    update_fields = normalise_person(update_fields, existing['Item'])

    # Build DynamoDB update expression
    expr_parts = []
    expr_names = {}
    expr_values = {}
    for i, (field, value) in enumerate(update_fields.items()):
        placeholder_name = f"#f{i}"
        placeholder_value = f":v{i}"
        expr_parts.append(f"{placeholder_name} = {placeholder_value}")
        expr_names[placeholder_name] = field
        expr_values[placeholder_value] = value

    # Any advisor edit counts as a review, so always bump last_reviewed to
    # today (data-freshness signal). System-managed — not part of
    # WRITABLE_FIELDS, so a client cannot spoof it.
    expr_parts.append("#last_reviewed = :last_reviewed")
    expr_names["#last_reviewed"] = "last_reviewed"
    expr_values[":last_reviewed"] = date.today().isoformat()

    update_expression = "SET " + ", ".join(expr_parts)

    table.update_item(
        Key={'customer_id': customer_id},
        UpdateExpression=update_expression,
        ExpressionAttributeNames=expr_names,
        ExpressionAttributeValues=expr_values,
    )

    print(f"Updated profile: customer_id={customer_id}, fields={list(update_fields.keys())}")
    return create_response(200, {'message': 'Profile updated', 'customer_id': customer_id, 'updated_fields': list(update_fields.keys())})


# --- Notes ------------------------------------------------------------------

def _is_advisor_sign_in(event):
    """True when the request carries a signed-in advisor's verified claims."""
    try:
        return bool(event['requestContext']['authorizer']['claims'].get('username'))
    except (KeyError, TypeError, AttributeError):
        return False


# The AgentCore Gateway's request interceptor adds this header to every call it
# makes to this API on an advisor's behalf, replacing any value the caller sent
# (see lambda/gateway_interceptor). It is a label, not a credential: the advisor
# is identified by the token alone. A person calling the API directly does not
# send it, and sending it only makes their own request count as the assistant's.
VIA_GATEWAY_HEADER = 'x-advisor-via-gateway'


def _via_assistant(event):
    """True when the call came through the AI assistant (the gateway)."""
    headers = event.get('headers') or {}
    return any(str(k).lower() == VIA_GATEWAY_HEADER and v for k, v in headers.items())


def _valid_iso_date(value):
    if not isinstance(value, str) or not _ISO_DATE_RE.match(value):
        return False
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return False
    return 1900 <= parsed.year <= 2100


def _owned_profile(customer_id, advisor_id):
    """Return (item, error_response). The profile must belong to the advisor."""
    existing = table.get_item(Key={'customer_id': customer_id})
    item = existing.get('Item')
    if not item:
        return None, create_response(404, {'message': 'Profile not found'})
    if item.get('advisor_id') != advisor_id:
        return None, create_response(403, {'message': 'Not authorized to change this profile'})
    return item, None


def handle_add_note(event):
    """Append one note to the profile's `notes` list.

    Called from the advisor UI and, through the AgentCore Gateway, by the AI
    assistant when the advisor asks it to record something. `source` and
    `created_by` are set here, never taken from the request.
    """
    advisor_id = extract_advisor_id(event)
    if not advisor_id:
        return create_response(401, {'message': 'Unauthorized - no advisor ID found'})

    try:
        body = json.loads(event.get('body') or '{}')
    except json.JSONDecodeError:
        return create_response(400, {'message': 'Body is not valid JSON'})
    if not isinstance(body, dict):
        return create_response(400, {'message': 'Body must be a JSON object'})

    customer_id = body.get('customer_id')
    if not isinstance(customer_id, str) or not customer_id.strip():
        return create_response(400, {'message': "Field 'customer_id' is required"})

    text = body.get('text')
    text = text.strip() if isinstance(text, str) else ''
    if not text:
        return create_response(400, {'message': "Field 'text' is required"})
    if len(text) > NOTE_TEXT_MAX:
        return create_response(400, {'message': f"Note text is limited to {NOTE_TEXT_MAX} characters"})

    note_type = body.get('type') or 'interaction'
    if note_type not in NOTE_TYPES:
        return create_response(400, {'message': f"Field 'type' must be one of {sorted(NOTE_TYPES)}"})

    event_date = body.get('event_date')
    if event_date in (None, ''):
        event_date = None
    elif not _valid_iso_date(event_date):
        return create_response(400, {'message': "Field 'event_date' must be a date in YYYY-MM-DD format"})

    note = {
        'note_id': str(uuid.uuid4()),
        'type': note_type,
        'text': text,
        'created_at': datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        'created_by': advisor_id,
        'source': 'assistant' if _via_assistant(event) else 'advisor_ui',
    }
    if event_date:
        note['event_date'] = event_date

    # One conditional write: the profile must exist, belong to this advisor,
    # and have room for another note. Checked atomically so two concurrent
    # adds can't exceed the cap or slip past the ownership check.
    try:
        table.update_item(
            Key={'customer_id': customer_id},
            UpdateExpression=(
                'SET #notes = list_append(if_not_exists(#notes, :empty), :new), '
                '#last_reviewed = :today'
            ),
            ConditionExpression=(
                'attribute_exists(customer_id) AND advisor_id = :advisor '
                'AND (attribute_not_exists(#notes) OR size(#notes) < :max)'
            ),
            ExpressionAttributeNames={'#notes': 'notes', '#last_reviewed': 'last_reviewed'},
            ExpressionAttributeValues={
                ':empty': [],
                ':new': [note],
                ':today': date.today().isoformat(),
                ':advisor': advisor_id,
                ':max': NOTES_PER_PROFILE_MAX,
            },
        )
    except ClientError as exc:
        if exc.response['Error']['Code'] != 'ConditionalCheckFailedException':
            raise
        # Work out which condition failed, for a useful error.
        _item, error = _owned_profile(customer_id, advisor_id)
        if error:
            return error
        return create_response(409, {
            'message': f'This profile already has {NOTES_PER_PROFILE_MAX} notes. '
                       'Delete an old note in the Notes panel before adding a new one.'
        })

    print(f"Added note to profile: customer_id={customer_id}, type={note_type}, source={note['source']}")
    return create_response(201, {'message': 'Note added', 'customer_id': customer_id, 'note': note})


def handle_delete_note(event):
    """Remove one note. The advisor in the app only, never the assistant.

    No gateway tool exists for this. The check here is a second layer: a call
    that came through the gateway is refused even if a tool were added later.
    """
    if not _is_advisor_sign_in(event) or _via_assistant(event):
        return create_response(403, {'message': 'Notes can only be deleted by the advisor in the app'})

    advisor_id = extract_advisor_id(event)
    if not advisor_id:
        return create_response(401, {'message': 'Unauthorized - no advisor ID found'})

    note_id = (event.get('pathParameters') or {}).get('note_id') or ''
    if not _NOTE_ID_RE.match(note_id):
        return create_response(400, {'message': 'Invalid note_id'})
    customer_id = (event.get('queryStringParameters') or {}).get('customer_id')
    if not customer_id:
        return create_response(400, {'message': "Query parameter 'customer_id' is required"})

    item, error = _owned_profile(customer_id, advisor_id)
    if error:
        return error
    notes = item.get('notes') or []
    index = next((i for i, n in enumerate(notes) if n.get('note_id') == note_id), None)
    if index is None:
        return create_response(404, {'message': 'Note not found'})

    # Guard against the list having shifted since the read (a concurrent add
    # or delete): only remove position `index` if it still holds this note.
    try:
        table.update_item(
            Key={'customer_id': customer_id},
            UpdateExpression=f'REMOVE #notes[{index}] SET #last_reviewed = :today',
            ConditionExpression=f'advisor_id = :advisor AND #notes[{index}].note_id = :nid',
            ExpressionAttributeNames={'#notes': 'notes', '#last_reviewed': 'last_reviewed'},
            ExpressionAttributeValues={
                ':advisor': advisor_id,
                ':nid': note_id,
                ':today': date.today().isoformat(),
            },
        )
    except ClientError as exc:
        if exc.response['Error']['Code'] != 'ConditionalCheckFailedException':
            raise
        return create_response(409, {'message': 'The notes changed while deleting. Refresh and try again.'})

    print(f"Deleted note from profile: customer_id={customer_id}")
    return create_response(200, {'message': 'Note deleted', 'customer_id': customer_id, 'note_id': note_id})
