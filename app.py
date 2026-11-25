#!/usr/bin/env python3
"""
Insurance Advisor AgentCore - CDK Application Entry Point
Deploys authentication, tools, and AgentCore infrastructure
"""
import aws_cdk as cdk
from cdk.auth_stack import AuthStack
from cdk.tools_stack import ToolsStack
from cdk.agentcore_stack import AgentCoreStack, GATEWAY_NAME, RUNTIME_NAME as TEXT_RUNTIME_NAME
from cdk.voice_stack import VoiceStack, RUNTIME_NAME as VOICE_RUNTIME_NAME
from cdk.frontend_stack import FrontendStack, REACT_DIST_DIR
from cdk.iam_helpers import RUNTIME_GATEWAY_PROVIDER_NAME
from cdk.log_retention import apply_helper_log_retention
from pathlib import Path
# CDK NAG imports
from aws_cdk import Aspects, aws_lambda as lambda_
import cdk_nag
from cdk_nag import NagPackSuppression, NagSuppressions, RegexAppliesTo

# Initialize CDK app
app = cdk.App()

# The helper Lambdas of the CDK library (the AwsCustomResource provider and the BucketDeployment
# handler) get log groups with a retention of 731 days; this gives them 90 days.
apply_helper_log_retention(app)

# Deploy stacks in order with dependencies
auth_stack = AuthStack(app, "insadv-01-auth")
tools_stack = ToolsStack(app, "insadv-02-tools", auth_stack=auth_stack)
agentcore_stack = AgentCoreStack(app, "insadv-03-agentcore", 
                                auth_stack=auth_stack, 
                                tools_stack=tools_stack)
voice_stack = VoiceStack(app, "insadv-04-voice",
                         auth_stack=auth_stack,
                         agentcore_stack=agentcore_stack)

# Frontend stack is only synthesized after the React app has been built
# (deploy.sh handles the ordering: backend stacks → npm build → frontend stack).
# Skipping it on a cold synth keeps `cdk deploy insadv-01-auth` etc. usable
# before any frontend build exists.
react_dist = Path(REACT_DIST_DIR)
frontend_stack = None
if react_dist.exists() and any(react_dist.iterdir()):
    # The Content-Security-Policy names the API and the two buckets that presigned URLs point at.
    # Termination protection keeps the stack, and with it the CloudFront URL, from an
    # accidental delete; destroy.sh switches it off only when DESTROY_FRONTEND=1 is set.
    frontend_stack = FrontendStack(
        app, "insadv-05-frontend",
        api=tools_stack.api,
        documents_bucket=tools_stack.documents_bucket,
        brochures_bucket=tools_stack.brochures_bucket,
        termination_protection=True,
    )

# CDK NAG configuration
Aspects.of(app).add(cdk_nag.AwsSolutionsChecks(verbose=True))

# =================================================================
# CDK NAG SUPPRESSIONS (consolidated)
# =================================================================
# A suppression with `applies_to` covers only the findings it lists. It is built as a
# NagPackSuppression: a plain dict would lose the `applies_to` key on its way into cdk-nag,
# and the suppression would then cover every finding of its rule.

def _suppress(rule, reason, applies_to=None):
    return NagPackSuppression(id=f"AwsSolutions-{rule}", reason=reason, applies_to=applies_to)


def _regex(pattern):
    return RegexAppliesTo(regex=f"/{pattern}/")


_AGENTCORE_ARN = "arn:aws:bedrock-agentcore:<AWS::Region>:<AWS::AccountId>"
_LOGS_ARN = "arn:aws:logs:<AWS::Region>:<AWS::AccountId>:log-group:/aws/bedrock-agentcore"
_SECRETS_ARN = "arn:aws:secretsmanager:<AWS::Region>:<AWS::AccountId>:secret:bedrock-agentcore-identity!default/oauth2"
_ECR_ARN = "arn:aws:ecr:<AWS::Region>:<AWS::AccountId>:repository/*"
_L1_APPLICATION_FUNCTION = (
    "The function runs on Python 3.13, the version its code targets. cdk-nag expects the "
    "newest Python runtime; moving to it is a separate change that needs the function re-verified"
)

