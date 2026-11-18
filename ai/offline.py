"""Offline 'provider': no API, no key, no Claude. The rule-based versions of each AI task
live next to the task (analyzer/hook_detector.offline_moments, ai/caption_generator.offline_package);
this class only marks the end of the provider chain."""

from ai.base import AIProvider


class OfflineProvider(AIProvider):
    name = "offline"
    is_offline = True

    def json(self, system, user, schema, max_tokens=4000):
        raise NotImplementedError("offline tasks are handled by each module's offline_* function")
