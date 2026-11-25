#!/usr/bin/env bash
# Wipe AgentCore Memory records for one customer across all three
# strategies (summaries, preferences, facts). Without a search filter it also
# deletes the customer's stored conversation events.
#
# To erase a customer completely, with a dry run first, use
# scripts/erase_customer_memory.sh.
#
# Use this when a past conversation has poisoned the memory store with
# topic-bias that keeps trickling back into every later chat for the
# same customer — for example a single "maternity benefits" inquiry
# that turns into a sticky preference and trips guardrail topic filters
# in subsequent unrelated chats.
#
# Usage:
#   EXPECTED_AWS_ACCOUNT_ID=<12-digit account id> AWS_PROFILE=<your-profile> ./scripts/wipe_memory_records.sh <customer_id> [search_query]
#   (the script stops unless the credentials belong to that account)
#
# Examples:
#   # Wipe all records and all events for customer 3
#   ./scripts/wipe_memory_records.sh 3
#
#   # Wipe only records that match "maternity" for customer 3 (events are kept)
#   ./scripts/wipe_memory_records.sh 3 maternity
#
# Prereqs:
#   - AWS_PROFILE set to a profile with bedrock-agentcore:* on the memory
#   - MEMORY_ID detected from the deployed runtime env

set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/require-expected-account.sh"
source "$(dirname "${BASH_SOURCE[0]}")/memory-erase-lib.sh"

CUSTOMER_ID="${1:?customer_id required}"
SEARCH_QUERY="${2:-}"
REGION="${AWS_REGION:-us-east-1}"
valid_customer_id "$CUSTOMER_ID" || {
    echo "ERROR: the customer id may only contain letters, digits, dot, underscore and hyphen." >&2
    exit 2
}

require_expected_account

# Find the deployed text runtime by its name (insurance_advisor_agent); the
# memory id is read from that runtime's environment below.
MEMORY_ID="$(aws bedrock-agentcore-control list-agent-runtimes \
    --region "$REGION" \
    --query "agentRuntimes[?agentRuntimeName=='insurance_advisor_agent'].agentRuntimeId | [0]" \
    --output text)"

if [[ -z "$MEMORY_ID" || "$MEMORY_ID" == "None" ]]; then
    echo "ERROR: could not auto-detect memory id" >&2
    exit 1
fi

# The lookup above returned the runtime id. The memory id is the runtime's
# BEDROCK_AGENTCORE_MEMORY_ID environment variable.
MEMORY_ID="$(aws bedrock-agentcore-control get-agent-runtime \
    --agent-runtime-id "$MEMORY_ID" \
    --region "$REGION" \
    --query 'environmentVariables.BEDROCK_AGENTCORE_MEMORY_ID' \
    --output text)"

echo "Using memory id: $MEMORY_ID"
echo "Customer namespace: $CUSTOMER_ID"
[[ -n "$SEARCH_QUERY" ]] && echo "Search filter: $SEARCH_QUERY" || echo "Wiping ALL records and events (no search filter)"
echo

if [[ -z "$SEARCH_QUERY" ]]; then
    # No filter: delete every record and every event of exactly this customer id.
    erase_customer_records "$MEMORY_ID" "$CUSTOMER_ID" 1
    erase_customer_events "$MEMORY_ID" "$CUSTOMER_ID" 1
    echo
    echo "Done. The memory search index is eventually consistent: a retrieval can still"
    echo "show stale entries for a few minutes."
    exit 0
fi

# With a filter: only the records the search returns, in the three strategies' namespaces,
# each with and without the trailing slash. A record is deleted only if one of its
# namespaces is this customer's own, and each record once. The conversation events are kept.
SEEN=" "
for NS in "/summaries/$CUSTOMER_ID/" "/summaries/$CUSTOMER_ID" \
    "/preferences/$CUSTOMER_ID" "/preferences/$CUSTOMER_ID/" \
    "/facts/$CUSTOMER_ID" "/facts/$CUSTOMER_ID/"; do
    echo "--- namespace: $NS ---"
    SEARCH_CRITERIA="searchQuery=$SEARCH_QUERY"

    ROWS="$(_aws_list "the memory records of $NS" bedrock-agentcore retrieve-memory-records \
        --memory-id "$MEMORY_ID" \
        --namespace "$NS" \
        --search-criteria "$SEARCH_CRITERIA" \
        --region "$REGION" \
        --max-items 50 \
        --query 'memoryRecordSummaries[].[memoryRecordId, join(`,`, namespaces || `[]`)]' \
        --output text)" || exit 1

    FOUND=0
    while IFS=$'\t' read -r ID NAMESPACES; do
        [[ -n "$ID" && "$ID" != "None" ]] || continue
        _is_customers_record "$NAMESPACES" "$CUSTOMER_ID" || continue
        [[ "$SEEN" != *" $ID "* ]] || continue
        SEEN+="$ID "
        FOUND=1
        echo -n "  deleting $ID ... "
        aws bedrock-agentcore delete-memory-record \
            --memory-id "$MEMORY_ID" \
            --memory-record-id "$ID" \
            --region "$REGION" \
            --query 'memoryRecordId' \
            --output text >/dev/null
        echo "ok"
    done <<<"$ROWS"
    [[ "$FOUND" == "1" ]] || echo "  (no records)"
done

echo
echo "Done. Note: search index is eventually consistent. A subsequent"
echo "retrieve-memory-records call may still show stale entries for"
echo "a few minutes — the actual records are gone."
