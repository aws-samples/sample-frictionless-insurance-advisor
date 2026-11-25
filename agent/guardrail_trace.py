"""Say why Bedrock Guardrails stopped a turn, without logging what was said.

The text assistant creates its model with `guardrail_trace="enabled"`, so the
Bedrock ConverseStream ends each model call with a `metadata` chunk that carries
`trace.guardrail`: the assessment (which side, which policy, which topic or
filter). Strands 1.54.0 forwards that chunk unchanged to the caller of
`agent.stream_async()` as `{"event": {"metadata": {...}}}`, and it logs it only
at DEBUG. The `messageStop` chunk before it carries the stop reason.

`GuardrailWatcher` reads those two chunks. When the stop reason is
`guardrail_intervened` it writes ONE INFO line that names the side, the policy
type, the topic name or filter type, the action, and nothing else. It also
tells the caller whether the answer was really blocked, because Bedrock reports
the same stop reason when it only masks PII (action ANONYMIZED) and the reply
is still delivered.

Privacy: the assessment can hold the model's original reply (`modelOutput`),
and PII and word matches hold the matched text. None of that is read here. Only
the allow-listed fields below are copied, and each value is cut to a short,
plain token first, so a surprise value cannot carry text into the log.
"""
from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

STOP_REASON = "guardrail_intervened"

# Values copied into the log are names and enum words, never free text.
_TOKEN = re.compile(r"[^A-Za-z0-9_.:+-]")
_MAX_TOKEN = 48
_MAX_FINDINGS = 12

_ACTIVE_ACTIONS = {"BLOCKED", "ANONYMIZED"}


def _token(value: Any) -> str:
    """A short, plain token for the log, or '?' when the value is not a string/number."""
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return "?"
    return _TOKEN.sub("_", str(value))[:_MAX_TOKEN] or "?"


def _entries(policy: Any, key: str) -> list[dict]:
    items = policy.get(key) if isinstance(policy, dict) else None
    return [i for i in items if isinstance(i, dict)] if isinstance(items, list) else []


def _is_active(entry: dict) -> bool:
    """True for something the guardrail found or acted on (not a 'looked, found nothing' row)."""
    return entry.get("detected") is True or entry.get("action") in _ACTIVE_ACTIONS


def _assessment_findings(side: str, assessment: Any) -> list[dict]:
    """Allow-listed facts from one GuardrailAssessment."""
    if not isinstance(assessment, dict):
        return []
    found: list[dict] = []
    for e in _entries(assessment.get("topicPolicy"), "topics"):
        if _is_active(e):
            found.append({"side": side, "policy": "topicPolicy", "topic": _token(e.get("name")),
                          "action": _token(e.get("action"))})
    for e in _entries(assessment.get("contentPolicy"), "filters"):
        if _is_active(e) and e.get("confidence") != "NONE":
            found.append({"side": side, "policy": "contentPolicy", "filter": _token(e.get("type")),
                          "confidence": _token(e.get("confidence")), "action": _token(e.get("action"))})
    sensitive = assessment.get("sensitiveInformationPolicy")
    for e in _entries(sensitive, "piiEntities"):
        if _is_active(e):
            found.append({"side": side, "policy": "sensitiveInformationPolicy", "pii": _token(e.get("type")),
                          "action": _token(e.get("action"))})
    for e in _entries(sensitive, "regexes"):
        if _is_active(e):
            found.append({"side": side, "policy": "sensitiveInformationPolicy", "regex": _token(e.get("name")),
                          "action": _token(e.get("action"))})
    words = assessment.get("wordPolicy")
    for e in _entries(words, "managedWordLists"):
        if _is_active(e):
            found.append({"side": side, "policy": "wordPolicy", "wordlist": _token(e.get("type")),
                          "action": _token(e.get("action"))})
    for e in _entries(words, "customWords"):
        if _is_active(e):  # the matched word itself is never copied
            found.append({"side": side, "policy": "wordPolicy", "wordlist": "custom",
                          "action": _token(e.get("action"))})
    for e in _entries(assessment.get("contextualGroundingPolicy"), "filters"):
        if _is_active(e):
            found.append({"side": side, "policy": "contextualGroundingPolicy", "filter": _token(e.get("type")),
                          "score": _token(e.get("score")), "threshold": _token(e.get("threshold")),
                          "action": _token(e.get("action"))})
    return found


def summarize_guardrail_trace(guardrail_trace: Any) -> list[dict]:
    """Findings (side, policy type, topic/filter/entity, action) from a `trace.guardrail` object."""
    if not isinstance(guardrail_trace, dict):
        return []
    findings: list[dict] = []
    input_assessment = guardrail_trace.get("inputAssessment")
    if isinstance(input_assessment, dict):
        for assessment in input_assessment.values():
            findings.extend(_assessment_findings("input", assessment))
    output_assessments = guardrail_trace.get("outputAssessments")
    if isinstance(output_assessments, dict):
        for group in output_assessments.values():
            for assessment in group if isinstance(group, list) else [group]:
                findings.extend(_assessment_findings("output", assessment))
    return findings


def format_findings(findings: list[dict]) -> str:
    """One log line body: `output topicPolicy topic=LegalAdvice action=BLOCKED; ...`."""
    parts = []
    for f in findings[:_MAX_FINDINGS]:
        detail = " ".join(f"{k}={v}" for k, v in f.items() if k not in ("side", "policy"))
        parts.append(f"{f['side']} {f['policy']} {detail}".strip())
    if len(findings) > _MAX_FINDINGS:
        parts.append(f"(+{len(findings) - _MAX_FINDINGS} more)")
    return "; ".join(parts)


class GuardrailWatcher:
    """Feed it each Strands `event` chunk of one turn; it logs the assessment of an intervened model call.

    `observe(chunk)` and `finish()` return True when the call's answer was
    replaced by the guardrail's canned text, so the caller can tell the browser.
    """

    def __init__(self, log: logging.Logger | None = None) -> None:
        self._log = log or logger
        self._reset()

    def _reset(self) -> None:
        self._stop: str | None = None
        self._meta_seen = False
        self._trace: Any = None

    def observe(self, chunk: Any) -> bool:
        if not isinstance(chunk, dict):
            return False
        if "messageStart" in chunk:
            return self.finish()  # a new model call: settle the previous one first
        stop = chunk.get("messageStop")
        if isinstance(stop, dict):
            self._stop = str(stop.get("stopReason") or "")
        meta = chunk.get("metadata")
        if isinstance(meta, dict):
            self._meta_seen = True
            trace = meta.get("trace")
            self._trace = trace.get("guardrail") if isinstance(trace, dict) else None
        if self._stop is not None and self._meta_seen:
            return self._settle()
        return False

    def finish(self) -> bool:
        """The stream ended (or a new call began) with a stop reason but no metadata chunk."""
        if self._stop is not None:
            return self._settle()
        self._reset()
        return False

    def _settle(self) -> bool:
        intervened = self._stop == STOP_REASON
        trace = self._trace
        self._reset()
        if not intervened:
            return False
        if not isinstance(trace, dict):
            self._log.info("Guardrail intervened (stop_reason=%s); no assessment in the stream", STOP_REASON)
            return True  # cannot tell masking from blocking: Strands also treats this as blocked
        findings = summarize_guardrail_trace(trace)
        blocked = any(f.get("action") == "BLOCKED" for f in findings)
        self._log.info(
            "Guardrail intervened (stop_reason=%s) blocked=%s: %s",
            STOP_REASON, blocked, format_findings(findings) or "no policy details in the assessment",
        )
        return blocked or not findings
