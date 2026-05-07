from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import anthropic

from .prompts import SYSTEM_PROMPT, build_user_message

DEFAULT_MODEL = "claude-sonnet-4-6"
DEFAULT_MAX_TOKENS = 16000


@dataclass
class RowVerdict:
    question: str
    verdict: str
    reasoning: str


@dataclass
class BatchResult:
    verdicts: list[RowVerdict]
    patch: dict[str, Any]
    raw_response: dict[str, Any]
    usage: dict[str, Any]


class JudgeError(RuntimeError):
    pass


class Judge:
    def __init__(self, api_key: str, model: str = DEFAULT_MODEL):
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model

    def judge_batch(
        self,
        serialized_space: dict[str, Any],
        rows: list[dict[str, Any]],
        *,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> BatchResult:
        # Two cached system blocks: the (frozen) instruction prompt, and the
        # serialized_space context. Stable content first; the per-row payload goes
        # in the user message after the last cache breakpoint.
        system_blocks = [
            {
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            },
            {
                "type": "text",
                "text": (
                    "Current Genie space configuration (serialized_space JSON). "
                    "Use this when proposing patches; reference real table and "
                    "column identifiers from here.\n\n"
                    + json.dumps(serialized_space, indent=2, default=str)
                ),
                "cache_control": {"type": "ephemeral"},
            },
        ]

        response = self.client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system_blocks,
            messages=[{"role": "user", "content": build_user_message(rows)}],
        )

        text = "".join(b.text for b in response.content if b.type == "text").strip()
        if not text:
            raise JudgeError("Empty response from Claude")

        # Tolerate ```json fences if the model wraps the output despite instructions.
        if text.startswith("```"):
            text = text.strip("`")
            if text.startswith("json"):
                text = text[len("json") :]
            text = text.strip()

        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as e:
            raise JudgeError(f"Could not parse JSON from Claude: {e}\n---\n{text[:1000]}") from e

        verdicts_raw = parsed.get("verdicts", [])
        verdicts = [
            RowVerdict(
                question=v.get("question", ""),
                verdict=v.get("verdict", ""),
                reasoning=v.get("reasoning", ""),
            )
            for v in verdicts_raw
        ]
        patch = parsed.get("patch", {}) or {}

        usage = {
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
            "cache_creation_input_tokens": getattr(
                response.usage, "cache_creation_input_tokens", 0
            ),
            "cache_read_input_tokens": getattr(response.usage, "cache_read_input_tokens", 0),
        }

        return BatchResult(
            verdicts=verdicts,
            patch=patch,
            raw_response=parsed,
            usage=usage,
        )
