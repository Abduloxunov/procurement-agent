"""OpenRouter client.

OpenRouter is OpenAI-compatible, so the official openai SDK works with a
changed base_url. Three named roles (default / reasoning / vision) map to
env vars, so a single node can be pointed at a stronger model without a
code change -- see design doc S13.
"""

from __future__ import annotations

import base64
import json
from typing import Any, Literal, TypeVar

from openai import OpenAI
from pydantic import BaseModel

from app.config import get_settings

Role = Literal["default", "reasoning", "vision"]
T = TypeVar("T", bound=BaseModel)

_client: OpenAI | None = None


def client() -> OpenAI:
    global _client
    if _client is None:
        settings = get_settings()
        if not settings.openrouter_api_key:
            raise RuntimeError(
                "OPENROUTER_API_KEY is not set. Copy .env.example to .env "
                "and add your key."
            )
        _client = OpenAI(
            api_key=settings.openrouter_api_key,
            base_url=settings.openrouter_base_url,
        )
    return _client


def model_for(role: Role) -> str:
    settings = get_settings()
    return {
        "default": settings.model_default,
        "reasoning": settings.model_reasoning,
        "vision": settings.model_vision,
    }[role]


def complete(
    prompt: str,
    *,
    system: str | None = None,
    role: Role = "default",
    temperature: float = 0.0,
) -> str:
    """Plain text completion."""
    messages: list[dict[str, Any]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    response = client().chat.completions.create(
        model=model_for(role),
        messages=messages,
        temperature=temperature,
    )
    return response.choices[0].message.content or ""


def structured(
    prompt: str,
    schema: type[T],
    *,
    system: str | None = None,
    role: Role = "default",
    temperature: float = 0.0,
) -> T:
    """Completion constrained to a Pydantic schema.

    Uses JSON-schema response_format, which gemini-2.5-flash-lite supports.
    The parse cannot fail; whether the answer is *right* is a separate
    question that the golden set measures.
    """
    messages: list[dict[str, Any]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    response = client().chat.completions.create(
        model=model_for(role),
        messages=messages,
        temperature=temperature,
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": schema.__name__,
                "strict": True,
                "schema": schema.model_json_schema(),
            },
        },
    )
    raw = response.choices[0].message.content or "{}"
    return schema.model_validate(json.loads(raw))


def describe_image(
    image_bytes: bytes,
    prompt: str,
    *,
    mime_type: str = "image/png",
) -> str:
    """Caption an image so it becomes searchable text.

    Datasheet specifications live in tables and diagrams that are images
    inside the PDF. Without this, half the specs are invisible to retrieval.
    """
    encoded = base64.b64encode(image_bytes).decode("ascii")
    response = client().chat.completions.create(
        model=model_for("vision"),
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime_type};base64,{encoded}"},
                    },
                ],
            }
        ],
        temperature=0.0,
    )
    return response.choices[0].message.content or ""
