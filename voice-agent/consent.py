"""Consent for recording a voice conversation.

The advisor is asked, once per session, whether the customer has agreed to the
conversation being transcribed. Until the answer is "confirmed" three things
are blocked in code, whatever the model says:

* memory writes (a session manager that refuses to create events),
* form filling,
* transcript storage.

A declined answer ends the session. Switching from one customer to another
resets consent, because a different person is now being discussed.

The answer comes from one place: the browser's consent message (the Confirmed /
Declined buttons), which the runtime handles on the server. The model has no tool
that can set it, so a misheard word, or text the model reads, cannot start a
recording.

Memory is bound to one customer for the whole connection: the one the advisor
owned when the connection started. While the customer in scope is a different
one, nothing is read from or written to memory (see `memory_bound_to`).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

PENDING = "pending"
CONFIRMED = "confirmed"
DECLINED = "declined"

NOT_CONFIRMED_MESSAGE = (
    "Consent to record this conversation has not been confirmed yet. Ask the "
    "advisor the consent question first. The advisor answers with the buttons on screen."
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

    def stale_for(self, customer_id: str | None) -> bool:
        """True when the consent state was given for a different customer than `customer_id`.

        `customer_id` here is the person the state was recorded or asked for, not
        the customer last seen, so a switch that passes through "no customer"
        (A, then nobody, then B) is still caught.
        """
        return bool(self.customer_id) and bool(customer_id) and self.customer_id != customer_id

    def follow(self, customer_id: str | None) -> None:
        """A prospect that was asked or confirmed with no id has now been given one: same person."""
        if customer_id and not self.customer_id and (self.confirmed or self.prompted):
            self.customer_id = customer_id

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


def consent_reset_needed(consent: "ConsentState", previous: str | None, new: str | None) -> bool:
    """Must the consent state be reset for the customer now in scope?

    True for a direct switch from one customer to another, and also when the
    state was recorded or asked for another customer than `new` even though the
    scope passed through "no customer" in between.
    """
    return customer_changed(previous, new) or consent.stale_for(new)


def memory_bound_to(bound_customer_id: str | None, current_customer_id: str | None) -> bool:
    """True while the customer in scope is the one the memory session was built for.

    The memory session manager is built once per connection, for a customer the
    advisor was verified to own. If the advisor then picks another customer on
    the open connection, the manager still points at the first one, so it must
    neither write the new customer's conversation to the old customer's memory
    nor read the old customer's memories into the new customer's conversation.
    Going back to the first customer turns memory on again. With no bound
    customer there is no memory at all.
    """
    return bool(bound_customer_id) and bound_customer_id == current_customer_id


def make_gated_session_manager(
    base_cls: type,
    gate: Callable[[], bool],
    bound: Callable[[], bool] | None = None,
) -> Callable[..., Any]:
    """A memory session manager that writes nothing until `gate()` is true.

    `create_message` is the one place the AgentCore session manager writes a
    conversation event. Returning None is the documented "nothing was stored"
    result, which `append_message` handles. Reads (restoring history, retrieving
    memories) are unaffected by `gate`.

    `bound()`, when given, must also be true for a write and for a long-term
    memory lookup (`retrieve_customer_context`): it says the customer in scope
    is still the one the manager was built for.
    """

    class ConsentGatedSessionManager(base_cls):  # type: ignore[misc, valid-type]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self._consent_gate = gate
            self._customer_bound = bound or (lambda: True)
            super().__init__(*args, **kwargs)

        def create_message(self, *args: Any, **kwargs: Any) -> Any:
            if not self._consent_gate() or not self._customer_bound():
                return None
            return super().create_message(*args, **kwargs)

        def retrieve_customer_context(self, *args: Any, **kwargs: Any) -> Any:
            if not self._customer_bound():
                return None
            return super().retrieve_customer_context(*args, **kwargs)

    return ConsentGatedSessionManager
