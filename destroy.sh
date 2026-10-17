#!/bin/bash

# Same pinned CDK CLI as deploy.sh; keep the two in step with aws-cdk-lib.
CDK_CLI_VERSION="2.1144.0"
run_cdk() { uv run npx --yes "aws-cdk@${CDK_CLI_VERSION}" "$@"; }

echo "🗑️ Destroying Insurance Advisor AgentCore..."

# Destroy the CDK stack
echo "🏗️ Destroying CDK stack..."
run_cdk destroy --all --force

echo "✅ Destruction completed!"
