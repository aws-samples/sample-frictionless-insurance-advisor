"""
Customer Profile Lambda Function
Handles read/write access to customer profiles with advisor-based filtering.

Supported operations:
  GET    /profile                   - Retrieve profiles (existing behavior)
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

# Fields that the agent is allowed to write
WRITABLE_FIELDS = {
    'name', 'email', 'phone', 'address',
    'date_of_birth', 'marital_status', 'dependents',
    'occupation', 'employment_status', 'annual_income',
    'gender', 'home_owner', 'smoking', 'medical_conditions',
    'financial_objective', 'time_horizon', 'risk_tolerance', 'liquidity_needs',
}

# Used by the application forms and the life-product rate tables, which only
# have male and female.
GENDERS = {'male', 'female'}

NOTE_TYPES = {'life_event', 'interaction'}
NOTE_TEXT_MAX = 1000
NOTES_PER_PROFILE_MAX = 100
_ISO_DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')
# note_id reaches us in the URL path; constrain it before use.
_NOTE_ID_RE = re.compile(r'^[A-Za-z0-9-]{1,64}$')


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
    try:
        response = boto3.client('cognito-idp').admin_get_user(
            UserPoolId=os.environ.get('USER_POOL_ID'),
            Username=username
        )
        for attr in response['UserAttributes']:
            if attr['Name'] == 'email':
                print("Resolved advisor_id from Cognito user email")
                return attr['Value']
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

    body = json.loads(event.get('body') or '{}')
    name = body.get('name')
    if not name:
        return create_response(400, {'message': "Field 'name' is required to create a profile"})
    bad = _bad_gender(body)
    if bad:
        return bad

    customer_id = str(uuid.uuid4())[:8]  # Short unique ID for demo purposes

    item = {
        'customer_id': customer_id,
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

    # Add any additional writable fields provided in the request
    for field in WRITABLE_FIELDS:
        if field in body and field != 'name':
            item[field] = body[field]

    table.put_item(Item=item)
    print(f"Created new prospect profile: customer_id={customer_id}, name={name}")

    return create_response(201, {'message': 'Profile created', 'customer_id': customer_id, 'profile': item})


def handle_update(event):
    """Update fields on an existing profile."""
    advisor_id = extract_advisor_id(event)
    if not advisor_id:
        return create_response(401, {'message': 'Unauthorized - no advisor ID found'})

    body = json.loads(event.get('body') or '{}')
    customer_id = body.get('customer_id')
    if not customer_id:
        return create_response(400, {'message': "Field 'customer_id' is required for update"})

    # Build update expression from writable fields present in the body
    bad = _bad_gender(body)
    if bad:
        return bad
    update_fields = {k: v for k, v in body.items() if k in WRITABLE_FIELDS and v is not None}
    if not update_fields:
        return create_response(400, {'message': 'No valid fields to update'})

    # Verify the profile belongs to this advisor
    existing = table.get_item(Key={'customer_id': customer_id})
    if 'Item' not in existing:
        return create_response(404, {'message': 'Profile not found'})
    if existing['Item'].get('advisor_id') != advisor_id:
        return create_response(403, {'message': 'Not authorized to update this profile'})

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
