"""
Customer Portfolio Lambda Function
Reads portfolio data from S3 bucket and returns concatenated content
"""
import json
import boto3
import os

s3_client = boto3.client('s3')

def create_response(status_code, body):
    """Create standardized API response with CORS headers"""
    return {
        'statusCode': status_code,
        'headers': {
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Headers': 'Content-Type,Authorization',
            'Access-Control-Allow-Methods': 'GET,OPTIONS'
        },
        'body': json.dumps(body)
    }

def get_portfolio_data():
    """Read all files from portfolio S3 bucket and concatenate content"""
    bucket_name = os.environ.get('PORTFOLIO_BUCKET')
    if not bucket_name:
        raise ValueError("PORTFOLIO_BUCKET environment variable not set")
    
    try:
        # List all objects in the bucket
        response = s3_client.list_objects_v2(Bucket=bucket_name)
        
        if 'Contents' not in response:
            return "No portfolio data available"
        
        concatenated_content = []
        
        # Read each file and concatenate content
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
    """Main Lambda handler for portfolio operations"""
    try:
        # Get customer ID from query params or direct call (optional for this implementation)
        customer_id = (
            event.get('queryStringParameters', {}).get('customer_id') if event.get('queryStringParameters') 
            else event.get('customer_id')
        )
        
        # Read portfolio data from S3
        portfolio_content = get_portfolio_data()
        
        result = {
            'customer_id': customer_id or 'all',
            'portfolio_data': portfolio_content
        }
        
        return create_response(200, result)
        
    except Exception as e:
        print(f"Error in portfolio handler: {str(e)}")
        return create_response(500, {'message': 'An internal error occurred while processing your request'})