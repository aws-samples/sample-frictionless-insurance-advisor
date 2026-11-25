"""Frontend Stack - S3 + CloudFront hosting for the React app.

The React build is purely static (Cognito JWT only, no IAM creds in the
browser), so this stack:

- Stores the built `react-frontend/dist/` in a private S3 bucket
- Serves it via CloudFront with Origin Access Control (OAC)
- Sends a Content-Security-Policy, a Permissions-Policy and the other security headers
  with every response
- Sets Cache-Control on the uploaded objects: hashed assets and fonts are immutable,
  everything else (index.html included) is revalidated on each use
- Answers unknown paths with a 404 page (the app has no client-side routes)
- Invalidates the distribution on every redeploy
- Optionally serves the distribution under a custom domain name with an existing ACM certificate
  (CDK context `custom_domain` and `custom_domain_certificate_arn`); the CloudFront URL keeps working

The React app must already be built before this stack is synthesized;
`deploy.sh` takes care of that ordering.
"""
import re
from pathlib import Path

from aws_cdk import (
    CfnOutput,
    Duration,
    RemovalPolicy,
    Stack,
    aws_apigateway as apigateway,
    aws_certificatemanager as acm,
    aws_cloudfront as cloudfront,
    aws_cloudfront_origins as origins,
    aws_s3 as s3,
    aws_s3_deployment as s3deploy,
    aws_ssm as ssm,
)
from constructs import Construct


REACT_DIST_DIR = "react-frontend/dist"

# CDK context keys for the optional custom domain. Both are set together or not at all; with neither set the
# distribution keeps its cloudfront.net name and the CloudFront default certificate.
CUSTOM_DOMAIN_CONTEXT_KEY = "custom_domain"
CUSTOM_DOMAIN_CERTIFICATE_CONTEXT_KEY = "custom_domain_certificate_arn"

# A CloudFront distribution accepts only an ACM certificate from us-east-1.
_CERTIFICATE_ARN_PATTERN = re.compile(r"^arn:aws:acm:us-east-1:\d{12}:certificate/[0-9a-fA-F-]{36}$")
_DOMAIN_NAME_PATTERN = re.compile(
    r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$"
)


def read_custom_domain(node) -> tuple[str, str] | None:
    """Returns (domain name, ACM certificate ARN) from the CDK context, or None when neither is set."""
    domain = str(node.try_get_context(CUSTOM_DOMAIN_CONTEXT_KEY) or "").strip().lower()
    certificate_arn = str(node.try_get_context(CUSTOM_DOMAIN_CERTIFICATE_CONTEXT_KEY) or "").strip()
    if not domain and not certificate_arn:
        return None
    if not domain or not certificate_arn:
        raise ValueError(
            f"context '{CUSTOM_DOMAIN_CONTEXT_KEY}' and '{CUSTOM_DOMAIN_CERTIFICATE_CONTEXT_KEY}' "
            "must be set together"
        )
    if not _DOMAIN_NAME_PATTERN.match(domain) or domain.endswith(".cloudfront.net"):
        raise ValueError(f"context '{CUSTOM_DOMAIN_CONTEXT_KEY}' must be a domain name of your own, got {domain!r}")
    if not _CERTIFICATE_ARN_PATTERN.match(certificate_arn):
        raise ValueError(
            f"context '{CUSTOM_DOMAIN_CERTIFICATE_CONTEXT_KEY}' must be the ARN of an ACM certificate "
            "in us-east-1"
        )
    return domain, certificate_arn

# Strict-Transport-Security for every response: 18 months, subdomains included.
HSTS_MAX_AGE = Duration.seconds(47304000)

# Permissions-Policy for the app: only the voice assistant needs a device (the microphone, own
# origin). Every other capability the browser can gate is switched off.
APP_PERMISSIONS_POLICY = (
    "accelerometer=(), camera=(), display-capture=(), geolocation=(), gyroscope=(), "
    "magnetometer=(), microphone=(self), midi=(), payment=(), usb=()"
)

