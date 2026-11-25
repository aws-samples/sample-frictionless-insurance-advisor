/**
 * SSE parser for the AgentCore streaming response.
 *
 * Reads the AgentCore SSE stream:
 *   - iterates over line-delimited SSE events (`data: {...}`)
 *   - JSON-parses the payload
 *   - yields `{ kind: 'text' }` for `event.data` when it is a string
 *   - yields `{ kind: 'app_event' }` for the typed events the agent adds
 *     beside the text (`app_event`: form_open, form_fill, sources)
 *   - yields `{ kind: 'tool_use' }` when the agent starts a tool call, so the
 *     caller can tell that the next text belongs to a new model turn
 *   - yields `{ kind: 'error' }` for an `error` event (its code and reference id only)
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
  | { kind: 'app_event'; event: AppEvent }
  | { kind: 'tool_use'; name: string }
  | { kind: 'error'; code?: string; correlationId?: string };

/**
 * Append a streamed text chunk to the answer shown so far.
 *
 * The agent writes a short lead-in, calls its tools, and then writes the real
 * answer as a separate model turn. The two texts arrive as plain chunks with no
 * separator, so "Checking the record." and "## Review pack" would be glued into
 * one line and the heading would not render. When `afterTool` is true (a tool
 * call came between the previous chunk and this one) and both sides have text,
 * make sure a blank line separates them. Chunks of the same turn concatenate
 * unchanged.
 */
export function appendChunk(content: string, chunk: string, afterTool: boolean): string {
  if (!afterTool || content.trim() === '' || chunk === '') {
    return content + chunk;
  }
  const trailing = /\n*$/.exec(content)?.[0].length ?? 0;
  const leading = /^\n*/.exec(chunk)?.[0].length ?? 0;
  return content + '\n'.repeat(Math.max(0, 2 - trailing - leading)) + chunk;
}

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
      if ('tool_use' in event) {
        const name = (event as { tool_use: unknown }).tool_use;
        if (typeof name === 'string') {
          return { kind: 'tool_use', name };
        }
      }
      if ('error' in event) {
        // Only the code and the reference id are used: the sentence the runtime sends is not shown.
        const { error_code: code, correlation_id: correlationId } = event as {
          error_code?: unknown;
          correlation_id?: unknown;
        };
        return {
          kind: 'error',
          code: typeof code === 'string' ? code : undefined,
          correlationId: typeof correlationId === 'string' ? correlationId : undefined,
        };
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
