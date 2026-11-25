"""IAM building blocks shared by the stacks that create AgentCore service roles."""
from aws_cdk import Stack, aws_iam as iam

# Name of the AgentCore Identity OAuth2 credential provider through which both
# runtimes obtain the machine token for the gateway.
RUNTIME_GATEWAY_PROVIDER_NAME = "insurance-advisor-runtime-gateway-auth"


def agentcore_service_principal(stack: Stack, source_resource: str = "*") -> iam.IPrincipal:
    """The AgentCore service principal, restricted to this account and to AgentCore resources
    of this region.

    `source_resource` is the part of the source ARN after the account id. The wildcard form
    (`*`) is the one the AgentCore documentation gives for runtime and memory roles. A role
    cannot name its own runtime or memory resource, because that resource refers to the role.
    """
    return iam.PrincipalWithConditions(
        iam.ServicePrincipal("bedrock-agentcore.amazonaws.com"),
        {
            "StringEquals": {"aws:SourceAccount": stack.account},
            "ArnLike": {
                "aws:SourceArn": f"arn:aws:bedrock-agentcore:{stack.region}:{stack.account}:{source_resource}"
            },
        },
    )


def grant_runtime_agentcore_access(
    role: iam.Role,
    *,
    stack: Stack,
    runtime_name: str,
    memory_id: str,
    delete_memory_events: bool = False,
) -> None:
    """Grant a runtime role the AgentCore calls its container makes, on the resources it uses.

    - Memory: the session manager of the Strands integration writes and reads events of the
      one memory and retrieves long-term records from it. `delete_memory_events` adds
      DeleteEvent, which the session manager uses to replace a stored message after the
      guardrail redacted it.
    - Identity: `@requires_access_token` asks the token vault for the machine token of the
      gateway (`GetResourceOauth2Token`) with the workload access token that the runtime
      service passes to the container. The token vault reads the provider's client secret
      with this role's permission, so the secret of that one provider is readable here.
    - Workload access tokens: the runtime service obtains them for the runtime's own
      workload identity (named after the runtime) when a JWT-authorized caller invokes it.
    """
    arn_prefix = f"arn:aws:bedrock-agentcore:{stack.region}:{stack.account}"
    directory = f"{arn_prefix}:workload-identity-directory/default"
    workload_identities = f"{directory}/workload-identity/{runtime_name}-*"
    token_vault = f"{arn_prefix}:token-vault/default"
    provider = f"{token_vault}/oauth2credentialprovider/{RUNTIME_GATEWAY_PROVIDER_NAME}"
    # Identity keeps the provider's client secret under this name (a random suffix follows).
    provider_secret = (
        f"arn:aws:secretsmanager:{stack.region}:{stack.account}:"
        f"secret:bedrock-agentcore-identity!default/oauth2/{RUNTIME_GATEWAY_PROVIDER_NAME}*"
    )

    memory_actions = [
        "bedrock-agentcore:CreateEvent",
        "bedrock-agentcore:ListEvents",
        "bedrock-agentcore:GetEvent",
        "bedrock-agentcore:RetrieveMemoryRecords",
    ]
    if delete_memory_events:
        memory_actions.append("bedrock-agentcore:DeleteEvent")

    role.add_to_policy(iam.PolicyStatement(
        sid="MemoryEventsAndRecords",
        effect=iam.Effect.ALLOW,
        actions=memory_actions,
        resources=[f"{arn_prefix}:memory/{memory_id}"],
    ))
    role.add_to_policy(iam.PolicyStatement(
        sid="WorkloadAccessTokens",
        effect=iam.Effect.ALLOW,
        actions=[
            "bedrock-agentcore:GetWorkloadAccessToken",
            "bedrock-agentcore:GetWorkloadAccessTokenForJWT",
        ],
        resources=[directory, workload_identities],
    ))
    role.add_to_policy(iam.PolicyStatement(
        sid="GatewayOauth2Token",
        effect=iam.Effect.ALLOW,
        actions=["bedrock-agentcore:GetResourceOauth2Token"],
        resources=[directory, workload_identities, token_vault, provider],
    ))
    role.add_to_policy(iam.PolicyStatement(
        sid="GatewayProviderClientSecret",
        effect=iam.Effect.ALLOW,
        actions=["secretsmanager:GetSecretValue"],
        resources=[provider_secret],
    ))
