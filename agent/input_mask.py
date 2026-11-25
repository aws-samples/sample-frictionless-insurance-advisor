"""Mask sensitive identifiers in the advisor's typed message before anything stores it.

Why this exists: the Bedrock guardrail on the model call hides an SSN or card number
from the model, but Strands writes the message to AgentCore Memory, and OpenTelemetry
logs it, before the model is called. So the message is masked first, at the top of
`invoke()`, and only the masked text goes on to the Agent, Memory and the logs.

`mask_user_text()` calls the Bedrock `ApplyGuardrail` API (source INPUT) with the same
guardrail and version as the model call:
  * action NONE                      -> the original text, outcome `none`
  * GUARDRAIL_INTERVENED, only masks -> the masked text, outcome `masked`
  * GUARDRAIL_INTERVENED, any BLOCK  -> outcome `blocked` and the guardrail's own
                                        blocked-input sentence (password, AWS key, denied
                                        topic, prompt attack); the caller answers at once
  * anything else (error, timeout, guardrail or version unknown, odd response)
                                     -> the local masker below, outcome `fallback`

The local masker is a conservative regex + checksum pass for the identifiers that matter
most (SSN, card numbers with a Luhn check, IBAN with the mod-97 check). It never refuses
the turn.

Privacy: this module never logs the text, the guardrail's `match` values or an exception
message. It logs the outcome, the entity TYPE names and the time.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger(__name__)

NONE = "none"
MASKED = "masked"
BLOCKED = "blocked"
FALLBACK = "fallback"

# Used only if the guardrail blocks but returns no sentence (the CDK guardrail's own wording).
DEFAULT_BLOCKED_MESSAGE = (
    "I'm sorry, I can't process that request. Please rephrase your question about insurance services."
)

SSN = "US_SOCIAL_SECURITY_NUMBER"
ITIN = "US_INDIVIDUAL_TAX_IDENTIFICATION_NUMBER"
CARD = "CREDIT_DEBIT_CARD_NUMBER"
CARD_CVV = "CREDIT_DEBIT_CARD_CVV"
CARD_EXPIRY = "CREDIT_DEBIT_CARD_EXPIRY"
IBAN = "INTERNATIONAL_BANK_ACCOUNT_NUMBER"


@dataclass(frozen=True)
class MaskResult:
    """What to use instead of the typed text.

    `text` is the text the rest of the request may use. For `blocked` it is empty: the
    caller answers with `blocked_message` and must not touch the Agent or Memory.
    """
    text: str
    outcome: str
    blocked_message: Optional[str]
    elapsed_ms: int


# --- Bedrock ApplyGuardrail ----------------------------------------------------------------

# One attempt, at most 1.5 s to connect and 3 s to read: a single call is over after 4.5 s at the
# latest (DNS lookups are not covered by these two; the caller's own wait covers them). A second
# attempt would allow 9 s, more than the 6 s the caller waits, so there is none: a failed call
# falls back to the local masker at once. A healthy call takes 0.2 to 1.0 s.
CONNECT_TIMEOUT_SECONDS = 1.5
READ_TIMEOUT_SECONDS = 3
TOTAL_ATTEMPTS = 1

_client: Any = None
_client_lock = threading.Lock()
_TOKEN = re.compile(r"[^A-Za-z0-9_.:+-]")
_REASON_TOKEN = re.compile(r"[^A-Za-z_:]")  # letters only: no digit from a text can end up in a reason


def _get_client(region: str):
    """One bedrock-runtime client per container, created on first use (thread-safe).

    It has its own boto3 Session so creating it never races with other clients being
    built from the default session in other threads.
    """
    global _client
    if _client is not None:
        return _client
    with _client_lock:
        if _client is None:
            import boto3
            from botocore.config import Config

            _client = boto3.session.Session().client(
                "bedrock-runtime",
                region_name=region,
                config=Config(
                    connect_timeout=CONNECT_TIMEOUT_SECONDS,
                    read_timeout=READ_TIMEOUT_SECONDS,
                    retries={"total_max_attempts": TOTAL_ATTEMPTS},
                ),
            )
        return _client


def prewarm(region: str) -> bool:
    """Create the bedrock-runtime client now (service model load and credential lookup), so the first
    real message of this container does not pay for it (0.2 to 0.8 s of the first `ApplyGuardrail` call).

    Makes no API call and handles no user text. Never raises; returns True when the client is ready.
    """
    try:
        _get_client(region)
        return True
    except Exception as exc:  # noqa: BLE001 - the first message creates the client itself
        logger.warning("Input mask: client prewarm failed (%s)", type(exc).__name__)
        return False


def prewarm_in_background(region: str) -> None:
    """`prewarm` in a daemon thread."""
    threading.Thread(target=prewarm, args=(region,), name="input-mask-prewarm", daemon=True).start()


_prewarm_requested = False
_prewarm_requested_lock = threading.Lock()


def prewarm_once_in_background(region: str) -> bool:
    """Start `prewarm_in_background` the first time this is called in the process; later calls do nothing.

    Called from the request handler, so the thread and the client are created after the
    container is running for a session. Creating them at import would put a thread and a
    credentialed client into the AgentCore Runtime V2 snapshot, which every session then
    restores. Returns True when this call started the prewarm.
    """
    global _prewarm_requested
    with _prewarm_requested_lock:
        if _prewarm_requested:
            return False
        _prewarm_requested = True
    prewarm_in_background(region)
    return True


def _name(value: Any) -> str:
    """A short plain token for the log (an enum word), never free text."""
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return "?"
    return _TOKEN.sub("_", str(value))[:48] or "?"


def _walk_assessments(assessments: Any) -> tuple[bool, list[str]]:
    """(any BLOCKED action, entity/topic type names) from the `assessments` list. Never reads `match`."""
    blocked = False
    names: list[str] = []
    if not isinstance(assessments, list):
        return blocked, names

    def entries(policy: Any, key: str) -> list[dict]:
        items = policy.get(key) if isinstance(policy, dict) else None
        return [i for i in items if isinstance(i, dict)] if isinstance(items, list) else []

    for assessment in assessments:
        if not isinstance(assessment, dict):
            continue
        groups = (
            ("topic", entries(assessment.get("topicPolicy"), "topics"), "name"),
            ("filter", entries(assessment.get("contentPolicy"), "filters"), "type"),
            ("pii", entries(assessment.get("sensitiveInformationPolicy"), "piiEntities"), "type"),
            ("regex", entries(assessment.get("sensitiveInformationPolicy"), "regexes"), "name"),
            ("wordlist", entries(assessment.get("wordPolicy"), "managedWordLists"), "type"),
            ("word", entries(assessment.get("wordPolicy"), "customWords"), None),
        )
        for kind, items, key in groups:
            for item in items:
                action = item.get("action")
                if action == "BLOCKED":
                    blocked = True
                if action in ("BLOCKED", "ANONYMIZED"):
                    # A custom word is the user's own text: never copy its name or value.
                    names.append(_name(item.get(key)) if key else "custom")
    return blocked, names


def _apply_guardrail(text: str, guardrail_id: str, guardrail_version: str, region: str) -> MaskResult:
    started = time.monotonic()
    response = _get_client(region).apply_guardrail(
        guardrailIdentifier=guardrail_id,
        guardrailVersion=guardrail_version,
        source="INPUT",
        content=[{"text": {"text": text}}],
    )
    elapsed = int((time.monotonic() - started) * 1000)
    action = response.get("action")
    if action == "NONE":
        logger.info("Input mask: outcome=none entities=- elapsed_ms=%d", elapsed)
        return MaskResult(text, NONE, None, elapsed)
    if action != "GUARDRAIL_INTERVENED":
        raise ValueError("unexpected action")
    outputs = response.get("outputs")
    output_text = None
    if isinstance(outputs, list) and outputs and isinstance(outputs[0], dict):
        output_text = outputs[0].get("text")
    blocked, names = _walk_assessments(response.get("assessments"))
    if blocked or not names:
        # No assessment that only masks: Strands also treats an intervention it cannot
        # explain as a block, so the turn is answered with the guardrail's sentence.
        message = output_text if isinstance(output_text, str) and output_text.strip() else DEFAULT_BLOCKED_MESSAGE
        logger.info("Input mask: outcome=blocked entities=%s elapsed_ms=%d", ",".join(dict.fromkeys(names)) or "-", elapsed)
        return MaskResult("", BLOCKED, message, elapsed)
    if not isinstance(output_text, str) or not output_text:
        raise ValueError("masked output missing")
    logger.info("Input mask: outcome=masked entities=%s elapsed_ms=%d", ",".join(dict.fromkeys(names)), elapsed)
    return MaskResult(output_text, MASKED, None, elapsed)


# --- Local masker (fallback) ---------------------------------------------------------------

def _luhn_ok(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = ord(ch) - 48
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


# Issuer prefixes: Visa 4, Mastercard 51-55 and 2221-2720, Amex/Diners/JCB 30-38, Discover/UnionPay 6.
_CARD_PREFIX = re.compile(r"^(?:4|5[1-5]|2(?:2[2-9]|[3-6]\d|7[01]|720)|3[0-8]|6(?:011|5|4[4-9]|2))")
_CARD = re.compile(
    r"(?<![\w-])(?:"
    r"\d{4}([ -]?)\d{4}\1\d{4}\1\d{1,7}"      # 4-4-4-1..7 (13-19 digits)
    r"|\d{4}([ -])\d{6}\2\d{4,5}"              # Amex style 4-6-5 / diners 4-6-4
    r"|\d{13,19}"
    r")(?![\w-])"
)
_SSN_DASH = re.compile(r"(?<![\d-])(\d{3})-(\d{2})-(\d{4})(?![\d-])")
_SSN_SPACE = re.compile(r"(?<![\d-])(\d{3}) (\d{2}) (\d{4})(?![\d-])")
_SSN_WORD = re.compile(
    r"(?i)\b(ssn|social security(?: number)?|social)\b([^\d\n]{0,20}?)(?<![\d-])(\d{9})(?![\d-])"
)
_IBAN = re.compile(r"(?<![A-Za-z0-9])[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,3})?(?![A-Za-z0-9])")
_CARD_EXPIRY = re.compile(
    r"(?i)\b(exp(?:iry|iration|ires)?(?: date)?|valid (?:thru|through))\b([^\d\n]{0,6})(\d{1,2}\s*/\s*\d{2,4})(?!\d)"
)
_CARD_CVV = re.compile(r"(?i)\b(cvv2?|cvc2?|cvn|security code)\b([^\d\n]{0,6})(\d{3,4})(?!\d)")
_NEAR = 80  # characters between a masked card number and its expiry / CVV


def _placeholder(name: str) -> str:
    return "{" + name + "}"


def _is_itin(a: str, b: str) -> bool:
    mid = int(b)
    return a.startswith("9") and (50 <= mid <= 65 or 70 <= mid <= 88 or 90 <= mid <= 92 or 94 <= mid <= 99)


def _iban_ok(candidate: str) -> bool:
    compact = candidate.replace(" ", "")
    if not 15 <= len(compact) <= 34:
        return False
    rearranged = compact[4:] + compact[:4]
    number = "".join(str(int(c, 36)) for c in rearranged)
    return int(number) % 97 == 1


def local_mask(text: str) -> tuple[str, list[str]]:
    """Regex + checksum masking. Returns (masked text, entity type names found).

    Conservative on purpose: a card needs an issuer prefix and a valid Luhn digit, an IBAN
    needs a valid mod-97 check, an SSN needs the dashed or spaced 3-2-4 shape (or nine
    digits right after "SSN" / "social security"). Policy numbers, customer ids, phone
    numbers, ZIP codes, dates and amounts are left alone.
    """
    found: list[str] = []

    def iban_sub(m: re.Match) -> str:
        candidate = m.group(0)
        while len(candidate) >= 15:
            if _iban_ok(candidate):
                found.append(IBAN)
                return _placeholder(IBAN) + m.group(0)[len(candidate):]
            if " " not in candidate:
                break
            candidate = candidate.rsplit(" ", 1)[0]
        return m.group(0)

    text = _IBAN.sub(iban_sub, text)

    def card_sub(m: re.Match) -> str:
        digits = re.sub(r"\D", "", m.group(0))
        if 13 <= len(digits) <= 19 and _CARD_PREFIX.match(digits) and _luhn_ok(digits):
            found.append(CARD)
            return _placeholder(CARD)
        return m.group(0)

    text = _CARD.sub(card_sub, text)

    def ssn_sub(m: re.Match) -> str:
        if _is_itin(m.group(1), m.group(2)):
            found.append(ITIN)
            return _placeholder(ITIN)
        found.append(SSN)
        return _placeholder(SSN)

    text = _SSN_DASH.sub(ssn_sub, text)
    text = _SSN_SPACE.sub(ssn_sub, text)

    def ssn_word_sub(m: re.Match) -> str:
        found.append(SSN)
        return m.group(1) + m.group(2) + _placeholder(SSN)

    text = _SSN_WORD.sub(ssn_word_sub, text)

    # Expiry and CVV only when a masked card number sits right next to them.
    card_marker = _placeholder(CARD)
    card_spots = [m.start() for m in re.finditer(re.escape(card_marker), text)]
    if card_spots:
        def near_card(start: int, end: int) -> bool:
            return any(start - _NEAR <= spot <= end + _NEAR for spot in card_spots)

        for pattern, name in ((_CARD_EXPIRY, CARD_EXPIRY), (_CARD_CVV, CARD_CVV)):
            out, last = [], 0
            for m in pattern.finditer(text):
                if near_card(m.start(), m.end()):
                    out.append(text[last:m.start(3)])
                    out.append(_placeholder(name))
                    last = m.end(3)
                    found.append(name)
            out.append(text[last:])
            text = "".join(out)
            card_spots = [m.start() for m in re.finditer(re.escape(card_marker), text)]
    return text, list(dict.fromkeys(found))


def _fallback(text: str, started: float) -> MaskResult:
    try:
        masked, names = local_mask(text)
    except Exception as exc:  # noqa: BLE001 - the turn must not be lost; the type only is logged
        logger.warning("Input mask: local masker failed (%s); text left as typed", type(exc).__name__)
        masked, names = text, []
    elapsed = int((time.monotonic() - started) * 1000)
    logger.info("Input mask: outcome=fallback entities=%s elapsed_ms=%d", ",".join(names) or "-", elapsed)
    return MaskResult(masked, FALLBACK, None, elapsed)


def local_fallback(text: str, started: float, reason: str) -> MaskResult:
    """The local masker for a call that did not finish in time (or failed outside this module).

    `reason` is a short token ("timeout", "error:<ExceptionType>"), never free text. Logs one WARNING
    with the outcome, reason, time since `started` and entity type names; never the text.
    """
    try:
        masked, names = local_mask(text)
    except Exception as exc:  # noqa: BLE001 - the turn must not be lost; the type only is logged
        logger.warning("Input mask: local masker failed (%s); text left as typed", type(exc).__name__)
        masked, names = text, []
    elapsed = int((time.monotonic() - started) * 1000)
    logger.warning(
        "Input mask: outcome=fallback reason=%s elapsed_ms=%d entities=%s", _REASON_TOKEN.sub("_", str(reason))[:40] or "?", elapsed, ",".join(names) or "-"
    )
    return MaskResult(masked, FALLBACK, None, elapsed)


def mask_user_text(
    text: str,
    guardrail_id: Optional[str],
    guardrail_version: Optional[str],
    region: str = "us-east-1",
) -> MaskResult:
    """Mask `text` with Bedrock ApplyGuardrail, or with the local masker if that cannot be done. Never raises."""
    started = time.monotonic()
    if not text:
        return MaskResult(text or "", NONE, None, 0)
    try:
        if not (guardrail_id and guardrail_version):
            raise LookupError("guardrail id or version unresolved")
        return _apply_guardrail(text, guardrail_id, guardrail_version, region)
    except Exception as exc:  # noqa: BLE001 - timeout, throttle, API error, odd response, no version
        logger.warning("Input mask: apply_guardrail unavailable (%s); using the local masker", type(exc).__name__)
        return _fallback(text, started)
