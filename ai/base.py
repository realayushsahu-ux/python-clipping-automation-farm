"""One interface for every AI provider, so OpenAI / Gemini / offline are interchangeable.

Pipeline code never imports a provider directly; it calls get_provider() and uses .json().
Provider order: settings.ai_provider → settings.ai_fallback_provider → offline.
A provider without its API key is skipped automatically.
"""

from core.logger import get_logger

log = get_logger("ai")


class AIUnavailable(Exception):
    """Provider has no key / package, or failed after its own retries."""


class AIProvider:
    name = "base"
    is_offline = False

    def available(self) -> bool:
        return True

    def json(self, system: str, user: str, schema: dict, max_tokens: int = 4000, effort: str | None = None) -> dict:
        """Return a dict that matches `schema` (a JSON Schema object). effort: low/medium/high
        reasoning for models that support it (ignored otherwise)."""
        raise NotImplementedError


def _make(name: str) -> AIProvider | None:
    name = (name or "").strip().lower()
    if name == "openai":
        from ai.openai_client import OpenAIProvider
        return OpenAIProvider()
    if name == "gemini":
        from ai.gemini_client import GeminiProvider
        return GeminiProvider()
    if name in ("offline", "none", "local"):
        from ai.offline import OfflineProvider
        return OfflineProvider()
    return None


def providers(settings) -> list[AIProvider]:
    """Usable providers in preference order; always ends with the offline one."""
    out, seen = [], set()
    for name in (settings.get("ai_provider"), settings.get("ai_fallback_provider"), "offline"):
        p = _make(name)
        if p and p.name not in seen and p.available():
            out.append(p)
            seen.add(p.name)
    return out


def ask_json(settings, system, user, schema, max_tokens=4000, offline_fn=None, voice=True, effort=None):
    """Try each provider in order. The offline provider can't read text, so callers pass
    `offline_fn` (their rule-based version of the same task) for that last step.
    voice=True adds config/voice.md to the system prompt."""
    if voice:
        from ai.voice import with_voice
        system = with_voice(system)
    for p in providers(settings):
        if p.is_offline:
            return offline_fn() if offline_fn else None
        try:
            return p.json(system, user, schema, max_tokens, effort)
        except Exception as exc:  # noqa: BLE001 - next provider
            log.warning(f"{p.name} failed ({str(exc)[:160]}); trying the next provider")
    return offline_fn() if offline_fn else None
