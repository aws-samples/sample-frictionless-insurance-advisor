import i18n from '../i18n/config';

/**
 * A failed call to the backend. `message` is always safe to show to the advisor: it is the
 * backend's own short message for a client error (400, 404, 409, ...), or a fixed translated
 * sentence for anything else: "temporarily unavailable, try again in a few minutes" for 503 and
 * "could not be completed, try again" for the other server errors. The response body itself is only
 * written to the browser console.
 */
export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

/** Longest backend message that is shown as it is. */
const MAX_SERVER_MESSAGE_CHARS = 300;

/** Statuses whose message is replaced by a fixed sentence, whatever the body says. */
const FIXED_TEXT_STATUSES = new Set([401, 403, 429]);

/** The `message` of a JSON error body when it is a short string, else undefined. */
function serverMessage(body: string): string | undefined {
  try {
    const parsed = JSON.parse(body) as { message?: unknown };
    const message = parsed?.message;
    if (typeof message === 'string') {
      const text = message.trim();
      if (text && text.length <= MAX_SERVER_MESSAGE_CHARS) return text;
    }
  } catch {
    /* not JSON: nothing to show */
  }
  return undefined;
}

function fixedMessage(status: number): string {
  if (status === 401 || status === 403) return i18n.t('common.errors.unauthorized');
  if (status === 429) return i18n.t('common.errors.tooManyRequests');
  if (status === 503) return i18n.t('common.errors.serviceUnavailable');
  return i18n.t('common.errors.requestFailed');
}

/**
 * Turn a failed response into an ApiError. `what` names the call for the console
 * (an endpoint path). The body is logged there and never put in the error message.
 */
export function apiErrorFromResponse(what: string, status: number, body: string): ApiError {
  console.error(`[api] ${what} returned ${status}`, body.slice(0, 1000));
  const fromServer =
    status >= 400 && status < 500 && !FIXED_TEXT_STATUSES.has(status) ? serverMessage(body) : undefined;
  return new ApiError(status, fromServer ?? fixedMessage(status));
}

/** The text shown for an error event the chat runtime streamed (a fixed code, never exception text). */
export function chatErrorText(code: string | undefined, correlationId: string | undefined): string {
  switch (code) {
    case 'guardrail_unavailable':
      return i18n.t('common.errors.chat.guardrailUnavailable');
    case 'prompt_too_long':
      return i18n.t('common.errors.chat.promptTooLong');
    case 'limit_reached':
      return i18n.t('common.errors.chat.limitReached');
    default: {
      const text = i18n.t('common.errors.chat.internal');
      // The id is only shown when it has the shape the runtime generates.
      return correlationId && /^[0-9a-f]{6,32}$/.test(correlationId)
        ? `${text} (${i18n.t('common.errors.reference', { id: correlationId })})`
        : text;
    }
  }
}
