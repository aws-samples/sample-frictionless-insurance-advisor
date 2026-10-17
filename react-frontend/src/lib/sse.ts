/**
 * SSE parser for the AgentCore streaming response.
 *
 * Reads the AgentCore SSE stream:
 *   - iterates over line-delimited SSE events (`data: {...}`)
 *   - JSON-parses the payload
 *   - yields `{ kind: 'text' }` for `event.data` when it is a string
 *   - yields `{ kind: 'app_event' }` for the typed events the agent adds
 *     beside the text (`app_event`: form_open, form_fill, sources)
 *   - ignores everything else
 *
 * Non-JSON data, empty lines, and [DONE] markers are skipped. Partial lines
 * are buffered across reads so a chunk split mid-JSON is handled correctly.
 */

/** A typed event raised by one of the agent's own tools (not by the model). */
export interface AppEvent {
  type: string;
  [key: string]: unknown;
}

export type StreamItem =
  | { kind: 'text'; text: string }
  | { kind: 'app_event'; event: AppEvent };

export async function* readSSEStream(response: Response): AsyncGenerator<StreamItem> {
  if (!response.body) {
    throw new Error('No response body');
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) {
        break;
      }
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      // Preserve any trailing partial line for the next read.
      buffer = lines.pop() ?? '';
      for (const line of lines) {
        const item = extractItem(line);
        if (item) {
          yield item;
        }
      }
    }
    // Flush any final buffered line at end of stream.
    if (buffer.length > 0) {
      const item = extractItem(buffer);
      if (item) {
        yield item;
      }
    }
  } finally {
    reader.releaseLock();
  }
}

/** Extract the payload from a single SSE line, or null if there is none. */
function extractItem(line: string): StreamItem | null {
  if (!line.startsWith('data: ')) {
    return null;
  }
  const raw = line.slice(6).trim();
  if (!raw || raw === '[DONE]') {
    return null;
  }
  try {
    const event = JSON.parse(raw);
    if (event && typeof event === 'object') {
      if ('data' in event) {
        const payload = (event as { data: unknown }).data;
        if (typeof payload === 'string' && payload.length > 0) {
          return { kind: 'text', text: payload };
        }
      }
      if ('app_event' in event) {
        const appEvent = (event as { app_event: unknown }).app_event;
        if (
          appEvent &&
          typeof appEvent === 'object' &&
          typeof (appEvent as { type?: unknown }).type === 'string'
        ) {
          return { kind: 'app_event', event: appEvent as AppEvent };
        }
      }
    }
    return null;
  } catch {
    // Non-JSON lines (e.g., Python repr strings) are ignored.
    return null;
  }
}
