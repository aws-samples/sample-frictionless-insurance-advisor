"""Make `add_note` a tool the assistant can only use when asked.

The system prompt and the tool description both say: save a note only when the
advisor explicitly asks, and never because they merely mentioned something
("Lisa told me she is moving"). Models still get this wrong some of the time
(2 of 6 in a live test), and a wrongly saved note is a write to the customer's
record. So the rule is also enforced here, in code, before the tool runs.

`add_note` is allowed when:
* the advisor's own message asks for a note ("add a note that...", "save this
  as a note", "note that she...", "please note..."), or
* the advisor answers "yes" (or similar) right after the assistant offered to
  save a note.

Otherwise the call is cancelled with a message that tells the model what to do
instead: answer, then offer. Nothing is written.
"""
from __future__ import annotations

import re
from typing import Any

_VERB_NOTE = re.compile(
    r"\b(add|save|record|log|write|put|store|create|make|jot|take|keep|enter|file|attach)\b[^.\n]{0,40}\bnotes?\b",
    re.IGNORECASE,
)
_NOTE_THAT = re.compile(r"\bnote\s+(that|this|down|it|the fact|these)\b", re.IGNORECASE)
_NOTE_COLON = re.compile(r"(^|\n)\s*notes?\s*:", re.IGNORECASE)
_PLEASE_NOTE = re.compile(r"\b(please|pls|kindly)\s+note\b", re.IGNORECASE)
_AFFIRM = re.compile(
    r"^\s*[\"'*_]*(yes|yeah|yep|yup|sure|ok|okay|please|go ahead|do it|add it|save it|that'?s right|correct)\b",
    re.IGNORECASE,
)

CANCEL_MESSAGE = (
    "Not saved. The advisor has not asked for a note in this message, so no note was written. "
    "Answer what they said, then offer once at the end: \"Want me to add that as a note?\" "
    "Save it only after they say yes."
)


def advisor_text(message_text: str) -> str:
    """The advisor's own words, without the hidden customer header or any
    memory context the runtime wraps around them."""
    marker = message_text.rfind("Question:")
    text = message_text[marker + len("Question:"):] if marker != -1 else message_text
    # A form-state note and attachment tags are appended by the app, not typed.
    text = re.sub(r"\[Attached document:[^\]]*\]", " ", text)
    text = re.sub(r"\n\[[^\]]*(form|application)[^\]]*\]\s*$", " ", text, flags=re.IGNORECASE)
    return text.strip()


def asks_for_note(text: str) -> bool:
    """True if the advisor's words instruct the assistant to save a note."""
    if not text:
        return False
    return bool(
        _VERB_NOTE.search(text)
        or _NOTE_THAT.search(text)
        or _NOTE_COLON.search(text)
        or _PLEASE_NOTE.search(text)
    )


def _texts(message: dict) -> list[str]:
    out: list[str] = []
    for block in message.get("content") or []:
        if isinstance(block, dict) and isinstance(block.get("text"), str):
            # Retrieved memory is added as its own block; it is not the advisor's words.
            if block["text"].lstrip().startswith("<user_context>"):
                continue
            out.append(block["text"])
    return out


def _current_advisor_message(messages: list[Any]) -> tuple[str, int]:
    """The latest user message that carries text (not a tool result), and its index."""
    for i in range(len(messages) - 1, -1, -1):
        m = messages[i]
        if isinstance(m, dict) and m.get("role") == "user":
            texts = _texts(m)
            if texts:
                return advisor_text("\n".join(texts)), i
    return "", -1


def note_allowed(messages: list[Any]) -> bool:
    """May `add_note` run now, given the conversation so far?"""
    text, idx = _current_advisor_message(messages)
    if asks_for_note(text):
        return True
    if text and _AFFIRM.match(text) and idx > 0:
        # "yes" only counts as an answer to an offer to save a note.
        for j in range(idx - 1, -1, -1):
            m = messages[j]
            if isinstance(m, dict) and m.get("role") == "assistant":
                prev = " ".join(_texts(m)).lower()
                if prev:
                    return bool(re.search(r"\bnotes?\b", prev)) and bool(re.search(r"\b(want me|shall i|should i|would you like|do you want|can i|i can)\b", prev))
    return False


def attach_note_guard(agent: Any) -> None:
    """Register the guard on a Strands agent."""
    from strands.hooks import BeforeToolCallEvent

    def before_tool(event: Any) -> None:
        name = str((getattr(event, "tool_use", None) or {}).get("name", ""))
        if name.rsplit("___", 1)[-1] != "add_note":
            return
        try:
            allowed = note_allowed(list(getattr(agent, "messages", []) or []))
        except Exception:  # noqa: BLE001 - if the check itself fails, do not write
            allowed = False
        if not allowed:
            event.cancel_tool = CANCEL_MESSAGE

    agent.hooks.add_callback(BeforeToolCallEvent, before_tool)
