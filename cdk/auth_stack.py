from aws_cdk import (
    Stack,
    aws_cognito as cognito,
    aws_ssm as ssm,
    RemovalPolicy,
    CfnOutput,
)
from constructs import Construct
import hashlib
import os


class AuthStack(Stack):
    """Authentication stack with simplified Cognito user pools for different authentication patterns"""

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Generate unique suffix from account ID hash (first 8 chars of MD5)
        # Use environment variable or STS to get actual account ID at synth time
        account_id = os.environ.get('CDK_DEFAULT_ACCOUNT') or self.account
        unique_suffix = hashlib.md5(account_id.encode(), usedforsecurity=False).hexdigest()[:8]

        # =================================================================
        # USER POOL: External access (React SPA + AgentCore Gateway)
        # =================================================================
        self.user_pool = cognito.UserPool(
            self, "UserPool",
            user_pool_name="insurance-advisor-user",
            self_sign_up_enabled=True,  # React app uses Amplify Authenticator self-signup
            sign_in_aliases=cognito.SignInAliases(email=True),
            auto_verify=cognito.AutoVerifiedAttrs(email=True),  # Auto-verify email
            removal_policy=RemovalPolicy.DESTROY,
            password_policy=cognito.PasswordPolicy(
                min_length=12,  # Stronger for users
                require_lowercase=True,
                require_uppercase=True,
                require_digits=True,
                require_symbols=True
            ),
            mfa=cognito.Mfa.OFF,  # Disable MFA for easier demo testing
        )
        
        # User Pool Domain for OAuth endpoints
        self.user_pool_domain = cognito.UserPoolDomain(
            self, "UserPoolDomain",
            user_pool=self.user_pool,
            cognito_domain=cognito.CognitoDomainOptions(
                domain_prefix=f"insadv-users-{unique_suffix}"
            )
        )

        # Resource Server for User Pool to provide custom OAuth scopes for API Gateway access
        api_access_scope = cognito.ResourceServerScope(
            scope_name="api.access",
            scope_description="Access to Insurance Advisor API Gateway"
        )
        
        self.user_pool_resource_server = cognito.UserPoolResourceServer(
            self, "UserPoolResourceServer",
            user_pool=self.user_pool,
            identifier="insurance-advisor-api",
            user_pool_resource_server_name="Insurance Advisor API Resource Server",
            scopes=[api_access_scope]
        )

        # App Client for user-based OAuth (React SPA + AgentCore runtime JWT
        # authorization).
        self.app_client = cognito.UserPoolClient(
            self, "AppClient",
            user_pool=self.user_pool,
            user_pool_client_name="AppClient",
            generate_secret=False,  # No secret needed for user flows
            auth_flows=cognito.AuthFlow(
                user_password=True,
                user_srp=True,
                admin_user_password=True  # Enabled for the signup Lambda's admin_initiate_auth path
            ),
            o_auth=cognito.OAuthSettings(
                flows=cognito.OAuthFlows(
                    authorization_code_grant=True
                ),
                scopes=[cognito.OAuthScope.OPENID, cognito.OAuthScope.EMAIL, cognito.OAuthScope.PROFILE]
            )
        )

        # Gateway Client for AgentCore Gateway OAuth (client credentials flow for API Gateway access)
        self.gateway_client = cognito.UserPoolClient(
            self, "GatewayClient",
            user_pool=self.user_pool,
            user_pool_client_name="GatewayClient",
            generate_secret=True,  # Secret required for client credentials flow
            # No auth_flows needed for client credentials
            o_auth=cognito.OAuthSettings(
                flows=cognito.OAuthFlows(
                    client_credentials=True  # Only client credentials for gateway-to-api auth
                ),
                # Use custom scope from resource server for API Gateway access
                scopes=[cognito.OAuthScope.resource_server(self.user_pool_resource_server, api_access_scope)]
            )
        )

        # =================================================================
        # GATEWAY POOL: Internal access (AgentCore Runtime → AgentCore Gateway)
        # =================================================================
        self.gateway_pool = cognito.UserPool(
            self, "GatewayPool",
            user_pool_name="insurance-advisor-gateway",
            self_sign_up_enabled=False,  # No self-signup for services
            sign_in_aliases=cognito.SignInAliases(email=True),
            removal_policy=RemovalPolicy.DESTROY,
            password_policy=cognito.PasswordPolicy(
                min_length=32,  # Stronger for services
                require_lowercase=True,
                require_uppercase=True,
                require_digits=True,
                require_symbols=True
            ),
            mfa=cognito.Mfa.OFF,
        )

        # Gateway Pool Domain
        self.gateway_pool_domain = cognito.UserPoolDomain(
            self, "GatewayPoolDomain",
            user_pool=self.gateway_pool,
            cognito_domain=cognito.CognitoDomainOptions(
                domain_prefix=f"insadv-gateway-{unique_suffix}"
            )
        )

        # Resource Server for Gateway Pool to provide custom OAuth scopes
        gateway_access_scope = cognito.ResourceServerScope(
            scope_name="gateway.access",
            scope_description="Access to AgentCore Gateway"
        )
        
        self.gateway_resource_server = cognito.UserPoolResourceServer(
            self, "GatewayResourceServer",
            user_pool=self.gateway_pool,
            identifier="agentcore-gateway",
            user_pool_resource_server_name="AgentCore Gateway Resource Server",
            scopes=[gateway_access_scope]
        )

        # Runtime Client for AgentCore Runtime OAuth (client credentials flow only)
        self.runtime_client = cognito.UserPoolClient(
            self, "RuntimeClient",
            user_pool=self.gateway_pool,
            user_pool_client_name="RuntimeClient",
            generate_secret=True,  # Secret required for client credentials flow
            # No auth_flows needed for client credentials
            o_auth=cognito.OAuthSettings(
                flows=cognito.OAuthFlows(
                    client_credentials=True  # Only client credentials for runtime-to-gateway auth
                ),
                # Use custom scope from resource server
                scopes=[cognito.OAuthScope.resource_server(self.gateway_resource_server, gateway_access_scope)]
            )
        )

        # =================================================================
        # SSM PARAMETERS - Store configuration for applications
        # =================================================================
        
        # User Pool Parameters (React App)
        ssm.StringParameter(
            self, "UserPoolIdParam",
            parameter_name="/insurance-advisor/cognito/user-pool-id",
            string_value=self.user_pool.user_pool_id,
            description="Cognito User Pool ID for external access (React SPA + Gateway)"
        )

        ssm.StringParameter(
            self, "AppClientIdParam",
            parameter_name="/insurance-advisor/cognito/app-client-id",
            string_value=self.app_client.user_pool_client_id,
            description="Cognito App Client ID for React SPA + AgentCore runtime JWT auth"
        )

        ssm.StringParameter(
            self, "GatewayClientIdParam",
            parameter_name="/insurance-advisor/cognito/gateway-client-id",
            string_value=self.gateway_client.user_pool_client_id,
            description="Cognito Gateway Client ID for AgentCore Gateway OAuth"
        )

        # Gateway Pool Parameters (AgentCore Runtime)
        ssm.StringParameter(
            self, "GatewayPoolIdParam",
            parameter_name="/insurance-advisor/cognito/gateway-pool-id",
            string_value=self.gateway_pool.user_pool_id,
            description="Cognito Gateway Pool ID for internal access"
        )

        ssm.StringParameter(
            self, "RuntimeClientIdParam",
            parameter_name="/insurance-advisor/cognito/runtime-client-id",
            string_value=self.runtime_client.user_pool_client_id,
            description="Cognito Runtime Client ID for AgentCore Runtime OAuth"
        )

        # Shared Parameters
        ssm.StringParameter(
            self, "CognitoRegionParam",
            parameter_name="/insurance-advisor/cognito/region",
            string_value=self.region,
            description="AWS Region for Cognito"
        )

        # =================================================================
        # OUTPUTS
        # =================================================================
        
        # User Pool Outputs
        CfnOutput(
            self, "UserPoolId",
            value=self.user_pool.user_pool_id,
            description="Cognito User Pool ID for external access"
        )

        CfnOutput(
            self, "AppClientId", 
            value=self.app_client.user_pool_client_id,
            description="Cognito App Client ID"
        )

        CfnOutput(
            self, "GatewayClientId", 
            value=self.gateway_client.user_pool_client_id,
            description="Cognito Gateway Client ID"
        )

        CfnOutput(
            self, "UserPoolArn",
            value=self.user_pool.user_pool_arn,
            description="Cognito User Pool ARN"
        )

        # Gateway Pool Outputs
        CfnOutput(
            self, "GatewayPoolId",
            value=self.gateway_pool.user_pool_id,
            description="Cognito Gateway Pool ID for internal access"
        )

        CfnOutput(
            self, "RuntimeClientId", 
            value=self.runtime_client.user_pool_client_id,
            description="Cognito Runtime Client ID"
        )

        CfnOutput(
            self, "GatewayPoolArn",
            value=self.gateway_pool.user_pool_arn,
            description="Cognito Gateway Pool ARN"
        )
