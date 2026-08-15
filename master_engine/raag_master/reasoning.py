"""The reasoning backend.

Deliberately thin. Everything that makes RAAG's output good — blast radius,
scoped retrieval, metric injection — happens before this module is called.
What happens here is one API request and the bookkeeping around it.

Kept behind a protocol for the same reason the embedder is: so the pipeline
can be tested end to end without network access or cost, and so swapping
models is configuration rather than a code change.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

__all__ = [
    "AnthropicReasoner",
    "DryRunReasoner",
    "Reasoner",
    "ReasoningResult",
    "default_model",
]

DEFAULT_MODEL = "claude-sonnet-5"


def default_model() -> str:
    """Model to use when the caller has no preference.

    Read from the environment so a model change does not require editing code.
    Model identifiers change over time; anything hard-coded goes stale.
    """
    return os.environ.get("RAAG_MODEL", DEFAULT_MODEL)


@dataclass(frozen=True, slots=True)
class ReasoningResult:
    """What came back from a reasoning request.

    Token counts are recorded because they are the honest cost signal. A
    pipeline that quietly sends 80k tokens per request is expensive in a way
    that no amount of good output justifies going unmeasured.
    """

    text: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    duration_ms: int = 0
    error: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.error is None


@runtime_checkable
class Reasoner(Protocol):
    """Anything that turns an assembled prompt into an answer."""

    @property
    def model(self) -> str: ...

    def reason(self, system_prompt: str, user_prompt: str) -> ReasoningResult: ...


class DryRunReasoner:
    """Returns a description of the request instead of calling anything.

    Not a mock in the testing sense — it is a genuinely useful mode. Prompt
    assembly is the part of this pipeline most likely to be wrong, and being
    able to inspect exactly what would be sent, without paying for it or
    waiting on it, is how that gets checked. The CLI exposes it as --dry-run.
    """

    def __init__(self, model: str = "dry-run") -> None:
        self._model = model

    @property
    def model(self) -> str:
        return self._model

    def reason(self, system_prompt: str, user_prompt: str) -> ReasoningResult:
        summary = (
            "[dry run - no API call was made]\n\n"
            f"System prompt: {len(system_prompt)} characters\n"
            f"User prompt:   {len(user_prompt)} characters\n"
            f"Combined:      {len(system_prompt) + len(user_prompt)} characters\n"
            f"Rough token estimate: ~{(len(system_prompt) + len(user_prompt)) // 4}\n"
        )
        return ReasoningResult(text=summary, model=self._model)


class AnthropicReasoner:
    """Calls the Anthropic API."""

    def __init__(
        self,
        model: str | None = None,
        *,
        api_key: str | None = None,
        max_tokens: int = 4_000,
    ) -> None:
        try:
            from anthropic import Anthropic
        except ImportError as error:  # pragma: no cover - depends on install
            raise RuntimeError(
                "The anthropic package is not installed. "
                "Install it with `pip install anthropic`, or use --dry-run."
            ) from error

        key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set. Copy .env.example to .env and "
                "add your key, or use --dry-run to assemble the prompt without "
                "calling the API."
            )

        self._client = Anthropic(api_key=key)
        self._model = model or default_model()
        self._max_tokens = max_tokens

    @property
    def model(self) -> str:
        return self._model

    def reason(self, system_prompt: str, user_prompt: str) -> ReasoningResult:
        """Send one request.

        Failures are returned rather than raised. A failed reasoning call is
        still an event worth logging with its prompt intact — the audit trail
        should record that a question was asked and went unanswered, not
        silently lose the attempt.
        """
        started = time.monotonic()

        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=self._max_tokens,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            )

            text = "\n".join(
                block.text for block in response.content if block.type == "text"
            )

            return ReasoningResult(
                text=text,
                model=self._model,
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
                duration_ms=int((time.monotonic() - started) * 1000),
            )

        except Exception as error:
            # Broad except is deliberate: the failure is surfaced through
            # ReasoningResult.error rather than raised, so the audit record
            # is still written even when the call fails.
            return ReasoningResult(
                text="",
                model=self._model,
                duration_ms=int((time.monotonic() - started) * 1000),
                error=f"{type(error).__name__}: {error}",
            )
