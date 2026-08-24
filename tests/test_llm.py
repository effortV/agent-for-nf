import asyncio
from types import SimpleNamespace
from typing import Any

from app.config import Settings
from app.services.llm import DeepSeekClient


class FakeCompletions:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        message = SimpleNamespace(content="ok")
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def configured_settings() -> Settings:
    return Settings(
        siliconflow_api_key="test-key",
        llm_model="Pro/deepseek-ai/DeepSeek-V3.2",
        chat_llm_model="deepseek-ai/DeepSeek-V4-Pro",
    )


def test_background_client_keeps_v32_without_thinking_parameter() -> None:
    client = DeepSeekClient(configured_settings())
    completions = FakeCompletions()
    client._client = SimpleNamespace(chat=SimpleNamespace(completions=completions))  # type: ignore[assignment]

    asyncio.run(client.chat([{"role": "user", "content": "test"}]))

    assert completions.calls[0]["model"] == "Pro/deepseek-ai/DeepSeek-V3.2"
    assert "extra_body" not in completions.calls[0]


def test_v4_chat_can_toggle_deep_thinking() -> None:
    settings = configured_settings()
    client = DeepSeekClient(
        settings,
        model_name=settings.chat_llm_model,
        enable_thinking=True,
    )
    completions = FakeCompletions()
    client._client = SimpleNamespace(chat=SimpleNamespace(completions=completions))  # type: ignore[assignment]

    asyncio.run(client.chat([{"role": "user", "content": "first"}]))
    asyncio.run(client.chat([{"role": "user", "content": "second"}], enable_thinking=False))

    assert completions.calls[0]["model"] == "deepseek-ai/DeepSeek-V4-Pro"
    assert completions.calls[0]["extra_body"] == {"enable_thinking": True}
    assert completions.calls[1]["extra_body"] == {"enable_thinking": False}
