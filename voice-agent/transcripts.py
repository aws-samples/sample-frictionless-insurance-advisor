"""Voice transcripts, kept in S3 for one day.

What is stored: the conversation as text. Each turn has a time, who spoke
(the advisor or the assistant), whether it was spoken or typed, and the
customer in scope at that moment. Not stored: audio, tool calls, form values.

When: only while the advisor has confirmed the customer's consent for this
session (see consent.py). Nothing is recorded before that, and a declined
consent leaves nothing behind. The recorder starts disabled and every `add_*`
call is ignored until `enable()`.

Where: one JSON object per voice session, rewritten about every 30 seconds
while there are new turns and once more when the connection closes, so a
dropped connection loses at most the last few seconds. The key is
`<advisor>/<start time>-<session id>.json`, and the data Lambda lists only the
signed-in advisor's own prefix.

Transcript text is never written to the logs.
"""
from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.parse import quote

MAX_TURNS = 3000
MAX_TURN_CHARS = 6000
# A typed message may be echoed back as a transcript of the advisor's speech.
_TYPED_ECHO_WINDOW_S = 20.0


def safe_advisor_path(advisor_id: str) -> str:
    """Same mapping as lambda/documents and lambda/comparator_ingest, and the
    data Lambda's reader, so the advisor's prefix lines up everywhere."""
    return advisor_id.replace("@", "_at_").replace("/", "_").replace(":", "_")


def _safe_session(session_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "", session_id or "")[:48] or "session"


def _iso(now: datetime) -> str:
    return now.astimezone(timezone.utc).isoformat(timespec="seconds")


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip()).lower()


