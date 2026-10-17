"""Second layer: keep the model out of the advisor identity on every tool call.

The control that matters is on the server. The AgentCore Gateway's request
interceptor (lambda/gateway_interceptor) verifies the signed-in advisor's own
token before any tool runs, runs the tool as that advisor, and overwrites
`advisor_id` on the Lambda tools that still take it. The REST API accepts only
the advisor's token. So nothing a caller or the model writes can pick the advisor.

This hook is the matching check inside the runtime, so the model is never even
offered the chance. Before any tool runs, `advisor_id` is overwritten with the
identity taken from the verified Cognito token, for every tool whose schema
declares that argument (today `estimate_premium` and
`extract_policy_from_document`); a missing argument is filled in. With no
verified identity the call is refused. If the gateway ever stopped enforcing,
this would still hold the line for these tools; it cannot, and does not try to,
protect the REST tools, which no longer take an advisor argument at all.

Used by both runtimes (the text agent and the voice agent): Strands fires the
same `BeforeToolCallEvent` for both.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

ARG = "advisor_id"


def _declares_advisor_id(tool: Any) -> bool:
    """True if the tool's input schema has an `advisor_id` property."""
    try:
        spec = getattr(tool, "tool_spec", None) or {}
        schema = (spec.get("inputSchema") or {}).get("json") or {}
        return ARG in (schema.get("properties") or {})
    except Exception:  # noqa: BLE001 - an unreadable schema is treated as "not declared"
        return False


def pin_advisor_id(tool_use: dict, tool: Any, advisor_id: str | None) -> bool:
    """Set `advisor_id` on one tool call. Returns True if the call is allowed.

    * Tools that do not declare the argument are left alone.
    * With no verified identity, a call to a tool that declares the argument is
      refused (returns False): better to fail than to run as nobody.
    """
    if not _declares_advisor_id(tool):
        return True
    if not advisor_id:
        return False
    arguments = tool_use.get("input")
    if not isinstance(arguments, dict):
        arguments = {}
        tool_use["input"] = arguments
    supplied = arguments.get(ARG)
    if supplied not in (None, advisor_id):
        # Log that it happened, never the value: it is another user's address.
        logger.warning("Replaced a model-supplied advisor_id that did not match the signed-in advisor")
    arguments[ARG] = advisor_id
    return True


REFUSED = "No signed-in advisor was resolved for this session, so this tool cannot run."


def attach_identity_guard(agent: Any, advisor_id_getter) -> None:
    """Register the guard on a Strands agent (text or voice).

    `advisor_id_getter` is called on every tool call, so a value resolved after
    the agent was built (the voice runtime sets it per connection) is honoured.
    """
    from strands.hooks import BeforeToolCallEvent

    def before_tool(event: Any) -> None:
        try:
            allowed = pin_advisor_id(event.tool_use, getattr(event, "selected_tool", None), advisor_id_getter())
        except Exception:  # noqa: BLE001 - if the guard itself fails, do not run the tool
            allowed = False
        if not allowed:
            event.cancel_tool = REFUSED

    agent.hooks.add_callback(BeforeToolCallEvent, before_tool)
