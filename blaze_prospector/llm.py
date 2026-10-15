"""Thin wrapper around the Anthropic SDK that tracks token usage. Mock mode never touches the network."""

import json

from . import config


class LLMError(Exception):
    """A model call that returned nothing usable (refusal, truncation, API error)."""


class LLM:
    def __init__(self, mock: bool = False, model: str = config.MODEL):
        self.mock = mock
        self.model = model
        self.input_tokens = 0
        self.output_tokens = 0
        self.calls = 0
        self._client = None
        if not mock:
            import anthropic

            self._anthropic = anthropic
            self._client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env / .env

    def usage(self) -> dict:
        return {
            "model": self.model,
            "calls": self.calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cost_usd": round(config.cost_usd(self.model, self.input_tokens, self.output_tokens), 4),
            "simulated": self.mock,
        }

    def record_mock(self, prompt: str, response: str) -> None:
        """Mock mode: estimate tokens (~4 chars/token) so the cost card still has something to show."""
        self.calls += 1
        self.input_tokens += len(prompt) // 4
        self.output_tokens += len(response) // 4

    def complete_json(self, system: str, prompt: str, schema: dict) -> str:
        """Ask for JSON matching `schema`. Returns raw text; callers validate it themselves (fail closed)."""
        a = self._anthropic
        try:
            # Sonnet 5.5 can decline in a few safety categories; "default" fallbacks reroute those server-side.
            resp = self._client.beta.messages.create(
                model=self.model,
                max_tokens=8000,
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema}},
                system=system,
                messages=[{"role": "user", "content": prompt}],
            )
        except a.RateLimitError as e:
            raise LLMError(f"rate limited: {e.message}") from e
        except a.APIStatusError as e:
            raise LLMError(f"API error {e.status_code}: {e.message}") from e
        except a.APIConnectionError as e:
            raise LLMError("network error talking to the Anthropic API") from e

        self.calls += 1
        self.input_tokens += resp.usage.input_tokens or 0
        self.output_tokens += resp.usage.output_tokens or 0

        if resp.stop_reason == "refusal":
            raise LLMError("model declined the request")
        if resp.stop_reason == "max_tokens":
            raise LLMError("response truncated (max_tokens)")
        text = next((b.text for b in resp.content if b.type == "text"), "")
        if not text:
            raise LLMError("empty response")
        return text


def parse_json(text: str) -> dict:
    """Parse model output as a JSON object, tolerating a stray code fence."""
    t = text.strip()
    if t.startswith("```"):
        t = t.strip("`").removeprefix("json").strip()
    data = json.loads(t)
    if not isinstance(data, dict):
        raise ValueError("expected a JSON object")
    return data