class TranscriptRecorder:
    """Collects the final turns of one voice session and saves them to S3."""

    def __init__(
        self,
        advisor_id: str,
        session_id: str,
        *,
        bucket: str = "",
        put_object: Callable[..., Any] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.advisor_id = advisor_id
        self.session_id = session_id
        self.bucket = bucket
        self._put = put_object
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self.started_at = self._clock()
        self.enabled = False
        self.consent_at: str | None = None
        self.turns: list[dict] = []
        self._dirty = False
        self._closed = False
        self._interrupted = False
        self._last_typed: tuple[str, datetime] | None = None
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None

    # --- consent -----------------------------------------------------------
    def enable(self, at_iso: str | None = None) -> None:
        """Consent confirmed: start recording from now."""
        self.enabled = True
        self.consent_at = at_iso or _iso(self._clock())
        self._interrupted = True  # the first assistant turn starts a new bubble

    def pause(self) -> None:
        """Consent withdrawn or reset (a different customer): stop recording.
        What was already saved stays; it was recorded with consent."""
        self.enabled = False

    def clear(self) -> None:
        """Consent declined: drop everything held in memory. Nothing has been
        written, because nothing is written before consent."""
        self.enabled = False
        self.turns = []
        self._dirty = False

    # --- capture -----------------------------------------------------------
    def note_interruption(self) -> None:
        self._interrupted = True

    def add_user(
        self, text: str, customer_id: str | None, customer_name: str | None, *, typed: bool = False
    ) -> None:
        if not self.enabled:
            return
        text = (text or "").strip()
        if not text:
            return
        now = self._clock()
        if typed:
            self._last_typed = (_norm(text), now)
        elif self._last_typed:
            echo, at = self._last_typed
            if _norm(text) == echo and (now - at).total_seconds() <= _TYPED_ECHO_WINDOW_S:
                self._last_typed = None
                return
        self._append(
            {
                "t": _iso(now),
                "role": "advisor",
                "mode": "typed" if typed else "spoken",
                "text": text[:MAX_TURN_CHARS],
                "customer_id": customer_id,
                "customer": customer_name,
            }
        )

    def add_assistant(
        self,
        text: str,
        customer_id: str | None,
        customer_name: str | None,
    ) -> None:
        """Add one completed assistant transcript.

        The agent sends each assistant utterance once, as a completed block.
        Consecutive utterances join into one turn, as on screen, until an
        interruption or the advisor speaks again.
        """
        if not self.enabled:
            return
        text = (text or "").strip()
        if not text:
            return
        last = self.turns[-1] if self.turns else None
        if last and last["role"] == "assistant" and not self._interrupted:
            last["text"] = (last["text"].rstrip() + " " + text)[:MAX_TURN_CHARS]
            self._dirty = True
            return
        self._interrupted = False
        self._append(
            {
                "t": _iso(self._clock()),
                "role": "assistant",
                "mode": "spoken",
                "text": text[:MAX_TURN_CHARS],
                "customer_id": customer_id,
                "customer": customer_name,
            }
        )

    def observe(self, event: Any, customer_id: str | None, customer_name: str | None) -> None:
        """Look at one BidiAgent event. Never raises.

        Only completed transcripts are kept: `bidi_transcript_block` carries the
        whole utterance for either speaker, so no partial or repeated text is
        stored. A barge-in marks the next assistant text as a new turn.
        """
        try:
            if not isinstance(event, dict):
                return
            etype = event.get("type")
            if etype == "bidi_transcript_block":
                text = event.get("transcript") or ""
                if event.get("role") == "user":
                    self.add_user(text, customer_id, customer_name)
                else:
                    self.add_assistant(text, customer_id, customer_name)
            elif etype == "bidi_barge_in":
                self.note_interruption()
        except Exception:  # noqa: BLE001 - recording must never break the call
            return

    def _append(self, turn: dict) -> None:
        if len(self.turns) >= MAX_TURNS:
            return
        if turn["role"] == "advisor":
            self._interrupted = True  # a reply after the advisor speaks is a new turn
        self.turns.append(turn)
        self._dirty = True

    # --- saving ------------------------------------------------------------
    @property
    def key(self) -> str:
        stamp = self.started_at.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        return f"{safe_advisor_path(self.advisor_id)}/{stamp}-{_safe_session(self.session_id)}.json"

    def _document(self, ended: bool) -> tuple[bytes, dict[str, str]]:
        now = _iso(self._clock())
        doc = {
            "version": 1,
            "advisor": self.advisor_id,
            "session_id": self.session_id,
            "started_at": _iso(self.started_at),
            "consent_confirmed_at": self.consent_at,
            "updated_at": now,
            "ended": ended,
            "turns": self.turns,
        }
        last_customer = next((t.get("customer") for t in reversed(self.turns) if t.get("customer")), "") or ""
        meta = {
            "started": _iso(self.started_at),
            "updated": now,
            "turns": str(len(self.turns)),
            "customer": quote(last_customer, safe=""),
            "ended": "1" if ended else "0",
        }
        return json.dumps(doc, ensure_ascii=False).encode("utf-8"), meta

    async def flush(self, *, ended: bool = False) -> bool:
        """Write the object if there is something new. True if it wrote."""
        if not self.bucket or self._put is None or not self.turns:
            return False
        if not (self._dirty or ended):
            return False
        async with self._lock:
            body, meta = self._document(ended)
            self._dirty = False
            try:
                await asyncio.to_thread(
                    self._put,
                    Bucket=self.bucket,
                    Key=self.key,
                    Body=body,
                    ContentType="application/json",
                    ServerSideEncryption="AES256",
                    Metadata=meta,
                )
                return True
            except Exception as exc:  # noqa: BLE001 - log the type only, never the text
                self._dirty = True
                print(f"transcript save failed: {type(exc).__name__}")
                return False

    def start(self, interval: float = 30.0) -> None:
        """Begin the periodic save."""
        if self._task is None:
            self._task = asyncio.ensure_future(self._loop(interval))

    async def _loop(self, interval: float) -> None:
        try:
            while not self._closed:
                await asyncio.sleep(interval)
                await self.flush()
        except asyncio.CancelledError:
            raise

    async def close(self) -> None:
        """Connection ended: stop the loop and save a last time."""
        self._closed = True
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None
        await self.flush(ended=True)
