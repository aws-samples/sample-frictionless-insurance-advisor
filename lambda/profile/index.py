"""
Customer Profile Lambda Function
Handles read/write access to customer profiles with advisor-based filtering.

Supported operations:
  GET  - Retrieve profiles (existing behavior)
  POST - Create a new prospect profile
  PUT  - Update fields on an existing profile
"""
import json
import boto3
import os
import uuid
from datetime import date
from boto3.dynamodb.conditions import Key

# Initialize DynamoDB connection
dynamodb = boto3.resource('dynamodb')
table = dynamodb.Table(os.environ['PROFILES_TABLE'])

# Fields that the agent is allowed to write
WRITABLE_FIELDS = {
    'name', 'email', 'phone', 'address',
    'date_of_birth', 'marital_status', 'dependents',
    'occupation', 'employment_status', 'annual_income',
    'home_owner', 'smoking', 'medical_conditions',
    'financial_objective', 'time_horizon', 'risk_tolerance', 'liquidity_needs',
}

def create_response(status_code, body):
    """Create standardized API response with CORS headers"""
    return {
        'statusCode': status_code,
        'headers': {
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Headers': 'Content-Type,Authorization',
            'Access-Control-Allow-Methods': 'GET,POST,PUT,OPTIONS'
        },
        'body': json.dumps(body, default=str)
    }

def extract_advisor_id(event):
    # Extract advisor ID - handles both user access tokens and M2M access tokens
    advisor_id = None

    # Method 1: Try to get from JWT claims (works for access tokens)
    try:
        # For user access tokens, get username and fetch email from Cognito
        username = event['requestContext']['authorizer']['claims']['username']
        print("Found username from JWT claims")
        
        cognito_client = boto3.client('cognito-idp')
        user_pool_id = os.environ.get('USER_POOL_ID')
        
        response = cognito_client.admin_get_user(
            UserPoolId=user_pool_id,
            Username=username
        )
        
        # Extract email from user attributes
        for attr in response['UserAttributes']:
            if attr['Name'] == 'email':
                advisor_id = attr['Value']
                print("Resolved advisor_id from Cognito user email")
                break
                
    except (KeyError, TypeError) as e:
        print(f"Could not get username from claims: {e}")
        # Method 2: Try to get from query parameters (works for M2M tokens)
        try:
            if event.get('queryStringParameters') and event['queryStringParameters'].get('advisor_id'):
                advisor_id = event['queryStringParameters']['advisor_id']
                print("Resolved advisor_id from query parameters")
        except (KeyError, TypeError):
            pass
    except Exception as e:
        print(f"Error fetching user from Cognito: {e}")
    
    return advisor_id

def handler(event, context):
    """Main Lambda handler for profile operations"""
    try:
        http_method = event.get('httpMethod', 'GET')
        print(f"Received event: httpMethod={http_method}, "
              f"path={event.get('path')}, "
              f"resource={event.get('resource')}, "
              f"queryParams={list(event.get('queryStringParameters', {}).keys()) if event.get('queryStringParameters') else 'None'}")

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


def handle_create(event):
    """Create a new prospect profile. Requires at least 'name' in the body."""
    advisor_id = extract_advisor_id(event)
    if not advisor_id:
        return create_response(401, {'message': 'Unauthorized - no advisor ID found'})

    body = json.loads(event.get('body') or '{}')
    name = body.get('name')
    if not name:
        return create_response(400, {'message': "Field 'name' is required to create a profile"})

    customer_id = str(uuid.uuid4())[:8]  # Short unique ID for demo purposes

    item = {
        'customer_id': customer_id,
        'name': name,
        'advisor_id': advisor_id,
        'status': 'Active',
        'join_date': date.today().isoformat(),
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

    update_expression = "SET " + ", ".join(expr_parts)

    table.update_item(
        Key={'customer_id': customer_id},
        UpdateExpression=update_expression,
        ExpressionAttributeNames=expr_names,
        ExpressionAttributeValues=expr_values,
    )

    print(f"Updated profile: customer_id={customer_id}, fields={list(update_fields.keys())}")
    return create_response(200, {'message': 'Profile updated', 'customer_id': customer_id, 'updated_fields': list(update_fields.keys())})