"""Limits on prompts, agent loops, audio, sessions and uploads, set through CDK context values.

Each limit has a conservative default and can be changed per deployment, for example:

    cdk deploy insadv-03-agentcore --exclusively -c max_agent_turns=30

The stacks pass the values to the runtimes and Lambdas as environment variables. The runtimes
(agent/limits.py, voice-agent/limits.py) and lambda/documents read the same names and clamp them to
a range, so a bad value cannot switch a limit off. An invalid context value stops the synth.
The defaults here match the defaults in those files.
"""
from typing import Any

# context key -> (environment variable, default, minimum, maximum)
TEXT_AGENT_LIMITS = {
    "max_prompt_chars": ("MAX_PROMPT_CHARS", 16_000, 500, 200_000),
    "max_agent_turns": ("MAX_AGENT_TURNS", 50, 1, 200),
    "max_total_tokens": ("MAX_TOTAL_TOKENS", 2_000_000, 10_000, 10_000_000),
    "max_output_tokens": ("MAX_OUTPUT_TOKENS", 12_000, 256, 64_000),
}
VOICE_AGENT_LIMITS = {
    "max_audio_chunk_bytes": ("MAX_AUDIO_CHUNK_BYTES", 96_000, 1_000, 1_000_000),
    "max_session_audio_bytes": ("MAX_SESSION_AUDIO_BYTES", 64_000_000, 1_000_000, 2_000_000_000),
    "max_session_seconds": ("MAX_SESSION_SECONDS", 1800, 60, 14_400),
    "max_text_input_chars": ("MAX_TEXT_INPUT_CHARS", 4000, 100, 50_000),
}
UPLOAD_LIMITS = {
    "max_upload_bytes": ("MAX_UPLOAD_BYTES", 10 * 1024 * 1024, 1024, 50 * 1024 * 1024),
}


def int_context(node: Any, key: str, default: int, minimum: int, maximum: int) -> int:
    """The whole-number context value `key`, or `default` when it is not set. Out of range or not a number: ValueError."""
    raw = node.try_get_context(key)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        value = int(str(raw).strip())
    except ValueError:
        raise ValueError(f"Invalid context {key}={raw!r}: use a whole number (for example: -c {key}={default})") from None
    if not minimum <= value <= maximum:
        raise ValueError(f"Invalid context {key}={value}: use a value from {minimum} to {maximum}")
    return value


def limit_environment(node: Any, limits: dict[str, tuple[str, int, int, int]]) -> dict[str, str]:
    """Environment variables (name -> string value) for a set of limits, read from the context with defaults."""
    return {
        env_name: str(int_context(node, key, default, minimum, maximum))
        for key, (env_name, default, minimum, maximum) in limits.items()
    }
