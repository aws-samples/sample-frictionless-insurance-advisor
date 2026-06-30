/**
 * SSE parser for the AgentCore streaming response.
 *
 * Reads the AgentCore SSE stream:
 *   - iterates over line-delimited SSE events (`data: {...}`)
 *   - JSON-parses the payload
 *   - yields `event.data` when it's a string, ignores everything else
 *
 * Non-JSON data, empty lines, and [DONE] markers are skipped. Partial lines
 * are buffered across reads so a chunk split mid-JSON is handled correctly.
 */
export async function* readSSEStream(response: Response): AsyncGenerator<string> {
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
        const text = extractText(line);
        if (text) {
          yield text;
        }
      }
    }

    // Flush any final buffered line at end of stream.
    if (buffer.length > 0) {
      const text = extractText(buffer);
      if (text) {
        yield text;
      }
    }
  } finally {
    reader.releaseLock();
  }
}

/** Extract the `data` string from a single SSE line, or null if not a payload. */
function extractText(line: string): string | null {
  if (!line.startsWith('data: ')) {
    return null;
  }
  const raw = line.slice(6).trim();
  if (!raw || raw === '[DONE]') {
    return null;
  }
  try {
    const event = JSON.parse(raw);
    if (event && typeof event === 'object' && 'data' in event) {
      const payload = (event as { data: unknown }).data;
      if (typeof payload === 'string' && payload.length > 0) {
        return payload;
      }
    }
    return null;
  } catch {
    // Non-JSON lines (e.g., Python repr strings) are ignored.
    return null;
  }
}
