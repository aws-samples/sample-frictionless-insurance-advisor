"""Limits on one chat message and the error events the runtime sends to the browser.

Every limit is read once at import from an environment variable (set by the CDK stack from its
context values) and clamped to a range, so a typo cannot switch a limit off or set it absurdly low.

* MAX_PROMPT_CHARS    longest accepted `prompt`, in characters. The browser sends the advisor's
                      question plus a short customer summary, normally under 4,000 characters.
* MAX_AGENT_TURNS     model calls in one message (one turn = one model call plus its tool calls).
                      A normal answer takes 2 to 8; filling a long form takes a few more.
* MAX_TOTAL_TOKENS    input plus output tokens summed over all model calls of one message.
* MAX_OUTPUT_TOKENS   output tokens of a single model call.

Errors reach the browser as fixed sentences with a short code, never as exception text. The
correlation id in the event is also written to the log line for the same failure.
"""
import os
import uuid

DEFAULT_MAX_PROMPT_CHARS = 16_000
DEFAULT_MAX_AGENT_TURNS = 50
DEFAULT_MAX_TOTAL_TOKENS = 2_000_000
DEFAULT_MAX_OUTPUT_TOKENS = 12_000


def bounded_int_env(name: str, default: int, minimum: int, maximum: int) -> int:
    """An integer from the environment: `default` if unset or not a number, otherwise clamped to [minimum, maximum]."""
    raw = os.environ.get(name)
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return default
    return min(maximum, max(minimum, value))


MAX_PROMPT_CHARS = bounded_int_env("MAX_PROMPT_CHARS", DEFAULT_MAX_PROMPT_CHARS, 500, 200_000)
MAX_AGENT_TURNS = bounded_int_env("MAX_AGENT_TURNS", DEFAULT_MAX_AGENT_TURNS, 1, 200)
MAX_TOTAL_TOKENS = bounded_int_env("MAX_TOTAL_TOKENS", DEFAULT_MAX_TOTAL_TOKENS, 10_000, 10_000_000)
MAX_OUTPUT_TOKENS = bounded_int_env("MAX_OUTPUT_TOKENS", DEFAULT_MAX_OUTPUT_TOKENS, 256, 64_000)

# Passed to Agent.stream_async(limits=...).
AGENT_LIMITS = {"turns": MAX_AGENT_TURNS, "total_tokens": MAX_TOTAL_TOKENS}

# Stop reasons Strands reports when one of AGENT_LIMITS ends the loop.
LIMIT_STOP_REASONS = ("limit_turns", "limit_total_tokens", "limit_output_tokens")

# Fixed texts for the error events. The browser shows its own translated wording for each code.
PROMPT_TOO_LONG_MESSAGE = "Your message is too long. Please shorten it and try again."
GUARDRAIL_UNAVAILABLE_MESSAGE = (
    "The assistant is temporarily unavailable because its safety filter could not be loaded. "
    "Please try again in a few minutes."
)
LIMIT_REACHED_MESSAGE = "This request needed more steps than the assistant allows. Please ask for a smaller part of it."
INTERNAL_ERROR_MESSAGE = "Something went wrong while processing your request. Please try again."


def new_correlation_id() -> str:
    """A short random id that ties a client-facing error to its log line."""
    return uuid.uuid4().hex[:12]


def error_event(code: str, message: str, correlation_id: str | None = None) -> dict:
    """The stream event for a failed message: a fixed sentence, a code, and the correlation id when there is one."""
    event = {"error": message, "error_code": code}
    if correlation_id:
        event["correlation_id"] = correlation_id
    return event