# --- Auth Stack ---
NagSuppressions.add_resource_suppressions(
    auth_stack.user_pool,
    [
        {"id": "AwsSolutions-COG3", "reason": "Advanced Security Mode requires Plus feature plan which is not needed for this demo - UserPool"},
        # COG8 (cdk-nag 2.38 and later): same Plus-tier requirement as COG3, same rationale.
        {"id": "AwsSolutions-COG8", "reason": "Plus feature plan is not needed for this demo - UserPool"},
        {"id": "AwsSolutions-COG2", "reason": "MFA intentionally disabled for demo ease of use - UserPool"},
    ]
)
NagSuppressions.add_resource_suppressions(
    auth_stack.gateway_pool,
    [
        {"id": "AwsSolutions-COG3", "reason": "Advanced Security Mode requires Plus feature plan which is not needed for this demo - GatewayPool"},
        {"id": "AwsSolutions-COG8", "reason": "Plus feature plan not applicable - GatewayPool is for service-to-service auth with no human sign-ins"},
        {"id": "AwsSolutions-COG2", "reason": "MFA not applicable - GatewayPool is for service-to-service auth with no human users"},
    ]
)

# --- Tools Stack ---
NagSuppressions.add_resource_suppressions(
    tools_stack.access_log_bucket,
    [{"id": "AwsSolutions-S1", "reason": "This is the server access log bucket itself - enabling access logs on it would create an infinite loop"}]
)

# The Lambda service roles that CDK creates (the default role of a Function and the handlers
# of AwsCustomResource and BucketDeployment) use AWSLambdaBasicExecutionRole, which writes logs.
for stack in [tools_stack, agentcore_stack, voice_stack]:
    NagSuppressions.add_stack_suppressions(stack, [
        _suppress("IAM4", "The CDK-created Lambda service roles use the managed policy AWSLambdaBasicExecutionRole, which only writes logs",
                  ["Policy::arn:<AWS::Partition>:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"]),
    ])

# --- Tools Stack: S3 and DynamoDB grants generated by CDK ---
NagSuppressions.add_stack_suppressions(tools_stack, [
    _suppress("IAM5", "CDK grant_read, grant_read_write_data and BucketDeployment generate these S3 actions with a wildcard suffix, on single buckets",
              ["Action::s3:GetBucket*", "Action::s3:GetObject*", "Action::s3:List*", "Action::s3:Abort*", "Action::s3:DeleteObject*"]),
    _suppress("IAM5", "The same CDK grants add the object level (bucket/*, bucket/ingest/*) of one bucket and the index level (table/index/*) of one table",
              [_regex(r"^Resource::<[A-Za-z0-9]+(Bucket|Table)[0-9A-F]{8}\.Arn>/(\*|ingest/\*|index/\*)$")]),
    _suppress("IAM5", "BucketDeployment reads the CDK bootstrap bucket that holds the deployment assets",
              ["Resource::arn:<AWS::Partition>:s3:::cdk-hnb659fds-assets-<AWS::AccountId>-<AWS::Region>/*"]),
])
# The memory id is generated by the agentcore stack, which is created after this one.
NagSuppressions.add_resource_suppressions(
    tools_stack.mock_data_lambda,
    [_suppress("IAM5", "The function lists and deletes the records of the AgentCore memory. Memory ids are generated by the agentcore stack, which depends on this stack, so the grant names every memory of the account and region",
               [f"Resource::{_AGENTCORE_ARN}:memory/*"])],
    apply_to_children=True,
)

# The tools stack has one CDK-managed handler (BucketDeployment) next to its application functions.
for _fn in tools_stack.node.find_all():
    if (
        isinstance(_fn, lambda_.Function)
        and _fn.runtime.family == lambda_.RuntimeFamily.PYTHON
        and not _fn.node.id.startswith("Custom::")
    ):
        NagSuppressions.add_resource_suppressions(_fn, [_suppress("L1", _L1_APPLICATION_FUNCTION)])
NagSuppressions.add_resource_suppressions_by_path(
    tools_stack,
    "/insadv-02-tools/Custom::CDKBucketDeployment8693BB64968944B69AAFB0CC9EB8756C/Resource",
    [_suppress("L1", "The runtime of the BucketDeployment handler is set by the CDK library")],
)

NagSuppressions.add_resource_suppressions(
    tools_stack.api,
    [{"id": "AwsSolutions-APIG2", "reason": "Request validation is enabled on all API methods via RequestValidator - CDK Nag does not detect validators attached at method level"}]
)

