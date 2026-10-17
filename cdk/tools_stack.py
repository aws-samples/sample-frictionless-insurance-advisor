from aws_cdk import (
    Stack,
    aws_lambda as _lambda,
    aws_apigateway as apigateway,
    aws_bedrock as bedrock,
    aws_budgets as budgets,
    aws_s3 as s3,
    aws_s3_deployment as s3deploy,
    aws_dynamodb as dynamodb,
    aws_cloudwatch as cloudwatch,
    aws_cloudwatch_actions as cw_actions,
    aws_events as events,
    aws_events_targets as event_targets,
    aws_iam as iam,
    aws_kms as kms,
    aws_logs as logs,
    aws_sns as sns,
    aws_sns_subscriptions as sns_subs,
    aws_ssm as ssm,
    aws_wafv2 as wafv2,
    RemovalPolicy,
    CfnOutput,
    Duration,
    custom_resources as cr,
)
from constructs import Construct
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from .auth_stack import AuthStack

# One model for every text task in this stack: Claude Haiku 5.5 through the US
# cross-region inference profile. It rejects `temperature` (like Sonnet 5), so
# none of the Lambdas send one; forced tool choice keeps the output on schema.
TEXT_MODEL_ID = "us.anthropic.claude-haiku-5-5"
_HAIKU55_REGIONS = ("us-east-1", "us-east-2", "us-west-2")


def _text_model_arns(region: str, account: str) -> list[str]:
    """IAM resources for TEXT_MODEL_ID: the profile plus the models it routes to."""
    return [
        f"arn:aws:bedrock:{region}:{account}:inference-profile/{TEXT_MODEL_ID}",
        *[f"arn:aws:bedrock:{r}::foundation-model/anthropic.claude-haiku-5-5" for r in _HAIKU55_REGIONS],
    ]


def _seed_data_hash() -> str:
    """Hash of the mock seed files (JSON data and the date anchors).

    Part of the MockDataPopulator custom resource's physical_resource_id. A
    deploy-time stamp is added to it as well (see ToolsStack), so the
    populator runs on every deploy of this stack, not only when the seed
    files change. The populator resets the demo to the seed: it upserts the
    seed rows, deletes every other row, moves the seed dates to "today" and
    clears AgentCore memory.
    """
    seed_dir = Path(__file__).resolve().parent.parent / "lambda" / "mock_data"
    sha = hashlib.sha256()
    for filename in ("profiles.json", "policies.json", "catalog.json", "seed_anchors.json"):
        sha.update((seed_dir / filename).read_bytes())
    return sha.hexdigest()[:12]


# The one scope every API route accepts: the one a signed-in advisor's access token
# carries. The gateway's machine token (insurance-advisor-api/api.access) is NOT
# accepted on any route. A shared machine credential cannot say which advisor it
# speaks for, so letting it in would make the caller's own claim the only check.
# The assistants call this API with the advisor's token, which the AgentCore
# Gateway's request interceptor swaps in after verifying it (lambda/gateway_interceptor).
USER_TOKEN_SCOPE = "aws.cognito.signin.user.admin"