# The pitch deck needs no device at all.
DECK_PERMISSIONS_POLICY = (
    "accelerometer=(), camera=(), display-capture=(), geolocation=(), gyroscope=(), "
    "magnetometer=(), microphone=(), midi=(), payment=(), usb=()"
)

# Cache-Control values for the objects uploaded to the site bucket. Vite names everything under
# assets/ after a hash of its content and the font files carry their version in the file name, so
# those never change under the same URL. Every other object (index.html, the deck, the favicon, the
# sample policies) keeps its name between builds and is revalidated on each use.
CACHE_CONTROL_IMMUTABLE = "public, max-age=31536000, immutable"
CACHE_CONTROL_REVALIDATE = "no-cache"
IMMUTABLE_PREFIXES = ("assets", "fonts")


# html2canvas-pro (the PDF exports) renders into a frame and puts one <style> element with a fixed text into
# it, to hide the page's ::before/::after content while it draws them as nodes. The browser reports this hash
# for that text in html2canvas-pro 2.5.2; recompute it (the csp-harness lists the blocked hash) when the
# library is upgraded.
HTML2CANVAS_STYLE_HASH = "'sha256-UP0QZg7irvSMvOBz9mH2PIIE28+57UiavRfeVea0l3g='"


def s3_origins(bucket_name: str, region: str) -> list[str]:
    """Origins that S3 presigned URLs for one bucket can use (virtual-hosted style).

    botocore signs us-east-1 URLs against the global endpoint and every other region against the
    regional one, so both spellings of the same bucket are listed.
    """
    return [
        f"https://{bucket_name}.s3.{region}.amazonaws.com",
        f"https://{bucket_name}.s3.amazonaws.com",
    ]


def build_app_csp(
    *, region: str, api_id: str, documents_bucket: str, brochures_bucket: str,
) -> str:
    """Content-Security-Policy for the React app, built from the stack's own resources.

    - script-src 'self': the bundle, its lazy chunks and the audio worklet are same-origin files.
      There is no inline script and no eval.
    - style-src 'self' plus one hash: Tailwind ships as a stylesheet, React sets style attributes through
      the CSSOM (which a CSP does not block), and the hash covers the single <style> element that
      html2canvas-pro adds while exporting a PDF. There is no 'unsafe-inline'.
    - connect-src: Cognito, the AgentCore runtimes (HTTPS and the voice WebSocket), the REST API and
      the documents bucket (the presigned POST upload). Nothing else is reachable from script.
    - object-src and frame-src: only the brochures bucket, for the PDF viewer on the data page.
    - form-action: the documents bucket, so a presigned-POST form can submit there.
    - base-uri 'self': the page has no <base> tag; html2canvas adds one pointing at the page's own
      origin to the frame it renders into for the PDF exports, and nothing else may be set.
    """
    agentcore = f"bedrock-agentcore.{region}.amazonaws.com"
    documents = " ".join(s3_origins(documents_bucket, region))
    brochures = " ".join(s3_origins(brochures_bucket, region))
    return (
        "default-src 'none'; "
        "script-src 'self'; "
        f"style-src 'self' {HTML2CANVAS_STYLE_HASH}; "
        "font-src 'self'; "
        "img-src 'self' data:; "
        f"connect-src 'self' https://cognito-idp.{region}.amazonaws.com "
        f"https://{agentcore} wss://{agentcore} "
        f"https://{api_id}.execute-api.{region}.amazonaws.com {documents}; "
        f"object-src {brochures}; "
        f"frame-src {brochures}; "
        f"form-action 'self' {documents}; "
        "frame-ancestors 'none'; "
        "base-uri 'self'"
    )


# Content-Security-Policy for the pitch deck under /presentation/*. The deck loads nothing from
# another origin (reveal.js and the fonts are vendored), so every directive is limited to 'self'.
# 'unsafe-inline' stays for scripts and styles because the deck page has an inline <style> and an
# inline Reveal.initialize() script, and reveal's speaker notes window (about:blank + document.write)
# inherits this policy and runs inline script and style of its own. There is no 'unsafe-eval'.
# data: images are used by reveal.css (loader and arrow sprites).
DECK_CSP = (
    "default-src 'none'; "
    "script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline'; "
    "font-src 'self'; "
    "img-src 'self' data:; "
    "connect-src 'self'; "
    "frame-src 'self'; "
    "frame-ancestors 'self'; "
    "base-uri 'none'; "
    "form-action 'none'; "
    "object-src 'none'"
)


