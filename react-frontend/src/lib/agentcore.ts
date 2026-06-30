import { fetchAuthSession } from 'aws-amplify/auth';

import { readSSEStream } from './sse';

/**
 * Stream a chat response from the AgentCore Runtime.
 *
 * Calls the runtime's `/invocations?qualifier=DEFAULT` endpoint directly
 * over HTTPS with the Cognito access token in the Authorization header.
 * No proxy is needed — AgentCore accepts JWT-auth'd calls from the browser.
 */
export async function* streamChat(opts: {
  prompt: string;
  customerId?: string;
  sessionId: string;
}): AsyncGenerator<string> {
  const session = await fetchAuthSession();
  const token = session.tokens?.accessToken?.toString();
  if (!token) {
    throw new Error('Not authenticated');
  }

  // The runtime extracts the advisor's identity from the verified Cognito JWT
  // server-side (via requestHeaderConfiguration forwarding the Authorization
  // header into the container). We deliberately do NOT send `advisorId` in
  // the payload — that field would be client-controllable and would let an
  // authenticated user impersonate any other advisor. Trust only the signed
  // token.
  const region = import.meta.env.VITE_AWS_REGION;
  const runtimeArn = import.meta.env.VITE_AGENT_RUNTIME_ARN;
  const url =
    `https://bedrock-agentcore.${region}.amazonaws.com` +
    `/runtimes/${encodeURIComponent(runtimeArn)}/invocations?qualifier=DEFAULT`;

  const response = await fetch(url, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${token}`,
      'Content-Type': 'application/json',
      Accept: 'text/event-stream',
      'X-Amzn-Bedrock-AgentCore-Runtime-Session-Id': opts.sessionId,
    },
    body: JSON.stringify({
      prompt: opts.prompt,
      ...(opts.customerId && { customerId: opts.customerId }),
    }),
  });

  if (!response.ok) {
    const body = await response.text();
    throw new Error(`AgentCore ${response.status}: ${body}`);
  }

  yield* readSSEStream(response);
}
