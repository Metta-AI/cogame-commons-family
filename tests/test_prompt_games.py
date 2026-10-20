"""Complete simultaneous games verify wording, private views and normalized orders."""

import hashlib
import json

import pytest

from coworld.examples.commons_family.game.engine import observation
from coworld.examples.commons_family.game.prompts import (
    HELDOUT_VARIANTS,
    TRAINING_VARIANTS,
)
from tools.text_bridge import TextBridge
from tools.train_bridge import VARIANTS


@pytest.mark.parametrize("variant", VARIANTS)
def test_complete_matched_prompt_games(variant):
    bridge = TextBridge(variant)
    canonical = None
    introductions = set()
    for wording, seed in [(v, 0) for v in (*TRAINING_VARIANTS, *HELDOUT_VARIANTS)] + [
        ("mixed", 4)
    ]:
        reset = {
            "kind": "reset",
            "players": 6,
            "seed": "family-17",
            "promptVariant": wording,
            "promptSeed": seed,
            "split": "test" if wording in HELDOUT_VARIANTS else "train",
        }
        if wording == "canonical":
            for heldout in HELDOUT_VARIANTS:
                with pytest.raises(ValueError, match="evaluation-only"):
                    bridge.handle({**reset, "promptVariant": heldout})
            with pytest.raises(ValueError):
                bridge.handle({**reset, "promptSeed": True})
        decision = bridge.handle(reset)
        trace = []
        while decision["kind"] == "decision":
            slot = decision["seat"]
            view = decision["semantic_view"].copy()
            metadata = view.pop("prompt_template")
            assert view == observation(bridge.state, bridge.config, slot, bridge.module)
            assert metadata["variant"] == (
                TRAINING_VARIANTS[seed] if wording == "mixed" else wording
            )
            for message in decision["messages"]:
                assert (
                    metadata[message["role"] + "_sha256"]
                    == hashlib.sha256(message["content"].encode()).hexdigest()
                )
                for other in range(6):
                    if other != slot:
                        assert f"PRIVATE-SEAT-{other}" not in message["content"]
            system = decision["messages"][0]["content"]
            common = bridge.module.rules_text(bridge.config)
            assert common in system
            introductions.add(system)
            if not trace:
                with pytest.raises(ValueError, match="no JSON object"):
                    bridge.handle(
                        {
                            "kind": "step",
                            "decision_id": decision["decision_id"],
                            "response": "unreadable",
                        }
                    )
            proposal = json.loads(bridge.handle({"kind": "teacher"})["response"])
            proposal.update(
                note=f"PRIVATE-SEAT-{slot}:" + "雪" * 220,
                message="hello " + "雪" * 160,
                sanction=slot,
            )
            reply = bridge.handle(
                {
                    "kind": "step",
                    "decision_id": decision["decision_id"],
                    "response": json.dumps(proposal),
                }
            )
            assert reply["action"]["sanction"] is None
            assert len(reply["action"]["note"]) == 200
            if bridge.config.chat_enabled:
                assert len(reply["action"]["message"]) == bridge.config.chat_max_chars
            trace.append((view, decision["messages"][1], reply["action"]))
            decision = reply["observation"]
        assert len(trace) == 120 and bridge.state.round == 20
        outcome = trace, decision
        if wording == "canonical":
            canonical = outcome
        else:
            assert outcome == canonical
    assert len(introductions) == 42
