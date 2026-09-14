from __future__ import annotations

from types import SimpleNamespace

from fitness_coach.coach.openai_client import CoachOpenAIClient


def _call(name: str, arguments: str, call_id: str) -> SimpleNamespace:
    return SimpleNamespace(type="function_call", name=name, arguments=arguments, call_id=call_id)


def _response(rid: str, output: list[SimpleNamespace], text: str = "") -> SimpleNamespace:
    return SimpleNamespace(id=rid, output=output, output_text=text)


class _FakeResponses:
    def __init__(self, script: list[SimpleNamespace]) -> None:
        self.script = list(script)
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs: object) -> SimpleNamespace:
        self.calls.append(kwargs)
        return self.script.pop(0)


def _client(script: list[SimpleNamespace]) -> tuple[CoachOpenAIClient, _FakeResponses]:
    client = CoachOpenAIClient(api_key=None, model="m")
    fake = _FakeResponses(script)
    client.client = SimpleNamespace(responses=fake)  # type: ignore[assignment]
    return client, fake


def test_tool_loop_executes_calls_and_chains_previous_response() -> None:
    client, fake = _client(
        [
            _response("r1", [_call("get_exercise_history", '{"exercise_name": "bench"}', "c1")]),
            _response("r2", [SimpleNamespace(type="message")], text="Final answer"),
        ]
    )
    executed: list[tuple[str, str]] = []

    def executor(name: str, arguments: str) -> str:
        executed.append((name, arguments))
        return '{"ok": true}'

    tools = [{"type": "function", "name": "get_exercise_history"}]
    result = client.respond(
        system_prompt="sys", user_message="why is bench stuck", tools=tools, tool_executor=executor
    )

    assert result.text == "Final answer"
    assert executed == [("get_exercise_history", '{"exercise_name": "bench"}')]
    assert result.metadata["tool_calls"] == [
        {"name": "get_exercise_history", "arguments": '{"exercise_name": "bench"}'}
    ]
    assert result.metadata["tool_rounds"] == 1
    first, second = fake.calls
    assert first["tools"] == tools and first["input"] == "why is bench stuck"
    assert second["previous_response_id"] == "r1"
    assert second["input"] == [
        {"type": "function_call_output", "call_id": "c1", "output": '{"ok": true}'}
    ]
    assert second["instructions"] == "sys"


def test_round_cap_forces_text_answer() -> None:
    client, fake = _client(
        [
            _response("r1", [_call("t", "{}", "c1")]),
            _response("r2", [_call("t", "{}", "c2")]),
            _response("r3", [], text="Best effort"),
        ]
    )
    result = client.respond(
        system_prompt="s",
        user_message="u",
        tools=[{"type": "function", "name": "t"}],
        tool_executor=lambda name, args: "{}",
        max_tool_rounds=1,
    )
    assert result.text == "Best effort"
    assert result.metadata["tool_rounds"] == 1
    assert fake.calls[-1]["tool_choice"] == "none"
    assert "budget exhausted" in fake.calls[-1]["input"][0]["output"]


def test_no_tools_is_single_shot() -> None:
    client, fake = _client([_response("r1", [], text="hi")])
    result = client.respond(system_prompt="s", user_message="u")
    assert result.text == "hi"
    assert len(fake.calls) == 1
    assert "tools" not in fake.calls[0]


def test_offline_client_reports_offline() -> None:
    result = CoachOpenAIClient(api_key=None, model="m").respond(system_prompt="s", user_message="u")
    assert result.metadata == {"offline": True}
