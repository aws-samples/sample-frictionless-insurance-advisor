import { fetchAuthSession } from 'aws-amplify/auth';

import { runtimeInvocationUrl, SESSION_HEADER } from './runtimeEndpoint';

/**
 * Wake the Text Assistant's container before the first question.
 *
 * A chat session id has its own container on the runtime. Opening a container and
 * filling its caches takes a few seconds, which the advisor would otherwise wait
 * through after sending the first message. This sends a small request with the
 * session id the chat will use; the runtime answers at once and prepares in the
 * background. The request carries no question, so nothing enters the chat history
 * or AgentCore Memory.
 *
 * Fire and forget: no spinner, no error shown, never throws. At most one request
 * per session id in the window below, and none while signed out.
 */
export const WARMUP_WINDOW_MS = 5 * 60 * 1000;

const lastWarm = new Map<string, number>();

function claim(sessionId: string, now: number): boolean {
  const last = lastWarm.get(sessionId);
  if (last !== undefined && now - last < WARMUP_WINDOW_MS) return false;
  lastWarm.set(sessionId, now);
  // Keep the map small in a long-lived tab.
  if (lastWarm.size > 50) {
    for (const [id, at] of lastWarm) {
      if (now - at >= WARMUP_WINDOW_MS) lastWarm.delete(id);
    }
  }
  return true;
}

/** A real message wakes the container too, so no warm-up is needed for a while after one. */
export function markSessionWarm(sessionId: string, now: number = Date.now()): void {
  lastWarm.set(sessionId, now);
}

export async function warmUpSession(sessionId: string | undefined): Promise<void> {
  if (!sessionId || !claim(sessionId, Date.now())) return;
  try {
    const session = await fetchAuthSession();
    const token = session.tokens?.accessToken?.toString();
    if (!token) {
      lastWarm.delete(sessionId); // signed out: send nothing, allow a later try
      return;
    }
    const response = await fetch(runtimeInvocationUrl(), {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${token}`,
        'Content-Type': 'application/json',
        Accept: 'text/event-stream',
        [SESSION_HEADER]: sessionId,
      },
      body: JSON.stringify({ warmup: true }),
    });
    await response.text().catch(() => undefined); // let the connection be reused
  } catch {
    // A failed warm-up only means the first message is not sped up.
  }
}

/** For tests. */
export function resetWarmupState(): void {
  lastWarm.clear();
}
