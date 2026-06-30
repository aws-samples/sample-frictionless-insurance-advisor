#!/usr/bin/env python3
"""
CDK Construct for AgentCore OAuth2 Credential Provider
"""
import os
from aws_cdk import (
    CustomResource,
    Duration,
    aws_lambda as lambda_,
    aws_iam as iam,
    aws_logs as logs,
    RemovalPolicy,
)
from constructs import Construct
from typing import List


class AgentCoreOAuth2ProviderProps:
    """Properties for AgentCore OAuth2 Credential Provider"""

    def __init__(
        self,
        provider_name: str,
        client_id: str,
        client_secret: str,
        token_endpoint: str,
        user_pool_id: str,
        scopes: List[str] = None
    ):
        self.provider_name = provider_name
        self.client_id = client_id
        self.client_secret = client_secret
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
                "AgentCoreOAuth2Policy": iam.PolicyDocument(
                    statements=[
                        iam.PolicyStatement(
                            effect=iam.Effect.ALLOW,
                            actions=[
                                "bedrock-agentcore-control:CreateOAuth2CredentialProvider",
                                "bedrock-agentcore-control:DeleteOAuth2CredentialProvider",
                                "bedrock-agentcore-control:GetOAuth2CredentialProvider",
                                "bedrock-agentcore-control:ListOAuth2CredentialProviders",
                                "bedrock-agentcore:CreateOauth2CredentialProvider",
                                "bedrock-agentcore:DeleteOauth2CredentialProvider",
                                "bedrock-agentcore:GetOauth2CredentialProvider",
                                "bedrock-agentcore:ListOauth2CredentialProviders",
                                "bedrock-agentcore:CreateTokenVault",
                                "bedrock-agentcore:DeleteTokenVault",
                                "bedrock-agentcore:GetTokenVault",
                                "bedrock-agentcore:ListTokenVaults",
                                "secretsmanager:CreateSecret",
                                "secretsmanager:DeleteSecret",
                                "secretsmanager:GetSecretValue",
                                "secretsmanager:PutSecretValue",
                                "secretsmanager:UpdateSecret"
                            ],
                            resources=["*"]
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
                "ClientSecret": props.client_secret,
                "TokenEndpoint": props.token_endpoint,
                "Scopes": props.scopes,
                "UserPoolId": props.user_pool_id
            },
            removal_policy=RemovalPolicy.DESTROY
        )

        # Expose attributes
        self.provider_arn = self.custom_resource.get_att_string("ProviderArn")
        self.secret_arn = self.custom_resource.get_att_string("SecretArn")
