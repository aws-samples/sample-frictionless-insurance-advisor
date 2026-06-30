"""
Custom resource handler for AgentCore OAuth2 Credential Provider.
Manages the lifecycle (Create/Update/Delete) of OAuth2 credential providers
via the bedrock-agentcore-control API.
"""
import json
import boto3
import logging
import urllib3

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def lambda_handler(event, context):
    """Custom resource handler for AgentCore OAuth2 Credential Provider"""
    try:
        logger.info(f"Event: RequestType={event.get('RequestType')}, "
                    f"LogicalResourceId={event.get('LogicalResourceId')}, "
                    f"StackId={event.get('StackId', 'N/A')}")

        request_type = event['RequestType']
        properties = event['ResourceProperties']

        provider_name = properties['ProviderName']
        client_id = properties['ClientId']
        client_secret = properties['ClientSecret']
        token_endpoint = properties['TokenEndpoint']
        user_pool_id = properties['UserPoolId']

        # Derive discovery URL from token endpoint and user pool ID
        # Token endpoint: https://domain.auth.region.amazoncognito.com/oauth2/token
        # Discovery URL: https://cognito-idp.region.amazonaws.com/user_pool_id/.well-known/openid-configuration
        region = token_endpoint.split('.auth.')[1].split('.amazoncognito.com')[0]
        discovery_url = f"https://cognito-idp.{region}.amazonaws.com/{user_pool_id}/.well-known/openid-configuration"

        client = boto3.client('bedrock-agentcore-control')

        if request_type in ('Create', 'Update'):
            # For updates, delete the existing provider first
            if request_type == 'Update':
                physical_resource_id = event['PhysicalResourceId']
                try:
                    old_name = physical_resource_id.split('/')[-1] if '/' in physical_resource_id else provider_name
                    client.delete_oauth2_credential_provider(name=old_name)
                    logger.info(f"Deleted existing provider: {old_name}")
                except Exception as e:
                    logger.warning(f"Failed to delete existing provider: {e}")

            response = client.create_oauth2_credential_provider(
                name=provider_name,
                credentialProviderVendor='CustomOauth2',
                oauth2ProviderConfigInput={
                    'customOauth2ProviderConfig': {
                        'oauthDiscovery': {
                            'discoveryUrl': discovery_url
                        },
                        'clientId': client_id,
                        'clientSecret': client_secret
                    }
                }
            )

            provider_arn = response['credentialProviderArn']
            secret_arn = response['clientSecretArn']['secretArn']
            logger.info(f"Created provider ARN: {provider_arn}, secret ARN: {secret_arn}")

            send_response(event, context, 'SUCCESS', {
                'ProviderArn': provider_arn,
                'SecretArn': secret_arn
            }, provider_arn)

        elif request_type == 'Delete':
            physical_resource_id = event['PhysicalResourceId']
            try:
                name = physical_resource_id.split('/')[-1] if '/' in physical_resource_id else provider_name
                client.delete_oauth2_credential_provider(name=name)
                logger.info(f"Deleted OAuth2 credential provider: {name}")
            except Exception as e:
                logger.error(f"Failed to delete provider: {e}")

            send_response(event, context, 'SUCCESS', {}, physical_resource_id)

    except Exception as e:
        logger.error(f"Error: {str(e)}")
        send_response(event, context, 'FAILED', {},
                     event.get('PhysicalResourceId', 'failed-to-create'), str(e))


def send_response(event, context, response_status, response_data=None,
                 physical_resource_id=None, reason=None):
    """Send response to CloudFormation"""
    response_data = response_data or {}

    response_body = {
        'Status': response_status,
        'Reason': reason or f'See CloudWatch Log Stream: {context.log_stream_name}',
        'PhysicalResourceId': physical_resource_id or context.log_stream_name,
        'StackId': event['StackId'],
        'RequestId': event['RequestId'],
        'LogicalResourceId': event['LogicalResourceId'],
        'Data': response_data
    }

    json_response_body = json.dumps(response_body)
    headers = {
        'content-type': '',
        'content-length': str(len(json_response_body))
    }

    http = urllib3.PoolManager()
    try:
        response = http.request('PUT', event['ResponseURL'],
                              body=json_response_body, headers=headers)
        logger.info(f"Status code: {response.status}")
    except Exception as e:
        logger.error(f"Failed to send response: {e}")
