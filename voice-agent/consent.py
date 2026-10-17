"""Consent for recording a voice conversation.

The advisor is asked, once per session, whether the customer has agreed to the
conversation being transcribed. Until the answer is "confirmed" three things
are blocked in code, whatever the model says:

* memory writes (a session manager that refuses to create events),
* form filling,
* transcript storage.

A declined answer ends the session. Switching from one customer to another
resets consent, because a different person is now being discussed.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

PENDING = "pending"
CONFIRMED = "confirmed"
DECLINED = "declined"

NOT_CONFIRMED_MESSAGE = (
    "Consent to record this conversation has not been confirmed yet. Ask the "
    "advisor the consent question first, and call record_consent once they answer."
)


class ConsentState:
    def __init__(self, clock: Callable[[], datetime] | None = None) -> None:
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self.status = PENDING
        self.at: str | None = None
        self.customer_id: str | None = None
        self.prompted = False

    @property
    def confirmed(self) -> bool:
        return self.status == CONFIRMED

    def apply(self, decision: str, customer_id: str | None) -> bool:
        """Record the advisor's answer. False if it is not a valid answer."""
        decision = (decision or "").strip().lower()
        if decision in ("confirmed", "confirm", "yes", "agreed"):
            self.status = CONFIRMED
            self.at = self._clock().astimezone(timezone.utc).isoformat(timespec="seconds")
            self.customer_id = customer_id
            return True
        if decision in ("declined", "decline", "no", "refused"):
            self.status = DECLINED
            self.at = None
            return True
        return False

    def reset(self) -> None:
        self.status = PENDING
        self.at = None
        self.customer_id = None
        self.prompted = False

    def event(self, customer_id: str | None, reason: str | None = None) -> dict[str, Any]:
        """The message the browser receives."""
        return {
            "type": "consent",
            "status": self.status,
            "at": self.at,
            "customerId": customer_id,
            **({"reason": reason} if reason else {}),
        }


def customer_changed(previous: str | None, new: str | None) -> bool:
    """True when the customer in scope moves from one person to another.

    Prospect to customer (None to an id) is the same person getting a profile,
    so consent carries over. Customer to nobody does not need a new answer.
    """
    return bool(previous) and bool(new) and previous != new


def make_gated_session_manager(base_cls: type, gate: Callable[[], bool]) -> Callable[..., Any]:
    """A memory session manager that writes nothing until `gate()` is true.

    `create_message` is the one place the AgentCore session manager writes a
    conversation event. Returning None is the documented "nothing was stored"
    result, which `append_message` handles. Reads (restoring history, retrieving
    memories) are unaffected.
    """

    class ConsentGatedSessionManager(base_cls):  # type: ignore[misc, valid-type]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self._consent_gate = gate
            super().__init__(*args, **kwargs)

        def create_message(self, *args: Any, **kwargs: Any) -> Any:
            if not self._consent_gate():
                return None
            return super().create_message(*args, **kwargs)

    return ConsentGatedSessionManager
