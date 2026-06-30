#!/usr/bin/env python3
"""Assert AgentCore Runtime requestHeaderConfiguration after CDK deploy.

Why this exists
---------------
The CDK `AwsCustomResource` we use for the AgentCore Runtime calls the
`bedrock-agentcore-control` API via the AWS SDK that's installed in the
provider Lambda at deploy time. When `install_latest_aws_sdk=True` is set,
CDK runs `npm install aws-sdk` inside the Lambda — but the version that
gets pulled is non-deterministic and historically has not always included
the `requestHeaderConfiguration` field in `CreateAgentRuntime` /
`UpdateAgentRuntime`. When the field is missing from the SDK model the
service silently drops it (no validation error), and the runtime is
deployed without the Authorization header forwarded into the container.

Without that header, the agent cannot identify the calling advisor from
the inbound JWT and the only fallback is a client-supplied `advisorId`
in the request payload — which is a serious authorization bug
(authenticated user A can impersonate user B by sending B's email in
the body).

This script runs after the CDK stacks are deployed, using the project's
pinned `boto3` (which DOES support the field), and re-asserts the
`requestHeaderConfiguration={requestHeaderAllowlist:["Authorization"]}`
on both the text and voice runtimes. It's idempotent: if the field is
already correct it skips the update.
"""
from __future__ import annotations

import os
import sys
import time

import boto3


REGION = os.environ.get("AWS_REGION", "us-east-1")
RUNTIME_NAMES = ["insurance_advisor_runtime", "insurance_voice_runtime"]
DESIRED_HEADERS = ["Authorization"]


def _find_runtime_id(client, name: str) -> str | None:
    paginator = client.get_paginator("list_agent_runtimes")
    for page in paginator.paginate():
        for rt in page.get("agentRuntimes", []):
            if rt.get("agentRuntimeName") == name:
                return rt["agentRuntimeId"]
    return None


def _assert_runtime(client, name: str) -> bool:
    """Return True if the runtime ended up with the desired header config."""
    runtime_id = _find_runtime_id(client, name)
    if not runtime_id:
        print(f"[skip] runtime {name!r} not found", file=sys.stderr)
        return False

    current = client.get_agent_runtime(agentRuntimeId=runtime_id)
    have = (current.get("requestHeaderConfiguration") or {}).get(
        "requestHeaderAllowlist"
    ) or []

    if sorted(have) == sorted(DESIRED_HEADERS):
        print(f"[ok]   {name} ({runtime_id}) already has Authorization forwarding")
        return True

    print(
        f"[fix]  {name} ({runtime_id}) has {have!r}, applying {DESIRED_HEADERS!r}"
    )

    update_args = {
        "agentRuntimeId": runtime_id,
        "agentRuntimeArtifact": current["agentRuntimeArtifact"],
        "roleArn": current["roleArn"],
        "networkConfiguration": current["networkConfiguration"],
        "protocolConfiguration": current["protocolConfiguration"],
        "environmentVariables": current.get("environmentVariables", {}),
        "authorizerConfiguration": current.get("authorizerConfiguration"),
        "description": current.get("description") or "",
        "requestHeaderConfiguration": {
            "requestHeaderAllowlist": DESIRED_HEADERS,
        },
    }
    # Remove keys with None values so the API doesn't reject them.
    update_args = {k: v for k, v in update_args.items() if v is not None}

    client.update_agent_runtime(**update_args)

    # Wait for READY before verifying.
    for _ in range(30):
        time.sleep(5)  # nosemgrep: arbitrary-sleep -- intentional poll; no AgentCore runtime waiter exists
        check = client.get_agent_runtime(agentRuntimeId=runtime_id)
        if check.get("status") == "READY":
            break

    final = (check.get("requestHeaderConfiguration") or {}).get(
        "requestHeaderAllowlist"
    ) or []
    if sorted(final) != sorted(DESIRED_HEADERS):
        print(
            f"[err]  {name}: requestHeaderConfiguration did not stick "
            f"(have {final!r}, wanted {DESIRED_HEADERS!r}). "
            "boto3 may need to be upgraded.",
            file=sys.stderr,
        )
        return False
    print(f"[done] {name} now forwards {DESIRED_HEADERS!r}")
    return True


def main() -> int:
    client = boto3.client("bedrock-agentcore-control", region_name=REGION)
    failed = [name for name in RUNTIME_NAMES if not _assert_runtime(client, name)]
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
