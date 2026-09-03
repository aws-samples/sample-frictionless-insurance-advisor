"""
Form Schema Lambda Function

Returns the application-form schema for a given insurance product.

Unlike the other S3-backed knowledge tools (portfolio, company, competitive),
this one does a *targeted* GET rather than concatenating every object in the
bucket. The response is a machine-readable schema that the frontend renders
directly, so returning several schemas at once would be wasteful and
ambiguous.

Shared by both agents on purpose:
  - the voice agent fetches a schema when an application starts, so it knows
    which fields exist and can fill them from the conversation
  - the text agent uses it to answer "what does this application ask for?"
    without needing any form UI of its own
"""
import json
import os
import re

import boto3
from botocore.exceptions import ClientError

s3_client = boto3.client('s3')

# Product types map 1:1 onto <product_type>.json in the forms bucket. The
# allowlist keeps a caller-supplied value from reaching S3 as a raw key.
KNOWN_PRODUCT_TYPES = {
    'term_life',
    'whole_life',
    'universal_life',
    'variable_life',
}

# Defence in depth behind the allowlist: no separators, no traversal.
SAFE_KEY_RE = re.compile(r'^[a-z0-9_]+$')


def create_response(status_code, body):
    """Create standardized API response with CORS headers"""
    return {
        'statusCode': status_code,
        'headers': {
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Headers': 'Content-Type,Authorization',
            'Access-Control-Allow-Methods': 'GET,POST,OPTIONS'
        },
        'body': json.dumps(body)
    }


def _extract_product_type(event):
    """Pull product_type from an MCP tool call, query string, or body."""
    # AgentCore Gateway passes tool inputs as top-level keys on the event.
    if isinstance(event.get('product_type'), str):
        return event['product_type']

    params = event.get('queryStringParameters') or {}
    if isinstance(params.get('product_type'), str):
        return params['product_type']

    raw_body = event.get('body')
    if raw_body:
        try:
            parsed = json.loads(raw_body)
            if isinstance(parsed.get('product_type'), str):
                return parsed['product_type']
        except (ValueError, AttributeError):
            pass

    return None


def get_form_schema(product_type):
    """Read one schema object from the forms bucket."""
    bucket_name = os.environ.get('FORMS_BUCKET')
    if not bucket_name:
        raise ValueError("FORMS_BUCKET environment variable not set")

    key = f"{product_type}.json"
    response = s3_client.get_object(Bucket=bucket_name, Key=key)
    return json.loads(response['Body'].read().decode('utf-8'))


def handler(event, context):
    product_type = _extract_product_type(event)

    if not product_type:
        return create_response(400, {
            'message': "Field 'product_type' is required",
            'available_product_types': sorted(KNOWN_PRODUCT_TYPES),
        })

    product_type = product_type.strip().lower()

    # Reject anything not explicitly published, and tell the caller what it
    # can ask for — the agent recovers from this without a second round trip.
    if product_type not in KNOWN_PRODUCT_TYPES or not SAFE_KEY_RE.match(product_type):
        return create_response(404, {
            'message': f"No application form is published for '{product_type}'",
            'available_product_types': sorted(KNOWN_PRODUCT_TYPES),
        })

    try:
        schema = get_form_schema(product_type)
    except ClientError as e:
        # Allowlisted but absent from the bucket — a deployment problem, not
        # a caller problem.
        print(f"S3 error reading schema for {product_type}: {e.response['Error']['Code']}")
        return create_response(500, {'message': 'Unable to load the application form'})
    except ValueError as e:
        print(f"Configuration error: {e}")
        return create_response(500, {'message': 'Unable to load the application form'})

    return create_response(200, schema)
