#!/usr/bin/env bash
# Account guard for the scripts that change or delete things in AWS.
#
# Source this file and call `require_expected_account` before the first change:
#   source "$(dirname "${BASH_SOURCE[0]}")/require-expected-account.sh"
#   require_expected_account
#
# The environment variable EXPECTED_AWS_ACCOUNT_ID must hold the 12-digit id of the
# account the script is meant to change. The call stops the calling script (exit 1) when
# the variable is unset or malformed, when the caller's account cannot be read, or when
# it differs from the expected one. Nothing is changed before the check. The account id
# is never stored in the repository.

require_expected_account() {
  local expected="${EXPECTED_AWS_ACCOUNT_ID:-}"
  if [[ -z "$expected" ]]; then
    echo "ERROR: EXPECTED_AWS_ACCOUNT_ID is not set." >&2
    echo "       Set it to the 12-digit id of the AWS account this script should change, then run it again:" >&2
    echo "         export EXPECTED_AWS_ACCOUNT_ID=<12-digit account id>" >&2
    exit 1
  fi
  if [[ ! "$expected" =~ ^[0-9]{12}$ ]]; then
    echo "ERROR: EXPECTED_AWS_ACCOUNT_ID must be exactly 12 digits." >&2
    exit 1
  fi
  local actual
  if ! actual="$(aws sts get-caller-identity --query Account --output text 2>/dev/null)" || [[ -z "$actual" ]]; then
    echo "ERROR: could not read the account of the current AWS credentials (aws sts get-caller-identity failed)." >&2
    exit 1
  fi
  if [[ "$actual" != "$expected" ]]; then
    echo "ERROR: the current AWS credentials belong to account $actual, but EXPECTED_AWS_ACCOUNT_ID is $expected." >&2
    echo "       Nothing was changed. Check AWS_PROFILE and the credentials in this shell." >&2
    exit 1
  fi
  echo "Account check passed ($actual)."
}