# Public sign-up endpoint cannot be authenticated by definition. WAFv2 with
# the AWS managed common rule set is associated with the API Gateway and
# provides baseline abuse protection. Targeted at just the /signup resource
# so the rules still apply to the rest of the API. The route exists only when
# the `enable_public_signup` context flag is set, so the suppression does too.
if tools_stack.public_signup_enabled:
    NagSuppressions.add_resource_suppressions_by_path(
        tools_stack,
        "/insadv-02-tools/InsuranceAdvisorApi/Default/signup/POST/Resource",
        [
            {"id": "AwsSolutions-APIG4",
             "reason": "Public sign-up endpoint is unauthenticated by design. The Cognito user pool is configured with AllowAdminCreateUserOnly, so self-service signup is not available. Backend Lambda creates the user via admin APIs after validating the request. WAFv2 protects the endpoint."},
            {"id": "AwsSolutions-COG4",
             "reason": "Public sign-up endpoint is unauthenticated by design - the user does not yet exist in the Cognito pool when calling /signup."},
        ],
    )

# --- AgentCore Stack ---
_RUNTIME_NO_RESOURCE_LEVEL = (
    "ecr:GetAuthorizationToken, the X-Ray calls and cloudwatch:PutMetricData accept no resource ARN; "
    "a condition limits PutMetricData to the AgentCore namespaces"
)
_FORMS_BUCKET_OBJECTS = _regex(r"^Resource::<FormsBucket[0-9A-F]{8}\.Arn>/\*$")
_S3_READ_ACTIONS = ["Action::s3:GetBucket*", "Action::s3:GetObject*", "Action::s3:List*"]

NagSuppressions.add_resource_suppressions(
    agentcore_stack.gateway_role,
    [
        _suppress("IAM5", "The X-Ray calls and cloudwatch:PutMetricData accept no resource ARN; a condition limits PutMetricData to the AgentCore namespace", ["Resource::*"]),
        _suppress("IAM5", "The service creates the gateway's log group with a name that starts with the gateway name; the prefix limits the grant to that group and its streams",
                  [f"Resource::{_LOGS_ARN}/gateways/{GATEWAY_NAME}*"]),
        _suppress("IAM5", "AgentCore names the gateway's workload identity after the gateway followed by a generated suffix",
                  [f"Resource::{_AGENTCORE_ARN}:workload-identity-directory/default/workload-identity/{GATEWAY_NAME}-*"]),
        _suppress("IAM5", "grant_invoke covers each tool function and also its published versions and aliases (function ARN followed by :*)",
                  [_regex(r"^Resource::<[A-Za-z0-9]+Lambda[0-9A-F]{8}\.Arn>:\*$")]),
    ],
    apply_to_children=True,
)
NagSuppressions.add_resource_suppressions(
    agentcore_stack.runtime_role,
    [
        _suppress("IAM5", _RUNTIME_NO_RESOURCE_LEVEL, ["Resource::*"]),
        _suppress("IAM5", "The image comes from the CDK bootstrap repository, whose name depends on the bootstrap qualifier; the grant is limited to this account and region",
                  [f"Resource::{_ECR_ARN}"]),
        _suppress("IAM5", "The service creates the runtime's log group, named after the runtime id; the prefix limits the grant to this runtime's group and its streams",
                  [f"Resource::{_LOGS_ARN}/runtimes/{TEXT_RUNTIME_NAME}*"]),
        _suppress("IAM5", "CDK grant_read on the forms bucket: read-only on one bucket, with the object level (bucket/*)",
                  _S3_READ_ACTIONS + [_FORMS_BUCKET_OBJECTS]),
        _suppress("IAM5", "AgentCore names the runtime's workload identity after the runtime followed by a generated suffix",
                  [f"Resource::{_AGENTCORE_ARN}:workload-identity-directory/default/workload-identity/{TEXT_RUNTIME_NAME}-*"]),
        _suppress("IAM5", "Secrets Manager appends a generated suffix to the name of the secret that holds the gateway provider's client secret",
                  [f"Resource::{_SECRETS_ARN}/{RUNTIME_GATEWAY_PROVIDER_NAME}*"]),
    ],
    apply_to_children=True,
)
for _provider in [agentcore_stack.oauth_provider, agentcore_stack.runtime_gateway_oauth_provider]:
    NagSuppressions.add_resource_suppressions(
        _provider.lambda_role,
        [
            _suppress("IAM5", "CloudWatch Logs needs the log group ARN followed by :* for the function's log streams",
                      [_regex(r"^Resource::<[A-Za-z0-9]+OAuth2ProviderLogGroup[0-9A-F]{8}\.Arn>:\*$")]),
            _suppress("IAM5", "Secrets Manager appends a generated suffix to the name of the secret that holds the provider's client secret",
                      [f"Resource::{_SECRETS_ARN}/{_provider.provider_name}*"]),
        ],
        apply_to_children=True,
    )
    NagSuppressions.add_resource_suppressions(_provider.function, [_suppress("L1", _L1_APPLICATION_FUNCTION)])
