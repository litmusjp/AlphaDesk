from __future__ import annotations

from types import SimpleNamespace
from typing import Literal

import pytest
from pydantic import BaseModel

from packages.ai.provider import AnthropicProvider, StructuredOutputError


class Probe(BaseModel):
    status: str


class StrictProbe(BaseModel):
    status: Literal["ok"]


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
        SimpleNamespace(content=[SimpleNamespace(type="tool_use", input={"status": "ok"})])
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


@pytest.mark.asyncio
async def test_anthropic_provider_reports_safe_schema_diagnostics() -> None:
    client = FakeClient(
        SimpleNamespace(
            stop_reason="tool_use",
            content=[SimpleNamespace(type="tool_use", input={"status": "invalid"})],
        )
    )
    provider = AnthropicProvider("test-key", model="claude-test", client=client)

    with pytest.raises(StructuredOutputError) as error_info:
        await provider.generate(
            agent_name="probe",
            instructions="Return status ok.",
            input_payload="{}",
            response_model=StrictProbe,
        )

    assert error_info.value.diagnostics == [{"loc": "status", "type": "literal_error"}]
    assert error_info.value.stop_reason == "tool_use"
