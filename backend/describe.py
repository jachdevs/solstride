"""Short natural-language route summary via OpenRouter (Claude Fable 5.1).

Kept off the /route critical path: the frontend calls /describe after the map renders.
"""

from __future__ import annotations

import json
import logging
import os

import httpx

log = logging.getLogger("nova.describe")
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


def _load_env() -> None:
    path = os.path.join(os.path.dirname(__file__), "..", ".env")
    if not os.path.exists(path):
        return
    for line in open(path):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


_load_env()

SYSTEM = (
    "You write route blurbs for a sun/shade-aware running and walking loop app. "
    "Given route stats, streets and current conditions, write 2 short sentences (max 45 words) "
    "a runner would find useful: name 1-3 notable streets, say how shady or sunny it is and why "
    "that suits the conditions, and mention hills only if relevant. No greetings, no emoji, no "
    "markdown, no invented landmarks. Use only the facts given."
)


def describe_route(payload: dict) -> str | None:
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        return None
    model = os.environ.get("OPENROUTER_MODEL", "anthropic/claude-fable-5.1")
    try:
        r = httpx.post(
            OPENROUTER_URL,
            headers={"Authorization": f"Bearer {key}", "X-Title": "Nova"},
            json={
                "model": model,
                # Fable always reasons on this endpoint; keep it light and leave room for the answer.
                "max_tokens": 600,
                "reasoning": {"effort": "low"},
                "messages": [
                    {"role": "system", "content": SYSTEM},
                    {"role": "user", "content": json.dumps(payload)},
                ],
            },
            timeout=12,
        )
        r.raise_for_status()
        choice = r.json()["choices"][0]
        text = (choice["message"].get("content") or "").strip()
        if choice.get("finish_reason") == "length" or not text:
            return None
        return text
    except Exception as e:
        log.warning("describe failed: %s", e)
        return None
