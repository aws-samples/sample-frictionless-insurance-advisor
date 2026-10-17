import json
from aws_cdk import (
    Stack,
    aws_iam as iam,
    aws_bedrock as bedrock,
    aws_bedrockagentcore as agentcore,
    aws_ecr_assets as ecr_assets,
    aws_lambda as _lambda,
    aws_logs as logs,
    aws_ssm as ssm,
    Duration,
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

        # Gateway request interceptor: ties every tool call to the signed-in advisor.
        #
        # The assistants reach the gateway with a machine token that cannot say which
        # advisor is being served. The runtimes therefore also send the advisor's own
        # access token (header X-Advisor-Token). This function, which the gateway runs
        # before every call, verifies that token with Cognito, refuses the call if there
        # is none, swaps the token into the call to the REST API, and overwrites
        # `advisor_id` for Lambda tools that take it. See lambda/gateway_interceptor.
        # TARGET_MODES (which target gets which treatment) is filled in below, from the
        # same target definitions, so the two cannot drift apart.
        self.interceptor_lambda = _lambda.Function(
            self, "GatewayInterceptorLambda",
            runtime=_lambda.Runtime.PYTHON_3_13,
            handler="index.handler",
            code=_lambda.Code.from_asset("lambda/gateway_interceptor"),
            environment={
                "USER_POOL_ID": auth_stack.user_pool.user_pool_id,
                "APP_CLIENT_ID": auth_stack.app_client.user_pool_client_id,
            },
            timeout=Duration.seconds(10),
            log_group=logs.LogGroup(
                self, "GatewayInterceptorLambdaLogGroup",
                retention=logs.RetentionDays.ONE_MONTH,
                removal_policy=RemovalPolicy.DESTROY,
            ),
        )
        # {gateway target name: how the interceptor treats its calls}
        interceptor_modes: dict[str, str] = {}

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
                                # The request interceptor. The profile and policies
                                # Lambdas are reached through the REST API, never
                                # invoked by the gateway directly, so they are not here.
                                self.interceptor_lambda.function_arn,
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
            # Runs before every tool call. passRequestHeaders is required: the
            # advisor's token arrives in a header. The function never logs it.
            interceptor_configurations=[
                agentcore.CfnGateway.GatewayInterceptorConfigurationProperty(
                    interception_points=["REQUEST"],
                    interceptor=agentcore.CfnGateway.InterceptorConfigurationProperty(
                        lambda_=agentcore.CfnGateway.LambdaInterceptorConfigurationProperty(
                            arn=self.interceptor_lambda.function_arn,
                        )
                    ),
                    input_configuration=agentcore.CfnGateway.InterceptorInputConfigurationProperty(
                        pass_request_headers=True,
                    ),
                )
            ],
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
                                            "gender": {"type": "string", "enum": ["male", "female"], "description": "Gender, male or female. Needed for life-insurance rates and application forms. Ask the advisor; never work it out from a name."},
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
                                            "gender": {"type": "string", "enum": ["male", "female"], "description": "Gender, male or female. Needed for life-insurance rates and application forms. Ask the advisor; never work it out from a name."},
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
                # Only POST is exposed to the agents. DELETE /profile/notes/{note_id}
                # exists on API Gateway but is deliberately left out of this
                # spec (and restricted to the advisor's sign-in scope), so the
                # assistant can add notes but never remove them.
                "/profile/notes": {
                    "post": {
                        "operationId": "add_note",
                        "summary": "Add a note to a customer or prospect profile",
                        "description": (
                            "Save a note on a customer's profile, but ONLY when the advisor's "
                            "message explicitly instructs you to add, save, record or note "
                            "something (for example 'add a note that...'). Do NOT call this "
                            "when the advisor merely tells you something about the customer "
                            "(for example 'Lisa told me she is moving'): answer, then offer to "
                            "save it. A note is a life event (marriage, new baby, house move, "
                            "job change) or an interaction (call, meeting, request, "
                            "follow-up). Notes cannot be edited or deleted through this tool."
                        ),
                        "requestBody": {
                            "required": True,
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "required": ["customer_id", "type", "text"],
                                        "properties": {
                                            "customer_id": {"type": "string", "description": "Customer ID the note belongs to (required)"},
                                            "type": {
                                                "type": "string",
                                                "enum": ["life_event", "interaction"],
                                                "description": "life_event for something that happened or will happen in the customer's life; interaction for a contact with the customer"
                                            },
                                            "text": {"type": "string", "description": "The note, in the advisor's words, up to 1,000 characters"},
                                            "event_date": {"type": "string", "description": "Optional date of the event (YYYY-MM-DD), past or planned"}
                                        }
                                    }
                                }
                            }
                        },
                        "responses": {
                            "201": {
                                "description": "Note added",
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object"
                                        }
                                    }
                                }
                            },
                            "400": {
                                "description": "Bad request - missing or invalid fields"
                            },
                            "401": {
                                "description": "Unauthorized"
                            },
                            "403": {
                                "description": "Forbidden - profile belongs to another advisor"
                            },
                            "404": {
                                "description": "Profile not found"
                            },
                            "409": {
                                "description": "The profile already has the maximum number of notes"
                            }
                        },
                        "security": [
                            {
                                "CognitoAuth": []
                            }
                        ]
                    }
                },
                "/priorities": {
                    "get": {
                        "operationId": "get_priorities",
                        "summary": "Get the advisor's priorities for today",
                        "description": "Returns the advisor's customers that most need attention today, most urgent first, each with the reason: profile review overdue (over 90 days), a life event coming up in the next 60 days or just passed with no follow-up, a policy renewing in the next 60 days (any insurer) or recently past its renewal date, yearly premiums above 10% of income, or a prospect with no contact for 30 days. Use it when the advisor asks what to do today, who to call, or which customers need attention. The ranking is computed by the service; report it as given.",
                        "parameters": [
                            {
                                "name": "limit",
                                "in": "query",
                                "required": False,
                                "schema": {
                                    "type": "integer"
                                },
                                "description": "Optional. How many customers to return (default 8, at most 25)."
                            }
                        ],
                        "responses": {
                            "200": {
                                "description": "Ranked priorities",
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
                    }
                },
                "/policy": {
                    "get": {
                        "operationId": "get_policy",
                        "summary": "Get insurance policy by customer ID or list all policies",
                        "description": "Retrieve insurance policy information by customer id. If no customer ID is provided, returns all policies for the authenticated advisor. Returned policies may be Awesome-issued or third-party (third_party=true with an insurer name). Each policy includes annual_premium (yearly cost) and days_until_renewal. Pass include_totals=true together with customer_id to get {policies, totals}: totals hold the yearly premium totals (own and other insurers), the renewals in date order and the share of income against the 10% guide, all computed by the service. Use those totals; never add premiums yourself.",
                        "parameters": [
                            {
                                "name": "customer_id",
                                "in": "query",
                                "required": False,
                                "schema": {
                                    "type": "string"
                                },
                                "description": "Customer ID (optional - filters policies for specific customer)"
                            },
                            {
                                "name": "include_totals",
                                "in": "query",
                                "required": False,
                                "schema": {
                                    "type": "boolean"
                                },
                                "description": "Optional. With customer_id, returns {policies, totals} with yearly premium totals, renewals and the affordability share computed by the service."
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
                        "description": "Create a record of a policy the customer holds with another insurance carrier. ONLY for third-party policies — Awesome-issued policies are read-only. The server forces third_party=true on the new record. Required: customer_id, type, insurer.",
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
                        "description": "Update fields on a third-party policy the customer already has on record. ONLY works on policies where third_party=true; Awesome-issued policies remain read-only.",
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
                            "403": {"description": "Forbidden - Awesome policy or different advisor"},
                            "404": {"description": "Policy not found"}
                        },
                        "security": [{"CognitoAuth": []}]
                    },
                    "delete": {
                        "operationId": "delete_third_party_policy",
                        "summary": "Delete a third-party insurance policy",
                        "description": "Delete a third-party policy record. ONLY works on policies where third_party=true; Awesome-issued policies cannot be deleted via this endpoint.",
                        "parameters": [
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
                            "403": {"description": "Forbidden - Awesome policy or different advisor"},
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
            # The interceptor adds this header to tell the Lambdas the call came through
            # the assistant. Propagation is by allowlist, so it has to be named here.
            metadata_configuration=agentcore.CfnGatewayTarget.MetadataConfigurationProperty(
                allowed_request_headers=["X-Advisor-Via-Gateway"],
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

        # The API takes no machine token on any route. The interceptor replaces the
        # token this OAuth provider fetches with the advisor's own, so the call is made
        # as the signed-in advisor. The provider stays as the target's outbound
        # credential; should the interceptor ever not replace it, API Gateway refuses
        # the machine token's scope, so the call fails rather than running unattributed.
        interceptor_modes["InsuranceAdvisorApiService"] = "forward_token"
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
            # A tool that takes `advisor_id` gets it from the interceptor, never from the model.
            if any("advisor_id" in (getattr(getattr(t, "input_schema", None), "properties", None) or {}) for t in tools):
                interceptor_modes[name] = "set_argument"
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
            description="Information about Awesome Insurance — history, ratings, claims process, customer service",
            lambda_function=tools_stack.company_lambda,
            tools=[_no_arg_tool(
                "get_company_info",
                "Retrieve factual information about Awesome Insurance: company overview, "
                "regulatory credentials and financial ratings, customer service channels, "
                "claims process, and digital platform capabilities. Use when the customer "
                "or prospect asks about the company itself rather than specific products.",
            )],
        )

        # Competitive Info Target with IAM credentials
        self.competitive_target = _lambda_target(
            "CompetitiveTarget",
            name="CompetitiveInfoService",
            description="Awesome Insurance's competitive positioning and advantages",
            lambda_function=tools_stack.competitive_lambda,
            tools=[_no_arg_tool(
                "get_competitive_info",
                "Retrieve talking points about why Awesome Insurance is a better choice than "
                "competitors — value propositions, advantages by product line, service standards, "
                "and multi-policy bundle benefits. Use when the advisor needs to position "
                "Awesome Insurance against alternatives during a sales conversation.",
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
                "(BigRival, Brookmere, QuickSafe) including their coverage, strengths, "
                "weaknesses, and pricing tier. Use when the customer mentions a specific "
                "competitor by name or asks how Awesome products compare to alternatives. "
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
                            "Set automatically from the signed-in advisor. You do not need to "
                            "fill it in: whatever is sent here is replaced by the gateway."
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
            description="Application-form schemas for Awesome Insurance products",
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
                            "'universal_life', 'variable_life', "
                            "'critical_illness', 'health', 'home', 'auto', "
                            "'travel', 'disability'."
                        ),
                    },
                ),
            )],
        )

        # Pricing targets. Rate tables live in their own bucket and are read
        # only by these two tools, which do the arithmetic in code, so the
        # model never multiplies a rate itself. Both agents get them: estimates
        # are indicative, labelled as such, and never a quote.
        self.pricing_table_target = _lambda_target(
            "PricingTableTarget",
            name="PricingTableService",
            description="Indicative premium rate tables for Awesome Insurance products",
            lambda_function=tools_stack.pricing_table_lambda,
            tools=[agentcore.CfnGatewayTarget.ToolDefinitionProperty(
                name="get_pricing_table",
                description=(
                    "List the products that have indicative premium rates and what "
                    "each needs to be priced, or (with product_type) return one "
                    "product's full rate file: its options with the allowed values, "
                    "the rate tables and the rating factors. Also returns the "
                    "affordability rule (total annual premium within 10% of income). "
                    "Use this to see which options estimate_premium accepts, or to "
                    "explain how a price is built. To price a configuration, call "
                    "estimate_premium; do not work out premiums from the tables yourself."
                ),
                input_schema=agentcore.CfnGatewayTarget.SchemaDefinitionProperty(
                    type="object",
                    properties={
                        "product_type": _string_field(
                            "Optional. One of term_life, whole_life, universal_life, "
                            "variable_life, critical_illness, health, home, auto, travel, "
                            "disability. Omit to list all products."
                        ),
                    },
                ),
            )],
        )
        self.pricing_estimate_target = _lambda_target(
            "PricingEstimateTarget",
            name="PremiumEstimateService",
            description="Indicative premium estimate and affordability check",
            lambda_function=tools_stack.pricing_estimate_lambda,
            tools=[agentcore.CfnGatewayTarget.ToolDefinitionProperty(
                name="estimate_premium",
                description=(
                    "Work out an INDICATIVE annual premium for one Awesome product "
                    "configuration, and, when customer_id is given, "
                    "check the customer's total annual premium (all active policies, "
                    "including other insurers', plus this one) against the 10% of "
                    "income affordability guide. Use it whenever the advisor asks what "
                    "a cover would cost or whether the customer can afford it. "
                    "Age and smoker status are taken from the customer's profile; the "
                    "other options go in `options`, a JSON object whose names and "
                    "allowed values come from get_pricing_table(product_type). If "
                    "details are missing the result has status needs_input and lists "
                    "them: ask the advisor, never guess. If the profile has no income "
                    "the affordability status is income_unknown: ask the advisor. "
                    "The result is an estimate, not a quote: always say so, and say "
                    "the final premium is set by Awesome after the application is "
                    "assessed."
                ),
                input_schema=agentcore.CfnGatewayTarget.SchemaDefinitionProperty(
                    type="object",
                    properties={
                        "product_type": _string_field(
                            "One of term_life, whole_life, universal_life, variable_life, "
                            "critical_illness, health, home, auto, travel, disability."
                        ),
                        "options": _string_field(
                            "JSON object text with the product's options, for example "
                            "'{\"sum_assured\": 500000, \"term_years\": \"20\", \"gender\": \"female\"}'. "
                            "Option names and allowed values: see get_pricing_table. "
                            "Also accepts annual_income and other_annual_premiums for "
                            "prospects with no profile."
                        ),
                        "customer_id": _string_field(
                            "Customer ID, to read age and smoker status and to run the affordability check. Omit for a prospect with no profile."
                        ),
                        "advisor_id": _string_field(
                            "Set automatically from the signed-in advisor. You do not need to "
                            "fill it in: whatever is sent here is replaced by the gateway."
                        ),
                    },
                    required=["product_type"],
                ),
            )],
        )
        # Savings-gap calculators. Both scenarios share one tool so there is a single
        # Lambda; the arithmetic runs in code and the answer lists its assumptions.
        self.calculators_target = _lambda_target(
            "CalculatorsTarget",
            name="CalculatorService",
            description="Education and retirement savings-gap calculators",
            lambda_function=tools_stack.calculators_lambda,
            tools=[agentcore.CfnGatewayTarget.ToolDefinitionProperty(
                name="calculate_savings_gap",
                description=(
                    "Work out an ILLUSTRATIVE savings gap. scenario 'education': what a "
                    "child's studies will cost and how much more to save. scenario "
                    "'retirement': the fund needed to pay a wanted income in retirement "
                    "and how much more to save. All maths is done by the service; report "
                    "its numbers and never calculate growth yourself. Required inputs: "
                    "education needs child_age and annual_cost_today; retirement needs "
                    "current_age and annual_income_today. If something is missing the "
                    "result has status needs_input: ask the advisor, never guess. "
                    "Assumptions (inflation, returns, ages) have defaults and are listed "
                    "in the result with their source; the advisor may override them. "
                    "The result is an illustration, not a forecast, quote or guarantee."
                ),
                input_schema=agentcore.CfnGatewayTarget.SchemaDefinitionProperty(
                    type="object",
                    properties={
                        "scenario": _string_field("Either 'education' or 'retirement'."),
                        "inputs": _string_field(
                            "JSON object text. education: child_age, annual_cost_today; optional "
                            "current_savings, monthly_saving, start_age, years_of_study, "
                            "education_cost_inflation_percent, investment_return_percent. "
                            "retirement: current_age, annual_income_today; optional "
                            "other_annual_income_today, current_savings, monthly_saving, "
                            "retirement_age, life_expectancy, income_replacement_percent, "
                            "inflation_percent, return_before_retirement_percent, "
                            "return_in_retirement_percent. Example: "
                            "'{\"child_age\": 6, \"annual_cost_today\": 25000}'."
                        ),
                    },
                    required=["scenario", "inputs"],
                ),
            )],
        )
        # Web Search connector (AWS-managed, no Lambda). The gateway reaches
        # the AWS-run web index with its own role, so there is no API key to
        # hold. The tool shows up in tools/list as
        # `WebSearchService___WebSearch` (target name + connector
        # configuration name). The text agent uses it for general insurance
        # questions only; the voice agent drops it at start-up (see
        # voice-agent/app.py) and the system prompt keeps customer details
        # out of queries.
        #
        # Domains: the open web minus a short list of social and Q&A sites,
        # set on the target so neither agent can widen it. CloudFormation's
        # ConnectorSource has no `version` property, so the target takes the
        # connector's default version (1.2.0 when this was deployed; checked
        # against a live target). Its target-level exclude list is used here.
        # 1.2.0 also lets the model pass per-request filters; those can only
        # narrow results, and the system prompt tells the model not to.
        self.web_search_target = agentcore.CfnGatewayTarget(
            _target_scope("WebSearchTarget"), "Resource",
            name="WebSearchService",
            description="Web search for general insurance questions (not for Awesome products or customers)",
            gateway_identifier=self.gateway_id,
            target_configuration=agentcore.CfnGatewayTarget.TargetConfigurationProperty(
                mcp=agentcore.CfnGatewayTarget.McpTargetConfigurationProperty(
                    connector=agentcore.CfnGatewayTarget.ConnectorTargetConfigurationProperty(
                        source=agentcore.CfnGatewayTarget.ConnectorSourceProperty(
                            connector_id="web-search"
                        ),
                        configurations=[
                            agentcore.CfnGatewayTarget.ConnectorConfigurationProperty(
                                name="WebSearch",
                                parameter_values={
                                    "domainFilter": {
                                        "exclude": [
                                            "reddit.com",
                                            "quora.com",
                                            "facebook.com",
                                            "instagram.com",
                                            "x.com",
                                            "twitter.com",
                                            "tiktok.com",
                                            "pinterest.com",
                                        ],
                                    },
                                },
                            )
                        ],
                    )
                )
            ),
            credential_provider_configurations=[
                agentcore.CfnGatewayTarget.CredentialProviderConfigurationProperty(
                    credential_provider_type="GATEWAY_IAM_ROLE"
                )
            ],
        )
        # The connector is AWS-owned, so the resource is an AWS-account ARN
        # rather than anything in this account.
        self.gateway_role.add_to_policy(
            iam.PolicyStatement(
                sid="InvokeWebSearch",
                effect=iam.Effect.ALLOW,
                actions=["bedrock-agentcore:InvokeWebSearch"],
                resources=[
                    f"arn:aws:bedrock-agentcore:{self.region}:aws:tool/web-search.v1"
                ],
            )
        )
        self.web_search_target.node.add_dependency(
            self.gateway_role.node.find_child("DefaultPolicy")
        )
        # Tell the interceptor how to treat each target, now that all are defined.
        self.interceptor_lambda.add_environment(
            "TARGET_MODES", json.dumps(interceptor_modes, sort_keys=True)
        )
        # Guard against regressions: the REST tools identify the advisor by the token the
        # interceptor forwards. A re-added `advisor_id` parameter would invite a
        # caller-chosen identity back in, so fail the build instead.
        assert '"advisor_id"' not in json.dumps(openapi_spec), (
            "The REST API tools must not take advisor_id; the advisor is the signed-in token."
        )
        assert interceptor_modes.get("InsuranceAdvisorApiService") == "forward_token"

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
        # Re-exported for the voice stack, which is the only writer.
        self.transcripts_bucket = tools_stack.transcripts_bucket

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
                                # Claude Haiku 5.5: cross-region inference profile
                                f"arn:aws:bedrock:{self.region}:{self.account}:inference-profile/us.anthropic.claude-haiku-5-5",
                                # Underlying foundation models in the profile's destination regions
                                "arn:aws:bedrock:us-east-1::foundation-model/anthropic.claude-haiku-5-5",
                                "arn:aws:bedrock:us-east-2::foundation-model/anthropic.claude-haiku-5-5",
                                "arn:aws:bedrock:us-west-2::foundation-model/anthropic.claude-haiku-5-5",
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
                        # AgentCore (memory, token vault, gateway, workload identity):
                        # limited to this account and region.
                        iam.PolicyStatement(
                            effect=iam.Effect.ALLOW,
                            actions=["bedrock-agentcore:*"],
                            resources=[f"arn:aws:bedrock-agentcore:{self.region}:{self.account}:*"],
                        ),
                        # Pull the container image: the token call has no resource-level scope;
                        # the layer calls are limited to this account's repositories.
                        iam.PolicyStatement(
                            effect=iam.Effect.ALLOW,
                            actions=["ecr:GetAuthorizationToken"],
                            resources=["*"],
                        ),
                        iam.PolicyStatement(
                            effect=iam.Effect.ALLOW,
                            actions=[
                                "ecr:BatchCheckLayerAvailability",
                                "ecr:GetDownloadUrlForLayer",
                                "ecr:BatchGetImage",
                            ],
                            resources=[f"arn:aws:ecr:{self.region}:{self.account}:repository/*"],
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
            # Retention: 90 days. Originally 365 (the platform max).
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
                # Raise to DEBUG only while troubleshooting: DEBUG logs contain prompts and customer data.
                "AGENT_LOG_LEVEL": self.node.try_get_context("agent_log_level") or "INFO",
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
                # Form schemas are read directly by the open_application_form
                # tool (agent/form_tools.py), the same way the voice agent does.
                "FORMS_BUCKET": self.forms_bucket.bucket_name,
            },
        )
        self.forms_bucket.grant_read(self.runtime_role)

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
    
