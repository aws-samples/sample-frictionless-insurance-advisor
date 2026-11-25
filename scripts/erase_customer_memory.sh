#!/usr/bin/env bash
# Erase everything AgentCore Memory holds for ONE customer: the long-term records
# (summaries, preferences, facts) and the stored conversation events.
#
# Use it to honour a deletion request for a customer, or after deleting a customer's profile.
# Both assistants write memory with the customer id as the actor id, so the customer id is
# the only argument and nothing outside that actor is touched.
#
# Usage:
#   EXPECTED_AWS_ACCOUNT_ID=<12-digit account id> AWS_PROFILE=<your-profile> \
#     ./scripts/erase_customer_memory.sh <customer_id> [--execute]
#
# Without --execute the script only lists and counts what it would delete (dry run).
# A customer that never wrote to memory has nothing stored: the script reports 0 records and
# 0 events and exits 0. Any other failed call stops the script.
# With --execute it deletes. The script stops unless the credentials belong to the account
# in EXPECTED_AWS_ACCOUNT_ID, before any call that reads memory.
#
# The memory id is read from the deployed text runtime's environment. Deleting is permanent.
# The memory search index is eventually consistent: a retrieval can still show a deleted
# record for a few minutes.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/require-expected-account.sh"
source "$(dirname "${BASH_SOURCE[0]}")/memory-erase-lib.sh"
usage() {
  echo "Usage: EXPECTED_AWS_ACCOUNT_ID=<12-digit account id> $0 <customer_id> [--execute]" >&2
  exit 2
}
CUSTOMER_ID=""
EXECUTE=0
for arg in "$@"; do
  case "$arg" in
    --execute) EXECUTE=1 ;;
    -*) echo "ERROR: unknown option $arg" >&2; usage ;;
    *)
      [[ -z "$CUSTOMER_ID" ]] || { echo "ERROR: exactly one customer id is allowed." >&2; usage; }
      CUSTOMER_ID="$arg"
      ;;
  esac
done
[[ -n "$CUSTOMER_ID" ]] || { echo "ERROR: customer id required." >&2; usage; }
valid_customer_id "$CUSTOMER_ID" || {
  echo "ERROR: the customer id may only contain letters, digits, dot, underscore and hyphen." >&2
  exit 2
}
REGION="${AWS_REGION:-us-east-1}"
require_expected_account
MEMORY_ID="$(find_memory_id)" || MEMORY_ID=""
if [[ -z "$MEMORY_ID" || "$MEMORY_ID" == "None" ]]; then
  echo "ERROR: could not detect the memory id of the deployed text runtime." >&2
  exit 1
fi
echo "Memory id: $MEMORY_ID"
echo "Customer (memory actor): $CUSTOMER_ID"
if [[ "$EXECUTE" == "1" ]]; then
  echo "Mode: ERASE (deletes the data)"
else
  echo "Mode: dry run (nothing is deleted; add --execute to delete)"
fi
echo
# Records first: the summaries are found through the session list, and a session
# disappears from that list when its last event is deleted.
erase_customer_records "$MEMORY_ID" "$CUSTOMER_ID" "$EXECUTE"
erase_customer_events "$MEMORY_ID" "$CUSTOMER_ID" "$EXECUTE"
echo
if [[ "$EXECUTE" == "1" ]]; then
  echo "Done. The memory search index is eventually consistent: a retrieval can still show"
  echo "stale entries for a few minutes."
else
  echo "Dry run finished. Run again with --execute to delete."
fi
