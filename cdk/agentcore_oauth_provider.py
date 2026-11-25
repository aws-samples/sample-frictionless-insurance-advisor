#!/usr/bin/env python3
"""
CDK Construct for AgentCore OAuth2 Credential Provider
"""
import os
from aws_cdk import (
    CustomResource,
    Duration,
    Stack,
    aws_lambda as lambda_,
    aws_iam as iam,
    aws_logs as logs,
    RemovalPolicy,
)
from constructs import Construct
from typing import List


class AgentCoreOAuth2ProviderProps:
    """Properties for AgentCore OAuth2 Credential Provider.

    There is deliberately no client secret: the provider's Lambda reads it from
    Cognito (DescribeUserPoolClient on this one user pool) while it runs, so the
    secret is never a template value, an output, a custom-resource property or
    a stack event.
    """

    def __init__(
        self,
        provider_name: str,
        client_id: str,
        token_endpoint: str,
        user_pool_id: str,
        scopes: List[str] = None
    ):
        self.provider_name = provider_name
        self.client_id = client_id
        self.token_endpoint = token_endpoint
        self.user_pool_id = user_pool_id
        self.scopes = scopes or []


class AgentCoreOAuth2Provider(Construct):
    """CDK Construct for AgentCore OAuth2 Credential Provider"""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        props: AgentCoreOAuth2ProviderProps
    ):
        super().__init__(scope, construct_id)
        stack = Stack.of(self)
        user_pool_arn = f"arn:aws:cognito-idp:{stack.region}:{stack.account}:userpool/{props.user_pool_id}"
        token_vault_arn = f"arn:aws:bedrock-agentcore:{stack.region}:{stack.account}:token-vault/default"
        provider_resource_arn = f"{token_vault_arn}/oauth2credentialprovider/{props.provider_name}"
        # AgentCore Identity keeps the client secret of a provider in Secrets Manager under
        # the provider's name, followed by a generated suffix.
        identity_secrets_arn = (
            f"arn:aws:secretsmanager:{stack.region}:{stack.account}:"
            f"secret:bedrock-agentcore-identity!default/oauth2/{props.provider_name}*"
        )

        # Create log group for the Lambda function
        oauth_provider_log_group = logs.LogGroup(
            self, "OAuth2ProviderLogGroup",
            retention=logs.RetentionDays.ONE_WEEK,
            removal_policy=RemovalPolicy.DESTROY
        )

        # Create IAM role for the Lambda function
        lambda_role = iam.Role(
            self, "OAuth2ProviderLambdaRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            inline_policies={
                "LambdaLoggingPolicy": iam.PolicyDocument(
                    statements=[
                        iam.PolicyStatement(
                            effect=iam.Effect.ALLOW,
                            actions=[
                                "logs:CreateLogStream",
                                "logs:PutLogEvents"
                            ],
                            resources=[
                                oauth_provider_log_group.log_group_arn,
                                f"{oauth_provider_log_group.log_group_arn}:*"
                            ]
                        )
                    ]
                ),
                "DescribeAppClientPolicy": iam.PolicyDocument(
                    statements=[
                        iam.PolicyStatement(
                            effect=iam.Effect.ALLOW,
                            actions=["cognito-idp:DescribeUserPoolClient"],
                            resources=[user_pool_arn]
                        )
                    ]
                ),
                "AgentCoreOAuth2Policy": iam.PolicyDocument(
                    statements=[
                        iam.PolicyStatement(
                            effect=iam.Effect.ALLOW,
                            actions=[
                                "bedrock-agentcore:CreateOauth2CredentialProvider",
                                "bedrock-agentcore:CreateTokenVault",
                                "bedrock-agentcore:DeleteOauth2CredentialProvider",
                                "bedrock-agentcore:GetOauth2CredentialProvider",
                                "bedrock-agentcore:GetTokenVault",
                            ],
                            resources=[token_vault_arn, provider_resource_arn]
                        ),
                        iam.PolicyStatement(
                            effect=iam.Effect.ALLOW,
                            actions=[
                                "secretsmanager:CreateSecret",
                                "secretsmanager:DeleteSecret",
                                "secretsmanager:GetSecretValue",
                                "secretsmanager:PutSecretValue",
                                "secretsmanager:UpdateSecret"
                            ],
                            resources=[identity_secrets_arn]
                        )
                    ]
                )
            }
        )

        # Create Lambda function for custom resource
        lambda_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "lambda", "oauth_provider")
        oauth_provider_function = lambda_.Function(
            self, "OAuth2ProviderFunction",
            runtime=lambda_.Runtime.PYTHON_3_13,
            handler="index.lambda_handler",
            code=lambda_.Code.from_asset(lambda_path),
            role=lambda_role,
            timeout=Duration.minutes(5),
            log_group=oauth_provider_log_group
        )

        # Create custom resource
        self.custom_resource = CustomResource(
            self, "OAuth2ProviderCustomResource",
            service_token=oauth_provider_function.function_arn,
            properties={
                "ProviderName": props.provider_name,
                "ClientId": props.client_id,
                "TokenEndpoint": props.token_endpoint,
                "Scopes": props.scopes,
                "UserPoolId": props.user_pool_id
            },
            removal_policy=RemovalPolicy.DESTROY
        )

        # Expose attributes
        self.provider_name = props.provider_name
        self.lambda_role = lambda_role
        self.function = oauth_provider_function
        self.provider_arn = self.custom_resource.get_att_string("ProviderArn")
        self.secret_arn = self.custom_resource.get_att_string("SecretArn")
