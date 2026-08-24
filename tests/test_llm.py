"""The LLM decision path, with a stubbed transport. No network, ever.

Covers the seven behaviours the design pins: clean JSON, JSON with trailing
prose, prose before the brace, missing fields defaulted, an out-of-range field
clamped, a timeout triggering exactly ONE retry and then the `steward` fallback
with the right cause, the batch issuing all six requests concurrently, and the
no-credentials path making zero network calls.
"""

from __future__ import annotations

import threading
import time

import pytest

from coworld.examples.commons_family.game.baselines import make_baseline
from coworld.examples.commons_family.game.engine import (
    CommonsConfig,
    module_for,
    new_game,
    observation,
    parse_decision,
)
from coworld.examples.commons_family.game.llm import (
    LlmDecider,
    LlmThrottled,
    LlmTimeout,
    LlmTransportError,
    build_transport,
    extract_json,
)


class StubTransport:
    """Answers with a canned script; counts every call it is asked to make."""

    def __init__(self, replies, barrier: threading.Barrier | None = None) -> None:
        self.replies = list(replies)
        self.calls = 0
        self.bodies: list[dict] = []
        self.barrier = barrier
        self.lock = threading.Lock()

    def complete(self, body: dict, timeout: float) -> str:
        if self.barrier is not None:
            # Times out (BrokenBarrierError) unless every seat is here at once.
            self.barrier.wait(timeout=5.0)
        with self.lock:
            self.calls += 1
            self.bodies.append(body)
            reply = self.replies[min(len(self.replies) - 1, self.calls - 1)]
        if isinstance(reply, Exception):
            raise reply
        return reply


class ExplodingTransport:
    """Any use at all is a failure: the no-credentials path must not call out."""

    def complete(self, body: dict, timeout: float) -> str:  # pragma: no cover
        raise AssertionError("the disabled client made a network call")


def setup(module: str = "cleanup", **overrides):
    overrides.setdefault("rounds", 5)
    config = CommonsConfig(module=module, num_agents=6, **overrides)
    state = new_game(config)
    return config, state, module_for(config)


def decide_one(transport, module: str = "cleanup", **overrides):
    config, state, module_impl = setup(module, **overrides)
    decider = LlmDecider(config, module_impl, transport=transport, model="claude-haiku-4-5")
    obs = observation(state, config, 0, module_impl)
    answers = decider.decide({0: (obs, "take one apple a round")}, time.monotonic() + 10)
    return answers[0], decider, config, state, module_impl, obs


# ---------------------------------------------------------------------------
# extraction
# ---------------------------------------------------------------------------


def test_clean_json_is_taken_as_is():
    assert extract_json('{"harvest": 2, "clean": 1}') == {"harvest": 2, "clean": 1}


def test_trailing_prose_is_tolerated():
    assert extract_json('{"harvest": 1} — one apple, as agreed.') == {"harvest": 1}


def test_prose_before_the_brace_is_tolerated():
    assert extract_json('Let me think. I will take one.\n{"harvest": 1}') == {"harvest": 1}


def test_the_first_balanced_span_wins_and_nesting_is_respected():
    assert extract_json('{"a": {"b": 1}, "harvest": 2}{"harvest": 9}') == \
        {"a": {"b": 1}, "harvest": 2}


def test_a_brace_inside_a_string_does_not_close_the_object():
    assert extract_json('{"message": "use { and }", "harvest": 1}') == \
        {"message": "use { and }", "harvest": 1}


@pytest.mark.parametrize("raw", ["", "no json here", "{ not json", "[1,2,3]", "}{"])
def test_unusable_replies_extract_to_nothing(raw):
    assert extract_json(raw) is None


# ---------------------------------------------------------------------------
# the seat
# ---------------------------------------------------------------------------


def test_a_clean_reply_is_used_and_the_prefill_brace_is_restored():
    transport = StubTransport(['"harvest": 2, "clean": 1}'])
    (reply, cause), decider, *_ = decide_one(transport)
    assert cause == ""
    assert reply == {"harvest": 2, "clean": 1}
    assert transport.calls == 1
    assert decider.requests == 1
    # haiku-4.5 gets the assistant prefill, and its smaller max_tokens.
    assert transport.bodies[0]["messages"][-1] == {"role": "assistant", "content": "{"}
    assert transport.bodies[0]["max_tokens"] == 300


