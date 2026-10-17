#!/usr/bin/env bash
# Wipe AgentCore Memory records for one customer across all three
# strategies (summaries, preferences, facts).
#
# Use this when a past conversation has poisoned the memory store with
# topic-bias that keeps trickling back into every later chat for the
# same customer — for example a single "maternity benefits" inquiry
# that turns into a sticky preference and trips guardrail topic filters
# in subsequent unrelated chats.
#
# Usage:
#   AWS_PROFILE=<your-profile> ./scripts/wipe_memory_records.sh <customer_id> [search_query]
#
# Examples:
#   # Wipe everything for customer 3
#   ./scripts/wipe_memory_records.sh 3
#
#   # Wipe only records that match "maternity" for customer 3
#   ./scripts/wipe_memory_records.sh 3 maternity
#
# Prereqs:
#   - AWS_PROFILE set to a profile with bedrock-agentcore:* on the memory
#   - MEMORY_ID detected from the deployed runtime env

set -euo pipefail

CUSTOMER_ID="${1:?customer_id required}"
SEARCH_QUERY="${2:-}"
REGION="${AWS_REGION:-us-east-1}"

# Auto-detect memory id from the deployed text runtime env. If you've
# renamed or deleted the runtime, edit this fallback.
MEMORY_ID="$(aws bedrock-agentcore-control list-agent-runtimes \
    --region "$REGION" \
    --query "agentRuntimes[?agentRuntimeName=='insurance_advisor_agent'].agentRuntimeId | [0]" \
    --output text)"

if [[ -z "$MEMORY_ID" || "$MEMORY_ID" == "None" ]]; then
    echo "ERROR: could not auto-detect memory id" >&2
    exit 1
fi

# The memory id on the runtime is the runtime id. The memory config
# inside the runtime points at insurance_advisor_ltm-*. Pull the actual
# memory id from the runtime env.
MEMORY_ID="$(aws bedrock-agentcore-control get-agent-runtime \
    --agent-runtime-id "$MEMORY_ID" \
    --region "$REGION" \
    --query 'environmentVariables.BEDROCK_AGENTCORE_MEMORY_ID' \
    --output text)"

echo "Using memory id: $MEMORY_ID"
echo "Customer namespace: $CUSTOMER_ID"
[[ -n "$SEARCH_QUERY" ]] && echo "Search filter: $SEARCH_QUERY" || echo "Wiping ALL records (no search filter)"
echo

for NS in "/summaries/$CUSTOMER_ID/" "/preferences/$CUSTOMER_ID" "/facts/$CUSTOMER_ID"; do
    echo "--- namespace: $NS ---"
    if [[ -n "$SEARCH_QUERY" ]]; then
        SEARCH_CRITERIA="searchQuery=$SEARCH_QUERY"
    else
        # No filter — retrieve as many records as the API gives back.
        SEARCH_CRITERIA="searchQuery= "
    fi

    IDS="$(aws bedrock-agentcore retrieve-memory-records \
        --memory-id "$MEMORY_ID" \
        --namespace "$NS" \
        --search-criteria "$SEARCH_CRITERIA" \
        --region "$REGION" \
        --max-items 50 \
        --query 'memoryRecordSummaries[].memoryRecordId' \
        --output text 2>/dev/null || echo "")"

    if [[ -z "$IDS" || "$IDS" == "None" ]]; then
        echo "  (no records)"
        continue
    fi

    for ID in $IDS; do
        echo -n "  deleting $ID ... "
        aws bedrock-agentcore delete-memory-record \
            --memory-id "$MEMORY_ID" \
            --memory-record-id "$ID" \
            --region "$REGION" \
            --query 'memoryRecordId' \
            --output text >/dev/null
        echo "ok"
    done
done

echo
echo "Done. Note: search index is eventually consistent. A subsequent"
echo "retrieve-memory-records call may still show stale entries for"
echo "a few minutes — the actual records are gone."
