"""Voice Stack - AgentCore Runtime with Nova Sonic 2 + full insurance tool access.

The voice runtime is now self-sufficient: it has the same MCP gateway tools,
AgentCore Memory, Cognito user lookup and Bedrock Guardrail access as the
main insurance agent, just with Nova Sonic 2 as the underlying model instead
of Claude. This removes the Claude-hop latency that the original proxy
design had.

The runtime reuses the existing AgentCore Gateway and its OAuth credential
provider from insadv-03-agentcore - no second gateway is needed.
"""
from aws_cdk import (
    CfnOutput,
    RemovalPolicy,
    Stack,
    aws_ecr_assets as ecr_assets,
    aws_iam as iam,
    aws_logs as logs,
    aws_ssm as ssm,
)
from constructs import Construct

from .agentcore_runtime_custom import (
    AgentCoreRuntimeCustom,
    AgentCoreRuntimeCustomProps,
    create_authorizer_configuration,
)
from .agentcore_stack import AgentCoreStack
from .auth_stack import AuthStack


RUNTIME_NAME = "insurance_voice_runtime"
MODEL_ID = "amazon.nova-2-sonic-v1:0"
BEDROCK_REGION = "us-east-1"


class VoiceStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        auth_stack: AuthStack,
        agentcore_stack: AgentCoreStack,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # --- Container image (builds from ../voice-agent) -------------
        voice_image = ecr_assets.DockerImageAsset(
            self,
            "VoiceAgentImage",
            directory="voice-agent",
            platform=ecr_assets.Platform.LINUX_ARM64,
        )

        # --- Execution role -------------------------------------------
        # Mirrors the insurance runtime's role with Nova Sonic + gateway
        # tool access + memory + Cognito lookup + guardrail + token vault.
        runtime_role = iam.Role(
            self,
            "VoiceRuntimeRole",
            assumed_by=iam.ServicePrincipal("bedrock-agentcore.amazonaws.com"),
            inline_policies={
                "BedrockInvokeNovaSonic": iam.PolicyDocument(
                    statements=[
                        iam.PolicyStatement(
                            effect=iam.Effect.ALLOW,
                            actions=[
                                "bedrock:InvokeModel",
                                "bedrock:InvokeModelWithResponseStream",
                                "bedrock:InvokeModelWithBidirectionalStream",
                            ],
                            resources=[
                                f"arn:aws:bedrock:{BEDROCK_REGION}::foundation-model/{MODEL_ID}",
                            ],
                        )
                    ]
                ),
                "BedrockGuardrail": iam.PolicyDocument(
                    statements=[
                        iam.PolicyStatement(
                            effect=iam.Effect.ALLOW,
                            actions=["bedrock:ApplyGuardrail", "bedrock:GetGuardrail"],
                            resources=[agentcore_stack.guardrail.attr_guardrail_arn],
                        ),
                        # Allow the voice runtime container to look up
                        # the current published guardrail version at
                        # cold start.
                        iam.PolicyStatement(
                            effect=iam.Effect.ALLOW,
                            actions=["ssm:GetParameter"],
                            resources=[agentcore_stack.guardrail_version_param.parameter_arn],
                        ),
                    ]
                ),
                "AgentCoreRuntime": iam.PolicyDocument(
                    statements=[
                        iam.PolicyStatement(
                            effect=iam.Effect.ALLOW,
                            actions=[
                                "bedrock-agentcore:*",
                                "ecr:GetAuthorizationToken",
                                "ecr:BatchCheckLayerAvailability",
                                "ecr:GetDownloadUrlForLayer",
                                "ecr:BatchGetImage",
                            ],
                            resources=["*"],
                        )
                    ]
                ),
                "CognitoUserLookup": iam.PolicyDocument(
                    statements=[
                        iam.PolicyStatement(
                            effect=iam.Effect.ALLOW,
                            actions=["cognito-idp:AdminGetUser"],
                            resources=[auth_stack.user_pool.user_pool_arn],
                        )
                    ]
                ),
                "Logging": iam.PolicyDocument(
                    statements=[
                        iam.PolicyStatement(
                            effect=iam.Effect.ALLOW,
                            actions=[
                                "logs:CreateLogGroup",
                                "logs:CreateLogStream",
                                "logs:PutLogEvents",
                                "logs:DescribeLogGroups",
                                "logs:DescribeLogStreams",
                            ],
                            resources=[
                                f"arn:aws:logs:{self.region}:{self.account}:" +
                                f"log-group:/aws/bedrock-agentcore/runtimes/{RUNTIME_NAME}*",
                            ],
                        )
                    ]
                ),
            },
        )
        runtime_role.apply_removal_policy(RemovalPolicy.DESTROY)

        # --- Extra IAM (matches the insurance runtime role) -----------
        # Gateway invoke + list.
        runtime_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "bedrock-agentcore:InvokeGateway",
                    "bedrock-agentcore:GetGateway",
                    "bedrock-agentcore:ListGatewayTargets",
                ],
                resources=[
                    f"arn:aws:bedrock-agentcore:{self.region}:{self.account}:" +
                    f"gateway/{agentcore_stack.agentcore_gateway.gateway_id}",
                    f"arn:aws:bedrock-agentcore:{self.region}:{self.account}:" +
                    f"gateway/{agentcore_stack.agentcore_gateway.gateway_id}/*",
                ],
            )
        )
        # OAuth credential provider lookup (resolves the provider name).
        runtime_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["bedrock-agentcore:GetOauth2CredentialProvider"],
                resources=[
                    f"arn:aws:bedrock-agentcore:{self.region}:{self.account}:" +
                    f"token-vault/default/oauth2credentialprovider/*"
                ],
            )
        )
        # Token vault token issuance + workload identity.
        runtime_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "bedrock-agentcore:GetResourceOauth2Token",
                    "bedrock-agentcore:GetWorkloadAccessToken",
                ],
                resources=[
                    f"arn:aws:bedrock-agentcore:{self.region}:{self.account}:token-vault/default",
                    f"arn:aws:bedrock-agentcore:{self.region}:{self.account}:token-vault/default/*",
                    f"arn:aws:bedrock-agentcore:{self.region}:{self.account}:" +
                    f"workload-identity-directory/default",
                    f"arn:aws:bedrock-agentcore:{self.region}:{self.account}:" +
                    f"workload-identity-directory/default/workload-identity/*",
                ],
            )
        )
        # IAM delegation: let the token vault read the Identity-managed
        # secret on this role's behalf when refreshing the gateway token.
        runtime_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["secretsmanager:GetSecretValue"],
                resources=[
                    f"arn:aws:secretsmanager:{self.region}:{self.account}:" +
                    f"secret:bedrock-agentcore-identity!default/oauth2/*"
                ],
            )
        )
        # AgentCore Memory read/write for the insurance advisor memory.
        runtime_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "bedrock-agentcore:RetrieveMemory",
                    "bedrock-agentcore:CreateMemoryEvent",
                    "bedrock-agentcore:GetMemory",
                ],
                resources=[
                    f"arn:aws:bedrock-agentcore:{self.region}:{self.account}:" +
                    f"memory/{agentcore_stack.ltm_memory.attr_memory_id}"
                ],
            )
        )

        # Pre-create log group for clean removal on destroy.
        log_group = logs.LogGroup(
            self,
            "VoiceRuntimeLogGroup",
            log_group_name=f"/aws/bedrock-agentcore/runtimes/{RUNTIME_NAME}-DEFAULT",
            retention=logs.RetentionDays.ONE_WEEK,
            removal_policy=RemovalPolicy.DESTROY,
        )

        # --- Voice AgentCore Runtime ----------------------------------
        # Same JWT authorizer as the insurance runtime so React users sign in
        # once via Amplify and use the same access token for both runtimes.
        voice_runtime = AgentCoreRuntimeCustom(
            self,
            "VoiceRuntime",
            AgentCoreRuntimeCustomProps(
                execution_role=runtime_role,
                runtime_name=RUNTIME_NAME,
                container_uri=voice_image.image_uri,
                server_protocol="HTTP",  # exposes /ping and /ws
                network_mode="PUBLIC",
                description=(
                    "Self-contained voice agent on Nova Sonic 2 with the same " +
                    "insurance tool access as the main agent"
                ),
                allowed_headers=["Authorization"],
                authorizer_configuration=create_authorizer_configuration(
                    discovery_url=(
                        f"https://cognito-idp.{self.region}.amazonaws.com/" +
                        f"{auth_stack.user_pool.user_pool_id}/.well-known/openid-configuration"
                    ),
                    allowed_clients=[auth_stack.app_client.user_pool_client_id],
                ),
                environment_variables={
                    # General
                    "AWS_REGION": self.region,
                    "BEDROCK_REGION": BEDROCK_REGION,
                    "MODEL_ID": MODEL_ID,
                    # Audio config
                    "INPUT_SAMPLE_RATE": "16000",
                    "OUTPUT_SAMPLE_RATE": "16000",
                    "CHANNELS": "1",
                    "FORMAT": "pcm",
                    # Gateway (reuses the insurance runtime's gateway + OAuth provider)
                    "AGENTCORE_GATEWAY_URL": agentcore_stack.agentcore_gateway.gateway_url,
                    "GATEWAY_CREDENTIAL_PROVIDER_NAME": "insurance-advisor-runtime-gateway-auth",
                    # Memory (shared with insurance runtime for cross-surface context)
                    "BEDROCK_AGENTCORE_MEMORY_ID": agentcore_stack.ltm_memory.attr_memory_id,
                    # Cognito lookup for advisor_id resolution from JWT
                    "USER_POOL_ID": auth_stack.user_pool.user_pool_id,
                    # Guardrail (same as insurance). Version is read
                    # from SSM at cold start via
                    # BEDROCK_GUARDRAIL_VERSION_PARAM_NAME so guardrail
                    # policy updates roll out without redeploying the
                    # voice runtime.
                    "BEDROCK_GUARDRAIL_ID": agentcore_stack.guardrail.attr_guardrail_id,
                    "BEDROCK_GUARDRAIL_VERSION_PARAM_NAME": agentcore_stack.guardrail_version_param_name,
                },
            ),
        )
        voice_runtime.node.add_dependency(log_group)

        # --- SSM parameter for the frontend to pick up -----------------
        ssm.StringParameter(
            self,
            "VoiceRuntimeArnParam",
            parameter_name="/insurance-advisor/voice/runtime-arn",
            string_value=voice_runtime.agent_runtime_arn,
            description="AgentCore Runtime ARN for the voice agent",
        )

        # --- Outputs ---------------------------------------------------
        CfnOutput(
            self,
            "VoiceRuntimeArn",
            value=voice_runtime.agent_runtime_arn,
            description="Voice AgentCore Runtime ARN",
        )
        CfnOutput(
            self,
            "VoiceLogGroupName",
            value=log_group.log_group_name,
            description="CloudWatch log group for voice runtime",
        )
