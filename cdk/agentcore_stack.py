import json
from aws_cdk import (
    Stack,
    aws_iam as iam,
    aws_bedrock as bedrock,
    aws_bedrockagentcore as agentcore,
    aws_bedrock_agentcore_alpha as agentcore_alpha,
    aws_ecr_assets as ecr_assets,
    aws_ssm as ssm,
    RemovalPolicy,
    CfnOutput,
)
from constructs import Construct
from .auth_stack import AuthStack
from .tools_stack import ToolsStack
from .agentcore_runtime_custom import (
    AgentCoreRuntimeCustom,
    AgentCoreRuntimeCustomProps,
    create_authorizer_configuration
)
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

        # Create AgentCore Gateway using alpha module with enhanced logging and debugging
        # Gateway Pool is used for machine-to-machine authentication (Runtime → Gateway)
        self.agentcore_gateway = agentcore_alpha.Gateway(
            self, "InsuranceAdvisorGateway",
            gateway_name="insurance-advisor-gateway",
            description="AgentCore Gateway for Insurance Advisor services with enhanced logging",
            # Use Cognito JWT authorization with Gateway Pool for machine-to-machine auth
            authorizer_configuration=agentcore_alpha.GatewayAuthorizer.using_cognito(
                user_pool=auth_stack.gateway_pool,
                allowed_clients=[auth_stack.runtime_client]
            ),
            role=self.gateway_role,
            # Enable debug-level exception messages for detailed troubleshooting
            exception_level=agentcore_alpha.GatewayExceptionLevel.DEBUG
        )

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

        # Create Gateway Targets using alpha module
        # API Gateway Target with AgentCore OAuth credentials
        self.api_gateway_target = self.agentcore_gateway.add_open_api_target(
            "ApiGatewayTarget",
            gateway_target_name="InsuranceAdvisorApiService",
            description="Insurance Advisor API Gateway service providing profile and policy management",
            api_schema=agentcore_alpha.InlineApiSchema(json.dumps(openapi_spec)),
            credential_provider_configurations=[
                agentcore_alpha.GatewayCredentialProvider.from_oauth_identity_arn(
                    provider_arn=self.oauth_provider.provider_arn,
                    scopes=["insurance-advisor-api/api.access"],
                    secret_arn=self.oauth_provider.secret_arn
                )
            ]
        )

        # Portfolio Target with IAM credentials
        self.portfolio_target = self.agentcore_gateway.add_lambda_target(
            "PortfolioTarget", 
            gateway_target_name="PortfolioService",
            description="Customer portfolio information service - returns all portfolio data from S3",
            lambda_function=tools_stack.portfolio_lambda,
            tool_schema=agentcore_alpha.InlineToolSchema([
                agentcore_alpha.ToolDefinition(
                    name="get_portfolio",
                    description="Get all customer portfolio information and insurance products data",
                    input_schema=agentcore_alpha.SchemaDefinition(
                        type=agentcore_alpha.SchemaDefinitionType.OBJECT,
                        properties={}
                    )
                )
            ]),
            credential_provider_configurations=[
                agentcore_alpha.GatewayCredentialProvider.from_iam_role()
            ]
        )

        # Promotions Target with IAM credentials  
        self.promotions_target = self.agentcore_gateway.add_lambda_target(
            "PromotionsTarget",
            gateway_target_name="PromotionsService", 
            description="Insurance promotions and offers service - returns all promotion data from S3",
            lambda_function=tools_stack.promotions_lambda,
            tool_schema=agentcore_alpha.InlineToolSchema([
                agentcore_alpha.ToolDefinition(
                    name="get_promotions",
                    description="Get all available insurance promotions and special offers",
                    input_schema=agentcore_alpha.SchemaDefinition(
                        type=agentcore_alpha.SchemaDefinitionType.OBJECT,
                        properties={}
                    )
                )
            ]),
            credential_provider_configurations=[
                agentcore_alpha.GatewayCredentialProvider.from_iam_role()
            ]
        )

        # Company Info Target with IAM credentials
        self.company_target = self.agentcore_gateway.add_lambda_target(
            "CompanyTarget",
            gateway_target_name="CompanyInfoService",
            description="Information about Unicorn Insurance — history, ratings, claims process, customer service",
            lambda_function=tools_stack.company_lambda,
            tool_schema=agentcore_alpha.InlineToolSchema([
                agentcore_alpha.ToolDefinition(
                    name="get_company_info",
                    description=(
                        "Retrieve factual information about Unicorn Insurance: company overview, " +
                        "regulatory credentials and financial ratings, customer service channels, " +
                        "claims process, and digital platform capabilities. Use when the customer " +
                        "or prospect asks about the company itself rather than specific products."
                    ),
                    input_schema=agentcore_alpha.SchemaDefinition(
                        type=agentcore_alpha.SchemaDefinitionType.OBJECT,
                        properties={}
                    )
                )
            ]),
            credential_provider_configurations=[
                agentcore_alpha.GatewayCredentialProvider.from_iam_role()
            ]
        )

        # Competitive Info Target with IAM credentials
        self.competitive_target = self.agentcore_gateway.add_lambda_target(
            "CompetitiveTarget",
            gateway_target_name="CompetitiveInfoService",
            description="Unicorn Insurance's competitive positioning and advantages",
            lambda_function=tools_stack.competitive_lambda,
            tool_schema=agentcore_alpha.InlineToolSchema([
                agentcore_alpha.ToolDefinition(
                    name="get_competitive_info",
                    description=(
                        "Retrieve talking points about why Unicorn Insurance is a better choice than " +
                        "competitors — value propositions, advantages by product line, service standards, " +
                        "and multi-policy bundle benefits. Use when the advisor needs to position " +
                        "Unicorn Insurance against alternatives during a sales conversation."
                    ),
                    input_schema=agentcore_alpha.SchemaDefinition(
                        type=agentcore_alpha.SchemaDefinitionType.OBJECT,
                        properties={}
                    )
                )
            ]),
            credential_provider_configurations=[
                agentcore_alpha.GatewayCredentialProvider.from_iam_role()
            ]
        )

        # Competitor Products Target with IAM credentials
        self.competitors_target = self.agentcore_gateway.add_lambda_target(
            "CompetitorsTarget",
            gateway_target_name="CompetitorProductsService",
            description="Reference information about competitor insurance products for comparison",
            lambda_function=tools_stack.competitors_lambda,
            tool_schema=agentcore_alpha.InlineToolSchema([
                agentcore_alpha.ToolDefinition(
                    name="get_competitor_products",
                    description=(
                        "Retrieve reference information about competitor insurance products " +
                        "(BigRival, StarInsure, QuickSafe) including their coverage, strengths, " +
                        "weaknesses, and pricing tier. Use when the customer mentions a specific " +
                        "competitor by name or asks how Unicorn products compare to alternatives. " +
                        "Always pair with get_competitive_info to frame the comparison favorably."
                    ),
                    input_schema=agentcore_alpha.SchemaDefinition(
                        type=agentcore_alpha.SchemaDefinitionType.OBJECT,
                        properties={}
                    )
                )
            ]),
            credential_provider_configurations=[
                agentcore_alpha.GatewayCredentialProvider.from_iam_role()
            ]
        )

        # Document Extraction Target — invoked when the advisor uploads an
        # insurance-policy PDF / image / markdown via the SPA's 📎 button.
        # The tool reads the document from S3 and returns a structured JSON
        # extraction. Agent then confirms with the user and calls
        # create_third_party_policy (and create_profile if no customer is
        # selected yet).
        self.extract_policy_target = self.agentcore_gateway.add_lambda_target(
            "ExtractPolicyTarget",
            gateway_target_name="DocumentExtractionService",
            description="Extract structured insurance-policy fields from an uploaded document",
            lambda_function=tools_stack.extract_policy_lambda,
            tool_schema=agentcore_alpha.InlineToolSchema([
                agentcore_alpha.ToolDefinition(
                    name="extract_policy_from_document",
                    description=(
                        "Extract structured insurance-policy fields (carrier, " +
                        "type, coverage amount, premium, dates, beneficiary) " +
                        "from a document the advisor uploaded via the SPA. " +
                        "Use this tool when the user references an attached " +
                        "document by document_id (e.g. 'create a third-party " +
                        "policy from the PDF I just attached'). After extraction, " +
                        "show the extracted fields to the user, ask for " +
                        "confirmation, and ONLY THEN call " +
                        "create_third_party_policy. If the user is in '+ New " +
                        "Prospect' mode (no customer_id selected), call " +
                        "create_profile FIRST using suggested_profile_fields, " +
                        "capture the new customer_id, then create the policy."
                    ),
                    input_schema=agentcore_alpha.SchemaDefinition(
                        type=agentcore_alpha.SchemaDefinitionType.OBJECT,
                        properties={
                            "document_id": agentcore_alpha.SchemaDefinition(
                                type=agentcore_alpha.SchemaDefinitionType.STRING,
                                description="The document_id returned by /documents/initiate when the file was uploaded.",
                            ),
                            "customer_id": agentcore_alpha.SchemaDefinition(
                                type=agentcore_alpha.SchemaDefinitionType.STRING,
                                description="Customer ID the document is being attached to. Pass null/omit for '+ New Prospect' mode.",
                            ),
                            "advisor_id": agentcore_alpha.SchemaDefinition(
                                type=agentcore_alpha.SchemaDefinitionType.STRING,
                                description="Advisor email (the calling user). Required for S3 namespace scoping.",
                            ),
                        },
                    ),
                )
            ]),
            credential_provider_configurations=[
                agentcore_alpha.GatewayCredentialProvider.from_iam_role()
            ]
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
                                f"arn:aws:logs:{self.region}:{self.account}:log-group:/aws/bedrock-agentcore/runtimes/insurance_advisor_runtime*"
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

        # AgentCore Runtime using Custom Resource with Cognito JWT authentication for runtime access
        # Runtime will use OAuth client credentials to authenticate to Gateway
        self.agentcore_runtime = AgentCoreRuntimeCustom(
            self, "InsuranceAdvisorRuntime",
            AgentCoreRuntimeCustomProps(
                execution_role=self.runtime_role,
                runtime_name="insurance_advisor_runtime",
                container_uri=self.agent_image.image_uri,
                server_protocol="HTTP",
                description="AgentCore Runtime for Insurance Advisor Agent with gateway integration and OAuth JWT authentication",
                # Enable Authorization header forwarding for JWT tokens
                allowed_headers=["Authorization"],
                # Cognito JWT authentication configuration for runtime access (React SPA users)
                authorizer_configuration=create_authorizer_configuration(
                    discovery_url=f"https://cognito-idp.{self.region}.amazonaws.com/{auth_stack.user_pool.user_pool_id}/.well-known/openid-configuration",
                    allowed_clients=[auth_stack.app_client.user_pool_client_id]
                ),
                environment_variables={
                    "AWS_REGION": self.region,
                    "AGENTCORE_GATEWAY_URL": self.agentcore_gateway.gateway_url,
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
                network_mode="PUBLIC"
            )
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
                    f"arn:aws:bedrock-agentcore:{self.region}:{self.account}:gateway/{self.agentcore_gateway.gateway_id}",
                    f"arn:aws:bedrock-agentcore:{self.region}:{self.account}:gateway/{self.agentcore_gateway.gateway_id}/*"
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
            string_value=self.agentcore_runtime.agent_runtime_arn,
            description="AgentCore Runtime ARN for Insurance Advisor agent"
        )

        ssm.StringParameter(
            self, "AgentCoreRuntimeIdParam",
            parameter_name="/insurance-advisor/agentcore/runtime-id",
            string_value=self.agentcore_runtime.agent_runtime_id,
            description="AgentCore Runtime ID"
        )

        ssm.StringParameter(
            self, "AgentCoreGatewayUrlParam",
            parameter_name="/insurance-advisor/agentcore/gateway-url",
            string_value=self.agentcore_gateway.gateway_url,
            description="AgentCore Gateway MCP URL"
        )

        ssm.StringParameter(
            self, "AgentCoreGatewayIdParam",
            parameter_name="/insurance-advisor/agentcore/gateway-id",
            string_value=self.agentcore_gateway.gateway_id,
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
            value=self.agentcore_gateway.gateway_id,
            description="AgentCore Gateway ID"
        )

        CfnOutput(
            self, "AgentCoreGatewayUrl",
            value=self.agentcore_gateway.gateway_url,
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
            value=self.agentcore_runtime.agent_runtime_arn,
            description="AgentCore Runtime ARN for Insurance Advisor agent"
        )

        CfnOutput(
            self, "AgentCoreRuntimeId",
            value=self.agentcore_runtime.agent_runtime_id,
            description="AgentCore Runtime ID"
        )
    