class FrontendStack(Stack):
    """Static hosting for the Insurance Advisor React frontend."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        api: apigateway.IRestApi,
        documents_bucket: s3.IBucket,
        brochures_bucket: s3.IBucket,
        **kwargs,
    ) -> None:
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
            # Log objects carry the caller's address: they expire after 90 days.
            # The bucket is not versioned.
            lifecycle_rules=[
                s3.LifecycleRule(
                    id="expire-access-logs",
                    enabled=True,
                    expiration=Duration.days(90),
                    abort_incomplete_multipart_upload_after=Duration.days(7),
                )
            ],
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
            # Log objects carry the viewer's address: they expire after 90 days.
            # The bucket is not versioned.
            lifecycle_rules=[
                s3.LifecycleRule(
                    id="expire-cloudfront-logs",
                    enabled=True,
                    expiration=Duration.days(90),
                    abort_incomplete_multipart_upload_after=Duration.days(7),
                )
            ],
        )

        # ----- Response headers -----------------------------------------
        # The same five security headers on every path (HSTS with subdomains, nosniff, a frame option,
        # referrer policy, XSS protection) plus a Content-Security-Policy and a Permissions-Policy. The
        # app and the pitch deck differ only in the CSP, the frame option and the Permissions-Policy.
        def security_headers(
            policy_id: str, *, comment: str, csp: str,
            frame_option: cloudfront.HeadersFrameOption, permissions_policy: str,
        ) -> cloudfront.ResponseHeadersPolicy:
            return cloudfront.ResponseHeadersPolicy(
                self, policy_id,
                comment=comment,
                security_headers_behavior=cloudfront.ResponseSecurityHeadersBehavior(
                    content_security_policy=cloudfront.ResponseHeadersContentSecurityPolicy(
                        content_security_policy=csp, override=True,
                    ),
                    content_type_options=cloudfront.ResponseHeadersContentTypeOptions(override=True),
                    frame_options=cloudfront.ResponseHeadersFrameOptions(
                        frame_option=frame_option, override=True,
                    ),
                    referrer_policy=cloudfront.ResponseHeadersReferrerPolicy(
                        referrer_policy=cloudfront.HeadersReferrerPolicy.STRICT_ORIGIN_WHEN_CROSS_ORIGIN,
                        override=True,
                    ),
                    strict_transport_security=cloudfront.ResponseHeadersStrictTransportSecurity(
                        access_control_max_age=HSTS_MAX_AGE, include_subdomains=True,
                        preload=False, override=True,
                    ),
                    xss_protection=cloudfront.ResponseHeadersXSSProtection(
                        protection=True, mode_block=True, override=True,
                    ),
                ),
                custom_headers_behavior=cloudfront.ResponseCustomHeadersBehavior(
                    custom_headers=[
                        cloudfront.ResponseCustomHeader(
                            header="Permissions-Policy", value=permissions_policy, override=True,
                        ),
                    ],
                ),
            )

        # The policy is built from the stack's own resources: the region, the REST API id and the two
        # buckets that presigned URLs point at, so no host name is written into the repository.
        app_csp = build_app_csp(
            region=self.region,
            api_id=api.rest_api_id,
            documents_bucket=documents_bucket.bucket_name,
            brochures_bucket=brochures_bucket.bucket_name,
        )
        app_headers = security_headers(
            "AppResponseHeaders",
            comment="Security headers, Content-Security-Policy and Permissions-Policy for the React app",
            csp=app_csp,
            frame_option=cloudfront.HeadersFrameOption.DENY,
            permissions_policy=APP_PERMISSIONS_POLICY,
        )
        deck_headers = security_headers(
            "DeckResponseHeaders",
            comment="Security headers and a strict Content-Security-Policy for the self-hosted pitch deck",
            csp=DECK_CSP,
            frame_option=cloudfront.HeadersFrameOption.SAMEORIGIN,
            permissions_policy=DECK_PERMISSIONS_POLICY,
        )

        site_origin = origins.S3BucketOrigin.with_origin_access_control(self.site_bucket)

        # ----- Optional custom domain -----------------------------------
        # The alias and the certificate belong to the distribution, so a deployment that leaves them out
        # removes them. The CSP uses 'self' and the API, Cognito and S3 hosts, so the same response headers
        # are valid on the custom host name; nothing in the stack depends on the host name a viewer uses.
        # With a custom certificate the viewer protocol floor is TLS 1.3 (2025 policy); the default
        # certificate ignores this setting.
        custom_domain = read_custom_domain(self.node)
        domain_settings: dict = {"minimum_protocol_version": cloudfront.SecurityPolicyProtocol.TLS_V1_2_2021}
        if custom_domain is not None:
            domain_name, certificate_arn = custom_domain
            domain_settings = {
                "domain_names": [domain_name],
                "certificate": acm.Certificate.from_certificate_arn(self, "CustomDomainCertificate", certificate_arn),
                "minimum_protocol_version": cloudfront.SecurityPolicyProtocol.TLS_V1_3_2025,
            }

        # ----- CloudFront distribution with OAC -------------------------
        self.distribution = cloudfront.Distribution(
            self, "Distribution",
            default_behavior=cloudfront.BehaviorOptions(
                origin=site_origin,
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                allowed_methods=cloudfront.AllowedMethods.ALLOW_GET_HEAD,
                cached_methods=cloudfront.CachedMethods.CACHE_GET_HEAD,
                compress=True,
                response_headers_policy=app_headers,
            ),
            additional_behaviors={
                "presentation/*": cloudfront.BehaviorOptions(
                    origin=site_origin,
                    viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                    allowed_methods=cloudfront.AllowedMethods.ALLOW_GET_HEAD,
                    cached_methods=cloudfront.CachedMethods.CACHE_GET_HEAD,
                    compress=True,
                    response_headers_policy=deck_headers,
                ),
            },
            default_root_object="index.html",
            # The app has no client-side routes, so an unknown path is a real 404. S3 answers a missing
            # key with 403 (the OAC role cannot list the bucket), so both codes map to the 404 page.
            error_responses=[
                cloudfront.ErrorResponse(
                    http_status=403,
                    response_http_status=404,
                    response_page_path="/404.html",
                    ttl=Duration.seconds(0),
                ),
                cloudfront.ErrorResponse(
                    http_status=404,
                    response_http_status=404,
                    response_page_path="/404.html",
                    ttl=Duration.seconds(0),
                ),
            ],
            log_bucket=self.cf_log_bucket,
            log_includes_cookies=False,
            **domain_settings,
        )

        # ----- Upload built React app and invalidate CloudFront cache ---
        # One upload per Cache-Control value. Each sync only sees its own part of the build
        # (--exclude/--include), so pruning one part never removes another's objects.
        immutable_globs = [f"{prefix}/*" for prefix in IMMUTABLE_PREFIXES]
        self.immutable_deployment = s3deploy.BucketDeployment(
            self, "SiteImmutableDeployment",
            sources=[s3deploy.Source.asset(REACT_DIST_DIR)],
            destination_bucket=self.site_bucket,
            exclude=["*"],
            include=immutable_globs,
            cache_control=[s3deploy.CacheControl.from_string(CACHE_CONTROL_IMMUTABLE)],
            retain_on_delete=False,
        )
        self.deployment = s3deploy.BucketDeployment(
            self, "SiteDeployment",
            sources=[s3deploy.Source.asset(REACT_DIST_DIR)],
            destination_bucket=self.site_bucket,
            exclude=immutable_globs,
            cache_control=[s3deploy.CacheControl.from_string(CACHE_CONTROL_REVALIDATE)],
            distribution=self.distribution,
            distribution_paths=["/*"],
            retain_on_delete=False,
        )
        # index.html must not point at hashed files that are not uploaded yet.
        self.deployment.node.add_dependency(self.immutable_deployment)

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