class ToolsStack(Stack):
    """Tools stack with S3, DynamoDB, Lambda functions, and API Gateway"""

    def __init__(self, scope: Construct, construct_id: str, auth_stack: AuthStack, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # S3 Access Log Bucket
        self.access_log_bucket = s3.Bucket(
            self, "AccessLogBucket",
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            enforce_ssl=True,
        )

        # S3 Buckets for portfolio and promotion data
        self.portfolio_bucket = s3.Bucket(
            self, "PortfolioBucket",
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            server_access_logs_bucket=self.access_log_bucket,
            server_access_logs_prefix="portfolio-logs/",
            enforce_ssl=True,
        )

        self.promotion_bucket = s3.Bucket(
            self, "PromotionBucket",
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            server_access_logs_bucket=self.access_log_bucket,
            server_access_logs_prefix="promotion-logs/",
            enforce_ssl=True,
        )

        # Deploy portfolio data to S3
        self.portfolio_deployment = s3deploy.BucketDeployment(
            self, "PortfolioDataDeployment",
            sources=[s3deploy.Source.asset("s3-data/portfolio")],
            destination_bucket=self.portfolio_bucket,
            retain_on_delete=False
        )

        # Deploy promotion data to S3
        self.promotion_deployment = s3deploy.BucketDeployment(
            self, "PromotionDataDeployment",
            sources=[s3deploy.Source.asset("s3-data/promotion")],
            destination_bucket=self.promotion_bucket,
            retain_on_delete=False
        )

        # Application-form schemas, one JSON object per product type. These are
        # structured (not prose) because the frontend renders them directly.
        self.forms_bucket = s3.Bucket(
            self, "FormsBucket",
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            server_access_logs_bucket=self.access_log_bucket,
            server_access_logs_prefix="forms-logs/",
            enforce_ssl=True,
        )

        self.forms_deployment = s3deploy.BucketDeployment(
            self, "FormsDataDeployment",
            sources=[s3deploy.Source.asset("s3-data/forms")],
            destination_bucket=self.forms_bucket,
            retain_on_delete=False
        )

        # Indicative premium rate tables, one JSON file per Awesome product plus
        # the affordability rule. A separate bucket (not the portfolio bucket)
        # because the get_portfolio tool returns every object in the portfolio
        # bucket to the model. The rates are reached only through the two
        # pricing tools, which do the arithmetic in code.
        self.pricing_bucket = s3.Bucket(
            self, "PricingBucket",
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            server_access_logs_bucket=self.access_log_bucket,
            server_access_logs_prefix="pricing-logs/",
            enforce_ssl=True,
        )
        self.pricing_deployment = s3deploy.BucketDeployment(
            self, "PricingDataDeployment",
            sources=[s3deploy.Source.asset("s3-data/pricing")],
            destination_bucket=self.pricing_bucket,
            retain_on_delete=False
        )
        # One-page product brochures (PDF), committed to the repo and built by
        # tools/brochures/build.py. A separate private bucket: no agent tool
        # and no agent-facing Lambda role can read it (the portfolio bucket,
        # by contrast, is returned to the model in full by get_portfolio). It
        # holds the ten Awesome brochures under awesome/ and the sample
        # competitor brochures under samples/. The Data tab and the Comparator
        # reach it only through the data Lambda, which hands out short-lived
        # presigned links.
        self.brochures_bucket = s3.Bucket(
            self, "BrochuresBucket",
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            server_access_logs_bucket=self.access_log_bucket,
            server_access_logs_prefix="brochures-logs/",
            enforce_ssl=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
        )
        self.brochures_deployment = s3deploy.BucketDeployment(
            self, "BrochuresDataDeployment",
            sources=[s3deploy.Source.asset("s3-data/brochures")],
            destination_bucket=self.brochures_bucket,
            retain_on_delete=False
        )
        # S3 Buckets for company, competitive, and competitors knowledge bases
        self.company_bucket = s3.Bucket(
            self, "CompanyBucket",
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            server_access_logs_bucket=self.access_log_bucket,
            server_access_logs_prefix="company-logs/",
            enforce_ssl=True,
        )

        self.competitive_bucket = s3.Bucket(
            self, "CompetitiveBucket",
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            server_access_logs_bucket=self.access_log_bucket,
            server_access_logs_prefix="competitive-logs/",
            enforce_ssl=True,
        )

        self.competitors_bucket = s3.Bucket(
            self, "CompetitorsBucket",
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            server_access_logs_bucket=self.access_log_bucket,
            server_access_logs_prefix="competitors-logs/",
            enforce_ssl=True,
        )

        # Documents bucket: holds advisor-uploaded insurance policy PDFs /
        # images / markdown that the agent extracts policy fields from.
        # Strict access controls because customer PII passes through here:
        #   - block_public_access enforced
        #   - 24-hour lifecycle expiration on every object (uploads are
        #     ephemeral; the agent reads them once and then they auto-delete)
        #   - server-side encryption (S3-managed for now; a customer-managed KMS
        #     key would be the production
        #     hardening)
        #   - server access logs enabled for audit
        # Bucket policy below additionally denies any principal except the
        # two Lambdas that need it.
        self.documents_bucket = s3.Bucket(
            self, "DocumentsBucket",
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            server_access_logs_bucket=self.access_log_bucket,
            server_access_logs_prefix="documents-logs/",
            enforce_ssl=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            cors=[
                s3.CorsRule(
                    # The browser uploads directly via presigned PUT, which
                    # crosses the SPA's origin -> S3 boundary. Allow any
                    # origin since auth is enforced by the presigned URL
                    # signature itself, not by Origin headers.
                    allowed_methods=[s3.HttpMethods.PUT],
                    allowed_origins=["*"],
                    allowed_headers=["*"],
                    exposed_headers=["ETag"],
                    max_age=3000,
                )
            ],
            lifecycle_rules=[
                s3.LifecycleRule(
                    id="auto-expire-uploads",
                    enabled=True,
                    expiration=Duration.days(1),
                    abort_incomplete_multipart_upload_after=Duration.days(1),
                )
            ],
        )

        # Voice transcripts: one JSON object per voice session,
        # written by the voice runtime only after the advisor has confirmed the
        # customer's consent. Private, encrypted, HTTPS only, access-logged.
        # Objects expire after 1 day: S3 lifecycle works in whole days and runs
        # once a day, so an object goes between roughly 24 and 48 hours after
        # its last write. Only the voice runtime can write (see voice_stack)
        # and only the data Lambda can read, scoped to the signed-in advisor's
        # own prefix. No agent tool and no other Lambda has any access.
        self.transcripts_bucket = s3.Bucket(
            self, "TranscriptsBucket",
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            server_access_logs_bucket=self.access_log_bucket,
            server_access_logs_prefix="transcripts-logs/",
            enforce_ssl=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            lifecycle_rules=[
                s3.LifecycleRule(
                    id="auto-expire-transcripts",
                    enabled=True,
                    expiration=Duration.days(1),
                    abort_incomplete_multipart_upload_after=Duration.days(1),
                )
            ],
        )
        # Deploy company data to S3
        self.company_deployment = s3deploy.BucketDeployment(
            self, "CompanyDataDeployment",
            sources=[s3deploy.Source.asset("s3-data/company")],
            destination_bucket=self.company_bucket,
            retain_on_delete=False
        )

        # Deploy competitive data to S3
        self.competitive_deployment = s3deploy.BucketDeployment(
            self, "CompetitiveDataDeployment",
            sources=[s3deploy.Source.asset("s3-data/competitive")],
            destination_bucket=self.competitive_bucket,
            retain_on_delete=False
        )

        # Deploy competitors data to S3
        self.competitors_deployment = s3deploy.BucketDeployment(
            self, "CompetitorsDataDeployment",
            sources=[s3deploy.Source.asset("s3-data/competitors")],
            destination_bucket=self.competitors_bucket,
            retain_on_delete=False
        )

        # DynamoDB Tables
        self.profiles_table = dynamodb.Table(
            self, "ProfilesTable",
            partition_key=dynamodb.Attribute(
                name="customer_id",
                type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=True
            ),
            # Demo hygiene: agent-created prospect profiles (via the
            # create_profile POST flow) set an `expires_at` epoch attribute so
            # DynamoDB TTL auto-deletes them ~2h later. Seed/mock customers do
            # NOT set this attribute, so TTL leaves them untouched — only
            # ephemeral demo prospects expire. TTL deletion is best-effort and
            # can lag up to ~48h past the timestamp.
            time_to_live_attribute="expires_at",
            removal_policy=RemovalPolicy.DESTROY
        )

        self.profiles_table.add_global_secondary_index(
            index_name="advisor-id-index",
            partition_key=dynamodb.Attribute(
                name="advisor_id",
                type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="customer_id",
                type=dynamodb.AttributeType.STRING
            )
        )

        self.policies_table = dynamodb.Table(
            self, "PoliciesTable", 
            partition_key=dynamodb.Attribute(
                name="id",
                type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=True
            ),
            # Demo hygiene: agent-created third-party policies (from the
            # PDF/markdown upload flow) set an `expires_at` epoch attribute so
            # DynamoDB TTL auto-deletes them ~2h later. Seed/mock rows and
            # Awesome-issued policies do NOT set this attribute, so TTL leaves
            # them untouched — only ephemeral demo uploads expire. TTL deletion
            # is best-effort and can lag up to ~48h past the timestamp.
            time_to_live_attribute="expires_at",
            removal_policy=RemovalPolicy.DESTROY
        )

        # Add GSI for advisor_id with customer_id sort key on policies table
        self.policies_table.add_global_secondary_index(
            index_name="advisor-id-index",
            partition_key=dynamodb.Attribute(
                name="advisor_id",
                type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="customer_id",
                type=dynamodb.AttributeType.STRING
            )
        )

        # Insurance product catalog table. Stores metadata only (carrier,
        # product name, product type, pricing tier, S3 pointer to markdown).
        # The Comparator page queries by product_type, so we add a GSI on
        # that attribute. The catalog Lambda never scans in the filter path.
        self.catalog_table = dynamodb.Table(
            self, "CatalogTable",
            partition_key=dynamodb.Attribute(
                name="product_id",
                type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=True
            ),
            removal_policy=RemovalPolicy.DESTROY
        )

        self.catalog_table.add_global_secondary_index(
            index_name="product-type-index",
            partition_key=dynamodb.Attribute(
                name="product_type",
                type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="product_id",
                type=dynamodb.AttributeType.STRING
            )
        )

        # Lambda Functions
        #
        # Helper: create an explicit CloudWatch LogGroup per function. This
        # replaces the deprecated `log_retention=` prop (which provisioned a
        # separate LogRetention custom-resource Lambda). Retention is bounded
        # to ONE_MONTH and the group is destroyed with the stack.
        def _log_group(construct_id: str) -> logs.LogGroup:
            return logs.LogGroup(
                self, f"{construct_id}LogGroup",
                retention=logs.RetentionDays.ONE_MONTH,
                removal_policy=RemovalPolicy.DESTROY,
            )

        self.profile_lambda = _lambda.Function(
            self, "ProfileLambda",
            runtime=_lambda.Runtime.PYTHON_3_13,
            handler="index.handler",
            code=_lambda.Code.from_asset("lambda/profile"),
            environment={
                "PROFILES_TABLE": self.profiles_table.table_name,
                "USER_POOL_ID": auth_stack.user_pool.user_pool_id,
            },
            timeout=Duration.seconds(30),
            log_group=_log_group("ProfileLambda"),
        )

        self.policies_lambda = _lambda.Function(
            self, "PoliciesLambda",
            runtime=_lambda.Runtime.PYTHON_3_13,
            handler="index.handler", 
            code=_lambda.Code.from_asset("lambda/policies"),
            environment={
                "POLICIES_TABLE": self.policies_table.table_name,
                "PROFILES_TABLE": self.profiles_table.table_name,
                "USER_POOL_ID": auth_stack.user_pool.user_pool_id,
            },
            timeout=Duration.seconds(30),
            log_group=_log_group("PoliciesLambda"),
        )

        # Priorities ("what should I do today?"): rules over profiles, notes and
        # policies, no model call. Used by the Cockpit and as the get_priorities tool.
        self.priorities_lambda = _lambda.Function(
            self, "PrioritiesLambda",
            runtime=_lambda.Runtime.PYTHON_3_13,
            handler="index.handler",
            code=_lambda.Code.from_asset("lambda/priorities"),
            environment={
                "PROFILES_TABLE": self.profiles_table.table_name,
                "POLICIES_TABLE": self.policies_table.table_name,
                "USER_POOL_ID": auth_stack.user_pool.user_pool_id,
            },
            timeout=Duration.seconds(30),
            log_group=_log_group("PrioritiesLambda"),
        )

        self.portfolio_lambda = _lambda.Function(
            self, "PortfolioLambda",
            runtime=_lambda.Runtime.PYTHON_3_13,
            handler="index.handler",
            code=_lambda.Code.from_asset("lambda/portfolio"),
            environment={
                "PORTFOLIO_BUCKET": self.portfolio_bucket.bucket_name
            },
            timeout=Duration.seconds(30),
            log_group=_log_group("PortfolioLambda"),
        )

        self.promotions_lambda = _lambda.Function(
            self, "PromotionsLambda",
            runtime=_lambda.Runtime.PYTHON_3_13,
            handler="index.handler",
            code=_lambda.Code.from_asset("lambda/promotions"),
            environment={
                "PROMOTION_BUCKET": self.promotion_bucket.bucket_name
            },
            timeout=Duration.seconds(30),
            log_group=_log_group("PromotionsLambda"),
        )

        self.formschema_lambda = _lambda.Function(
            self, "FormSchemaLambda",
            runtime=_lambda.Runtime.PYTHON_3_13,
            handler="index.handler",
            code=_lambda.Code.from_asset("lambda/formschema"),
            environment={
                "FORMS_BUCKET": self.forms_bucket.bucket_name
            },
            timeout=Duration.seconds(30),
            log_group=_log_group("FormSchemaLambda"),
        )

        # Pricing tools (gateway Lambda targets). Two handlers from one code
        # directory, so each MCP tool has its own function and its own, minimal
        # permissions: the table reader needs only the rate files; the
        # estimator also reads the customer's profile and policies.
        self.pricing_table_lambda = _lambda.Function(
            self, "PricingTableLambda",
            runtime=_lambda.Runtime.PYTHON_3_13,
            handler="index.handler_table",
            code=_lambda.Code.from_asset("lambda/pricing"),
            environment={"PRICING_BUCKET": self.pricing_bucket.bucket_name},
            timeout=Duration.seconds(30),
            log_group=_log_group("PricingTableLambda"),
        )
        self.pricing_estimate_lambda = _lambda.Function(
            self, "PricingEstimateLambda",
            runtime=_lambda.Runtime.PYTHON_3_13,
            handler="index.handler_estimate",
            code=_lambda.Code.from_asset("lambda/pricing"),
            environment={
                "PRICING_BUCKET": self.pricing_bucket.bucket_name,
                "PROFILES_TABLE": self.profiles_table.table_name,
                "POLICIES_TABLE": self.policies_table.table_name,
            },
            timeout=Duration.seconds(30),
            log_group=_log_group("PricingEstimateLambda"),
        )
        # Savings-gap calculators (education, retirement): pure arithmetic, no data
        # access, so the Lambda needs no permissions beyond logging.
        self.calculators_lambda = _lambda.Function(
            self, "CalculatorsLambda",
            runtime=_lambda.Runtime.PYTHON_3_13,
            handler="index.handler",
            code=_lambda.Code.from_asset("lambda/calculators"),
            timeout=Duration.seconds(15),
            log_group=_log_group("CalculatorsLambda"),
        )
        self.company_lambda = _lambda.Function(
            self, "CompanyLambda",
            runtime=_lambda.Runtime.PYTHON_3_13,
            handler="index.handler",
            code=_lambda.Code.from_asset("lambda/company"),
            environment={
                "COMPANY_BUCKET": self.company_bucket.bucket_name
            },
            timeout=Duration.seconds(30),
            log_group=_log_group("CompanyLambda"),
        )

        self.competitive_lambda = _lambda.Function(
            self, "CompetitiveLambda",
            runtime=_lambda.Runtime.PYTHON_3_13,
            handler="index.handler",
            code=_lambda.Code.from_asset("lambda/competitive"),
            environment={
                "COMPETITIVE_BUCKET": self.competitive_bucket.bucket_name
            },
            timeout=Duration.seconds(30),
            log_group=_log_group("CompetitiveLambda"),
        )

        self.competitors_lambda = _lambda.Function(
            self, "CompetitorsLambda",
            runtime=_lambda.Runtime.PYTHON_3_13,
            handler="index.handler",
            code=_lambda.Code.from_asset("lambda/competitors"),
            environment={
                "COMPETITORS_BUCKET": self.competitors_bucket.bucket_name
            },
            timeout=Duration.seconds(30),
            log_group=_log_group("CompetitorsLambda"),
        )

        # Catalog Lambda — read-only facade over the catalog DynamoDB table.
        # Used by the React frontend's Comparator page to list product types
        # and products, and by the comparator Lambda to resolve product_ids
        # to S3 keys.
        self.catalog_lambda = _lambda.Function(
            self, "CatalogLambda",
            runtime=_lambda.Runtime.PYTHON_3_13,
            handler="index.handler",
            code=_lambda.Code.from_asset("lambda/catalog"),
            environment={
                "CATALOG_TABLE": self.catalog_table.table_name,
            },
            timeout=Duration.seconds(30),
            log_group=_log_group("CatalogLambda"),
        )

        # Data Explorer Lambda — read-only browser over the reference
        # documents that ground the assistant's answers. Backs the React
        # frontend's "Data" tab. Needs read access to every knowledge bucket
        # because the corpus is split one-bucket-per-s3-data-subfolder; the
        # Lambda resolves a caller-supplied folder ALIAS to the bucket name
        # so bucket names never leave the backend.
        self.data_lambda = _lambda.Function(
            self, "DataLambda",
            runtime=_lambda.Runtime.PYTHON_3_13,
            handler="index.handler",
            code=_lambda.Code.from_asset("lambda/data"),
            environment={
                "PORTFOLIO_BUCKET": self.portfolio_bucket.bucket_name,
                "COMPANY_BUCKET": self.company_bucket.bucket_name,
                "COMPETITIVE_BUCKET": self.competitive_bucket.bucket_name,
                "COMPETITORS_BUCKET": self.competitors_bucket.bucket_name,
                "PROMOTION_BUCKET": self.promotion_bucket.bucket_name,
                "FORMS_BUCKET": self.forms_bucket.bucket_name,
                "PRICING_BUCKET": self.pricing_bucket.bucket_name,
                "BROCHURES_BUCKET": self.brochures_bucket.bucket_name,
                "TRANSCRIPTS_BUCKET": self.transcripts_bucket.bucket_name,
                "USER_POOL_ID": auth_stack.user_pool.user_pool_id,
            },
            timeout=Duration.seconds(30),
            log_group=_log_group("DataLambda"),
        )

        # Bedrock Guardrail — single content/PII/word-policy resource shared
        # by every Bedrock caller in the system: the AgentCore text runtime
        # (insadv-03-agentcore), the AgentCore voice runtime (insadv-04-voice),
        # and the comparator + recommend Lambdas defined below. Lives in
        # tools_stack so the Lambdas in this stack can wire it directly
        # without creating a cross-stack cycle.
        self.guardrail = bedrock.CfnGuardrail(
            self, "InsuranceAdvisorGuardrail",
            # Name suffixed -shared to differentiate from the legacy guardrail
            # that previously lived in the agentcore stack. Necessary because
            # CloudFormation deploys insadv-02-tools BEFORE insadv-03-agentcore
            # (now that agentcore depends on tools), which means the new
            # guardrail tries to CREATE while the old one still exists. Same
            # name = AlreadyExists. Different name = clean migration.
            name="insurance-advisor-guardrail-shared",
            description="Guardrail for Insurance Advisor agent - filters harmful content, blocks off-topic requests, and protects PII",
            blocked_input_messaging="I'm sorry, I can't process that request. Please rephrase your question about insurance services.",
            blocked_outputs_messaging="I'm sorry, I can't provide that response. Please ask me about insurance-related topics.",
            content_policy_config=bedrock.CfnGuardrail.ContentPolicyConfigProperty(
                filters_config=[
                    bedrock.CfnGuardrail.ContentFilterConfigProperty(
                        type="SEXUAL", input_strength="HIGH", output_strength="HIGH"
                    ),
                    bedrock.CfnGuardrail.ContentFilterConfigProperty(
                        type="VIOLENCE", input_strength="HIGH", output_strength="HIGH"
                    ),
                    bedrock.CfnGuardrail.ContentFilterConfigProperty(
                        type="HATE", input_strength="HIGH", output_strength="HIGH"
                    ),
                    bedrock.CfnGuardrail.ContentFilterConfigProperty(
                        type="INSULTS", input_strength="HIGH", output_strength="HIGH"
                    ),
                    # LOW on purpose (demo decision, 2026-10-09). At HIGH this
                    # filter blocked harmless lead-in sentences in the text
                    # assistant's application-form flow ("I'll pull the profile
                    # and open the application to pre-fill what we know").
                    bedrock.CfnGuardrail.ContentFilterConfigProperty(
                        type="MISCONDUCT", input_strength="LOW", output_strength="LOW"
                    ),
                    # MEDIUM on purpose (demo decision, 2026-10-14). At HIGH this
                    # filter blocked a plain reply in the application-form flow
                    # ("death benefit is 2m, option a, premium frequency is
                    # monthly") with a LOW-confidence prompt-attack detection,
                    # because Bedrock reads the whole conversation, not the last
                    # message. MEDIUM ignores LOW-confidence detections and still
                    # blocks real injection attempts.
                    bedrock.CfnGuardrail.ContentFilterConfigProperty(
                        type="PROMPT_ATTACK", input_strength="MEDIUM", output_strength="NONE"
                    ),
                ]
            ),
            sensitive_information_policy_config=bedrock.CfnGuardrail.SensitiveInformationPolicyConfigProperty(
                pii_entities_config=[
                    # Financial PII — anonymize. The agent never legitimately
                    # quotes these to an advisor; if they appear, scrub before
                    # the model sees them in either direction.
                    bedrock.CfnGuardrail.PiiEntityConfigProperty(type="US_SOCIAL_SECURITY_NUMBER", action="ANONYMIZE"),
                    bedrock.CfnGuardrail.PiiEntityConfigProperty(type="CREDIT_DEBIT_CARD_NUMBER", action="ANONYMIZE"),
                    bedrock.CfnGuardrail.PiiEntityConfigProperty(type="US_BANK_ACCOUNT_NUMBER", action="ANONYMIZE"),
                    bedrock.CfnGuardrail.PiiEntityConfigProperty(type="CREDIT_DEBIT_CARD_CVV", action="ANONYMIZE"),
                    bedrock.CfnGuardrail.PiiEntityConfigProperty(type="CREDIT_DEBIT_CARD_EXPIRY", action="ANONYMIZE"),
                    bedrock.CfnGuardrail.PiiEntityConfigProperty(type="PIN", action="ANONYMIZE"),
                    bedrock.CfnGuardrail.PiiEntityConfigProperty(type="INTERNATIONAL_BANK_ACCOUNT_NUMBER", action="ANONYMIZE"),
                    bedrock.CfnGuardrail.PiiEntityConfigProperty(type="SWIFT_CODE", action="ANONYMIZE"),
                    # Government identifiers — anonymize. Advisors don't need
                    # passport / driver / ITIN numbers to flow through the
                    # chat surface; if uploaded in a policy doc, the
                    # extracted fields will be redacted by the guardrail
                    # before the agent reads them out.
                    bedrock.CfnGuardrail.PiiEntityConfigProperty(type="US_PASSPORT_NUMBER", action="ANONYMIZE"),
                    bedrock.CfnGuardrail.PiiEntityConfigProperty(type="DRIVER_ID", action="ANONYMIZE"),
                    bedrock.CfnGuardrail.PiiEntityConfigProperty(type="US_INDIVIDUAL_TAX_IDENTIFICATION_NUMBER", action="ANONYMIZE"),
                    # Hard secrets — block the whole request. These should
                    # never appear in advisor-to-agent chat traffic; if
                    # they do, refuse rather than scrub.
                    bedrock.CfnGuardrail.PiiEntityConfigProperty(type="PASSWORD", action="BLOCK"),
                    bedrock.CfnGuardrail.PiiEntityConfigProperty(type="AWS_ACCESS_KEY", action="BLOCK"),
                    bedrock.CfnGuardrail.PiiEntityConfigProperty(type="AWS_SECRET_KEY", action="BLOCK"),
                    # NAME / EMAIL / PHONE / ADDRESS / AGE / DATE_OF_BIRTH
                    # are deliberately NOT anonymized: they are integral to
                    # legitimate insurance advisor workflows (a customer's
                    # phone number on their policy doc, an advisor typing
                    # "add policy for Robert" etc). Anonymizing them would
                    # break the chat experience entirely. Output-side
                    # redaction in CloudWatch logs is tracked separately.
                ]
            ),
            word_policy_config=bedrock.CfnGuardrail.WordPolicyConfigProperty(
                managed_word_lists_config=[
                    bedrock.CfnGuardrail.ManagedWordsConfigProperty(type="PROFANITY")
                ]
            ),
            # Denied topics — keep the agent in its lane. Each topic is a
            # short prompt-style description plus 1-2 examples; Bedrock uses
            # those to detect the topic in input and output. We use type=DENY
            # so blocked turns return blocked_outputs_messaging instead of
            # leaking model output. Examples are intentionally specific to
            # insurance advisor edge cases (variable life sub-account
            # allocation -> investment advice; 1035 exchange tax treatment ->
            # tax advice).
            #
            # There is deliberately no underwriting/claims-promise topic. It
            # fired on routine product questions ("what are the eligibility
            # rules?") and on advisor narration ("for underwriting"), and this
            # is a demo. The agents' system prompts still forbid promising
            # approvals, rate classes or claim payouts.
            topic_policy_config=bedrock.CfnGuardrail.TopicPolicyConfigProperty(
                topics_config=[
                    # NOTE on definitions: each definition is intentionally
                    # narrow and ends with an explicit "allowed" carve-out.
                    # Without the carve-out, Bedrock topic detection over-fires
                    # on routine insurance-product vocabulary (e.g. "maternity
                    # benefits", "beneficiary", "smoker status") that overlaps
                    # with the medical/legal/etc. domain. The carve-out tells
                    # the topic classifier that those routine references are
                    # NOT in-scope for the DENY action.
                    bedrock.CfnGuardrail.TopicConfigProperty(
                        name="LegalAdvice",
                        type="DENY",
                        definition=(
                            "Recommending litigation or settlement strategy, or " +
                            "interpreting statutes and case law. Routine " +
                            "insurance-policy admin is not in scope."
                        ),
                        examples=[
                            "Should I sue my employer for wrongful termination?",
                            "What's the statute of limitations for filing a personal-injury lawsuit?",
                        ],
                    ),
                    bedrock.CfnGuardrail.TopicConfigProperty(
                        name="InvestmentRecommendations",
                        type="DENY",
                        definition=(
                            "Telling the customer which specific securities or " +
                            "sub-accounts to buy. Factual descriptions of " +
                            "variable-product investment options are allowed."
                        ),
                        examples=[
                            "What percentage should I put into the equity sub-account?",
                            "Which mutual fund is best for my variable life policy?",
                        ],
                    ),
                    bedrock.CfnGuardrail.TopicConfigProperty(
                        name="TaxAdvice",
                        type="DENY",
                        definition=(
                            "Calculating specific tax owed or recommending a " +
                            "tax-strategy action. General descriptions of tax " +
                            "treatment for insurance products are allowed."
                        ),
                        examples=[
                            "How much will I owe in taxes if I surrender this policy?",
                            "Can I deduct my long-term-care premiums on my federal return?",
                        ],
                    ),
                ]
            ),
            # Contextual grounding — flags responses that aren't anchored in
            # the source content the caller passes via guardContent blocks
            # (only comparator + recommend Lambdas tag sources today). Text
            # and voice agents have the policy attached but it stays inert
            # because Strands tool results aren't tagged as grounding
            # sources. GROUNDING=0.75 catches answers that drift from the
            # source documents; RELEVANCE=0.5 catches answers that drift
            # from the user's question. Higher = stricter.
            contextual_grounding_policy_config=bedrock.CfnGuardrail.ContextualGroundingPolicyConfigProperty(
                filters_config=[
                    bedrock.CfnGuardrail.ContextualGroundingFilterConfigProperty(
                        type="GROUNDING",
                        threshold=0.75,
                    ),
                    bedrock.CfnGuardrail.ContextualGroundingFilterConfigProperty(
                        type="RELEVANCE",
                        threshold=0.5,
                    ),
                ]
            ),
        )
        self.guardrail.apply_removal_policy(RemovalPolicy.DESTROY)

        # ---------------------------------------------------------------
        # Bedrock guardrail published-version handling.
        #
        # Why this isn't just a CfnGuardrailVersion: AWS::Bedrock::Guardrail
        # versions are immutable once published, so any policy change has
        # to publish a new version. The natural CFN pattern (export the
        # version through Fn::GetAtt and Fn::ImportValue across stacks)
        # deadlocks the moment two downstream stacks consume the export —
        # CloudFormation refuses to update an export's value while it's
        # imported. We tried half a dozen workarounds; all failed because
        # CFN's pre-flight validator pre-emptively flags any export that
        # could "potentially update."
        #
        # The pattern here decouples version publication from the cross-
        # stack export graph entirely:
        #   1. SSM Parameter Store holds the current published version
        #      under a fixed parameter name. SSM parameters can be
        #      updated freely — no "in-use" interlock.
        #   2. A Lambda-backed custom resource publishes a fresh
        #      guardrail version whenever the policy hash changes, then
        #      writes the version into SSM atomically via a single
        #      putParameter call.
        #   3. Cross-stack consumers (Lambdas + AgentCore runtimes)
        #      receive only the parameter NAME (a hard-coded string,
        #      not a CDK Token), which never changes. They read SSM at
        #      cold start to resolve the version. Granting them
        #      ssm:GetParameter on the parameter ARN is the only IAM
        #      change required.
        #
        # The cross-stack interface from this stack is therefore static
        # strings — there is no exported value that could ever update.
        # ---------------------------------------------------------------

        # Hash the policy spec so the publisher only re-runs when the
        # policy actually changes. The hash is embedded in the custom
        # resource's physical_resource_id; CFN treats a same-hash deploy
        # as a no-op.
        _policy_spec = json.dumps(
            self.guardrail.topic_policy_config,
            sort_keys=True,
            default=str,
        ) + json.dumps(
            self.guardrail.contextual_grounding_policy_config,
            sort_keys=True,
            default=str,
        ) + json.dumps(
            self.guardrail.content_policy_config,
            sort_keys=True,
            default=str,
        ) + json.dumps(
            self.guardrail.sensitive_information_policy_config,
            sort_keys=True,
            default=str,
        ) + json.dumps(
            self.guardrail.word_policy_config,
            sort_keys=True,
            default=str,
        )
        _policy_hash = hashlib.sha256(_policy_spec.encode()).hexdigest()[:12]

        # Constant SSM parameter name. Hard-coded so cross-stack consumers
        # can reference the path without resolving any CDK Token. Edit
        # cautiously — Lambdas and runtimes hardcode this same string in
        # their env.
        self.guardrail_version_param_name = (
            "/insadv/bedrock/guardrail-version"
        )

        # Pre-create the SSM parameter with a placeholder so consumers
        # that read it before the publisher has run get a graceful
        # fallback rather than a NotFoundException. The publisher
        # overwrites it with the real version on every deploy that
        # touches the policy.
        self.guardrail_version_param = ssm.StringParameter(
            self, "GuardrailVersionParam",
            parameter_name=self.guardrail_version_param_name,
            string_value="DRAFT",
            description=(
                "Bedrock guardrail published version. Written by the "
                "InsuranceAdvisorGuardrailVersionPublisher custom "
                "resource on every deploy where the policy hash "
                "changes. Read by the Lambdas and agent runtimes at "
                "cold start. Do not edit manually."
            ),
            tier=ssm.ParameterTier.STANDARD,
        )

        # Publisher — calls createGuardrailVersion when the policy
        # changes, captures the returned version, and writes it to
        # SSM. AwsCustomResource only supports a single SDK call per
        # phase, so we chain two AwsCustomResource instances: the first
        # publishes, the second copies the response into SSM.
        self._guardrail_version_publisher = cr.AwsCustomResource(
            self, "InsuranceAdvisorGuardrailVersionPublisher",
            on_create=cr.AwsSdkCall(
                service="bedrock",
                action="createGuardrailVersion",
                parameters={
                    "guardrailIdentifier": self.guardrail.attr_guardrail_id,
                    "description": f"spec hash {_policy_hash}",
                },
                physical_resource_id=cr.PhysicalResourceId.of(
                    f"GuardrailVersion-{_policy_hash}"
                ),
                # output_paths trims the response so CDK can resolve
                # `get_response_field("version")` on the next call.
                output_paths=["version"],
            ),
            on_update=cr.AwsSdkCall(
                service="bedrock",
                action="createGuardrailVersion",
                parameters={
                    "guardrailIdentifier": self.guardrail.attr_guardrail_id,
                    "description": f"spec hash {_policy_hash}",
                },
                physical_resource_id=cr.PhysicalResourceId.of(
                    f"GuardrailVersion-{_policy_hash}"
                ),
                output_paths=["version"],
            ),
            policy=cr.AwsCustomResourcePolicy.from_sdk_calls(
                resources=[self.guardrail.attr_guardrail_arn],
            ),
        )
        self._guardrail_version_publisher.node.add_dependency(self.guardrail)

        # SsmWriter — copies the publisher's `version` response into the
        # SSM parameter that consumers read. Keyed off the same hash so
        # it re-runs in lockstep with the publisher.
        self._guardrail_version_ssm_writer = cr.AwsCustomResource(
            self, "InsuranceAdvisorGuardrailVersionSsmWriter",
            on_create=cr.AwsSdkCall(
                service="ssm",
                action="putParameter",
                parameters={
                    "Name": self.guardrail_version_param_name,
                    "Value": self._guardrail_version_publisher.get_response_field("version"),
                    "Type": "String",
                    "Overwrite": True,
                },
                physical_resource_id=cr.PhysicalResourceId.of(
                    f"GuardrailVersionSsm-{_policy_hash}"
                ),
            ),
            on_update=cr.AwsSdkCall(
                service="ssm",
                action="putParameter",
                parameters={
                    "Name": self.guardrail_version_param_name,
                    "Value": self._guardrail_version_publisher.get_response_field("version"),
                    "Type": "String",
                    "Overwrite": True,
                },
                physical_resource_id=cr.PhysicalResourceId.of(
                    f"GuardrailVersionSsm-{_policy_hash}"
                ),
            ),
            policy=cr.AwsCustomResourcePolicy.from_sdk_calls(
                resources=[self.guardrail_version_param.parameter_arn],
            ),
        )
        self._guardrail_version_ssm_writer.node.add_dependency(
            self._guardrail_version_publisher
        )
        self._guardrail_version_ssm_writer.node.add_dependency(
            self.guardrail_version_param
        )

        # Comparator Lambda — fetches the selected products' markdown from
        # S3 and calls Bedrock (Claude Haiku 5.5) via the Converse API with
        # a tool-forced JSON schema, returning a structured comparison.
        # Longer timeout (60s) accounts for LLM generation time.
        self.comparator_lambda = _lambda.Function(
            self, "ComparatorLambda",
            runtime=_lambda.Runtime.PYTHON_3_13,
            handler="index.handler",
            code=_lambda.Code.from_asset("lambda/comparator"),
            environment={
                "CATALOG_TABLE": self.catalog_table.table_name,
                "PORTFOLIO_BUCKET": self.portfolio_bucket.bucket_name,
                "COMPETITORS_BUCKET": self.competitors_bucket.bucket_name,
                # An uploaded brochure is read back from the
                # advisor's own ingest/ prefix in the documents bucket.
                "DOCUMENTS_BUCKET": self.documents_bucket.bucket_name,
                "USER_POOL_ID": auth_stack.user_pool.user_pool_id,
                # Claude Haiku 5.5 keeps total Lambda time well under
                # API Gateway's 29s integration timeout. The comparison is
                # a forced-tool-use structured generation against grounded
                # markdown.
                "BEDROCK_MODEL_ID": TEXT_MODEL_ID,
            },
            timeout=Duration.seconds(60),
            memory_size=512,
            log_group=_log_group("ComparatorLambda"),
        )
        # Comparator ingest: reads one uploaded competitor brochure into a fixed
        # structure with Claude Haiku 5.5 (forced tool, shared guardrail, no
        # `temperature`) and stores the cleaned result under ingest/<advisor>/
        # in the documents bucket, which expires after a day. The model reads
        # the PDF directly, as extract_policy does.
        self.comparator_ingest_lambda = _lambda.Function(
            self, "ComparatorIngestLambda",
            runtime=_lambda.Runtime.PYTHON_3_13,
            handler="index.handler",
            code=_lambda.Code.from_asset("lambda/comparator_ingest"),
            environment={
                "DOCUMENTS_BUCKET": self.documents_bucket.bucket_name,
                "CATALOG_TABLE": self.catalog_table.table_name,
                "USER_POOL_ID": auth_stack.user_pool.user_pool_id,
                "BEDROCK_MODEL_ID": TEXT_MODEL_ID,
            },
            timeout=Duration.seconds(25),
            memory_size=512,
            log_group=_log_group("ComparatorIngestLambda"),
        )

        # Recommend Lambda — produces a per-customer coverage-gap analysis +
        # Awesome product recommendations. Reads the customer's profile,
        # full policy set, the Awesome product catalog, and current
        # promotions, then calls Bedrock Converse with a tool-forced JSON
        # schema. Same model + IAM shape as the comparator.
        self.recommend_lambda = _lambda.Function(
            self, "RecommendLambda",
            runtime=_lambda.Runtime.PYTHON_3_13,
            handler="index.handler",
            code=_lambda.Code.from_asset("lambda/recommend"),
            environment={
                "PROFILES_TABLE": self.profiles_table.table_name,
                "POLICIES_TABLE": self.policies_table.table_name,
                "CATALOG_TABLE": self.catalog_table.table_name,
                "PROMOTION_BUCKET": self.promotion_bucket.bucket_name,
                "USER_POOL_ID": auth_stack.user_pool.user_pool_id,
                # Same model as the comparator — same latency
                # constraints, same workload shape.
                "BEDROCK_MODEL_ID": TEXT_MODEL_ID,
            },
            timeout=Duration.seconds(60),
            memory_size=512,
            log_group=_log_group("RecommendLambda"),
        )

        # Meeting summary: drafts a Summary / Concerns / Next actions note from a
        # conversation. Drafts only; the advisor saves it through the notes route.
        self.meeting_summary_lambda = _lambda.Function(
            self, "MeetingSummaryLambda",
            runtime=_lambda.Runtime.PYTHON_3_13,
            handler="index.handler",
            code=_lambda.Code.from_asset("lambda/meeting_summary"),
            environment={
                "PROFILES_TABLE": self.profiles_table.table_name,
                "USER_POOL_ID": auth_stack.user_pool.user_pool_id,
                "BEDROCK_MODEL_ID": TEXT_MODEL_ID,
            },
            timeout=Duration.seconds(60),
            memory_size=512,
            log_group=_log_group("MeetingSummaryLambda"),
        )

        # Role-play score: coaching feedback on a practice conversation. Reads the
        # product sheets to check the advisor's product statements. Stores nothing.
        self.roleplay_score_lambda = _lambda.Function(
            self, "RoleplayScoreLambda",
            runtime=_lambda.Runtime.PYTHON_3_13,
            handler="index.handler",
            code=_lambda.Code.from_asset("lambda/roleplay_score"),
            environment={
                "PROFILES_TABLE": self.profiles_table.table_name,
                "PORTFOLIO_BUCKET": self.portfolio_bucket.bucket_name,
                "USER_POOL_ID": auth_stack.user_pool.user_pool_id,
                "BEDROCK_MODEL_ID": TEXT_MODEL_ID,
            },
            timeout=Duration.seconds(90),
            memory_size=512,
            log_group=_log_group("RoleplayScoreLambda"),
        )

        # Documents Lambda — short-lived presigned-URL signer for the
        # POST /documents/initiate route. Doesn't call Bedrock; doesn't
        # need a guardrail. Just mints the URL and returns it.
        self.documents_lambda = _lambda.Function(
            self, "DocumentsLambda",
            runtime=_lambda.Runtime.PYTHON_3_13,
            handler="index.handler",
            code=_lambda.Code.from_asset("lambda/documents"),
            environment={
                "DOCUMENTS_BUCKET": self.documents_bucket.bucket_name,
                "USER_POOL_ID": auth_stack.user_pool.user_pool_id,
            },
            timeout=Duration.seconds(15),
            memory_size=256,
            log_group=_log_group("DocumentsLambda"),
        )

        # Extract Policy Lambda — invoked by the AgentCore Gateway as an
        # MCP tool target. Reads the uploaded document from S3 and runs a
        # Haiku 5.5 Converse call with tool-forced JSON output to extract
        # structured policy fields the agent can hand straight to
        # create_third_party_policy. Longer timeout for vision / PDF parsing.
        self.extract_policy_lambda = _lambda.Function(
            self, "ExtractPolicyLambda",
            runtime=_lambda.Runtime.PYTHON_3_13,
            handler="index.handler",
            code=_lambda.Code.from_asset("lambda/extract_policy"),
            environment={
                "DOCUMENTS_BUCKET": self.documents_bucket.bucket_name,
                "BEDROCK_MODEL_ID": TEXT_MODEL_ID,
            },
            timeout=Duration.seconds(120),
            memory_size=512,
            log_group=_log_group("ExtractPolicyLambda"),
        )

        # Apply the shared guardrail to all three LLM-calling Lambdas.
        # The version is resolved at cold start by reading the SSM
        # parameter named in BEDROCK_GUARDRAIL_VERSION_PARAM_NAME — that
        # decouples guardrail-policy updates from the CFN export graph,
        # so policy changes can roll out without redeploying any stack.
        # See the publisher comment block above.
        for llm_lambda in (
            self.comparator_lambda,
            self.recommend_lambda,
            self.extract_policy_lambda,
            self.comparator_ingest_lambda,
            self.meeting_summary_lambda,
            self.roleplay_score_lambda,
        ):
            llm_lambda.add_environment(
                "BEDROCK_GUARDRAIL_ID", self.guardrail.attr_guardrail_id
            )
            llm_lambda.add_environment(
                "BEDROCK_GUARDRAIL_VERSION_PARAM_NAME",
                self.guardrail_version_param_name,
            )
            llm_lambda.add_to_role_policy(
                iam.PolicyStatement(
                    effect=iam.Effect.ALLOW,
                    actions=[
                        "bedrock:ApplyGuardrail",
                        "bedrock:GetGuardrail",
                    ],
                    resources=[self.guardrail.attr_guardrail_arn],
                )
            )
            # Allow the Lambda to read the SSM parameter that holds the
            # current guardrail version. Scoped to that single
            # parameter so a compromised Lambda can't read other
            # parameters in the namespace.
            self.guardrail_version_param.grant_read(llm_lambda)

        # Public sign-up is OFF by default. Anyone who can reach the API could
        # otherwise claim a demo account on a fresh deployment and use the
        # model-backed assistants at the deployer's expense. The two demo users
        # are created by `scripts/create-demo-users.sh` (run by deploy.sh) with a
        # generated password. To turn the open sign-up route back on, deploy with
        # `-c enable_public_signup=true` (and set `-c signup_allowed_emails=a@x,b@y`).
        self.public_signup_enabled = str(
            self.node.try_get_context("enable_public_signup") or "false"
        ).lower() == "true"
        self.signup_lambda = None
        if self.public_signup_enabled:
            # Sign-up Lambda: a public endpoint that creates Cognito users via
            # admin_create_user + admin_set_user_password. Needed because the
            # user pool is closed to self-service sign-up.
            self.signup_lambda = _lambda.Function(
                self, "SignupLambda",
                runtime=_lambda.Runtime.PYTHON_3_13,
                handler="index.handler",
                code=_lambda.Code.from_asset("lambda/signup"),
                environment={
                    "USER_POOL_ID": auth_stack.user_pool.user_pool_id,
                    "ALLOWED_EMAILS": self.node.try_get_context("signup_allowed_emails") or "",
                },
                timeout=Duration.seconds(15),
                log_group=_log_group("SignupLambda"),
            )
            self.signup_lambda.add_to_role_policy(
                iam.PolicyStatement(
                    effect=iam.Effect.ALLOW,
                    actions=[
                        "cognito-idp:AdminCreateUser",
                        "cognito-idp:AdminSetUserPassword",
                        "cognito-idp:AdminDeleteUser",
                    ],
                    resources=[auth_stack.user_pool.user_pool_arn],
                )
            )
        # Tells the front-end build whether to show the "Create account" tab.
        ssm.StringParameter(
            self, "SignupEnabledParam",
            parameter_name="/insurance-advisor/frontend/signup-enabled",
            string_value="true" if self.public_signup_enabled else "false",
            description="Whether the public /signup route is deployed (read by react-frontend/scripts/setup-env.sh)",
        )
        # Profile Lambda needs read/write — the agent can create and update
        # prospect records through create_profile / update_profile tool calls.
        self.profiles_table.grant_read_write_data(self.profile_lambda)
        # Policies lambda needs read/write because the agents can manage
        # third-party policies (create_third_party_policy, etc.) for the
        # advisor's customers. The lambda code enforces that only third-party
        # rows are mutated.
        self.policies_table.grant_read_write_data(self.policies_lambda)
        # Read-only: the customer's income, for the totals the assistants ask for.
        self.profiles_table.grant_read_data(self.policies_lambda)
        # Priorities reads the advisor's profiles and policies through the advisor-id index. Read-only.
        self.profiles_table.grant_read_data(self.priorities_lambda)
        self.policies_table.grant_read_data(self.priorities_lambda)

        # Grant Cognito permissions to profile and policies Lambda for user lookup
        self.profile_lambda.add_to_role_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "cognito-idp:AdminGetUser"
                ],
                resources=[auth_stack.user_pool.user_pool_arn]
            )
        )
        
        self.policies_lambda.add_to_role_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "cognito-idp:AdminGetUser"
                ],
                resources=[auth_stack.user_pool.user_pool_arn]
            )
        )
        self.priorities_lambda.add_to_role_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["cognito-idp:AdminGetUser"],
                resources=[auth_stack.user_pool.user_pool_arn]
            )
        )

        # Grant read-only S3 permissions to Lambda functions
        self.forms_bucket.grant_read(self.formschema_lambda)
        self.pricing_bucket.grant_read(self.pricing_table_lambda)
        self.pricing_bucket.grant_read(self.pricing_estimate_lambda)
        # The estimator reads one customer's profile and policies (the
        # advisor-id-index query is scoped by the advisor the gateway forwards).
        self.profiles_table.grant_read_data(self.pricing_estimate_lambda)
        self.policies_table.grant_read_data(self.pricing_estimate_lambda)
        self.portfolio_bucket.grant_read(self.portfolio_lambda)
        self.promotion_bucket.grant_read(self.promotions_lambda)
        self.company_bucket.grant_read(self.company_lambda)
        self.competitive_bucket.grant_read(self.competitive_lambda)
        self.competitors_bucket.grant_read(self.competitors_lambda)

        # Data Explorer Lambda: read-only access to every knowledge bucket it
        # can browse. Read-only by construction — there is no write path in
        # the handler, and grant_read only adds Get/List.
        self.portfolio_bucket.grant_read(self.data_lambda)
        self.company_bucket.grant_read(self.data_lambda)
        self.competitive_bucket.grant_read(self.data_lambda)
        self.competitors_bucket.grant_read(self.data_lambda)
        self.promotion_bucket.grant_read(self.data_lambda)
        self.forms_bucket.grant_read(self.data_lambda)
        self.pricing_bucket.grant_read(self.data_lambda)
        self.brochures_bucket.grant_read(self.data_lambda)
        # Voice transcripts: read-only. The handler lists only the signed-in
        # advisor's own prefix, which it learns by looking the caller up in
        # Cognito, so it also needs AdminGetUser (as the other advisor-scoped
        # Lambdas do).
        self.transcripts_bucket.grant_read(self.data_lambda)
        self.data_lambda.add_to_role_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["cognito-idp:AdminGetUser"],
                resources=[auth_stack.user_pool.user_pool_arn],
            )
        )
        # Catalog Lambda: read-only DynamoDB access (table + GSI).
        self.catalog_table.grant_read_data(self.catalog_lambda)

        # Comparator Lambda needs:
        # 1. Read the catalog table to resolve product_id -> s3 pointer
        # 2. Read markdown from portfolio and competitors buckets
        # 3. Call Bedrock Converse on the configured foundation model
        self.catalog_table.grant_read_data(self.comparator_lambda)
        self.portfolio_bucket.grant_read(self.comparator_lambda)
        self.competitors_bucket.grant_read(self.comparator_lambda)
        self.comparator_lambda.add_to_role_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "bedrock:InvokeModel",
                    "bedrock:InvokeModelWithResponseStream",
                ],
                # Cross-region inference profile for Claude Haiku 5.5. The
                # us.anthropic.* profile fans out to us-east-1, us-east-2
                # and us-west-2, so all three foundation models are
                # authorised, and nothing else.
                resources=[
                    *_text_model_arns(self.region, self.account),
                ]
            )
        )

        # Recommend Lambda needs:
        #  1. Read profiles + policies for the requested customer
        #  2. Read the catalog (Awesome products to recommend from)
        #  3. Read the promotion bucket
        #  4. Look up Cognito user email -> advisor_id
        #  5. Call Bedrock Converse on the configured inference profile
        self.profiles_table.grant_read_data(self.recommend_lambda)
        self.policies_table.grant_read_data(self.recommend_lambda)
        self.catalog_table.grant_read_data(self.recommend_lambda)
        self.promotion_bucket.grant_read(self.recommend_lambda)
        self.recommend_lambda.add_to_role_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["cognito-idp:AdminGetUser"],
                resources=[auth_stack.user_pool.user_pool_arn],
            )
        )
        self.recommend_lambda.add_to_role_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "bedrock:InvokeModel",
                    "bedrock:InvokeModelWithResponseStream",
                ],
                # Same shape as the comparator policy above.
                resources=[
                    *_text_model_arns(self.region, self.account),
                ],
            )
        )

        # Meeting summary: read the profile (to check the customer is the
        # advisor's), resolve the advisor, call the model. No writes.
        self.profiles_table.grant_read_data(self.meeting_summary_lambda)
        self.meeting_summary_lambda.add_to_role_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["cognito-idp:AdminGetUser"],
                resources=[auth_stack.user_pool.user_pool_arn],
            )
        )
        self.meeting_summary_lambda.add_to_role_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "bedrock:InvokeModel",
                    "bedrock:InvokeModelWithResponseStream",
                ],
                resources=[
                    *_text_model_arns(self.region, self.account),
                ],
            )
        )
        # Role-play score: read-only on the profile (ownership check) and the product sheets.
        self.profiles_table.grant_read_data(self.roleplay_score_lambda)
        self.portfolio_bucket.grant_read(self.roleplay_score_lambda)
        self.roleplay_score_lambda.add_to_role_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["cognito-idp:AdminGetUser"],
                resources=[auth_stack.user_pool.user_pool_arn],
            )
        )
        self.roleplay_score_lambda.add_to_role_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "bedrock:InvokeModel",
                    "bedrock:InvokeModelWithResponseStream",
                ],
                resources=[
                    *_text_model_arns(self.region, self.account),
                ],
            )
        )
        # Documents Lambda needs:
        #   1. Look up Cognito user email -> advisor_id
        #   2. Mint presigned PUT URLs against the documents bucket
        self.documents_lambda.add_to_role_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["cognito-idp:AdminGetUser"],
                resources=[auth_stack.user_pool.user_pool_arn],
            )
        )
        # generate_presigned_url requires s3:PutObject on the resource so
        # the IAM policy is evaluated as part of signature derivation.
        self.documents_bucket.grant_put(self.documents_lambda)

        # Extract Policy Lambda needs:
        #   1. Read the uploaded document from the documents bucket
        #   2. Call Bedrock Converse on the same Haiku 5.5 inference profile
        #      used by the comparator and recommender
        self.documents_bucket.grant_read(self.extract_policy_lambda)
        # Comparator ingest: read the advisor's upload; write the cleaned reading
        # under ingest/ only. The comparator then reads from ingest/ only.
        self.documents_bucket.grant_read(self.comparator_ingest_lambda)
        self.documents_bucket.grant_put(self.comparator_ingest_lambda, "ingest/*")
        self.documents_bucket.grant_read(self.comparator_lambda, "ingest/*")
        self.catalog_table.grant_read_data(self.comparator_ingest_lambda)
        for fn in (self.comparator_ingest_lambda, self.comparator_lambda):
            fn.add_to_role_policy(
                iam.PolicyStatement(
                    effect=iam.Effect.ALLOW,
                    actions=["cognito-idp:AdminGetUser"],
                    resources=[auth_stack.user_pool.user_pool_arn],
                )
            )
        self.comparator_ingest_lambda.add_to_role_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "bedrock:InvokeModel",
                    "bedrock:InvokeModelWithResponseStream",
                ],
                resources=[
                    *_text_model_arns(self.region, self.account),
                ],
            )
        )
        self.extract_policy_lambda.add_to_role_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "bedrock:InvokeModel",
                    "bedrock:InvokeModelWithResponseStream",
                ],
                resources=[
                    *_text_model_arns(self.region, self.account),
                ],
            )
        )

        # KMS key for CloudWatch log encryption. Mitigation M9: in addition
        # to bounding log retention to a finite window, we encrypt the
        # API Gateway access log group with a customer-managed key so
        # access requires both `logs:GetLogEvents` AND `kms:Decrypt` on
        # this key. Reduces the blast radius of an over-broad CloudWatch
        # read grant — the most common path for PII-in-logs leakage.
        # The per-Lambda log groups (explicit LogGroup resources created
        # via the `_log_group` helper above) are not encrypted with this
        # CMK — the key is defined here, after the functions — so their
        # content is bounded only by the ONE_MONTH retention set on each.
        # Encrypting them would require moving this key above the Lambda
        # definitions.
        self.log_kms_key = kms.Key(
            self, "LogsKmsKey",
            description="CMK for CloudWatch log groups in insadv-02-tools",
            enable_key_rotation=True,
            removal_policy=RemovalPolicy.DESTROY,
        )
        # CloudWatch Logs needs Encrypt/Decrypt/ReEncrypt/GenerateDataKey
        # permission on the key for any log group encrypted with it.
        self.log_kms_key.add_to_resource_policy(
            iam.PolicyStatement(
                sid="AllowCloudWatchLogsUseOfTheKey",
                effect=iam.Effect.ALLOW,
                principals=[iam.ServicePrincipal(f"logs.{self.region}.amazonaws.com")],
                actions=[
                    "kms:Encrypt*",
                    "kms:Decrypt*",
                    "kms:ReEncrypt*",
                    "kms:GenerateDataKey*",
                    "kms:Describe*",
                ],
                resources=["*"],
                conditions={
                    "ArnEquals": {
                        "kms:EncryptionContext:aws:logs:arn": (
                            f"arn:aws:logs:{self.region}:{self.account}:log-group:*"
                        )
                    }
                },
            )
        )

        # CloudWatch Log Group for API Gateway access logs
        self.api_log_group = logs.LogGroup(
            self, "ApiGatewayAccessLogs",
            log_group_name="/aws/apigateway/insurance-advisor-api",
            removal_policy=RemovalPolicy.DESTROY,
            retention=logs.RetentionDays.ONE_WEEK,
            encryption_key=self.log_kms_key,
        )

        # API Gateway with CORS enabled and CloudWatch logging
        # Note: CloudWatch role is assumed to be already configured at account level
        self.api = apigateway.RestApi(
            self, "InsuranceAdvisorApi",
            rest_api_name="Insurance Advisor API",
            description="API for Insurance Advisor AgentCore",
            cloud_watch_role=False,
            # Regional endpoint required: edge-optimized APIs front the API
            # with CloudFront, which strips Bearer Authorization headers from
            # non-GET/HEAD requests (CloudFront expects SigV4 only). That breaks
            # POST /profile (create_profile) and PUT /profile (update_profile).
            endpoint_configuration=apigateway.EndpointConfiguration(
                types=[apigateway.EndpointType.REGIONAL],
            ),
            deploy_options=apigateway.StageOptions(
                access_log_destination=apigateway.LogGroupLogDestination(self.api_log_group),
                access_log_format=apigateway.AccessLogFormat.clf(),
                logging_level=apigateway.MethodLoggingLevel.INFO,
                # Per-method throttling on the model-backed
                # routes. 5 rps steady-state with a 10-burst
                # is plenty for an interactive advisor click cadence
                # (the UI throttles user clicks anyway) and floors any
                # cost-bomb attempt. The cap is API-wide on the route
                # (not per-Cognito-user), but with only 2 allowlisted
                # advisors the difference is academic; the budget
                # alarm catches the rest.
                method_options={
                    "/comparator/compare/POST": apigateway.MethodDeploymentOptions(
                        throttling_rate_limit=5,
                        throttling_burst_limit=10,
                    ),
                    "/comparator/ingest/POST": apigateway.MethodDeploymentOptions(
                        throttling_rate_limit=5,
                        throttling_burst_limit=10,
                    ),
                    "/recommend/POST": apigateway.MethodDeploymentOptions(
                        throttling_rate_limit=5,
                        throttling_burst_limit=10,
                    ),
                    "/meeting-summary/POST": apigateway.MethodDeploymentOptions(
                        throttling_rate_limit=5,
                        throttling_burst_limit=10,
                    ),
                    "/roleplay-score/POST": apigateway.MethodDeploymentOptions(
                        throttling_rate_limit=5,
                        throttling_burst_limit=10,
                    ),
                },
            ),
            default_cors_preflight_options=apigateway.CorsOptions(
                allow_origins=apigateway.Cors.ALL_ORIGINS,
                allow_methods=apigateway.Cors.ALL_METHODS,
                allow_headers=["Content-Type", "X-Amz-Date", "Authorization", "X-Api-Key", "X-Amz-Security-Token"]
            ),
        )

        # API Gateway serves the *deployment snapshot* the stage points to, not the
        # live API definition. When a route is removed, CloudFormation creates the new
        # snapshot before it deletes the old method, so the snapshot can still contain
        # the removed route (it then answers 500 because its backend is gone). Tying the
        # snapshot's identity to the sign-up setting makes it be recreated when that
        # setting changes. After TURNING SIGN-UP OFF on an existing deployment, run
        # `cdk deploy` a second time so the new snapshot is taken without the route.
        # Bump DEPLOYMENT_REVISION to force a fresh snapshot for the same reason.
        DEPLOYMENT_REVISION = 2
        self.api.latest_deployment.add_to_logical_id(
            {"public_signup": self.public_signup_enabled, "revision": DEPLOYMENT_REVISION}
        )
        self.cognito_authorizer = apigateway.CognitoUserPoolsAuthorizer(
            self, "CognitoAuthorizer",
            cognito_user_pools=[auth_stack.user_pool]
        )

        # Request validator
        request_validator = apigateway.RequestValidator(
            self, "RequestValidator",
            rest_api=self.api,
            request_validator_name="validate-params",
            validate_request_parameters=True,
        )

        # API Gateway integrations for profile and policies
        profile_integration = apigateway.LambdaIntegration(self.profile_lambda)
        policies_integration = apigateway.LambdaIntegration(self.policies_lambda)
        
        # API Gateway resources and methods with Lambda authorization
        profile_resource = self.api.root.add_resource("profile")
        profile_resource.add_method(
            "GET", 
            profile_integration,
            authorizer=self.cognito_authorizer,
            authorization_type=apigateway.AuthorizationType.COGNITO,
            authorization_scopes=[USER_TOKEN_SCOPE],
            request_validator=request_validator,
        )
        profile_resource.add_method(
            "POST",
            profile_integration,
            authorizer=self.cognito_authorizer,
            authorization_type=apigateway.AuthorizationType.COGNITO,
            authorization_scopes=[USER_TOKEN_SCOPE],
            request_validator=request_validator,
        )
        profile_resource.add_method(
            "PUT",
            profile_integration,
            authorizer=self.cognito_authorizer,
            authorization_type=apigateway.AuthorizationType.COGNITO,
            authorization_scopes=[USER_TOKEN_SCOPE],
            request_validator=request_validator,
        )

        # Customer notes (life events and interactions), nested in the profile.
        # POST is called by the browser and, when the advisor asks the assistant
        # to record a note, by the AgentCore Gateway with the advisor's own token
        # (the interceptor adds a marker header, so the note is labelled as the
        # assistant's). DELETE is the same route family, but no gateway tool exists
        # for it and the Lambda refuses any call carrying the marker.
        notes_resource = profile_resource.add_resource("notes")
        notes_resource.add_method(
            "POST",
            profile_integration,
            authorizer=self.cognito_authorizer,
            authorization_type=apigateway.AuthorizationType.COGNITO,
            authorization_scopes=[USER_TOKEN_SCOPE],
            request_validator=request_validator,
        )
        note_item_resource = notes_resource.add_resource("{note_id}")
        note_item_resource.add_method(
            "DELETE",
            profile_integration,
            authorizer=self.cognito_authorizer,
            authorization_type=apigateway.AuthorizationType.COGNITO,
            authorization_scopes=[USER_TOKEN_SCOPE],
            request_validator=request_validator,
            request_parameters={"method.request.querystring.customer_id": True},
        )

        # GET /priorities: the advisor's ranked to-do list (Cockpit and get_priorities tool).
        priorities_resource = self.api.root.add_resource("priorities")
        priorities_resource.add_method(
            "GET",
            apigateway.LambdaIntegration(self.priorities_lambda),
            authorizer=self.cognito_authorizer,
            authorization_type=apigateway.AuthorizationType.COGNITO,
            authorization_scopes=[USER_TOKEN_SCOPE],
            request_validator=request_validator,
        )
        policy_resource = self.api.root.add_resource("policy") 
        policy_resource.add_method(
            "GET", 
            policies_integration,
            authorizer=self.cognito_authorizer,
            authorization_type=apigateway.AuthorizationType.COGNITO,
            authorization_scopes=[USER_TOKEN_SCOPE],
            request_validator=request_validator,
        )
        # POST/PUT/DELETE /policy operate ONLY on third-party policies. The
        # lambda enforces that server-side; the agents reach these via the
        # corresponding gateway tools (create/update/delete_third_party_policy).
        policy_resource.add_method(
            "POST",
            policies_integration,
            authorizer=self.cognito_authorizer,
            authorization_type=apigateway.AuthorizationType.COGNITO,
            authorization_scopes=[USER_TOKEN_SCOPE],
            request_validator=request_validator,
        )
        policy_resource.add_method(
            "PUT",
            policies_integration,
            authorizer=self.cognito_authorizer,
            authorization_type=apigateway.AuthorizationType.COGNITO,
            authorization_scopes=[USER_TOKEN_SCOPE],
            request_validator=request_validator,
        )
        policy_resource.add_method(
            "DELETE",
            policies_integration,
            authorizer=self.cognito_authorizer,
            authorization_type=apigateway.AuthorizationType.COGNITO,
            authorization_scopes=[USER_TOKEN_SCOPE],
            request_validator=request_validator,
        )

        # Catalog routes:
        #   GET /catalog/product-types
        #   GET /catalog/products?type=<type>
        #   GET /catalog/products/{product_id}
        # All share a single Lambda integration and the standard Cognito auth.
        catalog_integration = apigateway.LambdaIntegration(self.catalog_lambda)
        catalog_auth_kwargs = dict(
            authorizer=self.cognito_authorizer,
            authorization_type=apigateway.AuthorizationType.COGNITO,
            authorization_scopes=[USER_TOKEN_SCOPE],
            request_validator=request_validator,
        )

        catalog_resource = self.api.root.add_resource("catalog")
        catalog_types_resource = catalog_resource.add_resource("product-types")
        catalog_types_resource.add_method("GET", catalog_integration, **catalog_auth_kwargs)

        catalog_products_resource = catalog_resource.add_resource("products")
        catalog_products_resource.add_method("GET", catalog_integration, **catalog_auth_kwargs)

        catalog_product_item_resource = catalog_products_resource.add_resource("{product_id}")
        catalog_product_item_resource.add_method("GET", catalog_integration, **catalog_auth_kwargs)

        # Data Explorer routes (backs the frontend's "Data" tab):
        #   GET /data/files                       -> grouped listing of all docs
        #   GET /data/file?folder=<f>&key=<k>     -> one document's content
        # Cognito-authenticated like the catalog routes: this corpus includes
        # competitive battlecards and competitor analysis, which should not be
        # world-readable. Query params (not a {proxy+} path) so nested keys
        # such as products/bigrival/primecare-health.md need no encoding.
        data_integration = apigateway.LambdaIntegration(self.data_lambda)
        data_resource = self.api.root.add_resource("data")
        data_files_resource = data_resource.add_resource("files")
        data_files_resource.add_method("GET", data_integration, **catalog_auth_kwargs)
        data_file_resource = data_resource.add_resource("file")
        data_file_resource.add_method("GET", data_integration, **catalog_auth_kwargs)
        data_samples_resource = data_resource.add_resource("samples")
        data_samples_resource.add_method("GET", data_integration, **catalog_auth_kwargs)
        # Comparator route:
        #   POST /comparator/compare  with body { product_ids: [], locale }
        # `locale` is validated against SUPPORTED_LOCALES in the Lambda, which
        # is the single source of truth — don't re-enumerate locales here.
        comparator_integration = apigateway.LambdaIntegration(self.comparator_lambda)
        comparator_resource = self.api.root.add_resource("comparator")
        comparator_compare_resource = comparator_resource.add_resource("compare")
        comparator_compare_resource.add_method(
            "POST",
            comparator_integration,
            **catalog_auth_kwargs,
        )

        # Comparator ingest route:
        #   POST /comparator/ingest  with body { document_id, product_type }
        # Reads an uploaded competitor brochure. Same Cognito auth and the same
        # per-method throttle as the other LLM-backed routes; the handler also
        # needs the advisor's sign-in token (it refuses the machine token).
        comparator_ingest_integration = apigateway.LambdaIntegration(self.comparator_ingest_lambda)
        comparator_ingest_resource = comparator_resource.add_resource("ingest")
        comparator_ingest_resource.add_method(
            "POST",
            comparator_ingest_integration,
            **catalog_auth_kwargs,
        )
        # Recommend route:
        #   POST /recommend  with body { customer_id, locale }
        # Returns a structured coverage-gap analysis. Same Cognito auth as
        # the comparator route.
        recommend_integration = apigateway.LambdaIntegration(self.recommend_lambda)
        recommend_resource = self.api.root.add_resource("recommend")
        recommend_resource.add_method(
            "POST",
            recommend_integration,
            **catalog_auth_kwargs,
        )

        # Meeting summary route:
        #   POST /meeting-summary  with body { customer_id, turns, locale }
        # Returns a draft note. Same Cognito auth as the other model routes.
        meeting_summary_resource = self.api.root.add_resource("meeting-summary")
        meeting_summary_resource.add_method(
            "POST",
            apigateway.LambdaIntegration(self.meeting_summary_lambda),
            **catalog_auth_kwargs,
        )
        # Role-play score route:
        #   POST /roleplay-score  with body { customer_id, turns, difficulty, focus, locale }
        roleplay_score_resource = self.api.root.add_resource("roleplay-score")
        roleplay_score_resource.add_method(
            "POST",
            apigateway.LambdaIntegration(self.roleplay_score_lambda),
            **catalog_auth_kwargs,
        )
        # Sign-up route (opt-in, see `enable_public_signup`):
        #   POST /signup  with body { email, password }
        # Public and unauthenticated by design. The Lambda only accepts the
        # addresses in `signup_allowed_emails`, enforces a strong password and uses
        # the Cognito admin APIs to create and confirm the user. WAFv2 rate-limits
        # the route.
        if self.public_signup_enabled:
            signup_integration = apigateway.LambdaIntegration(self.signup_lambda)
            signup_resource = self.api.root.add_resource("signup")
            signup_resource.add_method(
                "POST",
                signup_integration,
                authorization_type=apigateway.AuthorizationType.NONE,
                request_validator=request_validator,
            )
        # Documents upload-init route:
        #   POST /documents/initiate  with body { filename, content_type, customer_id? }
        # Returns a presigned S3 PUT URL the SPA uses to upload the binary
        # directly to the documents bucket (bypasses API GW's 10 MB cap).
        # Authenticated; advisor_id derived from the JWT.
        documents_integration = apigateway.LambdaIntegration(self.documents_lambda)
        documents_resource = self.api.root.add_resource("documents")
        documents_initiate_resource = documents_resource.add_resource("initiate")
        documents_initiate_resource.add_method(
            "POST",
            documents_integration,
            **catalog_auth_kwargs,
        )

        # WAFv2 WebACL for API Gateway protection
        self.web_acl = wafv2.CfnWebACL(
            self, "ApiWebAcl",
            default_action=wafv2.CfnWebACL.DefaultActionProperty(allow={}),
            scope="REGIONAL",
            visibility_config=wafv2.CfnWebACL.VisibilityConfigProperty(
                cloud_watch_metrics_enabled=True,
                metric_name="InsuranceAdvisorApiWebAcl",
                sampled_requests_enabled=True,
            ),
            rules=[
                *([
                # Rate limit POST /signup at the edge so a bot army cannot
                # enumerate Cognito by spraying admin_create_user requests.
                # Abuse protection for the sign-up route. We scope the rate
                # limit to the signup URI so a sloppy IP that legitimately
                # uses the signed-in API doesn't get throttled by mistake.
                # 100 req / 5 min / source IP is roomy enough for retries
                # and dev testing but hard floor for bot traffic.
                wafv2.CfnWebACL.RuleProperty(
                    name="RateLimitSignup",
                    priority=0,
                    action=wafv2.CfnWebACL.RuleActionProperty(block={}),
                    statement=wafv2.CfnWebACL.StatementProperty(
                        rate_based_statement=wafv2.CfnWebACL.RateBasedStatementProperty(
                            limit=100,
                            aggregate_key_type="IP",
                            evaluation_window_sec=300,
                            scope_down_statement=wafv2.CfnWebACL.StatementProperty(
                                byte_match_statement=wafv2.CfnWebACL.ByteMatchStatementProperty(
                                    field_to_match=wafv2.CfnWebACL.FieldToMatchProperty(
                                        uri_path={}
                                    ),
                                    positional_constraint="ENDS_WITH",
                                    search_string="/signup",
                                    text_transformations=[
                                        wafv2.CfnWebACL.TextTransformationProperty(
                                            priority=0,
                                            type="LOWERCASE",
                                        )
                                    ],
                                )
                            ),
                        )
                    ),
                    visibility_config=wafv2.CfnWebACL.VisibilityConfigProperty(
                        cloud_watch_metrics_enabled=True,
                        metric_name="RateLimitSignup",
                        sampled_requests_enabled=True,
                    ),
                )
                ] if self.public_signup_enabled else []),
                wafv2.CfnWebACL.RuleProperty(
                    name="AWSManagedRulesCommonRuleSet",
                    priority=1,
                    override_action=wafv2.CfnWebACL.OverrideActionProperty(none={}),
                    statement=wafv2.CfnWebACL.StatementProperty(
                        managed_rule_group_statement=wafv2.CfnWebACL.ManagedRuleGroupStatementProperty(
                            vendor_name="AWS",
                            name="AWSManagedRulesCommonRuleSet",
                        )
                    ),
                    visibility_config=wafv2.CfnWebACL.VisibilityConfigProperty(
                        cloud_watch_metrics_enabled=True,
                        metric_name="AWSManagedRulesCommonRuleSet",
                        sampled_requests_enabled=True,
                    ),
                ),
            ],
        )

        # Associate WAFv2 WebACL with API Gateway stage
        wafv2.CfnWebACLAssociation(
            self, "ApiWebAclAssociation",
            resource_arn=f"arn:aws:apigateway:{self.region}::/restapis/{self.api.rest_api_id}/stages/{self.api.deployment_stage.stage_name}",
            web_acl_arn=self.web_acl.attr_arn,
        )

        # ---------------------------------------------------------------
        # Cost-bomb defense.
        # AWS Budgets alarm on the per-month Bedrock + Lambda spend in this
        # account. The threat is T_chat_loop / T_recommend_loop: a single
        # authenticated advisor (or an OAuth credential leak) loops the
        # LLM endpoints and racks up Bedrock spend before anyone notices.
        # We don't auto-stop traffic — that would be too aggressive for
        # a demo — but we publish a SNS topic that can be subscribed to
        # for early notification at 50 / 80 / 100 percent of budget.
        #
        # The dollar threshold and notification email are CDK context
        # values so they can be set per-environment without committing
        # personal information:
        #
        #   cdk deploy ... -c bedrock_budget_usd=200 \
        #                  -c bedrock_budget_alarm_email=alerts@example.com
        #
        # Default budget if no context is set: $50/month, no email
        # subscription (operator subscribes via the console).
        # ---------------------------------------------------------------
        budget_limit_usd = float(
            self.node.try_get_context("bedrock_budget_usd") or 50
        )
        alarm_email = self.node.try_get_context("bedrock_budget_alarm_email")

        self.budget_alarm_topic = sns.Topic(
            self,
            "BedrockBudgetAlarmTopic",
            display_name="insadv-bedrock-budget-alarms",
            master_key=self.log_kms_key,
        )
        if alarm_email:
            self.budget_alarm_topic.add_subscription(
                sns_subs.EmailSubscription(alarm_email)
            )

        # AWS Budgets needs explicit publish permission on the SNS topic.
        # The principal `budgets.amazonaws.com` only gets to publish to
        # this specific topic.
        self.budget_alarm_topic.add_to_resource_policy(
            iam.PolicyStatement(
                sid="AllowBudgetsToPublish",
                effect=iam.Effect.ALLOW,
                principals=[iam.ServicePrincipal("budgets.amazonaws.com")],
                actions=["SNS:Publish"],
                resources=[self.budget_alarm_topic.topic_arn],
            )
        )

        # AWS Budget scoped to Bedrock + Lambda spend in this account.
        # CostFilters on Service narrows the scope, so noisy other
        # services (CloudWatch, S3, etc.) don't trip the alarm.
        #
        # Billing splits Bedrock across several service names. Claude token
        # usage is billed under "Amazon Bedrock Service" and the runtimes,
        # gateway and memory under "Amazon Bedrock AgentCore"; plain
        # "Amazon Bedrock" only carries Nova Sonic and guardrail units.
        # Without the first two the alarm missed most of this app's spend.
        # The filter is by service, not by project, so it counts the whole
        # account's usage of these services.
        budgets.CfnBudget(
            self,
            "BedrockSpendBudget",
            budget=budgets.CfnBudget.BudgetDataProperty(
                budget_name="insadv-bedrock-and-lambda-monthly",
                budget_type="COST",
                time_unit="MONTHLY",
                budget_limit=budgets.CfnBudget.SpendProperty(
                    amount=budget_limit_usd,
                    unit="USD",
                ),
                cost_filters={
                    "Service": [
                        "Amazon Bedrock",
                        "Amazon Bedrock Service",
                        "Amazon Bedrock AgentCore",
                        "AWS Lambda",
                    ],
                },
            ),
            notifications_with_subscribers=[
                budgets.CfnBudget.NotificationWithSubscribersProperty(
                    notification=budgets.CfnBudget.NotificationProperty(
                        comparison_operator="GREATER_THAN",
                        notification_type="ACTUAL",
                        threshold=threshold_pct,
                        threshold_type="PERCENTAGE",
                    ),
                    subscribers=[
                        budgets.CfnBudget.SubscriberProperty(
                            subscription_type="SNS",
                            address=self.budget_alarm_topic.topic_arn,
                        ),
                    ],
                )
                for threshold_pct in (50, 80, 100)
            ],
        )

        # Lambda function for populating mock data
        self.mock_data_lambda = _lambda.Function(
            self, "MockDataPopulatorLambda",
            runtime=_lambda.Runtime.PYTHON_3_13,
            handler="index.lambda_handler",
            timeout=Duration.minutes(5),
            memory_size=256,
            code=_lambda.Code.from_asset("lambda/mock_data"),
            environment={
                "PROFILES_TABLE": self.profiles_table.table_name,
                "POLICIES_TABLE": self.policies_table.table_name,
                "CATALOG_TABLE": self.catalog_table.table_name,
                # Published by insadv-03-agentcore. Read at run time because that
                # stack is deployed after this one.
                "MEMORY_ID_PARAM": "/insurance-advisor/agentcore/ltm-memory-id",
            },
            description="Resets the demo to its seed: tables, dates and AgentCore memory",
            log_group=_log_group("MockDataPopulatorLambda"),
        )

        # Grant read/write permissions to the mock data Lambda
        self.profiles_table.grant_read_write_data(self.mock_data_lambda)
        self.policies_table.grant_read_write_data(self.mock_data_lambda)
        self.catalog_table.grant_read_write_data(self.mock_data_lambda)

        # Clearing AgentCore long-term memory on reset: find the memory id in SSM,
        # then list and delete its records. Memory ids are generated by the
        # agentcore stack, so the memory resource is a wildcard.
        self.mock_data_lambda.add_to_role_policy(iam.PolicyStatement(
            actions=["ssm:GetParameter"],
            resources=[f"arn:aws:ssm:{self.region}:{self.account}:parameter/insurance-advisor/agentcore/ltm-memory-id"],
        ))
        self.mock_data_lambda.add_to_role_policy(iam.PolicyStatement(
            actions=[
                "bedrock-agentcore:ListActors",
                "bedrock-agentcore:ListSessions",
                "bedrock-agentcore:ListMemoryRecords",
                "bedrock-agentcore:BatchDeleteMemoryRecords",
            ],
            resources=[f"arn:aws:bedrock-agentcore:{self.region}:{self.account}:memory/*"],
        ))
        # AwsCustomResource to invoke the Lambda function.
        # The physical_resource_id carries the seed hash and a deploy-time
        # stamp, so CloudFormation runs on_update (and so the reset) on every
        # deploy of this stack. The populator resets the demo to its seed, so
        # running it again is safe; it also means `cdk diff` always shows this
        # one resource as changed.
        seed_hash = _seed_data_hash()
        deploy_stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        populator_physical_id = cr.PhysicalResourceId.of(f"MockDataPopulator-{seed_hash}-{deploy_stamp}")

        invoke_sdk_call = cr.AwsSdkCall(
            service="Lambda",
            action="invoke",
            parameters={
                "FunctionName": self.mock_data_lambda.function_name,
                "Payload": "{}"
            },
            physical_resource_id=populator_physical_id
        )

        # We let CDK auto-create the worker role (no explicit `role=` param)
        # so the `policy=...` block is honoured. Passing an explicit role
        # silently disables `policy=`, which previously caused a deploy-time
        # race: the inline policy on the custom role wasn't attached before
        # the worker Lambda fired, leading to "lambda:InvokeFunction" auth
        # failures that rolled back the entire stack creation.
        #
        # We use from_statements rather than from_sdk_calls because CDK's
        # SDK-call → IAM-action mapping for `Lambda.invoke` resolves to
        # `lambda:Invoke` (incorrect), not `lambda:InvokeFunction`. So
        # we spell the permission out manually.
        self.mock_data_resource = cr.AwsCustomResource(
            self, "MockDataCustomResource",
            on_create=invoke_sdk_call,
            on_update=invoke_sdk_call,
            # The wrapper's default limit is 2 minutes; the populator has up to 5
            # (its own memory-clear budget is far shorter, see lambda/mock_data).
            timeout=Duration.minutes(5),
            policy=cr.AwsCustomResourcePolicy.from_statements(
                statements=[
                    iam.PolicyStatement(
                        effect=iam.Effect.ALLOW,
                        actions=["lambda:InvokeFunction"],
                        resources=[self.mock_data_lambda.function_arn],
                    )
                ],
            ),
        )

        # Ensure tables and Lambda are created before the custom resource runs
        self.mock_data_resource.node.add_dependency(self.profiles_table)
        self.mock_data_resource.node.add_dependency(self.policies_table)
        self.mock_data_resource.node.add_dependency(self.catalog_table)
        self.mock_data_resource.node.add_dependency(self.mock_data_lambda)

        # ---------------------------------------------------------------
        # Automatic reset: the demo goes back to its seed at least every 48
        # hours (every second day of the month; the longest gap is two days,
        # at month ends it is one). The hour is UTC, default 19:00 (03:00 in
        # UTC+8) so a reset does not land in the middle of a demo. Override with
        #   cdk deploy -c demo_reset_hour_utc=11   or  -c demo_reset_enabled=false
        # ---------------------------------------------------------------
        reset_hour = str(int(self.node.try_get_context("demo_reset_hour_utc") or 19))
        reset_enabled = str(self.node.try_get_context("demo_reset_enabled") or "true").lower() != "false"
        self.demo_reset_rule = events.Rule(
            self, "DemoResetSchedule",
            description="Resets the demo data to its seed at least every 48 hours",
            enabled=reset_enabled,
            schedule=events.Schedule.cron(minute="0", hour=reset_hour, day="1/2", month="*", year="*"),
            targets=[event_targets.LambdaFunction(
                self.mock_data_lambda,
                event=events.RuleTargetInput.from_object({"mode": "scheduled"}),
                retry_attempts=2,
            )],
        )
        # Two alarms: a reset that failed, and a reset that did not happen at all
        # (schedule disabled or deleted). Three days without a single run raises
        # the second; a run is guaranteed every second day, and every deploy runs one.
        self.demo_reset_failed_alarm = cloudwatch.Alarm(
            self, "DemoResetFailedAlarm",
            alarm_description="The demo reset Lambda failed",
            metric=self.mock_data_lambda.metric_errors(period=Duration.hours(1), statistic="Sum"),
            threshold=1,
            evaluation_periods=1,
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
        )
        self.demo_reset_missing_alarm = cloudwatch.Alarm(
            self, "DemoResetMissingAlarm",
            alarm_description="The demo has not been reset for three days",
            metric=self.mock_data_lambda.metric_invocations(period=Duration.days(1), statistic="Sum"),
            threshold=1,
            evaluation_periods=3,
            datapoints_to_alarm=3,
            comparison_operator=cloudwatch.ComparisonOperator.LESS_THAN_THRESHOLD,
            treat_missing_data=cloudwatch.TreatMissingData.BREACHING,
        )
        # Notify through the existing (encrypted) alarm topic. CloudWatch needs
        # permission to use the topic's key. The topic only emails someone when
        # `-c bedrock_budget_alarm_email=...` was given at deploy time.
        self.log_kms_key.add_to_resource_policy(iam.PolicyStatement(
            sid="AllowCloudWatchAlarmsToPublishToEncryptedTopic",
            effect=iam.Effect.ALLOW,
            principals=[iam.ServicePrincipal("cloudwatch.amazonaws.com")],
            actions=["kms:Decrypt", "kms:GenerateDataKey*"],
            resources=["*"],
            conditions={"StringEquals": {"aws:SourceAccount": self.account}},
        ))
        for alarm in (self.demo_reset_failed_alarm, self.demo_reset_missing_alarm):
            alarm.add_alarm_action(cw_actions.SnsAction(self.budget_alarm_topic))

        # Store API Gateway URL in SSM so the React frontend can pick it
        # up via setup-env.sh on each deploy.
        ssm.StringParameter(
            self, "ApiGatewayUrlParam",
            parameter_name="/insurance-advisor/api/gateway-url",
            string_value=self.api.url,
            description="API Gateway URL for the React frontend"
        )

        # Outputs
        CfnOutput(
            self, "ApiGatewayUrl",
            value=self.api.url,
            description="API Gateway URL"
        )

        CfnOutput(
            self, "ProfileLambdaArn",
            value=self.profile_lambda.function_arn,
            description="Profile Lambda Function ARN"
        )

        CfnOutput(
            self, "PoliciesLambdaArn",
            value=self.policies_lambda.function_arn,
            description="Policies Lambda Function ARN"
        )

        CfnOutput(
            self, "PortfolioLambdaArn",
            value=self.portfolio_lambda.function_arn,
            description="Portfolio Lambda Function ARN"
        )

        CfnOutput(
            self, "PromotionsLambdaArn",
            value=self.promotions_lambda.function_arn,
            description="Promotions Lambda Function ARN"
        )

        CfnOutput(
            self, "MockDataLambdaArn",
            value=self.mock_data_lambda.function_arn,
            description="Mock Data Populator Lambda Function ARN"
        )

        CfnOutput(
            self, "ApiGatewayLogGroupArn",
            value=self.api_log_group.log_group_arn,
            description="API Gateway CloudWatch Log Group ARN"
        )
