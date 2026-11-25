"""Limits on one voice connection.

Each value is read once at import from an environment variable (set by the CDK stack from its
context values) and clamped to a range, so a typo cannot switch a limit off or set it absurdly low.

* MAX_AUDIO_CHUNK_BYTES     largest single audio message from the browser. A real frame is a few
                            hundred bytes to a few kB; 96,000 bytes is about 3 s of 16 kHz mono PCM.
* MAX_SESSION_AUDIO_BYTES   total microphone audio accepted on one connection. Real-time audio at
                            16 kHz mono 16-bit is 32,000 bytes per second, so 64,000,000 bytes is
                            about 33 minutes.
* MAX_SESSION_SECONDS       wall-clock length of one connection (30 minutes). The runtime's own
                            maximum lifetime is 8 hours.
* MAX_TEXT_INPUT_CHARS      longest typed message sent to the model.
* MAX_PREFILL_FIELDS / MAX_PREFILL_VALUE_CHARS
                            size of the form prefill the browser sends with the customer context.
* MAX_CUSTOMER_NAME_CHARS / MAX_CUSTOMER_ID_CHARS
                            size of the customer name and id the browser sends; both reach the
                            model in short context notes.
"""
import os
from typing import Any


def bounded_int_env(name: str, default: int, minimum: int, maximum: int) -> int:
    """An integer from the environment: `default` if unset or not a number, otherwise clamped to [minimum, maximum]."""
    raw = os.environ.get(name)
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return default
    return min(maximum, max(minimum, value))


MAX_AUDIO_CHUNK_BYTES = bounded_int_env("MAX_AUDIO_CHUNK_BYTES", 96_000, 1_000, 1_000_000)
MAX_SESSION_AUDIO_BYTES = bounded_int_env("MAX_SESSION_AUDIO_BYTES", 64_000_000, 1_000_000, 2_000_000_000)
MAX_SESSION_SECONDS = bounded_int_env("MAX_SESSION_SECONDS", 1800, 60, 14_400)
MAX_TEXT_INPUT_CHARS = bounded_int_env("MAX_TEXT_INPUT_CHARS", 4000, 100, 50_000)
MAX_PREFILL_FIELDS = 200
MAX_PREFILL_VALUE_CHARS = 500
MAX_CUSTOMER_NAME_CHARS = 200
MAX_CUSTOMER_ID_CHARS = 64


def clipped_text(value: Any, limit: int) -> str | None:
    """`value` as a stripped string of at most `limit` characters, or None if it is not a non-empty string."""
    if not isinstance(value, str):
        return None
    text = value.strip()[:limit]
    return text or None


def customer_id_from(value: Any) -> str | None:
    """The customer id the browser sent, or None when it is missing, not a string or too long."""
    if not isinstance(value, str) or not value.strip() or len(value) > MAX_CUSTOMER_ID_CHARS:
        return None
    return value.strip()


def bounded_prefill(value: Any) -> dict[str, Any]:
    """The browser's form prefill reduced to at most MAX_PREFILL_FIELDS string-keyed scalar values.

    Strings are cut to MAX_PREFILL_VALUE_CHARS; anything that is not a string, number or boolean is dropped.
    """
    if not isinstance(value, dict):
        return {}
    out: dict[str, Any] = {}
    for path, item in value.items():
        if len(out) >= MAX_PREFILL_FIELDS:
            break
        if not isinstance(path, str) or isinstance(item, (dict, list)) or item is None:
            continue
        out[path[:200]] = item[:MAX_PREFILL_VALUE_CHARS] if isinstance(item, str) else item
    return out
