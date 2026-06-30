#!/bin/bash
# Reads SSM parameters written by the deployed CDK stacks and writes
# react-frontend/.env.local. Run before `npm run dev` whenever the
# backend has been (re)deployed.
#
# Requires valid AWS credentials in the current shell (e.g., via `aws configure`
# or `aws sso login`).
set -euo pipefail

REGION="${AWS_REGION:-us-east-1}"
OUT="$(dirname "$0")/../.env.local"

get_param() {
  aws ssm get-parameter --region "$REGION" --name "$1" --query 'Parameter.Value' --output text
}

USER_POOL_ID=$(get_param /insurance-advisor/cognito/user-pool-id)
CLIENT_ID=$(get_param /insurance-advisor/cognito/app-client-id)
API_URL=$(get_param /insurance-advisor/api/gateway-url)
RUNTIME_ARN=$(get_param /insurance-advisor/agentcore/runtime-arn)
VOICE_RUNTIME_ARN=$(get_param /insurance-advisor/voice/runtime-arn 2>/dev/null || echo "")

cat >"$OUT" <<EOF
VITE_AWS_REGION=$REGION
VITE_COGNITO_USER_POOL_ID=$USER_POOL_ID
VITE_COGNITO_CLIENT_ID=$CLIENT_ID
VITE_API_BASE_URL=$API_URL
VITE_AGENT_RUNTIME_ARN=$RUNTIME_ARN
VITE_VOICE_RUNTIME_ARN=$VOICE_RUNTIME_ARN
EOF

echo "Wrote $OUT"
