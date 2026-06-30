#!/bin/bash
set -euo pipefail

uv sync

echo "🚀 Deploying Insurance Advisor AgentCore..."

# 1) Deploy the backend stacks first so SSM parameters exist before the
#    React app is built. The frontend stack is excluded here because its
#    asset (react-frontend/dist/) does not exist yet on a clean checkout.
#
#    --concurrency 4 lets CDK deploy stacks in parallel where the
#    dependency graph allows. In our DAG, 01-auth must finish before
#    02-tools, and 02-tools must finish before 03-agentcore and
#    04-voice — but 03 and 04 are siblings of each other and can deploy
#    in parallel, which saves ~3-4 min on a full deploy.
echo "🏗️  Deploying backend stacks (auth, tools, agentcore, voice)..."
uv run cdk deploy \
    insadv-01-auth \
    insadv-02-tools \
    insadv-03-agentcore \
    insadv-04-voice \
    --concurrency 4 \
    --require-approval never

# 1.5) Re-assert AgentCore Runtime requestHeaderConfiguration. CDK's
#      AwsCustomResource provider Lambda installs the AWS SDK at deploy
#      time and the version pulled is non-deterministic; older versions
#      silently drop the requestHeaderConfiguration field. This script
#      uses our pinned boto3 to ensure both runtimes forward the
#      Authorization header into the container, so the agents can
#      identify the caller from their verified Cognito JWT instead of
#      relying on a client-supplied advisor_id (which would let an
#      authenticated user impersonate any other advisor).
echo "🔐 Asserting Authorization header forwarding on AgentCore Runtimes..."
uv run python scripts/assert_runtime_header_config.py

# 2) Pull SSM values into react-frontend/.env.local and build the SPA.
#    First generate the sample policy PDFs from their markdown sources.
#    PDFs are gitignored (generated artefacts), so a clean checkout has
#    none — and the frontend's prebuild sync step (sync-mock-policies.sh)
#    would otherwise copy zero PDFs into the published bundle, leaving the
#    "sample policies" download menu without its PDF documents.
echo "📄 Generating sample policy PDFs..."
./s3-data/mock-policies/build-pdfs.sh

echo "🎨 Building React frontend..."
pushd react-frontend >/dev/null
./scripts/setup-env.sh
npm install
npm run build
popd >/dev/null

# 3) Deploy the frontend (S3 + CloudFront) once the dist/ exists.
echo "☁️  Deploying frontend stack (S3 + CloudFront)..."
uv run cdk deploy insadv-05-frontend --require-approval never

# 4) Print the CloudFront URL prominently. The frontend stack writes it
#    both as a CfnOutput and to SSM (/insurance-advisor/frontend/site-url),
#    so we read SSM here for a clean, single-line summary at the very
#    end of the deploy log.
echo ""
SITE_URL=$(aws ssm get-parameter \
    --name /insurance-advisor/frontend/site-url \
    --query 'Parameter.Value' \
    --output text 2>/dev/null || echo "")
if [ -n "${SITE_URL}" ]; then
    echo "✅ Deployment completed!"
    echo ""
    echo "    🌐 Live at: ${SITE_URL}"
    echo ""
else
    echo "✅ Deployment completed (could not read /insurance-advisor/frontend/site-url from SSM)."
fi
