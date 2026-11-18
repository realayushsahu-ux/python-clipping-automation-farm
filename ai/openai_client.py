"""OpenAI provider (default). Uses Structured Outputs (strict JSON Schema) so the answer
always has exactly the fields the pipeline expects. Model: OPENAI_MODEL in .env (gpt-5-mini)."""

import json
import time

from ai.base import AIProvider, AIUnavailable
from config.settings import env


def _strict(schema: dict) -> dict:
    """OpenAI strict mode: every object lists all properties as required, no extras."""
    if isinstance(schema, dict):
        s = {k: _strict(v) for k, v in schema.items()}
        if s.get("type") == "object" and "properties" in s:
            s["required"] = list(s["properties"].keys())
            s["additionalProperties"] = False
        return s
    if isinstance(schema, list):
        return [_strict(x) for x in schema]
    return schema


def _log_usage(model, usage):
    """One line per call in logs/farm.log + running totals in data/ai_usage.json (for cost tracking)."""
    if usage is None:
        return
    try:
        from core.logger import get_logger
        from core.paths import DATA_DIR
        tin, tout = int(getattr(usage, "input_tokens", 0) or 0), int(getattr(usage, "output_tokens", 0) or 0)
        get_logger("ai").info(f"openai {model}: {tin} in / {tout} out tokens")
        f = DATA_DIR / "ai_usage.json"
        tot = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
        day = time.strftime("%Y-%m-%d")
        d = tot.setdefault(day, {}).setdefault(model, {"calls": 0, "input_tokens": 0, "output_tokens": 0})
        d["calls"] += 1
        d["input_tokens"] += tin
        d["output_tokens"] += tout
        f.write_text(json.dumps(tot, indent=1), encoding="utf-8")
    except Exception:  # noqa: BLE001 - never break a job for bookkeeping
        pass


class OpenAIProvider(AIProvider):
    name = "openai"

    def available(self) -> bool:
        if not env("OPENAI_API_KEY"):
            return False
        try:
            import openai  # noqa: F401
        except ImportError:
            return False
        return True

    def json(self, system, user, schema, max_tokens=4000, effort=None):
        from openai import OpenAI
        client = OpenAI(api_key=env("OPENAI_API_KEY"), timeout=180, max_retries=3)
        model = env("OPENAI_MODEL", "gpt-5-mini") or "gpt-5-mini"
        last = None
        for attempt in range(3):
            try:
                kwargs = dict(model=model,
                              input=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                              text={"format": {"type": "json_schema", "name": "farm_output",
                                               "schema": _strict(schema), "strict": True}},
                              max_output_tokens=max_tokens)
                if model.startswith(("gpt-5", "o")):
                    kwargs["reasoning"] = {"effort": effort or "low"}
                resp = client.responses.create(**kwargs)
                _log_usage(model, getattr(resp, "usage", None))
                return json.loads(resp.output_text)
            except Exception as exc:  # noqa: BLE001
                last = exc
                time.sleep(5 * (attempt + 1))
        raise AIUnavailable(f"OpenAI failed: {last}")
