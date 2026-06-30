"""Frontend Stack - S3 + CloudFront hosting for the React app.

The React build is purely static (Cognito JWT only, no IAM creds in the
browser), so this stack:

- Stores the built `react-frontend/dist/` in a private S3 bucket
- Serves it via CloudFront with Origin Access Control (OAC)
- Rewrites 403/404 to /index.html so the SPA's client-side routing works
- Invalidates the distribution on every redeploy

The React app must already be built before this stack is synthesized;
`deploy.sh` takes care of that ordering.
"""
from pathlib import Path

from aws_cdk import (
    CfnOutput,
    Duration,
    RemovalPolicy,
    Stack,
    aws_cloudfront as cloudfront,
    aws_cloudfront_origins as origins,
    aws_s3 as s3,
    aws_s3_deployment as s3deploy,
    aws_ssm as ssm,
)
from constructs import Construct


REACT_DIST_DIR = "react-frontend/dist"


class FrontendStack(Stack):
    """Static hosting for the Insurance Advisor React frontend."""

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Fail synthesis early with a clear message if the React app has not
        # been built yet — otherwise BucketDeployment would silently upload
        # an empty bucket.
        dist_path = Path(REACT_DIST_DIR)
        if not dist_path.exists() or not any(dist_path.iterdir()):
            raise FileNotFoundError(
                f"{REACT_DIST_DIR}/ is missing or empty. Build the React app first "
                "(deploy.sh handles this automatically: backend stacks → npm build → frontend stack)."
            )

        # ----- Access log bucket (S3 server access logs) ----------------
        self.access_log_bucket = s3.Bucket(
            self, "AccessLogBucket",
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            enforce_ssl=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            # Server access logs require ACL-based delivery
            object_ownership=s3.ObjectOwnership.OBJECT_WRITER,
        )

        # ----- Site bucket (private, served via OAC) --------------------
        self.site_bucket = s3.Bucket(
            self, "SiteBucket",
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            enforce_ssl=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            server_access_logs_bucket=self.access_log_bucket,
            server_access_logs_prefix="site-logs/",
        )

        # ----- CloudFront access log bucket -----------------------------
        self.cf_log_bucket = s3.Bucket(
            self, "CloudFrontLogBucket",
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            enforce_ssl=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            # CloudFront log delivery requires ACLs on the destination bucket
            object_ownership=s3.ObjectOwnership.OBJECT_WRITER,
        )

        # ----- CloudFront distribution with OAC -------------------------
        self.distribution = cloudfront.Distribution(
            self, "Distribution",
            default_behavior=cloudfront.BehaviorOptions(
                origin=origins.S3BucketOrigin.with_origin_access_control(self.site_bucket),
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                allowed_methods=cloudfront.AllowedMethods.ALLOW_GET_HEAD,
                cached_methods=cloudfront.CachedMethods.CACHE_GET_HEAD,
                compress=True,
            ),
            default_root_object="index.html",
            # SPA routing: any 403/404 from S3 returns index.html with 200
            error_responses=[
                cloudfront.ErrorResponse(
                    http_status=403,
                    response_http_status=200,
                    response_page_path="/index.html",
                    ttl=Duration.seconds(0),
                ),
                cloudfront.ErrorResponse(
                    http_status=404,
                    response_http_status=200,
                    response_page_path="/index.html",
                    ttl=Duration.seconds(0),
                ),
            ],
            log_bucket=self.cf_log_bucket,
            log_includes_cookies=False,
            minimum_protocol_version=cloudfront.SecurityPolicyProtocol.TLS_V1_2_2021,
        )

        # ----- Upload built React app and invalidate CloudFront cache ---
        self.deployment = s3deploy.BucketDeployment(
            self, "SiteDeployment",
            sources=[s3deploy.Source.asset(REACT_DIST_DIR)],
            destination_bucket=self.site_bucket,
            distribution=self.distribution,
            distribution_paths=["/*"],
            retain_on_delete=False,
        )

        # ----- SSM + Outputs --------------------------------------------
        site_url = f"https://{self.distribution.distribution_domain_name}"

        ssm.StringParameter(
            self, "SiteUrlParam",
            parameter_name="/insurance-advisor/frontend/site-url",
            string_value=site_url,
            description="CloudFront URL hosting the React frontend",
        )

        CfnOutput(
            self, "SiteUrl",
            value=site_url,
            description="CloudFront URL serving the React frontend",
        )

        CfnOutput(
            self, "SiteBucketName",
            value=self.site_bucket.bucket_name,
            description="S3 bucket name holding the React build artifacts",
        )

        CfnOutput(
            self, "DistributionId",
            value=self.distribution.distribution_id,
            description="CloudFront distribution ID",
        )
