#!/usr/bin/env bash
# Shared helpers for the scripts that delete one customer's AgentCore Memory data.
#
# Source this file; it changes nothing by itself. The caller sets REGION first and calls
# require_expected_account (scripts/require-expected-account.sh) before any function here.
#
# The customer id is the memory actor id (both assistants write with actor_id = customer id).
# Per customer the memory holds:
#   - long-term records in /preferences/<id>, /facts/<id> and /summaries/<id>/<session>
#   - conversation events, listed per session (each session id comes from list-sessions)
# The summaries of a session are found through the session list, and a session is listed
# only while it still has events. The summaries are also asked for by the hierarchical path
# /summaries/<id>/ so that those of a session whose events have expired are found as well.
# A namespace may be stored with or without a trailing slash, so each prefix is asked for in
# both forms. Every record found is kept only if one of its namespaces is the customer's own
# (the id followed by the end of the namespace or a slash, so c1 never matches c10), so a
# listing that is wider than expected never deletes another customer's data.

# A failed list call is an error, never an empty list: every list below stops with a message
# and a non-zero status, so a customer's data is never reported as "0 records" because the
# call to find it failed. The one exception is a customer that never wrote to memory: the
# service answers ResourceNotFoundException "Actor <id> not found", which is a normal
# "nothing stored" and lists as empty. "Memory not found" and every other error still fail.
# The list functions return their status to the caller; none of them
# exits, so a caller keeps control (a script with `set -e` stops at the first failure).
# valid_customer_id <id>: the id becomes part of a memory namespace, so only plain
# identifier characters are accepted (no slash, wildcard, space or "..").
valid_customer_id() {
  [[ "$1" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$ && "$1" != *..* ]]
}

# find_memory_id: print the memory id, read from the deployed text runtime's environment.
find_memory_id() {
  local runtime_id
  runtime_id="$(aws bedrock-agentcore-control list-agent-runtimes \
    --region "$REGION" \
    --query "agentRuntimes[?agentRuntimeName=='insurance_advisor_agent'].agentRuntimeId | [0]" \
    --output text)"
  if [[ -z "$runtime_id" || "$runtime_id" == "None" ]]; then
    return 1
  fi
  aws bedrock-agentcore-control get-agent-runtime \
    --agent-runtime-id "$runtime_id" \
    --region "$REGION" \
    --query 'environmentVariables.BEDROCK_AGENTCORE_MEMORY_ID' \
    --output text
}

# _ids <text>: one id per line from the CLI's text output, dropping empty and "None".
_ids() {
  tr -s '[:space:]' '\n' <<<"$1" | grep -v -e '^$' -e '^None$' || true
}

# _list_failed <what>: the message printed when a list call fails.
_list_failed() {
  echo "ERROR: could not list $1. Stopping: a failed call is not an empty list." >&2
}

# _aws_list <what> <aws arguments...>: run one list call and print its output. A failure
# prints the CLI's error and returns 1, except "Actor not found" (a customer that never
# wrote to memory), which prints nothing and returns 0.
_aws_list() {
  local what="$1" out errfile err
  shift
  errfile="$(mktemp)"
  if out="$(aws "$@" 2>"$errfile")"; then
    rm -f "$errfile"
    [[ -z "$out" ]] || printf '%s\n' "$out"
    return 0
  fi
  err="$(cat "$errfile")"
  rm -f "$errfile"
  if grep -qiE 'ResourceNotFoundException.*: actor( [^ ]+)? not found' <<<"$err"; then
    return 0
  fi
  [[ -z "$err" ]] || echo "$err" >&2
  _list_failed "$what"
  return 1
}

# customer_sessions <memory> <customer>: the session ids that have events for the customer.
customer_sessions() {
  local out
  out="$(_aws_list "the sessions of customer $2" bedrock-agentcore list-sessions \
    --memory-id "$1" --actor-id "$2" --region "$REGION" \
    --query 'sessionSummaries[].sessionId' --output text)" || return 1
  _ids "$out"
}

