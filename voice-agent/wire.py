"""The messages between the browser and the voice runtime.

The browser speaks a small protocol of its own (`bidi_audio_input`,
`bidi_audio_stream`, `bidi_transcript_stream`, `bidi_interruption`, plus the
`voice_*`, `consent` and form messages). Strands' BidiAgent has its own event
types, which have changed between releases. This module is the one place that
translates between the two, so a library upgrade touches this file and not the
browser.

    browser -> agent   `to_agent_input`      audio becomes an `audio_delta`
    agent -> browser   `BrowserEvents.convert`  stable `strands.bidi` events

Nothing here needs the Strands library: events are plain dicts, so it can be
tested without it.
"""
from __future__ import annotations

import base64
import binascii
from typing import Any

# Largest audio chunk accepted from the browser, in bytes (about 3 s of 16 kHz
# mono 16-bit PCM). A real frame is a few hundred bytes to a few kB.
MAX_AUDIO_BYTES = 96_000


def silence_input(sample_count: int = 1600) -> dict[str, Any]:
    """A short run of silent 16-bit PCM, in the shape `BidiAgent.send` accepts."""
    return {"audio_delta": {"format": "pcm", "source": {"bytes": bytes(sample_count * 2)}}}


def to_agent_input(msg: Any) -> dict[str, Any] | None:
    """Turn a browser audio message into a `BidiAgent.send` input.

    Returns None for anything that is not usable audio (the caller skips it).
    The model's input rate is fixed when the model is built, so the message's
    own `sample_rate` is not forwarded.
    """
    if not isinstance(msg, dict) or msg.get("type") != "bidi_audio_input":
        return None
    encoded = msg.get("audio")
    if not isinstance(encoded, str) or not encoded:
        return None
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        return None
    if not raw or len(raw) > MAX_AUDIO_BYTES:
        return None
    return {"audio_delta": {"format": "pcm", "source": {"bytes": raw}}}


class BrowserEvents:
    """Converts BidiAgent output events into the browser's messages.

    Assistant transcripts stream: each delta goes out as a partial, and the
    completed block goes out as the final text that replaces it. The advisor's
    own transcript is cumulative (the browser replaces its partial line) and
    final on the block. Anything the browser has no use for returns an empty
    list.
    """

    def __init__(self) -> None:
        self._user_partial: dict[str, str] = {}

    def convert(self, event: Any) -> list[dict[str, Any]]:
        if not isinstance(event, dict):
            return []
        etype = event.get("type")

        if etype == "bidi_audio_delta":
            audio = event.get("audio")
            if not audio:
                return []
            return [{
                "type": "bidi_audio_stream",
                "audio": audio,
                "format": event.get("format") or "pcm",
                "sample_rate": event.get("sample_rate") or 24000,
                "channels": event.get("channels") or 1,
            }]

        if etype == "bidi_transcript_delta":
            delta = event.get("delta") or ""
            role = "user" if event.get("role") == "user" else "assistant"
            if not delta:
                return []
            if role == "user":
                key = str(event.get("content_id") or "")
                text = self._user_partial.get(key, "") + delta
                self._user_partial[key] = text
                return [{"type": "bidi_transcript_stream", "role": "user", "text": text, "is_final": False}]
            return [{"type": "bidi_transcript_stream", "role": "assistant", "text": delta, "is_final": False}]

        if etype == "bidi_transcript_block":
            role = "user" if event.get("role") == "user" else "assistant"
            self._user_partial.pop(str(event.get("content_id") or ""), None)
            text = event.get("transcript") or ""
            if not text:
                return []
            return [{"type": "bidi_transcript_stream", "role": role, "text": text, "is_final": True}]

        if etype == "bidi_barge_in":
            self._user_partial.clear()
            return [{"type": "bidi_interruption"}]

        return []