def test_missing_fields_default_and_out_of_range_fields_clamp():
    transport = StubTransport(['"harvest": 99, "sanction": 0, "message": "hi"}'])
    (reply, cause), _, config, state, module, obs = decide_one(transport, sanctions_enabled=True)
    assert cause == ""
    decision = parse_decision(reply, 0, config, state, module, "llm")
    assert decision.harvest == config.effort_budget      # 99 clamped to 3
    assert decision.clean == 0                           # missing -> 0
    assert decision.sanction is None                     # slot 0 cannot burn itself
    assert decision.message == "hi"


def test_the_user_message_is_the_observation_with_type_and_round_seconds_dropped():
    transport = StubTransport(['"harvest": 1}'])
    _, _, _, _, _, obs = decide_one(transport)
    body = transport.bodies[0]
    user = body["messages"][0]["content"]
    assert '"type"' not in user
    assert '"round_seconds"' not in user
    assert obs["alias"] in user
    assert obs["alias"] in body["system"]
    assert "take one apple a round" in body["system"]     # standing orders, verbatim


def test_an_unusable_reply_is_retried_exactly_once_then_falls_back_to_steward():
    transport = StubTransport(["not json at all", "still not json"])
    (reply, cause), decider, config, state, module, obs = decide_one(transport)
    assert reply is None
    assert cause == "parse"
    assert transport.calls == 2, "exactly one retry, no more"
    fallback = make_baseline(config.fallback_scripted).act(obs)
    assert "harvest" in fallback


def test_the_retry_carries_the_hint():
    transport = StubTransport(["prose", '"harvest": 1}'])
    (reply, cause), *_ = decide_one(transport)
    assert cause == ""
    assert reply == {"harvest": 1}
    assert "Reply with ONE JSON object" in transport.bodies[1]["messages"][0]["content"]


def test_a_timeout_is_retried_once_and_reported_as_timeout():
    transport = StubTransport([LlmTimeout("slow"), LlmTimeout("slow")])
    (reply, cause), *_ = decide_one(transport)
    assert (reply, cause) == (None, "timeout")
    assert transport.calls == 2


def test_a_transport_error_is_reported_as_transport():
    transport = StubTransport([LlmTransportError("dns"), LlmTransportError("dns")])
    (reply, cause), *_ = decide_one(transport)
    assert (reply, cause) == (None, "transport")


def test_throttling_walks_the_ladder_inside_the_round_and_then_gives_up():
    transport = StubTransport([LlmThrottled("429")])
    (reply, cause), decider, *_ = decide_one(transport)
    assert (reply, cause) == (None, "timeout")
    # Four calls per attempt (the ladder is three sleeps), two attempts.
    assert transport.calls == 8
    assert decider.requests == 8


def test_the_round_deadline_stops_the_ladder():
    transport = StubTransport([LlmThrottled("429")])
    config, state, module = setup()
    decider = LlmDecider(config, module, transport=transport, model="claude-haiku-4-5")
    obs = observation(state, config, 0, module)
    started = time.monotonic()
    answers = decider.decide({0: (obs, "")}, started + 0.2)
    assert answers[0] == (None, "timeout")
    assert time.monotonic() - started < 5.0


def test_the_rate_budget_falls_the_seat_back_rather_than_waiting():
    transport = StubTransport(['"harvest": 1}'])
    config, state, module = setup(llm_max_requests_per_minute=1)
    decider = LlmDecider(config, module, transport=transport, model="claude-haiku-4-5")
    obs = observation(state, config, 0, module)
    deadline = time.monotonic() + 10
    assert decider.decide({0: (obs, "")}, deadline)[0][1] == ""
    assert decider.decide({0: (obs, "")}, deadline)[0] == (None, "rate_budget")
    assert transport.calls == 1


# ---------------------------------------------------------------------------
# the batch
# ---------------------------------------------------------------------------


