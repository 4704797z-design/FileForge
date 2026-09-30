import logging
import os
import time

import requests

from app.services import mixen

log = logging.getLogger(__name__)

POLLINATIONS_URL = "https://text.pollinations.ai/openai"
POLLINATIONS_MODEL = "openai-fast"


def chat(messages: list[dict], response_format: dict | None = None) -> str:
    """Text model for the AI Video director.

    Uses the configured Mixen text model when MIXEN_TEXT_MODEL is set,
    otherwise falls back to the free Pollinations endpoint.
    """
    if os.getenv("MIXEN_TEXT_MODEL", "").strip():
        return mixen.chat(messages, response_format)
    payload = {"model": POLLINATIONS_MODEL, "messages": messages, "temperature": 0.7}
    if response_format:
        payload["response_format"] = response_format
    last_error: Exception | None = None
    for attempt in range(4):
        try:
            r = requests.post(POLLINATIONS_URL, json=payload, timeout=120)
            if r.status_code < 400:
                content = r.json()["choices"][0]["message"]["content"]
                if isinstance(content, str) and content.strip():
                    return content.strip()
                raise RuntimeError("Text AI response is empty")
            if r.status_code < 500:
                raise RuntimeError(f"Text AI error: HTTP {r.status_code}")
            last_error = RuntimeError(f"Text AI error: HTTP {r.status_code}")
        except requests.RequestException as e:
            last_error = e
        if attempt < 3:
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"Text AI request failed: {last_error}")
