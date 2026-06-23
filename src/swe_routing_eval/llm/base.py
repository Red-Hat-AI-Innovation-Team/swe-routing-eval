"""LLMClient abstract base class."""

from __future__ import annotations

from abc import ABC, abstractmethod

from swe_routing_eval.llm.types import LLMResponse, Message, ToolDef


class LLMClient(ABC):
    """Provider-agnostic interface for chat-with-tools.

    Each implementation:
    1. Translates ``list[ToolDef]`` to provider tool format
    2. Translates ``list[Message]`` to provider message format
    3. Calls the provider API
    4. Normalizes provider response -> ``LLMResponse``
    """

    @abstractmethod
    def chat(
        self,
        model_id: str,
        messages: list[Message],
        tools: list[ToolDef],
        max_tokens: int,
    ) -> LLMResponse: ...
