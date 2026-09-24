"""A Jev decision is one legal baseline action for the current module."""

from __future__ import annotations

import json

import pytest

from coworld.examples.commons_family.game.engine import (
    CommonsConfig,
    module_for,
    new_game,
    observation,
    parse_decision,
)
from coworld.examples.commons_family.player import player


@pytest.mark.parametrize(
    "module_name", ["cleanup", "harvest", "allelopathic", "mushrooms"]
)
def test_jev_ranks_current_module_actions(module_name, monkeypatch):
    config = CommonsConfig(module=module_name, num_agents=6, seed=7)
    game = new_game(config)
    module = module_for(config)
    obs = observation(game, config, 0, module)
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    monkeypatch.delenv("AWS_ENDPOINT_URL_BEDROCK_RUNTIME", raising=False)

    class Response:
        def __init__(self, payload):
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self):
            return json.dumps(self.payload).encode()

    def urlopen(request, timeout):
        assert timeout == 8
        sent = json.loads(request.data)
        assert sent["state"] == json.dumps(obs, sort_keys=True)
        criteria = sent["questions"]["decision"]["criteria"]
        assert "steward" in criteria
        probabilities = {name: float(name == "steward") for name in criteria}
        return Response(
            {
                "answers": {
                    "decision": {
                        "type": "choice",
                        "choice": next(iter(criteria)),
                        "probabilities": probabilities,
                        "confidence": 0.5,
                    }
                }
            }
        )

    monkeypatch.setattr(player, "urlopen", urlopen)
    action = player.jev_action(obs)
    assert action == player.make_baseline("steward", seed=obs["round"] * 1000).act(obs)
    decision = parse_decision(action, 0, config, game, module, "player")
    assert decision.src == "player"


def test_jev_registration_uses_steward_for_missed_deadlines(monkeypatch):
    monkeypatch.setenv("PLAYER_JEV", "1")
    monkeypatch.delenv("PLAYER_SCRIPTED", raising=False)
    assert player.registration()["scripted"] == "steward"
