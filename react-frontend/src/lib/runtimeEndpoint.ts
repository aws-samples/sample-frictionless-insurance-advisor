/** Where the browser talks to the Text Assistant runtime (AgentCore), shared by chat and warm-up. */

/** Header that carries the chat session id; AgentCore routes a session id to its own container. */
export const SESSION_HEADER = 'X-Amzn-Bedrock-AgentCore-Runtime-Session-Id';

export function runtimeInvocationUrl(): string {
  const region = import.meta.env.VITE_AWS_REGION;
  const runtimeArn = import.meta.env.VITE_AGENT_RUNTIME_ARN;
  return (
    `https://bedrock-agentcore.${region}.amazonaws.com` +
    `/runtimes/${encodeURIComponent(runtimeArn)}/invocations?qualifier=DEFAULT`
  );
}
