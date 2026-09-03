import json
from aws_cdk import (
    Stack,
    aws_iam as iam,
    aws_bedrock as bedrock,
    aws_bedrockagentcore as agentcore,
    aws_ecr_assets as ecr_assets,
    aws_ssm as ssm,
    RemovalPolicy,
    CfnOutput,
)
from constructs import Construct
from .auth_stack import AuthStack
from .tools_stack import ToolsStack
from .agentcore_oauth_provider import AgentCoreOAuth2Provider, AgentCoreOAuth2ProviderProps


class AgentCoreStack(Stack):
    """AgentCore stack with gateway, targets, identity, runtime, and memory resources"""

    def __init__(self, scope: Construct, construct_id: str, auth_stack: AuthStack, tools_stack: ToolsStack, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        
        # Gateway authentication is handled by AWS IAM

        # Create IAM role for AgentCore Gateway with enhanced logging permissions
        self.gateway_role = iam.Role(
            self, "AgentCoreGatewayRole",
            assumed_by=iam.ServicePrincipal("bedrock-agentcore.amazonaws.com"),
            inline_policies={
                "LambdaInvokePolicy": iam.PolicyDocument(
                    statements=[
                        iam.PolicyStatement(
                            effect=iam.Effect.ALLOW,
                            actions=["lambda:InvokeFunction"],
                            resources=[
                                tools_stack.profile_lambda.function_arn,
                                tools_stack.policies_lambda.function_arn,
                                tools_stack.portfolio_lambda.function_arn,
                                tools_stack.promotions_lambda.function_arn,
                                tools_stack.company_lambda.function_arn,
                                tools_stack.competitive_lambda.function_arn,
                                tools_stack.competitors_lambda.function_arn,
                                tools_stack.extract_policy_lambda.function_arn,
                                tools_stack.formschema_lambda.function_arn,
                            ]
                        )
                    ]
                ),
                "EnhancedLoggingPolicy": iam.PolicyDocument(
                    statements=[
                        iam.PolicyStatement(
                            effect=iam.Effect.ALLOW,
                            actions=[
                                "logs:CreateLogGroup",
                                "logs:CreateLogStream", 
                                "logs:PutLogEvents",
                                "logs:DescribeLogGroups",
                                "logs:DescribeLogStreams"
                            ],
                            resources=[
                                f"arn:aws:logs:{self.region}:{self.account}:log-group:/aws/bedrock-agentcore/gateways/insurance-advisor-gateway*"
                            ]
                        ),
                        iam.PolicyStatement(
                            effect=iam.Effect.ALLOW,
                            actions=[
                                "cloudwatch:PutMetricData"
                            ],
                            resources=["*"],
                            conditions={
                                "StringEquals": {
                                    "cloudwatch:namespace": "AWS/BedrockAgentCore"
                                }
                            }
                        ),
                        iam.PolicyStatement(
                            effect=iam.Effect.ALLOW,
                            actions=[
                                "xray:PutTraceSegments",
                                "xray:PutTelemetryRecords"
                            ],
                            resources=["*"]
                        )
                    ]
                ),
                "WorkloadIdentityPolicy": iam.PolicyDocument(
                    statements=[
                        iam.PolicyStatement(
                            sid="Statement1",
                            effect=iam.Effect.ALLOW,
                            actions=["bedrock-agentcore:GetWorkloadAccessToken"],
                            resources=[
                                f"arn:aws:bedrock-agentcore:{self.region}:{self.account}:workload-identity-directory/default/workload-identity/*",
                                f"arn:aws:bedrock-agentcore:{self.region}:{self.account}:workload-identity-directory/default"
                            ]
                        ),
                        iam.PolicyStatement(
                            sid="Statement2",
                            effect=iam.Effect.ALLOW,
                            actions=["bedrock-agentcore:GetResourceOauth2Token"],
                            resources=[
                                f"arn:aws:bedrock-agentcore:{self.region}:{self.account}:workload-identity-directory/default/workload-identity/*",
                                f"arn:aws:bedrock-agentcore:{self.region}:{self.account}:workload-identity-directory/default",
                                f"arn:aws:bedrock-agentcore:{self.region}:{self.account}:token-vault/default"
                            ]
                        )
                    ]
                )
            }
        )

        # AgentCore Gateway — stable L1 (AWS::BedrockAgentCore::Gateway).
        #
        # Previously the `Gateway` L2 from aws_bedrock_agentcore_alpha. The
        # stable module ships L1 only, so this is a deliberate L2 -> L1
        # downgrade: we hand-write the CloudFormation the L2 used to generate
        # in exchange for dropping a pre-release dependency that had to stay
        # version-locked to an exact aws-cdk-lib release and is slated for
        # removal in CDK v3.
        #
        # The construct tree is shaped to match what the L2 produced: a
        # wrapper scope named `InsuranceAdvisorGateway` whose resource child
        # is `Resource`, and one child scope per target. CDK derives logical
        # IDs from the construct path, not the construct class, so this keeps
        # every logical ID byte-identical (InsuranceAdvisorGateway066E037D and
        # friends) and the migration synthesises to an empty diff.
        #
        # That preservation is load-bearing, not cosmetic: the auto-generated
        # cross-stack exports consumed by insadv-04-voice embed the gateway's
        # logical ID, and a replacement would mint a new gateway URL that
        # cascades into both runtimes' environment variables.
        gateway_scope = Construct(self, "InsuranceAdvisorGateway")

        # Gateway Pool is used for machine-to-machine authentication (Runtime → Gateway)
        self.agentcore_gateway = agentcore.CfnGateway(
            gateway_scope, "Resource",
            name="insurance-advisor-gateway",
            description="AgentCore Gateway for Insurance Advisor services with enhanced logging",
            role_arn=self.gateway_role.role_arn,
            protocol_type="MCP",
            # Cognito JWT authorization against the Gateway Pool (M2M).
            authorizer_type="CUSTOM_JWT",
            authorizer_configuration=agentcore.CfnGateway.AuthorizerConfigurationProperty(
                custom_jwt_authorizer=agentcore.CfnGateway.CustomJWTAuthorizerConfigurationProperty(
                    discovery_url=(
                        f"https://cognito-idp.{self.region}.amazonaws.com/"
                        f"{auth_stack.gateway_pool.user_pool_id}"
                        "/.well-known/openid-configuration"
                    ),
                    allowed_clients=[auth_stack.runtime_client.user_pool_client_id],
                )
            ),
            # The alpha L2 injected these MCP defaults whenever no protocol
            # configuration was supplied, and the deployed gateway is running
            # with them. Pinned explicitly so this migration is a no-op rather
            # than silently handing the service a different configuration.
            protocol_configuration=agentcore.CfnGateway.GatewayProtocolConfigurationProperty(
                mcp=agentcore.CfnGateway.MCPGatewayConfigurationProperty(
                    instructions="Default gateway to connect to external MCP tools",
                    search_type="SEMANTIC",
                    supported_versions=["2025-03-26"],
                )
            ),
            # Debug-level exception messages for detailed troubleshooting.
            exception_level="DEBUG",
        )

        # Stack-level handles for the gateway's identity. Downstream code (and
        # the voice stack) reads these rather than reaching into the construct,
        # so the L1 attribute names stay an implementation detail here.
        self.gateway_url = self.agentcore_gateway.attr_gateway_url
        self.gateway_id = self.agentcore_gateway.attr_gateway_identifier

        # Create OpenAPI specification for the Insurance Advisor API
        openapi_spec = {
            "openapi": "3.0.0",
            "info": {
                "title": "Insurance Advisor API",
                "version": "1.0.0",
                "description": "API for Insurance Advisor AgentCore services"
            },
            "servers": [
                {
                    "url": tools_stack.api.url.rstrip('/'),
                    "description": "Insurance Advisor API Gateway"
                }
            ],
            "paths": {
                "/profile": {
                    "get": {
                        "operationId": "get_profile",
                        "summary": "Get customer profile by ID or list all profiles",
                        "description": "Retrieve customer profile information. If no ID is provided, returns all profiles for the authenticated advisor.",
                        "parameters": [
                            {
                                "name": "advisor_id",
                                "in": "query",
                                "required": True,
                                "schema": {
                                    "type": "string"
                                },
                                "description": "Advisor ID (required)"
                            },
                            {
                                "name": "customer_id",
                                "in": "query",
                                "required": False,
                                "schema": {
                                    "type": "string"
                                },
                                "description": "Customer ID (optional - if not provided, returns all profiles)"
                            }
                        ],
                        "responses": {
                            "200": {
                                "description": "Profile data retrieved successfully",
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object"
                                        }
                                    }
                                }
                            },
                            "401": {
                                "description": "Unauthorized"
                            },
                            "500": {
                                "description": "Internal server error"
                            }
                        },
                        "security": [
                            {
                                "CognitoAuth": []
                            }
                        ]
                    },
                    "post": {
                        "operationId": "create_profile",
                        "summary": "Create a new prospect profile",
                        "description": "Create a new prospect profile in the database. The agent uses this when onboarding a new prospect through conversation. Only 'name' is required; other fields can be added later via update_profile.",
                        "parameters": [
                            {
                                "name": "advisor_id",
                                "in": "query",
                                "required": True,
                                "schema": {
                                    "type": "string"
                                },
                                "description": "Advisor ID (required)"
                            }
                        ],
                        "requestBody": {
                            "required": True,
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "required": ["name"],
                                        "properties": {
                                            "name": {"type": "string", "description": "Full name of the prospect (required)"},
                                            "email": {"type": "string", "description": "Email address"},
                                            "phone": {"type": "string", "description": "Phone number"},
                                            "address": {"type": "string", "description": "Mailing address"},
                                            "date_of_birth": {"type": "string", "description": "Date of birth (YYYY-MM-DD)"},
                                            "marital_status": {"type": "string", "description": "Marital status (Single, Married, Divorced, Widowed)"},
                                            "dependents": {"type": "integer", "description": "Number of dependents"},
                                            "occupation": {"type": "string", "description": "Job title or occupation"},
                                            "employment_status": {"type": "string", "description": "Employment status (employed, self-employed, unemployed, retired)"},
                                            "annual_income": {"type": "integer", "description": "Annual income in USD"},
                                            "home_owner": {"type": "boolean", "description": "Whether the prospect owns their home"},
                                            "smoking": {"type": "boolean", "description": "Whether the prospect smokes"},
                                            "medical_conditions": {"type": "string", "description": "Known medical conditions"},
                                            "financial_objective": {"type": "string", "description": "Primary financial objective"},
                                            "time_horizon": {"type": "string", "description": "Investment/insurance time horizon"},
                                            "risk_tolerance": {"type": "string", "description": "Risk tolerance level (low, moderate, high)"},
                                            "liquidity_needs": {"type": "string", "description": "Liquidity needs (low, moderate, high)"}
                                        }
                                    }
                                }
                            }
                        },
                        "responses": {
                            "201": {
                                "description": "Profile created successfully",
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object"
                                        }
                                    }
                                }
                            },
                            "400": {
                                "description": "Bad request - missing required fields"
                            },
                            "401": {
                                "description": "Unauthorized"
                            }
                        },
                        "security": [
                            {
                                "CognitoAuth": []
                            }
                        ]
                    },
                    "put": {
                        "operationId": "update_profile",
                        "summary": "Update an existing customer or prospect profile",
                        "description": "Update fields on an existing profile. The agent uses this to save information collected during conversation. Only fields included in the request body will be updated.",
                        "parameters": [
                            {
                                "name": "advisor_id",
                                "in": "query",
                                "required": True,
                                "schema": {
                                    "type": "string"
                                },
                                "description": "Advisor ID (required)"
                            }
                        ],
                        "requestBody": {
                            "required": True,
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "required": ["customer_id"],
                                        "properties": {
                                            "customer_id": {"type": "string", "description": "Customer ID to update (required)"},
                                            "name": {"type": "string", "description": "Full name"},
                                            "email": {"type": "string", "description": "Email address"},
                                            "phone": {"type": "string", "description": "Phone number"},
                                            "address": {"type": "string", "description": "Mailing address"},
                                            "date_of_birth": {"type": "string", "description": "Date of birth (YYYY-MM-DD)"},
                                            "marital_status": {"type": "string", "description": "Marital status"},
                                            "dependents": {"type": "integer", "description": "Number of dependents"},
                                            "occupation": {"type": "string", "description": "Job title or occupation"},
                                            "employment_status": {"type": "string", "description": "Employment status"},
                                            "annual_income": {"type": "integer", "description": "Annual income in USD"},
                                            "home_owner": {"type": "boolean", "description": "Whether they own their home"},
                                            "smoking": {"type": "boolean", "description": "Whether they smoke"},
                                            "medical_conditions": {"type": "string", "description": "Known medical conditions"},
                                            "financial_objective": {"type": "string", "description": "Primary financial objective"},
                                            "time_horizon": {"type": "string", "description": "Investment/insurance time horizon"},
                                            "risk_tolerance": {"type": "string", "description": "Risk tolerance level (low, moderate, high)"},
                                            "liquidity_needs": {"type": "string", "description": "Liquidity needs (low, moderate, high)"}
                                        }
                                    }
                                }
                            }
                        },
                        "responses": {
                            "200": {
                                "description": "Profile updated successfully",
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object"
                                        }
                                    }
                                }
                            },
                            "400": {
                                "description": "Bad request - missing customer_id or no valid fields"
                            },
                            "401": {
                                "description": "Unauthorized"
                            },
                            "403": {
                                "description": "Forbidden - profile belongs to another advisor"
                            },
                            "404": {
                                "description": "Profile not found"
                            }
                        },
                        "security": [
                            {
                                "CognitoAuth": []
                            }
                        ]
                    }
                },
                "/policy": {
                    "get": {
                        "operationId": "get_policy",
                        "summary": "Get insurance policy by customer ID or list all policies",
                        "description": "Retrieve insurance policy information by customer id. If no customer ID is provided, returns all policies for the authenticated advisor. Returned policies may be Unicorn-issued or third-party (third_party=true with an insurer name).",
                        "parameters": [
                            {
                                "name": "advisor_id",
                                "in": "query",
                                "required": True,
                                "schema": {
                                    "type": "string"
                                },
                                "description": "Advisor ID (required)"
                            },
                            {
                                "name": "customer_id",
                                "in": "query",
                                "required": False,
                                "schema": {
                                    "type": "string"
                                },
                                "description": "Customer ID (optional - filters policies for specific customer)"
                            }
                        ],
                        "responses": {
                            "200": {
                                "description": "Policy data retrieved successfully",
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object"
                                        }
                                    }
                                }
                            },
                            "401": {
                                "description": "Unauthorized"
                            },
                            "500": {
                                "description": "Internal server error"
                            }
                        },
                        "security": [
                            {
                                "CognitoAuth": []
                            }
                        ]
                    },
                    "post": {
                        "operationId": "create_third_party_policy",
                        "summary": "Create a third-party insurance policy",
                        "description": "Create a record of a policy the customer holds with another insurance carrier. ONLY for third-party policies — Unicorn-issued policies are read-only. The server forces third_party=true on the new record. Required: customer_id, type, insurer.",
                        "parameters": [
                            {
                                "name": "advisor_id",
                                "in": "query",
                                "required": True,
                                "schema": {"type": "string"},
                                "description": "Advisor ID (required)"
                            }
                        ],
                        "requestBody": {
                            "required": True,
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "required": ["customer_id", "type", "insurer"],
                                        "properties": {
                                            "customer_id": {"type": "string", "description": "Customer the policy belongs to (required)"},
                                            "type": {"type": "string", "description": "Policy type, e.g. Auto Insurance, Home Insurance, Life Insurance (required)"},
                                            "insurer": {"type": "string", "description": "Name of the insurance carrier holding the policy (required)"},
                                            "product_name": {"type": "string", "description": "Insurer's product name for this policy"},
                                            "premium_amount": {"type": "number", "description": "Premium amount per period"},
                                            "premium_frequency": {"type": "string", "description": "yearly, monthly, etc."},
                                            "coverage_amount": {"type": "number", "description": "Total coverage amount"},
                                            "status": {"type": "string", "description": "Active, Lapsed, etc. (default: Active)"},
                                            "start_date": {"type": "string", "description": "Policy start date YYYY-MM-DD"},
                                            "renewal_date": {"type": "string", "description": "Renewal date YYYY-MM-DD"},
                                            "vehicle": {"type": "object", "description": "Auto-policy details: make, model, year, registration"},
                                            "property": {"type": "object", "description": "Home-policy details: address, property_type, year_built, square_feet"},
                                            "health_details": {"type": "object", "description": "Health-policy details: plan_tier, network, dependents"},
                                            "disability_details": {"type": "object", "description": "Disability-policy details: benefit_period_years, waiting_period_days, occupation_class"},
                                            "life_details": {"type": "object", "description": "Life-policy details: life_type, term_years, beneficiary, smoker"}
                                        }
                                    }
                                }
                            }
                        },
                        "responses": {
                            "201": {"description": "Policy created"},
                            "400": {"description": "Bad request"},
                            "401": {"description": "Unauthorized"},
                            "500": {"description": "Internal server error"}
                        },
                        "security": [{"CognitoAuth": []}]
                    },
                    "put": {
                        "operationId": "update_third_party_policy",
                        "summary": "Update an existing third-party insurance policy",
                        "description": "Update fields on a third-party policy the customer already has on record. ONLY works on policies where third_party=true; Unicorn-issued policies remain read-only.",
                        "parameters": [
                            {
                                "name": "advisor_id",
                                "in": "query",
                                "required": True,
                                "schema": {"type": "string"},
                                "description": "Advisor ID (required)"
                            }
                        ],
                        "requestBody": {
                            "required": True,
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "required": ["id"],
                                        "properties": {
                                            "id": {"type": "string", "description": "Policy id to update (required)"},
                                            "customer_id": {"type": "string"},
                                            "type": {"type": "string"},
                                            "insurer": {"type": "string"},
                                            "product_name": {"type": "string"},
                                            "premium_amount": {"type": "number"},
                                            "premium_frequency": {"type": "string"},
                                            "coverage_amount": {"type": "number"},
                                            "status": {"type": "string"},
                                            "start_date": {"type": "string"},
                                            "renewal_date": {"type": "string"},
                                            "vehicle": {"type": "object"},
                                            "property": {"type": "object"},
                                            "health_details": {"type": "object"},
                                            "disability_details": {"type": "object"},
                                            "life_details": {"type": "object"}
                                        }
                                    }
                                }
                            }
                        },
                        "responses": {
                            "200": {"description": "Policy updated"},
                            "400": {"description": "Bad request"},
                            "401": {"description": "Unauthorized"},
                            "403": {"description": "Forbidden - Unicorn policy or different advisor"},
                            "404": {"description": "Policy not found"}
                        },
                        "security": [{"CognitoAuth": []}]
                    },
                    "delete": {
                        "operationId": "delete_third_party_policy",
                        "summary": "Delete a third-party insurance policy",
                        "description": "Delete a third-party policy record. ONLY works on policies where third_party=true; Unicorn-issued policies cannot be deleted via this endpoint.",
                        "parameters": [
                            {
                                "name": "advisor_id",
                                "in": "query",
                                "required": True,
                                "schema": {"type": "string"},
                                "description": "Advisor ID (required)"
                            },
                            {
                                "name": "id",
                                "in": "query",
                                "required": True,
                                "schema": {"type": "string"},
                                "description": "Policy id to delete (required)"
                            }
                        ],
                        "responses": {
                            "200": {"description": "Policy deleted"},
                            "400": {"description": "Bad request"},
                            "401": {"description": "Unauthorized"},
                            "403": {"description": "Forbidden - Unicorn policy or different advisor"},
                            "404": {"description": "Policy not found"}
                        },
                        "security": [{"CognitoAuth": []}]
                    }
                }
            },
            "components": {
                "securitySchemes": {
                    "CognitoAuth": {
                        "type": "oauth2",
                        "flows": {
                            "clientCredentials": {
                                "tokenUrl": f"https://{auth_stack.user_pool_domain.domain_name}.auth.{self.region}.amazoncognito.com/oauth2/token",
                                "scopes": {}
                            }
                        }
                    }
                }
            }
        }

        # Create AgentCore OAuth2 Credential Provider for API Gateway authentication
        # This connects to the User Pool for Gateway → API Gateway OAuth flow
        self.oauth_provider = AgentCoreOAuth2Provider(
            self, "ApiGatewayOAuthProvider",
            AgentCoreOAuth2ProviderProps(
                provider_name="insurance-advisor-api-oauth",
                client_id=auth_stack.gateway_client.user_pool_client_id,
                client_secret=auth_stack.gateway_client.user_pool_client_secret.unsafe_unwrap(),
                token_endpoint=f"https://{auth_stack.user_pool_domain.domain_name}.auth.{self.region}.amazoncognito.com/oauth2/token",
                user_pool_id=auth_stack.user_pool.user_pool_id,
                scopes=["insurance-advisor-api/api.access"]  # Custom scope from User Pool resource server
            )
        )

        # --- Gateway Targets — stable L1 (AWS::BedrockAgentCore::GatewayTarget)
        #
        # The alpha L2 exposed `add_open_api_target` / `add_lambda_target`
        # helpers that also wired up the gateway role's IAM grants. On L1 the
        # resources and the grants are both explicit; the grants are added in
        # the same order the L2 added them so the role's inline policy
        # serialises identically (statement order is significant in a
        # CloudFormation policy document).
        def _target_scope(target_id: str) -> Construct:
            """Child scope per target, matching the alpha L2's construct tree."""
            return Construct(gateway_scope, target_id)

        # API Gateway Target with AgentCore OAuth credentials
        self.api_gateway_target = agentcore.CfnGatewayTarget(
            _target_scope("ApiGatewayTarget"), "Resource",
            name="InsuranceAdvisorApiService",
            description="Insurance Advisor API Gateway service providing profile and policy management",
            gateway_identifier=self.gateway_id,
            target_configuration=agentcore.CfnGatewayTarget.TargetConfigurationProperty(
                mcp=agentcore.CfnGatewayTarget.McpTargetConfigurationProperty(
                    open_api_schema=agentcore.CfnGatewayTarget.ApiSchemaConfigurationProperty(
                        inline_payload=json.dumps(openapi_spec)
                    )
                )
            ),
            credential_provider_configurations=[
                agentcore.CfnGatewayTarget.CredentialProviderConfigurationProperty(
                    credential_provider_type="OAUTH",
                    credential_provider=agentcore.CfnGatewayTarget.CredentialProviderProperty(
                        oauth_credential_provider=agentcore.CfnGatewayTarget.OAuthCredentialProviderProperty(
                            provider_arn=self.oauth_provider.provider_arn,
                            scopes=["insurance-advisor-api/api.access"],
                        )
                    ),
                )
            ],
        )

        # The gateway assumes its role to fetch the OAuth token (and the
        # client secret behind it) before calling API Gateway. The alpha L2
        # granted this implicitly when given an OAuth credential provider.
        # Note the secret ARN is only needed here — it is deliberately not
        # part of the target's credential provider configuration.
        self.gateway_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "bedrock-agentcore:GetResourceOauth2Token",
                    "bedrock-agentcore:GetWorkloadAccessToken",
                    "secretsmanager:DescribeSecret",
                    "secretsmanager:GetSecretValue",
                ],
                resources=[
                    self.oauth_provider.provider_arn,
                    self.oauth_provider.secret_arn,
                ],
            )
        )

        # The OpenAPI target cannot be created until the role can actually
        # fetch that token, so it waits on the role's inline policy. The alpha
        # L2 emitted this dependency on the OpenAPI target only — the Lambda
        # targets have none — and that asymmetry is preserved here.
        self.api_gateway_target.node.add_dependency(
            self.gateway_role.node.find_child("DefaultPolicy")
        )

        def _lambda_target(
            target_id: str,
            *,
            name: str,
            description: str,
            lambda_function,
            tools: list,
        ) -> agentcore.CfnGatewayTarget:
            """A Lambda-backed MCP target invoked with the gateway's own role.

            Also grants the gateway role permission to invoke that Lambda,
            which is what the alpha L2's add_lambda_target did implicitly.
            CDK's grant_invoke covers both the function ARN and its `:*`
            qualified form; all seven grants merge into a single sorted
            statement on the role's inline policy.
            """
            target = agentcore.CfnGatewayTarget(
                _target_scope(target_id), "Resource",
                name=name,
                description=description,
                gateway_identifier=self.gateway_id,
                target_configuration=agentcore.CfnGatewayTarget.TargetConfigurationProperty(
                    mcp=agentcore.CfnGatewayTarget.McpTargetConfigurationProperty(
                        lambda_=agentcore.CfnGatewayTarget.McpLambdaTargetConfigurationProperty(
                            lambda_arn=lambda_function.function_arn,
                            tool_schema=agentcore.CfnGatewayTarget.ToolSchemaProperty(
                                inline_payload=tools
                            ),
                        )
                    )
                ),
                credential_provider_configurations=[
                    agentcore.CfnGatewayTarget.CredentialProviderConfigurationProperty(
                        credential_provider_type="GATEWAY_IAM_ROLE"
                    )
                ],
            )
            lambda_function.grant_invoke(self.gateway_role)
            return target

        def _no_arg_tool(name: str, description: str) -> agentcore.CfnGatewayTarget.ToolDefinitionProperty:
            """Tool that takes no arguments — the Lambda returns its whole dataset."""
            return agentcore.CfnGatewayTarget.ToolDefinitionProperty(
                name=name,
                description=description,
                input_schema=agentcore.CfnGatewayTarget.SchemaDefinitionProperty(
                    type="object",
                    properties={},
                ),
            )

        def _string_field(description: str) -> agentcore.CfnGatewayTarget.SchemaDefinitionProperty:
            """A single string property inside a tool's input schema."""
            return agentcore.CfnGatewayTarget.SchemaDefinitionProperty(
                type="string",
                description=description,
            )

        # Portfolio Target with IAM credentials
        self.portfolio_target = _lambda_target(
            "PortfolioTarget",
            name="PortfolioService",
            description="Customer portfolio information service - returns all portfolio data from S3",
            lambda_function=tools_stack.portfolio_lambda,
            tools=[_no_arg_tool(
                "get_portfolio",
                "Get all customer portfolio information and insurance products data",
            )],
        )

        # Promotions Target with IAM credentials
        self.promotions_target = _lambda_target(
            "PromotionsTarget",
            name="PromotionsService",
            description="Insurance promotions and offers service - returns all promotion data from S3",
            lambda_function=tools_stack.promotions_lambda,
            tools=[_no_arg_tool(
                "get_promotions",
                "Get all available insurance promotions and special offers",
            )],
        )

        # Company Info Target with IAM credentials
        self.company_target = _lambda_target(
            "CompanyTarget",
            name="CompanyInfoService",
            description="Information about Unicorn Insurance — history, ratings, claims process, customer service",
            lambda_function=tools_stack.company_lambda,
            tools=[_no_arg_tool(
                "get_company_info",
                "Retrieve factual information about Unicorn Insurance: company overview, "
                "regulatory credentials and financial ratings, customer service channels, "
                "claims process, and digital platform capabilities. Use when the customer "
                "or prospect asks about the company itself rather than specific products.",
            )],
        )

        # Competitive Info Target with IAM credentials
        self.competitive_target = _lambda_target(
            "CompetitiveTarget",
            name="CompetitiveInfoService",
            description="Unicorn Insurance's competitive positioning and advantages",
            lambda_function=tools_stack.competitive_lambda,
            tools=[_no_arg_tool(
                "get_competitive_info",
                "Retrieve talking points about why Unicorn Insurance is a better choice than "
                "competitors — value propositions, advantages by product line, service standards, "
                "and multi-policy bundle benefits. Use when the advisor needs to position "
                "Unicorn Insurance against alternatives during a sales conversation.",
            )],
        )

        # Competitor Products Target with IAM credentials
        self.competitors_target = _lambda_target(
            "CompetitorsTarget",
            name="CompetitorProductsService",
            description="Reference information about competitor insurance products for comparison",
            lambda_function=tools_stack.competitors_lambda,
            tools=[_no_arg_tool(
                "get_competitor_products",
                "Retrieve reference information about competitor insurance products "
                "(BigRival, StarInsure, QuickSafe) including their coverage, strengths, "
                "weaknesses, and pricing tier. Use when the customer mentions a specific "
                "competitor by name or asks how Unicorn products compare to alternatives. "
                "Always pair with get_competitive_info to frame the comparison favorably.",
            )],
        )

        # Document Extraction Target — invoked when the advisor uploads an
        # insurance-policy PDF / image / markdown via the SPA's 📎 button.
        # The tool reads the document from S3 and returns a structured JSON
        # extraction. Agent then confirms with the user and calls
        # create_third_party_policy (and create_profile if no customer is
        # selected yet).
        self.extract_policy_target = _lambda_target(
            "ExtractPolicyTarget",
            name="DocumentExtractionService",
            description="Extract structured insurance-policy fields from an uploaded document",
            lambda_function=tools_stack.extract_policy_lambda,
            tools=[agentcore.CfnGatewayTarget.ToolDefinitionProperty(
                name="extract_policy_from_document",
                description=(
                    "Extract structured insurance-policy fields (carrier, "
                    "type, coverage amount, premium, dates, beneficiary) "
                    "from a document the advisor uploaded via the SPA. "
                    "Use this tool when the user references an attached "
                    "document by document_id (e.g. 'create a third-party "
                    "policy from the PDF I just attached'). After extraction, "
                    "show the extracted fields to the user, ask for "
                    "confirmation, and ONLY THEN call "
                    "create_third_party_policy. If the user is in '+ New "
                    "Prospect' mode (no customer_id selected), call "
                    "create_profile FIRST using suggested_profile_fields, "
                    "capture the new customer_id, then create the policy."
                ),
                input_schema=agentcore.CfnGatewayTarget.SchemaDefinitionProperty(
                    type="object",
                    properties={
                        "document_id": _string_field(
                            "The document_id returned by /documents/initiate when the file was uploaded."
                        ),
                        "customer_id": _string_field(
                            "Customer ID the document is being attached to. Pass null/omit for '+ New Prospect' mode."
                        ),
                        "advisor_id": _string_field(
                            "Advisor email (the calling user). Required for S3 namespace scoping."
                        ),
                    },
                ),
            )],
        )

        # Form Schema Target with IAM credentials.
        #
        # Deliberately on the shared gateway rather than local to the voice
        # runtime: it is a read-only data lookup that both agents can use. The
        # voice agent fetches a schema when an application starts so it knows
        # which fields to fill; the text agent uses it to answer "what does
        # this application ask for?" — useful even though the Assistant page
        # has no form to render.
        self.form_schema_target = _lambda_target(
            "FormSchemaTarget",
            name="FormSchemaService",
            description="Application-form schemas for Unicorn Insurance products",
            lambda_function=tools_stack.formschema_lambda,
            tools=[agentcore.CfnGatewayTarget.ToolDefinitionProperty(
                name="get_form_schema",
                description=(
                    "Retrieve the application form for an insurance product: "
                    "its sections, every field, each field's type, and which "
                    "fields are mandatory. Use this when starting an "
                    "application for a product, or when the advisor asks what "
                    "information an application requires or what is still "
                    "outstanding before it can be submitted. If the product "
                    "type is not published the response lists the ones that "
                    "are — pick from those rather than guessing."
                ),
                input_schema=agentcore.CfnGatewayTarget.SchemaDefinitionProperty(
                    type="object",
                    properties={
                        "product_type": _string_field(
                            "Product identifier, snake_case. Currently "
                            "published: 'term_life', 'whole_life', "
                            "'universal_life', 'variable_life'."
                        ),
                    },
                ),
            )],
        )

        # Amazon Bedrock Guardrail — defined in tools_stack so the Lambdas
        # in that stack can reference it without a cross-stack cycle. Keep
        # local handles so the rest of this stack reads naturally.
        self.guardrail = tools_stack.guardrail
        # Guardrail version is resolved at runtime cold-start by reading
        # an SSM parameter (rather than passing the version string through
        # a CFN export). This decouples policy updates from the cross-
        # stack graph and avoids the "Cannot update export … in use by …"
        # deadlock that broke every guardrail-policy change.
        self.guardrail_version_param = tools_stack.guardrail_version_param
        self.guardrail_version_param_name = tools_stack.guardrail_version_param_name

        # Re-exported for the voice stack. The voice runtime reads form
        # schemas directly from S3 rather than routing them back through the
        # model as tool arguments — see open_application_form in
        # voice-agent/app.py.
        self.forms_bucket = tools_stack.forms_bucket

        # Docker Image Asset - CDK will build and push automatically
        self.agent_image = ecr_assets.DockerImageAsset(
            self, "InsuranceAdvisorAgentImage",
            directory="agent",  # Directory containing Dockerfile and app.py
            platform=ecr_assets.Platform.LINUX_ARM64
        )

        # IAM role for AgentCore Runtime with enhanced logging permissions
        self.runtime_role = iam.Role(
            self, "AgentCoreRuntimeRole",
            assumed_by=iam.ServicePrincipal("bedrock-agentcore.amazonaws.com"),
            inline_policies={
                "BedrockModelAccessPolicy": iam.PolicyDocument(
                    statements=[
                        iam.PolicyStatement(
                            effect=iam.Effect.ALLOW,
                            actions=[
                                "bedrock:InvokeModel",
                                "bedrock:InvokeModelWithResponseStream",
                            ],
                            resources=[
                                # Cross-region inference profile
                                f"arn:aws:bedrock:{self.region}:{self.account}:inference-profile/us.anthropic.claude-haiku-4-5-20251001-v1:0",
                                # Underlying foundation models in destination regions
                                "arn:aws:bedrock:us-east-1::foundation-model/anthropic.claude-haiku-4-5-20251001-v1:0",
                                "arn:aws:bedrock:us-east-2::foundation-model/anthropic.claude-haiku-4-5-20251001-v1:0",
                                "arn:aws:bedrock:us-west-1::foundation-model/anthropic.claude-haiku-4-5-20251001-v1:0",
                                "arn:aws:bedrock:us-west-2::foundation-model/anthropic.claude-haiku-4-5-20251001-v1:0",
                                "arn:aws:bedrock:ca-central-1::foundation-model/anthropic.claude-haiku-4-5-20251001-v1:0",
                            ]
                        )
                    ]
                ),
                "BedrockGuardrailPolicy": iam.PolicyDocument(
                    statements=[
                        iam.PolicyStatement(
                            effect=iam.Effect.ALLOW,
                            actions=[
                                "bedrock:ApplyGuardrail",
                                "bedrock:GetGuardrail",
                            ],
                            resources=[
                                self.guardrail.attr_guardrail_arn,
                            ]
                        ),
                        # Allow the runtime container to look up the
                        # current published guardrail version at cold
                        # start. Scoped to the single parameter only.
                        iam.PolicyStatement(
                            effect=iam.Effect.ALLOW,
                            actions=["ssm:GetParameter"],
                            resources=[self.guardrail_version_param.parameter_arn],
                        ),
                    ]
                ),
                "AgentCoreRuntimePolicy": iam.PolicyDocument(
                    statements=[
                        iam.PolicyStatement(
                            effect=iam.Effect.ALLOW,
                            actions=[
                                "bedrock-agentcore:*",
                                "ecr:GetAuthorizationToken",
                                "ecr:BatchCheckLayerAvailability",
                                "ecr:GetDownloadUrlForLayer",
                                "ecr:BatchGetImage"
                            ],
                            resources=["*"]
                        )
                    ]
                ),
                "CognitoUserAccessPolicy": iam.PolicyDocument(
                    statements=[
                        iam.PolicyStatement(
                            effect=iam.Effect.ALLOW,
                            actions=["cognito-idp:AdminGetUser"],
                            resources=[auth_stack.user_pool.user_pool_arn]
                        )
                    ]
                ),
                "EnhancedRuntimeLoggingPolicy": iam.PolicyDocument(
                    statements=[
                        iam.PolicyStatement(
                            effect=iam.Effect.ALLOW,
                            actions=[
                                "logs:CreateLogGroup",
                                "logs:CreateLogStream",
                                "logs:PutLogEvents",
                                "logs:DescribeLogGroups",
                                "logs:DescribeLogStreams"
                            ],
                            resources=[
                                f"arn:aws:logs:{self.region}:{self.account}:log-group:/aws/bedrock-agentcore/runtimes/insurance_advisor_agent*"
                            ]
                        ),
                        iam.PolicyStatement(
                            effect=iam.Effect.ALLOW,
                            actions=[
                                "cloudwatch:PutMetricData"
                            ],
                            resources=["*"],
                            conditions={
                                "StringEquals": {
                                    "cloudwatch:namespace": ["AWS/BedrockAgentCore", "bedrock-agentcore"]
                                }
                            }
                        ),
                        iam.PolicyStatement(
                            effect=iam.Effect.ALLOW,
                            actions=[
                                "xray:PutTraceSegments",
                                "xray:PutTelemetryRecords",
                                "xray:GetSamplingRules",
                                "xray:GetSamplingTargets"
                            ],
                            resources=["*"]
                        )
                    ]
                )
            }
        )

        # IAM role for AgentCore Memory
        self.memory_role = iam.Role(
            self, "AgentCoreMemoryRole",
            assumed_by=iam.ServicePrincipal("bedrock-agentcore.amazonaws.com"),
            inline_policies={
                "MemoryPolicy": iam.PolicyDocument(
                    statements=[
                        iam.PolicyStatement(
                            effect=iam.Effect.ALLOW,
                            actions=[
                                "bedrock:InvokeModel",
                                "bedrock:InvokeModelWithResponseStream"
                            ],
                            resources=[
                                # Cross-region inference profile
                                f"arn:aws:bedrock:{self.region}:{self.account}:inference-profile/us.anthropic.claude-haiku-4-5-20251001-v1:0",
                                # Underlying foundation models in destination regions
                                "arn:aws:bedrock:us-east-1::foundation-model/anthropic.claude-haiku-4-5-20251001-v1:0",
                                "arn:aws:bedrock:us-east-2::foundation-model/anthropic.claude-haiku-4-5-20251001-v1:0",
                                "arn:aws:bedrock:us-west-1::foundation-model/anthropic.claude-haiku-4-5-20251001-v1:0",
                                "arn:aws:bedrock:us-west-2::foundation-model/anthropic.claude-haiku-4-5-20251001-v1:0",
                                "arn:aws:bedrock:ca-central-1::foundation-model/anthropic.claude-haiku-4-5-20251001-v1:0",
                            ]
                        )
                    ]
                )
            }
        )

        # Long-term Memory (LTM) - with all three memory strategies for comprehensive memory
        self.ltm_memory = agentcore.CfnMemory(
            self, "InsuranceAdvisorLTM", 
            name="insurance_advisor_ltm",
            description="Long-term memory for Insurance Advisor agent - extracts and summarizes key information across sessions",
            # Threat model M17: 90 days. Originally 365 (the platform max).
            # Bounding the retention window limits the GDPR right-to-erasure
            # window for chat history that DynamoDB profile/policy deletes
            # don't reach (M16 would couple them properly; deferred for now).
            event_expiry_duration=90,
            memory_execution_role_arn=self.memory_role.role_arn,
            memory_strategies=[
                # Summary Strategy - Summarizes customer interactions (requires sessionId in namespace)
                agentcore.CfnMemory.MemoryStrategyProperty(
                    summary_memory_strategy=agentcore.CfnMemory.SummaryMemoryStrategyProperty(
                        name="insurance_summary",
                        description="Summarizes customer interactions, preferences, and insurance needs",
                        namespaces=["/summaries/{actorId}/{sessionId}"]
                    )
                ),
                # User Preference Strategy - Extracts customer preferences for insurance products
                agentcore.CfnMemory.MemoryStrategyProperty(
                    user_preference_memory_strategy=agentcore.CfnMemory.UserPreferenceMemoryStrategyProperty(
                        name="customer_preferences",
                        description="Extracts customer preferences for insurance products and communication style",
                        namespaces=["/preferences/{actorId}"]
                    )
                ),
                # Semantic Strategy - Stores factual information about customers
                agentcore.CfnMemory.MemoryStrategyProperty(
                    semantic_memory_strategy=agentcore.CfnMemory.SemanticMemoryStrategyProperty(
                        name="customer_facts",
                        description="Stores factual information about customers such as family details, life events, and insurance history",
                        namespaces=["/facts/{actorId}"]
                    )
                )
            ]
        )

        # Apply removal policy to LTM Memory
        self.ltm_memory.apply_removal_policy(RemovalPolicy.DESTROY)

        # Register runtime-to-gateway OAuth credentials with AgentCore Identity Token Vault
        # AgentCore Identity manages the secret lifecycle and token caching per workload identity.
        # The provider name is passed to the runtime via env var; the SDK's @requires_access_token
        # decorator resolves it to the Token Vault at runtime.
        self.runtime_gateway_oauth_provider = AgentCoreOAuth2Provider(
            self, "RuntimeGatewayOAuthProvider",
            AgentCoreOAuth2ProviderProps(
                provider_name="insurance-advisor-runtime-gateway-auth",
                client_id=auth_stack.runtime_client.user_pool_client_id,
                client_secret=auth_stack.runtime_client.user_pool_client_secret.unsafe_unwrap(),
                token_endpoint=f"https://{auth_stack.gateway_pool_domain.domain_name}.auth.{self.region}.amazoncognito.com/oauth2/token",
                user_pool_id=auth_stack.gateway_pool.user_pool_id,
                scopes=["agentcore-gateway/gateway.access"]
            )
        )

        # AgentCore Runtime — stable L1 (AWS::BedrockAgentCore::Runtime).
        #
        # Previously an AwsCustomResource wrapper (cdk/agentcore_runtime_custom.py)
        # because requestHeaderConfiguration was not exposed by CloudFormation.
        # It is now a first-class property, so the runtime is plain CloudFormation:
        # no provider Lambda, no npm-installed SDK whose version determined whether
        # the Authorization allowlist silently vanished, and therefore no
        # post-deploy assertion script.
        #
        # Note on MMDSv2: AgentCore requires requireMMDSV2 on every runtime as of
        # 2026-06-30, but metadataConfiguration is Update-only in the API and is not
        # a CloudFormation property. The service defaults it to true for
        # newly-created runtimes (verified against a throwaway runtime), so there is
        # nothing to set here.
        self.agentcore_runtime = agentcore.CfnRuntime(
            self, "InsuranceAdvisorRuntimeL1",
            # Renamed from `insurance_advisor_runtime`. AgentRuntimeName is unique
            # per account/region and CloudFormation creates the replacement before
            # deleting the custom resource, so reusing the old name would fail with
            # ConflictException. The ARN changes on replacement regardless.
            agent_runtime_name="insurance_advisor_agent",
            description="AgentCore Runtime for Insurance Advisor Agent with gateway integration and OAuth JWT authentication",
            role_arn=self.runtime_role.role_arn,
            agent_runtime_artifact=agentcore.CfnRuntime.AgentRuntimeArtifactProperty(
                container_configuration=agentcore.CfnRuntime.ContainerConfigurationProperty(
                    container_uri=self.agent_image.image_uri
                )
            ),
            network_configuration=agentcore.CfnRuntime.NetworkConfigurationProperty(
                network_mode="PUBLIC"
            ),
            # Plain string in CloudFormation (MCP | HTTP | A2A | AGUI), not a struct.
            protocol_configuration="HTTP",
            # Forward the caller's JWT into the container so the agent derives the
            # advisor identity from a verified token instead of a client-supplied
            # advisor_id (which would let one advisor impersonate another).
            request_header_configuration=agentcore.CfnRuntime.RequestHeaderConfigurationProperty(
                request_header_allowlist=["Authorization"]
            ),
            # Cognito JWT authentication for runtime access (React SPA users)
            authorizer_configuration=agentcore.CfnRuntime.AuthorizerConfigurationProperty(
                custom_jwt_authorizer=agentcore.CfnRuntime.CustomJWTAuthorizerConfigurationProperty(
                    discovery_url=f"https://cognito-idp.{self.region}.amazonaws.com/{auth_stack.user_pool.user_pool_id}/.well-known/openid-configuration",
                    allowed_clients=[auth_stack.app_client.user_pool_client_id],
                )
            ),
            environment_variables={
                "AWS_REGION": self.region,
                "AGENTCORE_GATEWAY_URL": self.gateway_url,
                # AgentCore Identity provider name for runtime-to-gateway M2M auth
                # The SDK's @requires_access_token decorator resolves this name via the Token Vault
                "GATEWAY_CREDENTIAL_PROVIDER_NAME": "insurance-advisor-runtime-gateway-auth",
                "USER_POOL_ID": auth_stack.user_pool.user_pool_id,
                # Memory ID for AgentCore Memory integration
                "BEDROCK_AGENTCORE_MEMORY_ID": self.ltm_memory.attr_memory_id,
                # Bedrock Guardrail. The runtime resolves the
                # current version at cold start by reading the SSM
                # parameter named in BEDROCK_GUARDRAIL_VERSION_PARAM_NAME
                # so guardrail policy updates don't require a runtime
                # redeploy.
                "BEDROCK_GUARDRAIL_ID": self.guardrail.attr_guardrail_id,
                "BEDROCK_GUARDRAIL_VERSION_PARAM_NAME": self.guardrail_version_param_name,
            },
        )

        # Grant runtime role explicit permission to invoke the gateway
        self.runtime_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "bedrock-agentcore:InvokeGateway",
                    "bedrock-agentcore:GetGateway",
                    "bedrock-agentcore:ListGatewayTargets"
                ],
                resources=[
                    f"arn:aws:bedrock-agentcore:{self.region}:{self.account}:gateway/{self.gateway_id}",
                    f"arn:aws:bedrock-agentcore:{self.region}:{self.account}:gateway/{self.gateway_id}/*"
                ]
            )
        )

        # Grant runtime role permissions needed for AgentCore Identity Token Vault access
        # The SDK's @requires_access_token decorator calls these APIs internally:
        # - GetOauth2CredentialProvider: resolves provider name to Token Vault configuration
        # - GetResourceOauth2Token: retrieves cached M2M token or triggers fresh Cognito token issuance
        # - GetWorkloadAccessToken: resolves the runtime's workload identity for token scoping
        self.runtime_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "bedrock-agentcore:GetOauth2CredentialProvider",
                ],
                resources=[
                    f"arn:aws:bedrock-agentcore:{self.region}:{self.account}:token-vault/default/oauth2credentialprovider/*"
                ]
            )
        )

        self.runtime_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "bedrock-agentcore:GetResourceOauth2Token",
                    "bedrock-agentcore:GetWorkloadAccessToken",
                ],
                resources=[
                    f"arn:aws:bedrock-agentcore:{self.region}:{self.account}:token-vault/default",
                    f"arn:aws:bedrock-agentcore:{self.region}:{self.account}:token-vault/default/*",
                    f"arn:aws:bedrock-agentcore:{self.region}:{self.account}:workload-identity-directory/default",
                    f"arn:aws:bedrock-agentcore:{self.region}:{self.account}:workload-identity-directory/default/workload-identity/*",
                ]
            )
        )

        # IAM delegation for AgentCore Identity to read the client_secret on behalf of the runtime.
        # When the Token Vault needs a fresh token (cache miss), it reads the Identity-managed
        # secret using the runtime's role rather than its own service role. This prevents
        # privilege escalation via the Token Vault.
        self.runtime_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["secretsmanager:GetSecretValue"],
                resources=[
                    f"arn:aws:secretsmanager:{self.region}:{self.account}:secret:bedrock-agentcore-identity!default/oauth2/*"
                ]
            )
        )

        # Grant runtime role permission to use AgentCore Memory
        self.runtime_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "bedrock-agentcore:RetrieveMemory",
                    "bedrock-agentcore:CreateMemoryEvent",
                    "bedrock-agentcore:GetMemory"
                ],
                resources=[
                    f"arn:aws:bedrock-agentcore:{self.region}:{self.account}:memory/{self.ltm_memory.attr_memory_id}"
                ]
            )
        )

        # Store infrastructure variables in SSM Parameters
        ssm.StringParameter(
            self, "AgentCoreRuntimeArnParam",
            parameter_name="/insurance-advisor/agentcore/runtime-arn",
            string_value=self.agentcore_runtime.attr_agent_runtime_arn,
            description="AgentCore Runtime ARN for Insurance Advisor agent"
        )

        ssm.StringParameter(
            self, "AgentCoreRuntimeIdParam",
            parameter_name="/insurance-advisor/agentcore/runtime-id",
            string_value=self.agentcore_runtime.attr_agent_runtime_id,
            description="AgentCore Runtime ID"
        )

        ssm.StringParameter(
            self, "AgentCoreGatewayUrlParam",
            parameter_name="/insurance-advisor/agentcore/gateway-url",
            string_value=self.gateway_url,
            description="AgentCore Gateway MCP URL"
        )

        ssm.StringParameter(
            self, "AgentCoreGatewayIdParam",
            parameter_name="/insurance-advisor/agentcore/gateway-id",
            string_value=self.gateway_id,
            description="AgentCore Gateway ID"
        )

        # Store OAuth credentials for gateway-to-api authentication (machine-to-machine)
        ssm.StringParameter(
            self, "GatewayApiOAuthClientIdParam",
            parameter_name="/insurance-advisor/agentcore/gateway-oauth-client-id",
            string_value=auth_stack.gateway_client.user_pool_client_id,
            description="OAuth Client ID for AgentCore Gateway to API Gateway authentication"
        )

        ssm.StringParameter(
            self, "GatewayApiOAuthTokenEndpointParam",
            parameter_name="/insurance-advisor/agentcore/gateway-oauth-token-endpoint",
            string_value=f"https://{auth_stack.user_pool_domain.domain_name}.auth.{self.region}.amazoncognito.com/oauth2/token",
            description="OAuth Token Endpoint for AgentCore Gateway to API Gateway authentication"
        )

        # Memory ID parameter for external access
        ssm.StringParameter(
            self, "LTMMemoryIdParam",
            parameter_name="/insurance-advisor/agentcore/ltm-memory-id",
            string_value=self.ltm_memory.attr_memory_id,
            description="Long-term Memory ID for Insurance Advisor agent"
        )

        # Outputs (keep for backwards compatibility)
        CfnOutput(
            self, "AgentCoreGatewayId",
            value=self.gateway_id,
            description="AgentCore Gateway ID"
        )

        CfnOutput(
            self, "AgentCoreGatewayUrl",
            value=self.gateway_url,
            description="AgentCore Gateway MCP URL"
        )

        CfnOutput(
            self, "AgentImageUri",
            value=self.agent_image.image_uri,
            description="Docker image URI for agent container"
        )

        CfnOutput(
            self, "LTMMemoryId", 
            value=self.ltm_memory.attr_memory_id,
            description="Long-term Memory ID for Insurance Advisor agent"
        )

        CfnOutput(
            self, "AgentCoreRuntimeArn",
            value=self.agentcore_runtime.attr_agent_runtime_arn,
            description="AgentCore Runtime ARN for Insurance Advisor agent"
        )

        CfnOutput(
            self, "AgentCoreRuntimeId",
            value=self.agentcore_runtime.attr_agent_runtime_id,
            description="AgentCore Runtime ID"
        )
    
