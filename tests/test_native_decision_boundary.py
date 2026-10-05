"""Private ordinary prompt grammar and engine-issued decision evidence boundaries."""

import base64
import copy
import json
import time
from uuid import uuid4

import pytest

from commons_family.game.admission import DecisionAdmission, Roster
from commons_family.game.baselines import make_baseline
from commons_family.game.engine import CommonsConfig, module_for, new_game, observation, parse_decision
from commons_family.game.llm import LlmDecider, extract_json
from commons_family.game.prompt import DecisionRenderer, NativeProfile
from commons_family.shared.native import Attempt
from commons_family.shared.protocol import AttemptProgress, Register, RequestGrant, Submit


def test_ordinary_prefill_teacher_roundtrip_and_hidden_permutation():
    config = CommonsConfig(module="allelopathic", seed=6)
    module = module_for(config)
    state = new_game(config)
    obs = observation(state, config, 0, module)
    renderer = DecisionRenderer(config, module)
    profile = NativeProfile(standing_orders="  Preserve the commons.  ")
    old = LlmDecider(config, module, transport=None, model=profile.model)
    request = renderer.request(obs, profile)
    assert request.system == old.system_prompt(obs, profile.standing_orders)
    assert request.messages[0].content == old.user_message(obs)
    assert request.messages[-1].content == "{" and request.max_tokens == 300
    assert request.temperature is None and request.top_p is None and request.output_config is None
    raw = make_baseline("steward", seed=6).act(obs)
    completion = profile.teacher_completion(json.dumps(raw))
    restored = extract_json(profile.parser_input(completion))
    expected = parse_decision(raw, 0, config, state, module, "teacher")
    actual = parse_decision(restored, 0, config, state, module, "teacher")
    assert all(getattr(actual, field) == getattr(expected, field) for field in expected.__slots__)
    changed = copy.deepcopy(state)
    changed.module_state["favorites"][1:] = list(reversed(changed.module_state["favorites"][1:]))
    changed.notes[1:] = ["PRIVATE_OTHER_SEAT_SENTINEL"] * 5
    changed_obs = observation(changed, config, 0, module)
    assert changed_obs == obs
    assert renderer.request(changed_obs, profile) == request


def test_frozen_roster_rejects_wrong_duplicate_and_late_registration():
    roster = Roster(2)
    profile = NativeProfile(standing_orders="Preserve the commons.")
    first = Register(slot=0, profile=profile)
    with pytest.raises(ValueError, match="authenticated"):
        roster.register(1, first)
    roster.register(0, first)
    with pytest.raises(ValueError, match="frozen"):
        roster.register(0, first)
    roster.register(1, Register(slot=1, profile=profile))
    roster.freeze()
    with pytest.raises(ValueError, match="frozen"):
        roster.register(1, Register(slot=1, profile=profile))


def test_received_response_is_independently_parsed_before_action_admission(tmp_path, monkeypatch):
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    config = CommonsConfig(num_agents=2, seed=1)
    module = module_for(config)
    state = new_game(config)
    profile = NativeProfile(standing_orders="Preserve the commons.")
    roster = Roster(2)
    for slot in range(2):
        roster.register(slot, Register(slot=slot, profile=profile))
    roster.freeze()
    owner = DecisionAdmission(config, module, roster)
    window = owner.begin(state, time.monotonic() + 10)[0]
    grant = owner.grant(0, RequestGrant(
        window_id=window.window_id, slot=0, semantic_attempt=0, transport_attempt=0,
    ))
    text = '"harvest":1,"clean":1}'
    body = json.dumps({
        "model": profile.model,
        "content": [{"type": "text", "text": text}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 5, "output_tokens": 7},
    })
    attempt = Attempt(
        attempt_id=str(grant.grant_id), slot=0, stage="decision", request=grant.request,
        timeout_ms=grant.timeout_seconds * 1000, endpoint="http://127.0.0.1:8081",
        model=profile.model, response=text, raw_response=body,
        response_body_b64=base64.b64encode(body.encode()).decode(),
        response_complete=True, response_reader_joined=True, http_status=200,
        usage={"input_tokens": 5, "output_tokens": 7}, stop_reason="end_turn",
    )
    owner.progress(0, AttemptProgress(window_id=window.window_id, grant_id=grant.grant_id, attempt=attempt))
    with pytest.raises(ValueError, match="independently parsed"):
        owner.submit(0, Submit(
            window_id=window.window_id, slot=0, selected_attempt_id=str(grant.grant_id),
            action={"harvest": 3, "clean": 0},
        ), state)
    assert owner.windows[window.window_id].installed is None
    assert owner.windows[window.window_id].attempts[grant.grant_id] == attempt
    accepted = owner.submit(0, Submit(
        window_id=window.window_id, slot=0, selected_attempt_id=str(grant.grant_id),
        action={"harvest": 1, "clean": 1},
    ), state)
    assert accepted.harvest == 1 and accepted.clean == 1 and accepted.src == "model"
    owner.stop()
    owner.seal()
