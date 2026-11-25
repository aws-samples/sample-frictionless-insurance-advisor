from aws_cdk import (
    Duration,
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

        # Unique suffix from the account ID hash (first 8 chars of MD5).
        # The account ID is CDK_DEFAULT_ACCOUNT when set, else the stack's account.
        account_id = os.environ.get('CDK_DEFAULT_ACCOUNT') or self.account
        unique_suffix = hashlib.md5(account_id.encode(), usedforsecurity=False).hexdigest()[:8]

        # =================================================================
        # USER POOL: External access (React SPA + AgentCore Gateway)
        # =================================================================
        self.user_pool = cognito.UserPool(
            self, "UserPool",
            user_pool_name="insurance-advisor-user",
            # Closed pool: only an administrator (or the opt-in /signup Lambda, see
            # the `enable_public_signup` context flag) can create users. This is set
            # explicitly so the pool is closed whatever the account's own policies are.
            self_sign_up_enabled=False,
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
            # A changed email address stays unverified and the old address stays in
            # place until the new address is confirmed with a code.
            keep_original=cognito.KeepOriginalAttrs(email=True),
        )
        
        # User Pool Domain: the token endpoint of the GatewayClient's client
        # credentials flow below (the AgentCore Identity credential provider calls it)
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

        # App Client for the React SPA (also the client the AgentCore runtime JWT
        # authorization accepts). The SPA signs in with SRP through Amplify. The
        # hosted UI and the OAuth flows are off. ADMIN_USER_PASSWORD_AUTH stays for
        # scripted sign-in: it is an admin API call, so it needs IAM credentials and
        # no browser can use it.
        self.app_client = cognito.UserPoolClient(
            self, "AppClient",
            user_pool=self.user_pool,
            user_pool_client_name="AppClient",
            generate_secret=False,  # A browser app cannot keep a secret
            auth_flows=cognito.AuthFlow(
                user_srp=True,
                admin_user_password=True
            ),
            disable_o_auth=True,
            # The app never updates a user attribute. The writable list holds one
            # harmless attribute and excludes email and email_verified, so a signed-in
            # user cannot change the address that identifies the advisor. Nothing is
            # set for the readable attributes: the ID token carries the email claim.
            write_attributes=cognito.ClientAttributes().with_standard_attributes(fullname=True),
            prevent_user_existence_errors=True,
            # Amplify refreshes the access and ID tokens with the refresh token when
            # they expire.
            access_token_validity=Duration.minutes(30),
            id_token_validity=Duration.minutes(30),
            refresh_token_validity=Duration.days(1),
        )

        # Gateway Client for AgentCore Gateway OAuth (client credentials flow for API Gateway access).
        # The OAuth provider Lambda reads this client's secret with DescribeUserPoolClient, so the
        # secret is replaced by replacing the client (a new construct id), not by adding a second one.
        self.gateway_client = cognito.UserPoolClient(
            self, "GatewayMachineClient",
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

        # Runtime Client for AgentCore Runtime OAuth (client credentials flow only). Same rule as the
        # gateway client: the secret is replaced by replacing the client (a new construct id).
        self.runtime_client = cognito.UserPoolClient(
            self, "RuntimeMachineClient",
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
