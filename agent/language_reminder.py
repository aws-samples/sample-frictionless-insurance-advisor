"""Repeat the app-language reminder right before the model writes its answer after tool calls.

The reminder line ("[Reply language: Korean]", see app.reply_language_reminder) follows the advisor's
question. A turn that calls tools puts long English tool results between that line and the answer, and the
model then drifts away from the app language when it writes the answer. This hook adds the same fixed line
as a text block after the tool results, in the user message the model is about to read, for the duration of
that one model call only. Afterwards the block is taken out again, so the conversation that the guards, the
sources footer and AgentCore Memory see is the one without it.

The line comes from the allowlist in app.py and never from the request.
"""
from __future__ import annotations

from typing import Any


def _holds_tool_result(content: Any) -> bool:
    return isinstance(content, list) and any(isinstance(b, dict) and "toolResult" in b for b in content)


def attach_language_reminder(agent: Any, reminder: str) -> None:
    """Register the hook on a Strands agent. Does nothing when `reminder` is empty."""
    if not reminder:
        return
    from strands.hooks import AfterModelCallEvent, BeforeModelCallEvent

    added: list[tuple[list, dict]] = []

    def before_model(event: Any) -> None:
        messages = getattr(agent, "messages", None) or []
        last = messages[-1] if messages else None
        if not isinstance(last, dict) or last.get("role") != "user":
            return
        content = last.get("content")
        if not _holds_tool_result(content):
            return
        block = {"text": reminder}
        content.append(block)
        added.append((content, block))

    def after_model(event: Any) -> None:
        while added:
            content, block = added.pop()
            for i in range(len(content) - 1, -1, -1):
                if content[i] is block:
                    del content[i]
                    break

    agent.hooks.add_callback(BeforeModelCallEvent, before_model)
    agent.hooks.add_callback(AfterModelCallEvent, after_model)
