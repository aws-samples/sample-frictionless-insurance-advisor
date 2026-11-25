import { fetchAuthSession } from 'aws-amplify/auth';

import { runtimeInvocationUrl, SESSION_HEADER } from './runtimeEndpoint';
import { apiErrorFromResponse } from './errors';
import { readSSEStream, type StreamItem } from './sse';
import { markSessionWarm } from './warmup';

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
  /** The application form open on screen, so the agent knows what it holds. */
  form?: { productType: string; values: Record<string, string> };
  /** Field values the customer's record already supplies. */
  formPrefill?: Record<string, string>;
  /** Base code of the language chosen in the app (for example `ko`): the assistant replies in it. */
  language?: string;
}): AsyncGenerator<StreamItem> {
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
  // A real message wakes this session's container, so no warm-up is needed soon after.
  markSessionWarm(opts.sessionId);

  const response = await fetch(runtimeInvocationUrl(), {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${token}`,
      'Content-Type': 'application/json',
      Accept: 'text/event-stream',
      [SESSION_HEADER]: opts.sessionId,
    },
    body: JSON.stringify({
      prompt: opts.prompt,
      ...(opts.customerId && { customerId: opts.customerId }),
      ...(opts.language && { language: opts.language }),
      ...(opts.form && { form: opts.form }),
      ...(opts.formPrefill && Object.keys(opts.formPrefill).length > 0 && {
        formPrefill: opts.formPrefill,
      }),
    }),
  });

  if (!response.ok) {
    throw apiErrorFromResponse('agent runtime', response.status, await response.text());
  }

  yield* readSSEStream(response);
}
