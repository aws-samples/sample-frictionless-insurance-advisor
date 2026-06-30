from aws_cdk import (
    Stack,
    aws_lambda as _lambda,
    aws_apigateway as apigateway,
    aws_bedrock as bedrock,
    aws_budgets as budgets,
    aws_s3 as s3,
    aws_s3_deployment as s3deploy,
    aws_dynamodb as dynamodb,
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
from pathlib import Path

from .auth_stack import AuthStack


def _seed_data_hash() -> str:
    """Hash of the mock seed JSON files.

    Used as the suffix on the MockDataPopulator custom resource's
    physical_resource_id so any edit to profiles.json or policies.json
    changes the resource identity, which triggers CloudFormation to run
    `on_update` and re-seed the DynamoDB tables on the next deploy.

    The mock_data Lambda uses put_item on every row, so re-running the
    populator is idempotent — it overwrites matching primary keys and
    adds any new ones. Rows deleted from the seed files are not removed
    from DynamoDB; that requires a manual cleanup or stack destroy.
    """
    seed_dir = Path(__file__).resolve().parent.parent / "lambda" / "mock_data"
    sha = hashlib.sha256()
    for filename in ("profiles.json", "policies.json", "catalog.json"):
        sha.update((seed_dir / filename).read_bytes())
    return sha.hexdigest()[:12]


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
        #   - server-side encryption (S3-managed for now; threat model
        #     flagged that customer-managed KMS would be the production
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
            # DynamoDB TTL auto-deletes them ~24h later. Seed/mock rows and
            # Unicorn-issued policies do NOT set this attribute, so TTL leaves
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
                "USER_POOL_ID": auth_stack.user_pool.user_pool_id,
            },
            timeout=Duration.seconds(30),
            log_group=_log_group("PoliciesLambda"),
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
                    bedrock.CfnGuardrail.ContentFilterConfigProperty(
                        type="MISCONDUCT", input_strength="HIGH", output_strength="HIGH"
                    ),
                    bedrock.CfnGuardrail.ContentFilterConfigProperty(
                        type="PROMPT_ATTACK", input_strength="HIGH", output_strength="NONE"
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
                    # before the agent reads them out. Threat model M12.
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
            # tax advice; "are you covered?" -> binding underwriting).
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
                        name="UnderwritingDecisions",
                        type="DENY",
                        definition=(
                            "Promising specific approvals, rate classes, or " +
                            "claim payouts. General descriptions of underwriting " +
                            "and claims processes are allowed."
                        ),
                        examples=[
                            "Will I be approved for the term life policy at preferred rates?",
                            "Is my heart-attack claim going to be paid out?",
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
        # S3 and calls Bedrock (Claude Sonnet 4.5) via the Converse API with
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
                # Use Claude Haiku 4.5 to keep total Lambda time well under
                # API Gateway's 29s integration timeout. The comparison is
                # a forced-tool-use structured generation against grounded
                # markdown — Haiku produces the same shape at ~3-5x lower
                # latency than Sonnet for this workload.
                "BEDROCK_MODEL_ID": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
            },
            timeout=Duration.seconds(60),
            memory_size=512,
            log_group=_log_group("ComparatorLambda"),
        )

        # Recommend Lambda — produces a per-customer coverage-gap analysis +
        # Unicorn product recommendations. Reads the customer's profile,
        # full policy set, the Unicorn product catalog, and current
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
                # Same Haiku model as the comparator — same latency
                # constraints, same workload shape.
                "BEDROCK_MODEL_ID": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
            },
            timeout=Duration.seconds(60),
            memory_size=512,
            log_group=_log_group("RecommendLambda"),
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
        # Sonnet 4.5 Converse call with tool-forced JSON output to extract
        # structured policy fields the agent can hand straight to
        # create_third_party_policy. Longer timeout for vision / PDF parsing.
        self.extract_policy_lambda = _lambda.Function(
            self, "ExtractPolicyLambda",
            runtime=_lambda.Runtime.PYTHON_3_13,
            handler="index.handler",
            code=_lambda.Code.from_asset("lambda/extract_policy"),
            environment={
                "DOCUMENTS_BUCKET": self.documents_bucket.bucket_name,
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

        # Sign-up Lambda — public endpoint that creates new Cognito users
        # via admin_create_user + admin_set_user_password. Required because
        # this account enforces AllowAdminCreateUserOnly=True via an
        # organisation policy, which blocks the SPA's self-service SignUp
        # call. The admin_create_user + admin_set_user_password pattern is
        # the supported workaround.
        self.signup_lambda = _lambda.Function(
            self, "SignupLambda",
            runtime=_lambda.Runtime.PYTHON_3_13,
            handler="index.handler",
            code=_lambda.Code.from_asset("lambda/signup"),
            environment={
                "USER_POOL_ID": auth_stack.user_pool.user_pool_id,
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

        # Profile Lambda needs read/write — the agent can create and update
        # prospect records through create_profile / update_profile tool calls.
        self.profiles_table.grant_read_write_data(self.profile_lambda)
        # Policies lambda needs read/write because the agents can manage
        # third-party policies (create_third_party_policy, etc.) for the
        # advisor's customers. The lambda code enforces that only third-party
        # rows are mutated.
        self.policies_table.grant_read_write_data(self.policies_lambda)

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

        # Grant read-only S3 permissions to Lambda functions
        self.portfolio_bucket.grant_read(self.portfolio_lambda)
        self.promotion_bucket.grant_read(self.promotions_lambda)
        self.company_bucket.grant_read(self.company_lambda)
        self.competitive_bucket.grant_read(self.competitive_lambda)
        self.competitors_bucket.grant_read(self.competitors_lambda)

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
                # Cross-region inference profiles for Claude Haiku 4.5.
                # Comparator was on Sonnet 4.5 originally but moved to
                # Haiku to keep total Lambda time under API Gateway's 29s
                # integration timeout. The us.anthropic.* profile fans
                # out to us-east-1, us-east-2, and us-west-2 foundation
                # models, so we authorise all three. Sonnet 4.5 stays
                # in the allowlist as a fallback in case BEDROCK_MODEL_ID
                # is overridden.
                resources=[
                    f"arn:aws:bedrock:{self.region}:{self.account}:inference-profile/us.anthropic.claude-haiku-4-5-*",
                    "arn:aws:bedrock:us-east-1::foundation-model/anthropic.claude-haiku-4-5-*",
                    "arn:aws:bedrock:us-east-2::foundation-model/anthropic.claude-haiku-4-5-*",
                    "arn:aws:bedrock:us-west-2::foundation-model/anthropic.claude-haiku-4-5-*",
                    f"arn:aws:bedrock:{self.region}:{self.account}:inference-profile/us.anthropic.claude-sonnet-4-5-*",
                    "arn:aws:bedrock:us-east-1::foundation-model/anthropic.claude-sonnet-4-5-*",
                    "arn:aws:bedrock:us-east-2::foundation-model/anthropic.claude-sonnet-4-5-*",
                    "arn:aws:bedrock:us-west-2::foundation-model/anthropic.claude-sonnet-4-5-*",
                ]
            )
        )

        # Recommend Lambda needs:
        #  1. Read profiles + policies for the requested customer
        #  2. Read the catalog (Unicorn products to recommend from)
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
                # Same shape as the comparator policy above. Recommend now
                # runs on Haiku 4.5 too; Sonnet 4.5 stays in as a fallback
                # via the BEDROCK_MODEL_ID env override.
                resources=[
                    f"arn:aws:bedrock:{self.region}:{self.account}:inference-profile/us.anthropic.claude-haiku-4-5-*",
                    "arn:aws:bedrock:us-east-1::foundation-model/anthropic.claude-haiku-4-5-*",
                    "arn:aws:bedrock:us-east-2::foundation-model/anthropic.claude-haiku-4-5-*",
                    "arn:aws:bedrock:us-west-2::foundation-model/anthropic.claude-haiku-4-5-*",
                    f"arn:aws:bedrock:{self.region}:{self.account}:inference-profile/us.anthropic.claude-sonnet-4-5-*",
                    "arn:aws:bedrock:us-east-1::foundation-model/anthropic.claude-sonnet-4-5-*",
                    "arn:aws:bedrock:us-east-2::foundation-model/anthropic.claude-sonnet-4-5-*",
                    "arn:aws:bedrock:us-west-2::foundation-model/anthropic.claude-sonnet-4-5-*",
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
        #   2. Call Bedrock Converse on the same Sonnet inference profile
        #      used by the comparator and recommender
        self.documents_bucket.grant_read(self.extract_policy_lambda)
        self.extract_policy_lambda.add_to_role_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "bedrock:InvokeModel",
                    "bedrock:InvokeModelWithResponseStream",
                ],
                resources=[
                    f"arn:aws:bedrock:{self.region}:{self.account}:inference-profile/us.anthropic.claude-sonnet-4-5-*",
                    "arn:aws:bedrock:us-east-1::foundation-model/anthropic.claude-sonnet-4-5-*",
                    "arn:aws:bedrock:us-east-2::foundation-model/anthropic.claude-sonnet-4-5-*",
                    "arn:aws:bedrock:us-west-2::foundation-model/anthropic.claude-sonnet-4-5-*",
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
                # Threat model M4 — per-method throttling on the two
                # LLM-backed routes. 5 rps steady-state with a 10-burst
                # is plenty for an interactive advisor click cadence
                # (the UI throttles user clicks anyway) and floors any
                # cost-bomb attempt. The cap is API-wide on the route
                # (not per-Cognito-user), but with only 2 allowlisted
                # advisors the difference is academic; the M5 budget
                # alarm catches the rest.
                method_options={
                    "/comparator/compare/POST": apigateway.MethodDeploymentOptions(
                        throttling_rate_limit=5,
                        throttling_burst_limit=10,
                    ),
                    "/recommend/POST": apigateway.MethodDeploymentOptions(
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
            authorization_scopes=[
                "insurance-advisor-api/api.access",  # For M2M
                "aws.cognito.signin.user.admin"      # For user access tokens
            ],
            request_validator=request_validator,
        )
        profile_resource.add_method(
            "POST",
            profile_integration,
            authorizer=self.cognito_authorizer,
            authorization_type=apigateway.AuthorizationType.COGNITO,
            authorization_scopes=[
                "insurance-advisor-api/api.access",
                "aws.cognito.signin.user.admin"
            ],
            request_validator=request_validator,
        )
        profile_resource.add_method(
            "PUT",
            profile_integration,
            authorizer=self.cognito_authorizer,
            authorization_type=apigateway.AuthorizationType.COGNITO,
            authorization_scopes=[
                "insurance-advisor-api/api.access",
                "aws.cognito.signin.user.admin"
            ],
            request_validator=request_validator,
        )

        policy_resource = self.api.root.add_resource("policy") 
        policy_resource.add_method(
            "GET", 
            policies_integration,
            authorizer=self.cognito_authorizer,
            authorization_type=apigateway.AuthorizationType.COGNITO,
            authorization_scopes=[
                "insurance-advisor-api/api.access",  # For M2M
                "aws.cognito.signin.user.admin"      # For user access tokens
            ],
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
            authorization_scopes=[
                "insurance-advisor-api/api.access",
                "aws.cognito.signin.user.admin",
            ],
            request_validator=request_validator,
        )
        policy_resource.add_method(
            "PUT",
            policies_integration,
            authorizer=self.cognito_authorizer,
            authorization_type=apigateway.AuthorizationType.COGNITO,
            authorization_scopes=[
                "insurance-advisor-api/api.access",
                "aws.cognito.signin.user.admin",
            ],
            request_validator=request_validator,
        )
        policy_resource.add_method(
            "DELETE",
            policies_integration,
            authorizer=self.cognito_authorizer,
            authorization_type=apigateway.AuthorizationType.COGNITO,
            authorization_scopes=[
                "insurance-advisor-api/api.access",
                "aws.cognito.signin.user.admin",
            ],
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
            authorization_scopes=[
                "insurance-advisor-api/api.access",
                "aws.cognito.signin.user.admin",
            ],
            request_validator=request_validator,
        )

        catalog_resource = self.api.root.add_resource("catalog")
        catalog_types_resource = catalog_resource.add_resource("product-types")
        catalog_types_resource.add_method("GET", catalog_integration, **catalog_auth_kwargs)

        catalog_products_resource = catalog_resource.add_resource("products")
        catalog_products_resource.add_method("GET", catalog_integration, **catalog_auth_kwargs)

        catalog_product_item_resource = catalog_products_resource.add_resource("{product_id}")
        catalog_product_item_resource.add_method("GET", catalog_integration, **catalog_auth_kwargs)

        # Comparator route:
        #   POST /comparator/compare  with body { product_ids: [], locale: "en|ja|ko|es" }
        comparator_integration = apigateway.LambdaIntegration(self.comparator_lambda)
        comparator_resource = self.api.root.add_resource("comparator")
        comparator_compare_resource = comparator_resource.add_resource("compare")
        comparator_compare_resource.add_method(
            "POST",
            comparator_integration,
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

        # Sign-up route:
        #   POST /signup  with body { email, password }
        # Public, unauthenticated. The Lambda enforces a strong password
        # and uses Cognito admin APIs to create + confirm the user. WAFv2
        # protects this endpoint via the AWS managed common rule set.
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
                # Rate limit POST /signup at the edge so a bot army cannot
                # enumerate Cognito by spraying admin_create_user requests.
                # Mitigation M2 from the threat model. We scope the rate
                # limit to the signup URI so a sloppy IP that legitimately
                # uses the signed-in API doesn't get throttled by mistake.
                # 100 req / 5 min / source IP is roomy enough for retries
                # and dev testing but hard floor for the bot scenarios in
                # threats T3, T16.
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
                ),
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
        # Threat model M5 — Cost-bomb defense.
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
            timeout=Duration.minutes(2),
            memory_size=256,
            code=_lambda.Code.from_asset("lambda/mock_data"),
            environment={
                "PROFILES_TABLE": self.profiles_table.table_name,
                "POLICIES_TABLE": self.policies_table.table_name,
                "CATALOG_TABLE": self.catalog_table.table_name,
            },
            description="Lambda function for populating mock insurance data",
            log_group=_log_group("MockDataPopulatorLambda"),
        )

        # Grant read/write permissions to the mock data Lambda
        self.profiles_table.grant_read_write_data(self.mock_data_lambda)
        self.policies_table.grant_read_write_data(self.mock_data_lambda)
        self.catalog_table.grant_read_write_data(self.mock_data_lambda)

        # AwsCustomResource to invoke the Lambda function
        # Hash the seed files so the physical_resource_id changes whenever
        # profiles.json or policies.json is edited. That change triggers
        # CloudFormation to run on_update on the next deploy, which re-invokes
        # the populator Lambda. The populator uses put_item (idempotent), so
        # rerunning is safe — new rows are inserted, existing rows are
        # overwritten.
        seed_hash = _seed_data_hash()
        populator_physical_id = cr.PhysicalResourceId.of(f"MockDataPopulator-{seed_hash}")

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