NagSuppressions.add_resource_suppressions(agentcore_stack.interceptor_lambda, [_suppress("L1", _L1_APPLICATION_FUNCTION)])

# --- Voice Stack ---
NagSuppressions.add_resource_suppressions(
    voice_stack.runtime_role,
    [
        _suppress("IAM5", _RUNTIME_NO_RESOURCE_LEVEL, ["Resource::*"]),
        _suppress("IAM5", "The image comes from the CDK bootstrap repository, whose name depends on the bootstrap qualifier; the grant is limited to this account and region",
                  [f"Resource::{_ECR_ARN}"]),
        _suppress("IAM5", "The service creates the runtime's log group, named after the runtime id; the prefix limits the grant to this runtime's group and its streams",
                  [f"Resource::{_LOGS_ARN}/runtimes/{VOICE_RUNTIME_NAME}*"]),
        _suppress("IAM5", "CDK grant_read on the forms bucket: read-only on one bucket, with the object level (bucket/*)",
                  _S3_READ_ACTIONS + [_FORMS_BUCKET_OBJECTS]),
        _suppress("IAM5", "CDK grant_put on the transcripts bucket: write-only on one bucket, with the object level (bucket/*)",
                  ["Action::s3:Abort*", _regex(r"^Resource::<TranscriptsBucket[0-9A-F]{8}\.Arn>/\*$")]),
        _suppress("IAM5", "AgentCore names the runtime's workload identity after the runtime followed by a generated suffix",
                  [f"Resource::{_AGENTCORE_ARN}:workload-identity-directory/default/workload-identity/{VOICE_RUNTIME_NAME}-*"]),
        _suppress("IAM5", "Secrets Manager appends a generated suffix to the name of the secret that holds the gateway provider's client secret",
                  [f"Resource::{_SECRETS_ARN}/{RUNTIME_GATEWAY_PROVIDER_NAME}*"]),
    ],
    apply_to_children=True,
)

# --- Frontend Stack: S3 + CloudFront for the React app ---
if frontend_stack is not None:
    NagSuppressions.add_stack_suppressions(frontend_stack, [
        _suppress("IAM4", "The CDK-created BucketDeployment handler role uses the managed policy AWSLambdaBasicExecutionRole, which only writes logs",
                  ["Policy::arn:<AWS::Partition>:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"]),
        _suppress("IAM5", "CDK BucketDeployment generates these S3 actions with a wildcard suffix, on the site bucket and the CDK bootstrap bucket that holds the deployment assets",
                  ["Action::s3:GetBucket*", "Action::s3:GetObject*", "Action::s3:List*",
                   "Action::s3:Abort*", "Action::s3:DeleteObject*",
                   "Resource::arn:<AWS::Partition>:s3:::cdk-hnb659fds-assets-<AWS::AccountId>-<AWS::Region>/*",
                   _regex(r"^Resource::<SiteBucket[0-9A-F]{8}\.Arn>/\*$")]),
        _suppress("IAM5", "BucketDeployment grants the CloudFront invalidation calls it makes after a deployment on all resources",
                  ["Resource::*"]),
        {"id": "AwsSolutions-L1", "reason": "Lambda runtime is managed by CDK BucketDeployment construct and cannot be directly controlled"},
        # Server access logs on the access-log buckets themselves would loop,
        # so they are intentionally not configured.
        {"id": "AwsSolutions-S1", "reason": "Server access log destination buckets cannot themselves enable access logs without creating an infinite loop"},
        # CloudFront-specific
        {"id": "AwsSolutions-CFR1", "reason": "Geo restrictions are not required for this internal demo / advisor portal"},
        {"id": "AwsSolutions-CFR2", "reason": "WAF is not attached to CloudFront for this demo. The React app is a static SPA; the API Gateway it calls is already protected by AWS WAFv2 (see ToolsStack) and Cognito JWT authorization"},
        {"id": "AwsSolutions-CFR3", "reason": "CloudFront access logging is enabled to a dedicated log bucket - CDK Nag false positive when log_bucket is set via the L2 Distribution construct"},
        {"id": "AwsSolutions-CFR4", "reason": "Without the custom_domain context the distribution uses the default CloudFront domain (*.cloudfront.net), which only supports the default certificate. With it, the distribution uses an ACM certificate and TLS 1.3 as the minimum protocol"},
        {"id": "AwsSolutions-CFR7", "reason": "Origin Access Control (OAC) is used via S3BucketOrigin.with_origin_access_control - CDK Nag false positive that does not detect the new OAC construct"},
    ])

# Synthesize CloudFormation templates
app.synth()
