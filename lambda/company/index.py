"""
Company information Lambda — returns Unicorn Insurance company details
concatenated from S3 markdown files
"""
import json
import boto3
import os

s3_client = boto3.client('s3')

def create_response(status_code, body):
    """Create standardized API response (no CORS — invoked via AgentCore Gateway)"""
    return {
        'statusCode': status_code,
        'body': json.dumps(body)
    }

def get_company_data():
    """Read all files from company S3 bucket and concatenate content"""
    bucket_name = os.environ.get('COMPANY_BUCKET')
    if not bucket_name:
        raise ValueError("COMPANY_BUCKET environment variable not set")

    try:
        response = s3_client.list_objects_v2(Bucket=bucket_name)

        if 'Contents' not in response:
            return "No company data available"

        concatenated_content = []

        for obj in response['Contents']:
            key = obj['Key']
            file_response = s3_client.get_object(Bucket=bucket_name, Key=key)
            raw = file_response['Body'].read()
            try:
                content = raw.decode('utf-8')
            except UnicodeDecodeError:
                # Tolerate cp1252-only bytes (em dashes, smart quotes) that
                # sometimes sneak in via Word/.docx authoring. cp1252 is a
                # strict superset for those byte values; final fallback
                # replaces bad bytes so we never 502 over a single char.
                try:
                    content = raw.decode('cp1252')
                except UnicodeDecodeError:
                    content = raw.decode('utf-8', errors='replace')

            concatenated_content.append(f"=== {key} ===\n{content}\n")

        return "\n".join(concatenated_content)

    except Exception as e:
        print(f"Error reading from S3: {str(e)}")
        raise

def handler(event, context):
    """Main Lambda handler for company operations"""
    try:
        # Log query param keys only (not values) for debugging
        query_params = event.get('queryStringParameters') or {}
        if query_params:
            print(f"Received query param keys: {list(query_params.keys())}")

        company_content = get_company_data()

        result = {
            'company_data': company_content
        }

        return create_response(200, result)

    except Exception as e:
        print(f"Error in company handler: {str(e)}")
        return create_response(500, {'message': 'An internal error occurred while processing your request'})
