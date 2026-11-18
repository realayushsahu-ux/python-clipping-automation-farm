"""Gemini provider (alternative / free tier). JSON mode with a response schema.
Model: GEMINI_MODEL in .env."""

import json
import re
import time

from ai.base import AIProvider, AIUnavailable
from config.settings import env


class GeminiProvider(AIProvider):
    name = "gemini"

    def available(self) -> bool:
        if not env("GEMINI_API_KEY"):
            return False
        try:
            from google import genai  # noqa: F401
        except ImportError:
            return False
        return True

    def json(self, system, user, schema, max_tokens=4000, effort=None):
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=env("GEMINI_API_KEY"))
        model = env("GEMINI_MODEL", "gemini-flash-latest") or "gemini-flash-latest"
        last = None
        for attempt in range(3):
            try:
                try:
                    cfg = types.GenerateContentConfig(system_instruction=system, response_mime_type="application/json",
                                                      response_json_schema=schema, max_output_tokens=max_tokens)
                except TypeError:  # older SDK without response_json_schema
                    cfg = types.GenerateContentConfig(system_instruction=system, response_mime_type="application/json",
                                                      max_output_tokens=max_tokens)
                resp = client.models.generate_content(model=model, contents=user, config=cfg)
                text = resp.text or ""
                m = re.search(r"\{.*\}", text, re.S)
                return json.loads(m.group(0) if m else text)
            except Exception as exc:  # noqa: BLE001
                last = exc
                time.sleep(8 * (attempt + 1))
        raise AIUnavailable(f"Gemini failed: {last}")
