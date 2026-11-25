"""Voice Stack - AgentCore Runtime with Nova 2.5 Sonic + full insurance tool access.

The voice runtime is self-sufficient: it has the same MCP gateway tools,
AgentCore Memory and Cognito user lookup as the main insurance agent, just
with Nova 2.5 Sonic as the underlying model instead of Claude. It talks to
Nova Sonic directly, with no Claude hop in between.

The runtime reuses the AgentCore Gateway and its OAuth credential
provider from insadv-03-agentcore - no second gateway is needed.
"""
from aws_cdk import (
    CfnOutput,
    RemovalPolicy,
    Stack,
    aws_bedrockagentcore as agentcore,
    aws_ecr_assets as ecr_assets,
    aws_iam as iam,
    aws_ssm as ssm,
    custom_resources as cr,
)
from constructs import Construct

from .agentcore_stack import AgentCoreStack
from .auth_stack import AuthStack
from .iam_helpers import (
    RUNTIME_GATEWAY_PROVIDER_NAME,
    agentcore_service_principal,
    grant_runtime_agentcore_access,
)
from .limits import VOICE_AGENT_LIMITS, limit_environment


# Changing the name replaces the runtime: AgentRuntimeName is unique per
# account/region, and CloudFormation creates the replacement before deleting
# the original.
RUNTIME_NAME = "insurance_voice_agent"
# Nova 2.5 Sonic has the same API surface, voices and languages as Nova 2 Sonic
# (amazon.nova-2-sonic-v1:0), with better tool calling and lower latency. It runs on demand in
# us-east-1, so no inference profile is involved. This one constant sets both the IAM resource
# and the runtime's MODEL_ID. The IAM policy allows only this model, so changing it here is all
# a model switch needs.
MODEL_ID = "amazon.nova-2-5-sonic"
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
        # tool access + memory + Cognito lookup + token vault. There is no guardrail
        # grant: Bedrock Guardrails do not apply to the Nova Sonic bidirectional
        # stream (see README, "Why the voice assistant has no guardrail").
        runtime_role = iam.Role(
            self,
            "VoiceRuntimeRole",
            assumed_by=agentcore_service_principal(self),
            inline_policies={
                "BedrockInvokeNovaSonic": iam.PolicyDocument(
                    statements=[
                        iam.PolicyStatement(
                            effect=iam.Effect.ALLOW,
                            actions=[
                                "bedrock:InvokeModel",
                                "bedrock:InvokeModelWithResponseStream",
                            ],
                            resources=[
                                f"arn:aws:bedrock:{BEDROCK_REGION}::foundation-model/{MODEL_ID}",
                            ],
                        )
                    ]
                ),
                "AgentCoreRuntime": iam.PolicyDocument(
                    statements=[
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
        self.runtime_role = runtime_role

        # --- Observability (same permissions as the insurance runtime role) ----
        # Metrics are limited to the AgentCore namespaces. The X-Ray calls have no
        # resource-level scope.
        runtime_role.add_to_policy(
            iam.PolicyStatement(
                sid="AgentCoreMetrics",
                effect=iam.Effect.ALLOW,
                actions=["cloudwatch:PutMetricData"],
                resources=["*"],
                conditions={
                    "StringEquals": {
                        "cloudwatch:namespace": ["AWS/BedrockAgentCore", "bedrock-agentcore"]
                    }
                },
            )
        )
        runtime_role.add_to_policy(
            iam.PolicyStatement(
                sid="XRayTraces",
                effect=iam.Effect.ALLOW,
                actions=[
                    "xray:PutTraceSegments",
                    "xray:PutTelemetryRecords",
                    "xray:GetSamplingRules",
                    "xray:GetSamplingTargets",
                ],
                resources=["*"],
            )
        )

        # Read-only access to the application-form schemas. Scoped to this one
        # bucket; the runtime reads schemas directly rather than having the
        # model echo them back as tool arguments.
        agentcore_stack.forms_bucket.grant_read(runtime_role)
        # Voice transcripts: s3:PutObject and nothing else. The runtime cannot read, list,
        # tag, abort uploads on, or set retention or legal holds on objects in the bucket;
        # the data Lambda is the only reader. The resource is the whole bucket because the
        # one runtime role writes under each signed-in advisor's own key prefix.
        runtime_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["s3:PutObject"],
                resources=[agentcore_stack.transcripts_bucket.arn_for_objects("*")],
            )
        )
        # AgentCore calls of the voice runtime: the memory shared with the text runtime,
        # the gateway token from the token vault, and the workload identity. The gateway
        # accepts the Cognito JWT the runtime presents (CUSTOM_JWT), so the role needs no
        # permission on the gateway itself.
        grant_runtime_agentcore_access(
            runtime_role,
            stack=self,
            runtime_name=RUNTIME_NAME,
            memory_id=agentcore_stack.ltm_memory.attr_memory_id,
        )

        # --- Voice AgentCore Runtime ----------------------------------
        # Same JWT authorizer as the insurance runtime so React users sign in
        # once via Amplify and use the same access token for both runtimes.
        # L1 (AWS::BedrockAgentCore::Runtime) — see the equivalent comment
        # in agentcore_stack.py.
        voice_runtime = agentcore.CfnRuntime(
            self,
            "VoiceRuntime",
            agent_runtime_name=RUNTIME_NAME,
            description=(
                "Self-contained voice agent on Nova 2.5 Sonic with the same "
                "insurance tool access as the main agent"
            ),
            role_arn=runtime_role.role_arn,
            agent_runtime_artifact=agentcore.CfnRuntime.AgentRuntimeArtifactProperty(
                container_configuration=agentcore.CfnRuntime.ContainerConfigurationProperty(
                    container_uri=voice_image.image_uri
                )
            ),
            network_configuration=agentcore.CfnRuntime.NetworkConfigurationProperty(
                network_mode="PUBLIC"
            ),
            protocol_configuration="HTTP",  # exposes /ping and /ws
            # Platform V2 (snapshot-restore starts), always stated explicitly: leaving the
            # property out would keep the current version on an update but pick the
            # service default on a new runtime. The voice agent builds its Nova Sonic
            # model and its session state per WebSocket connection and keeps nothing
            # frozen at import (README, "Snapshot-safety rules"): a model shared
            # between connections breaks the real session. To run on V1 instead,
            # set "V1" here (do not delete the line: omitting it on an update keeps
            # the current version) and redeploy.
            platform_version="V2",
            request_header_configuration=agentcore.CfnRuntime.RequestHeaderConfigurationProperty(
                request_header_allowlist=["Authorization"]
            ),
            authorizer_configuration=agentcore.CfnRuntime.AuthorizerConfigurationProperty(
                custom_jwt_authorizer=agentcore.CfnRuntime.CustomJWTAuthorizerConfigurationProperty(
                    discovery_url=(
                        f"https://cognito-idp.{self.region}.amazonaws.com/"
                        f"{auth_stack.user_pool.user_pool_id}/.well-known/openid-configuration"
                    ),
                    allowed_clients=[auth_stack.app_client.user_pool_client_id],
                )
            ),
            environment_variables={
                # General
                "AWS_REGION": self.region,
                "BEDROCK_REGION": BEDROCK_REGION,
                "MODEL_ID": MODEL_ID,
                # Traces. The runtime starts under OpenTelemetry auto-instrumentation (see the
                # Dockerfile). Without this setting the model-call spans carry the customer's name,
                # the system prompt and tool results as attributes. An empty value for
                # gen_ai_unredacted_attributes turns that off: spans keep timing, tool names and
                # token counts, and the content is replaced by "[REDACTED]".
                "OTEL_SEMCONV_STABILITY_OPT_IN": "gen_ai_unredacted_attributes=",
                # The AWS distro also adds a span for every MCP call that copies the tool's arguments
                # and full result (customer profile, income, advisor address). It has no redaction
                # setting, and Strands already records each tool call (name, status, timing) without
                # content, so this one is switched off.
                "OTEL_PYTHON_DISABLED_INSTRUMENTATIONS": "aws_mcp",
                # The AWS distro switches on message capture in its botocore Bedrock instrumentation
                # by default, which writes prompts, answers and tool calls to the OTLP log records.
                # This runtime makes no botocore Bedrock call today (the voice model runs through
                # the Strands bidi client); "false" keeps it that way if one is added.
                "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT": "false",
                # Audio config
                "INPUT_SAMPLE_RATE": "16000",
                "OUTPUT_SAMPLE_RATE": "16000",
                "CHANNELS": "1",
                "FORMAT": "pcm",
                # Gateway (reuses the insurance runtime's gateway + OAuth provider)
                "AGENTCORE_GATEWAY_URL": agentcore_stack.gateway_url,
                "GATEWAY_CREDENTIAL_PROVIDER_NAME": RUNTIME_GATEWAY_PROVIDER_NAME,
                # REST API base address: the customer is checked against GET /profile with the advisor's
                # own token before AgentCore Memory is used for that customer.
                "API_BASE_URL": agentcore_stack.api_base_url,
                # Memory (shared with insurance runtime for cross-surface context)
                "BEDROCK_AGENTCORE_MEMORY_ID": agentcore_stack.ltm_memory.attr_memory_id,
                # Cognito lookup for advisor_id resolution from JWT
                "USER_POOL_ID": auth_stack.user_pool.user_pool_id,
                # The web app's client: the runtime accepts only access tokens issued to it.
                "APP_CLIENT_ID": auth_stack.app_client.user_pool_client_id,
                # Application-form schemas. Read straight from S3 by the
                # open_application_form tool so the schema is never routed
                # back through the model as a tool argument.
                "FORMS_BUCKET": agentcore_stack.forms_bucket.bucket_name,
                # Transcripts are written here once consent is confirmed.
                "TRANSCRIPTS_BUCKET": agentcore_stack.transcripts_bucket.bucket_name,
                # Limits on one voice connection: audio volume, session length, typed text (see
                # cdk/limits.py and voice-agent/limits.py). Change with, for example,
                # -c max_session_seconds=900.
                **limit_environment(self.node, VOICE_AGENT_LIMITS),
            },
        )

        # --- Log retention of the voice runtime: 90 days (the same as the text runtime) -------
        #
        # The service creates the runtime's log group on first use, named after the runtime id
        # (/aws/bedrock-agentcore/runtimes/<runtime id>-DEFAULT), so CloudFormation does not own
        # it and a logs.LogGroup here would clash with it. Two custom resources instead: the
        # first creates the group if it is not there yet (a fresh deployment, before the first
        # session; "already exists" is ignored and the service then uses this group), the
        # second sets the retention. Both only run when the runtime id changes (a replaced
        # runtime has a new log group) or when the days change. Neither deletes anything on
        # stack removal. The text runtime does the same in agentcore_stack.py.
        voice_runtime_id = voice_runtime.attr_agent_runtime_id
        log_group_name = f"/aws/bedrock-agentcore/runtimes/{voice_runtime_id}-DEFAULT"
        log_group_arn = f"arn:aws:logs:{self.region}:{self.account}:log-group:{log_group_name}"
        log_retention_days = 90
        log_group_ensure = cr.AwsCustomResource(
            self,
            "VoiceRuntimeLogGroupEnsure",
            on_create=cr.AwsSdkCall(
                service="CloudWatchLogs",
                action="createLogGroup",
                parameters={"logGroupName": log_group_name},
                physical_resource_id=cr.PhysicalResourceId.of(f"VoiceRuntimeLogGroup-{voice_runtime_id}"),
                ignore_error_codes_matching="ResourceAlreadyExistsException",
            ),
            on_update=cr.AwsSdkCall(
                service="CloudWatchLogs",
                action="createLogGroup",
                parameters={"logGroupName": log_group_name},
                physical_resource_id=cr.PhysicalResourceId.of(f"VoiceRuntimeLogGroup-{voice_runtime_id}"),
                ignore_error_codes_matching="ResourceAlreadyExistsException",
            ),
            policy=cr.AwsCustomResourcePolicy.from_statements([
                iam.PolicyStatement(
                    effect=iam.Effect.ALLOW,
                    actions=["logs:CreateLogGroup"],
                    resources=[log_group_arn],
                )
            ]),
            install_latest_aws_sdk=False,
        )
        log_group_ensure.node.add_dependency(voice_runtime)
        log_retention = cr.AwsCustomResource(
            self,
            "VoiceRuntimeLogRetention",
            on_create=cr.AwsSdkCall(
                service="CloudWatchLogs",
                action="putRetentionPolicy",
                parameters={"logGroupName": log_group_name, "retentionInDays": log_retention_days},
                physical_resource_id=cr.PhysicalResourceId.of(f"VoiceRuntimeLogRetention-{log_retention_days}-{voice_runtime_id}"),
                ignore_error_codes_matching="ResourceNotFoundException",
            ),
            on_update=cr.AwsSdkCall(
                service="CloudWatchLogs",
                action="putRetentionPolicy",
                parameters={"logGroupName": log_group_name, "retentionInDays": log_retention_days},
                physical_resource_id=cr.PhysicalResourceId.of(f"VoiceRuntimeLogRetention-{log_retention_days}-{voice_runtime_id}"),
                ignore_error_codes_matching="ResourceNotFoundException",
            ),
            policy=cr.AwsCustomResourcePolicy.from_statements([
                iam.PolicyStatement(
                    effect=iam.Effect.ALLOW,
                    actions=["logs:PutRetentionPolicy"],
                    resources=[log_group_arn],
                )
            ]),
            install_latest_aws_sdk=False,
        )
        log_retention.node.add_dependency(log_group_ensure)

        # --- SSM parameter for the frontend to pick up -----------------
        ssm.StringParameter(
            self,
            "VoiceRuntimeArnParam",
            parameter_name="/insurance-advisor/voice/runtime-arn",
            string_value=voice_runtime.attr_agent_runtime_arn,
            description="AgentCore Runtime ARN for the voice agent",
        )

        # --- Outputs ---------------------------------------------------
        CfnOutput(
            self,
            "VoiceRuntimeArn",
            value=voice_runtime.attr_agent_runtime_arn,
            description="Voice AgentCore Runtime ARN",
        )
        CfnOutput(
            self,
            "VoiceLogGroupName",
            value=log_group_name,
            description="CloudWatch log group for voice runtime",
        )