def test_all_six_seats_go_out_as_one_parallel_batch():
    """A barrier in the stub: it only clears if all six calls are in flight."""
    barrier = threading.Barrier(6)
    transport = StubTransport(['"harvest": 1}'], barrier=barrier)
    config, state, module = setup()
    decider = LlmDecider(config, module, transport=transport, model="claude-haiku-4-5")
    requests = {
        slot: (observation(state, config, slot, module), "")
        for slot in range(config.num_agents)
    }
    answers = decider.decide(requests, time.monotonic() + 10)
    assert sorted(answers) == list(range(6))
    assert all(cause == "" for _, cause in answers.values())
    assert transport.calls == 6
    assert decider.requests == 6


def test_each_seat_gets_its_own_system_prompt_built_once():
    transport = StubTransport(['"harvest": 1}'])
    config, state, module = setup()
    decider = LlmDecider(config, module, transport=transport, model="claude-haiku-4-5")
    requests = {
        slot: (observation(state, config, slot, module), f"orders for {slot}")
        for slot in range(2)
    }
    decider.decide(requests, time.monotonic() + 10)
    decider.decide(requests, time.monotonic() + 10)
    systems = {body["system"] for body in transport.bodies}
    assert len(systems) == 2                      # one per seat, reused
    assert any("orders for 0" in system for system in systems)
    assert any("orders for 1" in system for system in systems)


# ---------------------------------------------------------------------------
# no credentials
# ---------------------------------------------------------------------------


def test_without_credentials_the_client_is_disabled_and_makes_zero_calls(monkeypatch):
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY_URI",
                 "AWS_ENDPOINT_URL_BEDROCK_RUNTIME"):
        monkeypatch.delenv(name, raising=False)
    transport, model = build_transport()
    assert transport is None
    assert model == ""

    config, state, module = setup()
    decider = LlmDecider(config, module)
    assert decider.enabled is False
    obs = observation(state, config, 0, module)
    answers = decider.decide({0: (obs, "")}, time.monotonic() + 10)
    assert answers == {0: (None, "disabled")}
    assert decider.requests == 0


def test_a_disabled_decider_never_touches_its_transport():
    config, state, module = setup()
    decider = LlmDecider(config, module, transport=None, model="")
    assert decider.enabled is False
    decider.transport = None
    obs = observation(state, config, 0, module)
    assert decider.decide({0: (obs, "")}, time.monotonic() + 10) == {0: (None, "disabled")}


def test_an_episode_with_no_credentials_still_finishes_complete():
    from coworld.examples.commons_family import headless  # noqa: PLC0415
    from coworld.examples.commons_family.game.engine import results  # noqa: PLC0415

    config, _, module = setup(rounds=4)
    decider = LlmDecider(config, module, transport=None, model="")
    state = headless.run_episode(
        config,
        headless.build_policies(["steward"] * 6),
        parallel_seats=True,
        decider=decider,
        prompts={0: "take one", 1: "take two"},
    )
    payload = results(state, config, module, "complete", ["p"] * 6, decider.requests)
    assert payload["reason"] == "complete"
    assert payload["llm_requests"] == 0
    # Both prompt seats played the scripted fallback, all episode.
    assert payload["fallbacks"][0] == 4
    assert payload["fallbacks"][1] == 4


def test_the_credential_ladder_prefers_the_bedrock_sidecar(monkeypatch):
    monkeypatch.setenv("AWS_ENDPOINT_URL_BEDROCK_RUNTIME", "http://sidecar:8000")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    transport, model = build_transport()
    assert type(transport).__name__ == "BedrockTransport"
    assert "haiku" in model


def test_the_credential_ladder_falls_through_to_the_direct_api(monkeypatch):
    monkeypatch.delenv("AWS_ENDPOINT_URL_BEDROCK_RUNTIME", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    transport, model = build_transport()
    assert type(transport).__name__ == "AnthropicTransport"
    assert "haiku" in model


def test_the_credential_ladder_reads_the_key_uri_last(monkeypatch, tmp_path):
    key_file = tmp_path / "key"
    key_file.write_text("sk-from-uri\n", encoding="utf-8")
    monkeypatch.delenv("AWS_ENDPOINT_URL_BEDROCK_RUNTIME", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY_URI", key_file.as_uri())
    transport, _ = build_transport()
    assert type(transport).__name__ == "AnthropicTransport"
    assert transport.api_key == "sk-from-uri"
