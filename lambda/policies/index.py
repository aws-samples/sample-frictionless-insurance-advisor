"""
Insurance Policies Lambda Function

Supported operations:
  GET    - Read policies (Unicorn + third-party)
  POST   - Create a third-party policy
  PUT    - Update a third-party policy
  DELETE - Delete a third-party policy

Unicorn-issued policies are read-only — write operations refuse to touch any
row where ``third_party`` is missing or false. Real Unicorn policy issuance
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

# Initialize DynamoDB connection
dynamodb = boto3.resource('dynamodb')
table = dynamodb.Table(os.environ['POLICIES_TABLE'])

# Demo hygiene: agent-created third-party policies (from the upload/extract
# flow) are ephemeral. We stamp an `expires_at` epoch on create so DynamoDB
# TTL (configured on the table in tools_stack.py) auto-deletes them roughly a
# day later. Seed/mock rows never get this attribute, so they persist.
DEMO_POLICY_TTL_SECONDS = 24 * 60 * 60


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


def extract_advisor_id(event):
    """Resolve the advisor email from JWT claims (browser path) or query
    string (M2M path), matching the profile lambda's behavior."""
    advisor_id = None

    try:
        username = event['requestContext']['authorizer']['claims']['username']
        cognito_client = boto3.client('cognito-idp')
        user_pool_id = os.environ.get('USER_POOL_ID')

        response = cognito_client.admin_get_user(
            UserPoolId=user_pool_id,
            Username=username
        )
        for attr in response['UserAttributes']:
            if attr['Name'] == 'email':
                advisor_id = attr['Value']
                print("Resolved advisor_id from Cognito user email")
                break
    except (KeyError, TypeError):
        # Fallback for M2M tokens (no Cognito user). The agent runtime forwards
        # the user's email as the advisor_id query parameter.
        try:
            qs = event.get('queryStringParameters') or {}
            if qs.get('advisor_id'):
                advisor_id = qs['advisor_id']
                print("Resolved advisor_id from query parameters")
        except (KeyError, TypeError):
            pass
    except Exception as e:
        print(f"Error fetching user from Cognito: {e}")

    return advisor_id


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


def _parse_body(event):
    try:
        body = json.loads(event.get('body') or '{}')
    except json.JSONDecodeError:
        return None
    return _to_dynamodb_safe(body)


def _get_policy(policy_id):
    """Read a single policy row by partition key."""
    response = table.get_item(Key={'id': policy_id})
    return response.get('Item')


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


def handle_get(event, advisor_id):
    """Retrieve policies filtered by advisor_id and optional customer_id."""
    qs = event.get('queryStringParameters') or {}
    customer_id = qs.get('customer_id') or event.get('customer_id')

    key_condition = Key('advisor_id').eq(advisor_id)
    if customer_id:
        key_condition = key_condition & Key('customer_id').eq(customer_id)

    response = table.query(
        IndexName='advisor-id-index',
        KeyConditionExpression=key_condition
    )
    print(f"Query returned {len(response['Items'])} items")
    return create_response(200, response['Items'])


def handle_create(event, advisor_id):
    """Create a third-party policy.

    Required body fields: customer_id, type, insurer.
    Server-side enforces ``third_party=True``; the caller cannot create a
    Unicorn-issued policy through this endpoint.
    """
    body = _parse_body(event)
    if body is None:
        return create_response(400, {'message': 'Invalid JSON body'})

    customer_id = body.get('customer_id')
    policy_type = body.get('type')
    insurer = body.get('insurer')
    missing = [f for f, v in [('customer_id', customer_id),
                              ('type', policy_type),
                              ('insurer', insurer)] if not v]
    if missing:
        return create_response(400, {'message': f"Missing required fields: {', '.join(missing)}"})

    policy_id = body.get('id') or f"POL-3P-{uuid.uuid4().hex[:8].upper()}"

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
        # TTL: auto-expire this demo upload ~24h from now (epoch seconds).
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

    table.put_item(Item=item)
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

    policy_id = body.get('id')
    if not policy_id:
        return create_response(400, {'message': "Field 'id' is required for update"})

    existing = _get_policy(policy_id)
    if existing is None:
        return create_response(404, {'message': 'Policy not found'})
    if not existing.get('third_party'):
        return create_response(403, {
            'message': 'This is a Unicorn-issued policy and is read-only. '
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
            expr_values[f':{field}'] = body[field]
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
            'message': 'This is a Unicorn-issued policy and is read-only. '
                       'Only third-party policies can be deleted.'
        })
    if existing.get('advisor_id') != advisor_id:
        return create_response(403, {'message': 'Policy belongs to a different advisor'})

    table.delete_item(Key={'id': policy_id})
    print(f"Deleted third-party policy: id={policy_id}")
    return create_response(200, {'message': 'Third-party policy deleted', 'id': policy_id})