# _list_records <memory> <option> <value>: one line per record, "<id><TAB><namespace,namespace>".
# <option> is --namespace (exact) or --namespace-path (hierarchy).
_list_records() {
  _aws_list "the memory records of $3" bedrock-agentcore list-memory-records \
    --memory-id "$1" "$2" "$3" --region "$REGION" \
    --query 'memoryRecordSummaries[].[memoryRecordId, join(`,`, namespaces || `[]`)]' \
    --output text
}

# _is_customers_record <comma separated namespaces> <customer>: true when one of the
# namespaces is the customer's own: a strategy prefix followed by the id, with or without a
# trailing slash, or any namespace below that. The id must end at the end of the namespace
# or at a slash, so c1 does not match c10.
_is_customers_record() {
  local namespaces="$1" customer="$2" ns
  local IFS=,
  for ns in $namespaces; do
    case "$ns" in
      "/preferences/$customer" | "/preferences/$customer/"*) return 0 ;;
      "/facts/$customer" | "/facts/$customer/"*) return 0 ;;
      "/summaries/$customer" | "/summaries/$customer/"*) return 0 ;;
    esac
  done
  return 1
}

# customer_record_ids <memory> <customer>: the customer's record ids, once each.
customer_record_ids() {
  local memory="$1" customer="$2" sessions session rows part id namespaces prefix
  sessions="$(customer_sessions "$memory" "$customer")" || return 1
  rows=""
  for prefix in /preferences /facts /summaries; do
    part="$(_list_records "$memory" --namespace "$prefix/$customer")" || return 1
    rows+=$'\n'"$part"
    part="$(_list_records "$memory" --namespace "$prefix/$customer/")" || return 1
    rows+=$'\n'"$part"
    part="$(_list_records "$memory" --namespace-path "$prefix/$customer/")" || return 1
    rows+=$'\n'"$part"
  done
  while IFS= read -r session; do
    [[ -n "$session" ]] || continue
    part="$(_list_records "$memory" --namespace "/summaries/$customer/$session")" || return 1
    rows+=$'\n'"$part"
  done <<<"$sessions"
  while IFS=$'\t' read -r id namespaces; do
    [[ -n "$id" && "$id" != "None" ]] || continue
    if _is_customers_record "$namespaces" "$customer"; then
      printf '%s\n' "$id"
    fi
  done <<<"$rows" | sort -u
}

# erase_customer_records <memory> <customer> <execute 0|1>: delete (or, when execute is 0,
# count) every long-term record of the customer.
erase_customer_records() {
  local memory="$1" customer="$2" execute="$3" ids id total=0
  ids="$(customer_record_ids "$memory" "$customer")" || return 1
  while IFS= read -r id; do
    [[ -n "$id" ]] || continue
    total=$((total + 1))
    if [[ "$execute" == "1" ]]; then
      aws bedrock-agentcore delete-memory-record \
        --memory-id "$memory" --memory-record-id "$id" --region "$REGION" >/dev/null
    fi
  done <<<"$ids"
  if [[ "$execute" == "1" ]]; then
    echo "records deleted: $total"
  else
    echo "records that would be deleted: $total"
  fi
}

# erase_customer_events <memory> <customer> <execute 0|1>: delete (or count) every
# conversation event of the customer. Run it after the records: the summaries are found
# through the session list, and a session disappears when its last event is deleted.
erase_customer_events() {
  local memory="$1" customer="$2" execute="$3" sessions session events event total=0
  sessions="$(customer_sessions "$memory" "$customer")" || return 1
  while IFS= read -r session; do
    [[ -n "$session" ]] || continue
    events="$(_aws_list "the events of session $session" bedrock-agentcore list-events \
      --memory-id "$memory" --actor-id "$customer" --session-id "$session" --no-include-payloads \
      --region "$REGION" --query 'events[].eventId' --output text)" || return 1
    while IFS= read -r event; do
      [[ -n "$event" ]] || continue
      total=$((total + 1))
      if [[ "$execute" == "1" ]]; then
        aws bedrock-agentcore delete-event \
          --memory-id "$memory" --actor-id "$customer" --session-id "$session" --event-id "$event" \
          --region "$REGION" >/dev/null
      fi
    done <<<"$(_ids "$events")"
  done <<<"$sessions"
  if [[ "$execute" == "1" ]]; then
    echo "events deleted: $total"
  else
    echo "events that would be deleted: $total"
  fi
}
