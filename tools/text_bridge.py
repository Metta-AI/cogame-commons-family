"""Production text decisions queued against frozen simultaneous-round observations."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from coworld.examples.commons_family.game.baselines import make_baseline
from coworld.examples.commons_family.game.engine import (
    CommonsConfig,
    module_for,
    new_game,
    observation,
    open_round,
    parse_decision,
    results,
    settle_round,
)
from coworld.examples.commons_family.game.llm import LlmDecider, extract_json, logger
from coworld.examples.commons_family.game.prompts import (
    HELDOUT_VARIANTS,
    PromptSelection,
)
from tools.train_bridge import OPERATOR_PROMPT, seed_of


class TextBridge:
    def __init__(self, variant: str):
        manifest = json.loads(
            (
                Path(__file__).resolve().parents[1] / "coworld_manifest_template.json"
            ).read_text()
        )
        self.config = CommonsConfig.model_validate(
            next(v["game_config"] for v in manifest["variants"] if v["id"] == variant)
        )
        self.pending = []
        self.decision_id = 0

    def decision(self):
        slot = len(self.pending)
        obs = observation(self.state, self.config, slot, self.module)
        system = self.decider.system_prompt(obs, OPERATOR_PROMPT)
        user = self.decider.user_message(obs)
        properties = {
            "sanction": {"type": ["integer", "null"]},
            "message": {"type": "string", "maxLength": self.config.chat_max_chars},
            "note": {"type": "string", "maxLength": 200},
        }
        fields = {
            "cleanup": ("harvest", "clean"),
            "harvest": ("harvest", "patch"),
            "allelopathic": ("eat", "plant", "eat_color", "plant_color"),
            "mushrooms": ("eat", "eat_color"),
        }[self.config.module]
        for field in fields:
            properties[field] = (
                {"type": "string", "enum": ["red", "green", "blue"]}
                if field.endswith("color")
                else {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": self.config.patch_count - 1
                    if field == "patch"
                    else self.config.effort_budget,
                }
            )
        return {
            "kind": "decision",
            "game": "commons-family",
            "decision_id": self.decision_id,
            "seat": slot,
            "engine_seat": slot,
            "turn": self.state.round,
            "inference_mode": "text_action",
            "semantic_view": {
                **obs,
                "prompt_template": self.selection.metadata(system, user),
            },
            "inbox": [],
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "speech_messages": [],
            "typed_question": None,
            "action_schema": {"type": "object", "properties": properties},
        }

    def handle(self, command):
        match command["kind"]:
            case "reset":
                self.selection = PromptSelection.model_validate(command)
                split = command["split"] if "split" in command else "train"
                if split not in ("train", "validation", "test"):
                    raise ValueError("split must be train, validation, or test")
                if self.selection.selected in HELDOUT_VARIANTS and split == "train":
                    raise ValueError("held-out wording is evaluation-only")
                if command["players"] != self.config.num_agents:
                    raise ValueError("player count must match the published variant")
                self.config.seed = seed_of(command["seed"])
                self.module = module_for(self.config)
                self.decider = LlmDecider(
                    self.config,
                    self.module,
                    transport=None,
                    prompt_selection=self.selection,
                )
                self.state = new_game(self.config)
                self.policies = [
                    make_baseline("steward", seed=self.config.seed * 1000 + slot)
                    for slot in range(self.config.num_agents)
                ]
                self.pending = []
                self.decision_id = 0
                open_round(self.state, self.config, self.module)
                return self.decision()
            case "teacher":
                slot = len(self.pending)
                return {
                    "response": json.dumps(
                        self.policies[slot].act(
                            observation(self.state, self.config, slot, self.module)
                        )
                    )
                }
            case "step":
                if command["decision_id"] != self.decision_id:
                    raise ValueError("stale decision")
                raw = extract_json(command["response"])
                if raw is None:
                    raise ValueError("reply contains no JSON object")
                slot = len(self.pending)
                parsed = parse_decision(
                    raw, slot, self.config, self.state, self.module, "llm"
                )
                action = {
                    field: getattr(parsed, field)
                    for field in parsed.__slots__
                    if field != "src"
                }
                self.pending.append(parsed)
                self.decision_id += 1
                if len(self.pending) == self.config.num_agents:
                    settle_round(self.state, self.pending, self.config, self.module)
                    self.pending = []
                    if self.state.round < self.config.rounds:
                        open_round(self.state, self.config, self.module)
                if self.state.round == self.config.rounds:
                    outcome = results(
                        self.state,
                        self.config,
                        self.module,
                        "complete",
                        ["teacher"] * self.config.num_agents,
                        0,
                    )
                    next_observation = {
                        "kind": "terminal",
                        "scores": {
                            str(i): score for i, score in enumerate(outcome["scores"])
                        },
                    }
                else:
                    next_observation = self.decision()
                return {
                    "kind": "accepted",
                    "action": action,
                    "observation": next_observation,
                }
            case _:
                raise ValueError("unknown bridge command")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--variant", default="cleanup")
    logger.disabled = True
    bridge = TextBridge(parser.parse_args().variant)
    for line in sys.stdin:
        print(json.dumps(bridge.handle(json.loads(line))), flush=True)


if __name__ == "__main__":
    main()
