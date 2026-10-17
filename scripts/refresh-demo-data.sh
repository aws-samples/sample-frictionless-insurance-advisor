#!/usr/bin/env bash
# Reset the demo to its seed without deploying: re-seed the profiles, policies
# and catalog tables, move the seed dates to today, delete everything that is
# not in the seed (prospects, added policies, notes) and clear AgentCore memory.
#
# The same thing happens on every deploy of insadv-02-tools and, automatically,
# at least every 48 hours (see "Automatic reset" in cdk/tools_stack.py).
#
# Usage:
#   AWS_PROFILE=<profile> ./scripts/refresh-demo-data.sh [--yes] [--keep-notes] [--skip-memory]
#
#   --yes          do not ask for confirmation
#   --keep-notes   keep notes added after seeding
#   --skip-memory  leave AgentCore memory alone
set -euo pipefail

REGION="${AWS_REGION:-us-east-1}"
STACK="insadv-02-tools"
ASSUME_YES=0
PAYLOAD='{}'
KEEP_NOTES=false
SKIP_MEMORY=false
for arg in "$@"; do
  case "$arg" in
    --yes) ASSUME_YES=1 ;;
    --keep-notes) KEEP_NOTES=true ;;
    --skip-memory) SKIP_MEMORY=true ;;
    *) echo "Unknown option: $arg" >&2; exit 2 ;;
  esac
done
PAYLOAD="{\"keep_notes\": ${KEEP_NOTES}, \"skip_memory\": ${SKIP_MEMORY}}"

ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
FN_ARN="$(aws cloudformation describe-stacks --stack-name "$STACK" --region "$REGION" \
  --query "Stacks[0].Outputs[?OutputKey=='MockDataLambdaArn'].OutputValue" --output text)"
if [[ -z "$FN_ARN" || "$FN_ARN" == "None" ]]; then
  echo "ERROR: could not find the MockDataLambdaArn output of $STACK in $REGION" >&2
  exit 1
fi

echo "Account : $ACCOUNT"
echo "Region  : $REGION"
echo "Function: ${FN_ARN##*:function:}"
echo "This deletes every customer, policy, note and memory record that is not in the seed."
if [[ "$ASSUME_YES" -ne 1 ]]; then
  read -r -p "Reset the demo in account $ACCOUNT? [y/N] " answer
  [[ "$answer" == "y" || "$answer" == "Y" ]] || { echo "Cancelled."; exit 1; }
fi

OUT="$(mktemp)"
trap 'rm -f "$OUT"' EXIT
aws lambda invoke --function-name "$FN_ARN" --region "$REGION" \
  --cli-binary-format raw-in-base64-out --payload "$PAYLOAD" "$OUT" >/dev/null
cat "$OUT"; echo
