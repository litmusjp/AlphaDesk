from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from packages.ai.provider import AnthropicProvider


class Probe(BaseModel):
    status: str


class FakeMessages:
    def __init__(self, response: object) -> None:
        self.response = response
        self.calls: list[dict[str, object]] = []

    async def create(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        return self.response


class FakeClient:
    def __init__(self, response: object) -> None:
        self.messages = FakeMessages(response)


@pytest.mark.asyncio
async def test_anthropic_provider_parses_schema_constrained_tool_output() -> None:
    client = FakeClient(
        SimpleNamespace(
            content=[SimpleNamespace(type="tool_use", input={"status": "ok"})]
        )
    )
    provider = AnthropicProvider("test-key", model="claude-test", client=client)

    result = await provider.generate(
        agent_name="AlphaDeskCapabilityProbe",
        instructions="Return status ok.",
        input_payload='{"source":"test"}',
        response_model=Probe,
    )

    assert result == Probe(status="ok")
    request = client.messages.calls[0]
    assert request["model"] == "claude-test"
    assert request["tool_choice"] == {
        "type": "tool",
        "name": "AlphaDeskCapabilityProbe",
    }
    assert request["tools"][0]["input_schema"] == Probe.model_json_schema()


@pytest.mark.asyncio
async def test_anthropic_provider_rejects_missing_tool_output() -> None:
    client = FakeClient(SimpleNamespace(content=[]))
    provider = AnthropicProvider("test-key", model="claude-test", client=client)

    with pytest.raises(ValueError, match="no structured output"):
        await provider.generate(
            agent_name="probe",
            instructions="Return status ok.",
            input_payload="{}",
            response_model=Probe,
        )
