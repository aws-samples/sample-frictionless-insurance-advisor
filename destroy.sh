#!/bin/bash
# Usage:
#   EXPECTED_AWS_ACCOUNT_ID=<12-digit account id> ./destroy.sh
#   EXPECTED_AWS_ACCOUNT_ID=<12-digit account id> DESTROY_FRONTEND=1 ./destroy.sh
# The script stops before deleting anything unless the current AWS credentials belong to
# that account (see scripts/require-expected-account.sh).
#
# The frontend stack (insadv-05-frontend) owns the CloudFront distribution, so its URL, and
# it has termination protection. It is destroyed only when DESTROY_FRONTEND=1 is set: the
# script then switches the protection off, deletes the stack and goes on with the others.
# Without it, a deployed frontend stack is kept. It imports from the tools stack, which in
# turn needs the auth stack, so CloudFormation refuses to delete those two while the
# frontend exists; the script destroys only the voice and agentcore stacks in that case.
#
# The frontend stack is looked up in CloudFormation by name, so the script does not depend on
# react-frontend/dist: app.py synthesizes the stack only when that directory holds a build.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/scripts/require-expected-account.sh"
require_expected_account
# Same pinned CDK CLI as deploy.sh; keep the two in step with aws-cdk-lib.
CDK_CLI_VERSION="2.1144.0"
run_cdk() { uv run npx --yes "aws-cdk@${CDK_CLI_VERSION}" "$@"; }

FRONTEND_STACK="insadv-05-frontend"

# Succeeds when the frontend stack exists in CloudFormation. A lookup that fails for any
# other reason stops the script instead of being read as "absent".
frontend_stack_exists() {
  local out
  if out="$(aws cloudformation describe-stacks --stack-name "$FRONTEND_STACK" --query 'Stacks[0].StackStatus' --output text 2>&1)"; then
    return 0
  fi
  if [[ "$out" == *"does not exist"* ]]; then
    return 1
  fi
  echo "ERROR: could not read the stack $FRONTEND_STACK: $out" >&2
  exit 1
}

echo "🗑 Destroying Insurance Advisor AgentCore..."

BACKEND_STACKS=(insadv-04-voice insadv-03-agentcore insadv-02-tools insadv-01-auth)
if frontend_stack_exists; then
  if [[ "${DESTROY_FRONTEND:-}" == "1" ]]; then
    echo "☁️  Destroying the frontend stack $FRONTEND_STACK (the CloudFront URL is lost)..."
    aws cloudformation update-termination-protection --no-enable-termination-protection --stack-name "$FRONTEND_STACK"
    aws cloudformation delete-stack --stack-name "$FRONTEND_STACK"
    aws cloudformation wait stack-delete-complete --stack-name "$FRONTEND_STACK"
  else
    echo "Keeping $FRONTEND_STACK (termination protected, it holds the CloudFront URL) and the stacks it imports from, insadv-02-tools and insadv-01-auth; set DESTROY_FRONTEND=1 to destroy them too."
    BACKEND_STACKS=(insadv-04-voice insadv-03-agentcore)
  fi
elif [[ "${DESTROY_FRONTEND:-}" == "1" ]]; then
  echo "The frontend stack $FRONTEND_STACK is not deployed; nothing to destroy there."
fi

# Only the named stacks: without --exclusively, CDK also destroys the stacks that depend on them.
echo "🏗 Destroying CDK stacks: ${BACKEND_STACKS[*]}..."
run_cdk destroy "${BACKEND_STACKS[@]}" --exclusively --force

echo "✅ Destruction completed!"
