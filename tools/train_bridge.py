"""Persistent JSONL bridge for Commons Family numeric training."""

from __future__ import annotations

import json
import sys
from itertools import product
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, Field, TypeAdapter

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from coworld.examples.commons_family.game.baselines import make_baseline  # noqa: E402
from coworld.examples.commons_family.game.engine import (  # noqa: E402
    CommonsConfig,
    module_for,
    new_game,
    observation,
    open_round,
    parse_decision,
    results,
    settle_round,
)
from coworld.examples.commons_family.game.llm import LlmDecider, logger as llm_logger  # noqa: E402
from coworld.examples.commons_family.game.modules.base import COLORS  # noqa: E402

OPERATOR_PROMPT = "Keep the shared resource alive while maximizing your own score."
VARIANTS = (
    "cleanup",
    "harvest-open",
    "harvest-closed",
    "harvest-partnership",
    "allelopathic",
    "mushrooms",
)


class Reset(BaseModel):
    kind: Literal["reset"]
    seed: str
    players: int


class Encode(BaseModel):
    kind: Literal["encode"]


class Teacher(BaseModel):
    kind: Literal["teacher"]


class Step(BaseModel):
    kind: Literal["step"]
    decision_id: int
    response: str


REQUEST = TypeAdapter(
    Annotated[Reset | Encode | Teacher | Step, Field(discriminator="kind")]
)


def seed_of(value: str) -> int:
    hashed = 2166136261
    for char in value:
        hashed = ((hashed ^ ord(char)) * 16777619) & 0xFFFFFFFF
    return hashed & 0x7FFFFFFF


def action_catalog(config: CommonsConfig) -> list[dict]:
    """Every meaningful bounded module decision, in a stable order."""
    budget = config.effort_budget
    if config.module == "cleanup":
        return [
            {"harvest": harvest, "clean": clean}
            for harvest in range(budget + 1)
            for clean in range(budget - harvest + 1)
        ]
    if config.module == "harvest":
        return [
            {"patch": patch, "harvest": harvest}
            for patch, harvest in product(range(config.patch_count), range(budget + 1))
        ]
    if config.module == "allelopathic":
        return [
            {
                "eat": eat,
                "eat_color": eat_color,
                "plant": plant,
                "plant_color": plant_color,
            }
            for eat in range(budget + 1)
            for plant in range(budget - eat + 1)
            for eat_color, plant_color in product(COLORS, repeat=2)
        ]
    assert config.module == "mushrooms"
    return [
        {"eat": eat, "eat_color": color}
        for eat, color in product(range(budget + 1), COLORS)
    ]


def main() -> None:
    if len(sys.argv) not in (2, 3):
        raise SystemExit("usage: train_bridge.py MANIFEST [VARIANT]")
    variant = sys.argv[2] if len(sys.argv) == 3 else "cleanup"
    manifest = json.loads(Path(sys.argv[1]).read_text())
    variants = {entry["id"]: entry["game_config"] for entry in manifest["variants"]}
    assert variant in VARIANTS and variant in variants

    llm_logger.disabled = True  # JSONL protocol owns stdout; no network log shipping.
    config = CommonsConfig.model_validate(variants[variant])
    module = module_for(config)
    catalog = action_catalog(config)
    catalog_index = {
        json.dumps(action, sort_keys=True): index
        for index, action in enumerate(catalog)
    }
    compact_index = {
        module.compact(module.parse_decision(action, 0, config, {})): index
        for index, action in enumerate(catalog)
    }
    policies = []
    pending = []
    seat = 0
    decision_id = 0

    def current_observation() -> dict:
        return observation(state, config, seat, module)

    def decision() -> dict:
        obs = current_observation()
        return {
            "kind": "decision",
            "game": "commons-family",
            "decision_id": decision_id,
            "seat": seat,
            "engine_seat": seat,
            "turn": state.round,
            "semantic_view": obs,
            "inbox": [],
            "messages": [
                {
                    "role": "system",
                    "content": decider.system_prompt(obs, OPERATOR_PROMPT),
                },
                {"role": "user", "content": decider.user_message(obs)},
            ],
            "speech_messages": [],
            "action_schema": {
                "type": "object",
                "required": ["module_action", "sanction"],
                "properties": {
                    "module_action": {"type": "integer"},
                    "sanction": {"type": "integer"},
                },
            },
            "typed_question": None,
        }

    def encoding() -> dict:
        obs = current_observation()
        public = obs["module_state"]
        values = [int(variant == candidate) for candidate in VARIANTS]
        values.extend(int(seat == other) for other in range(config.num_agents))
        values.extend(
            [
                obs["round"],
                obs["rounds"],
                obs["effort_budget"],
                obs["score"],
                int(obs["your_last_gain"] is not None),
                obs["your_last_gain"] or 0,
                obs["sanctions_received_last_round"],
                int(obs["last_round_total_extracted"] is not None),
                obs["last_round_total_extracted"] or 0,
                int(obs["sanctions_enabled"]),
                obs["sanction_cost"],
                obs["sanction_burn"],
            ]
        )
        for row in obs["ledger"]:
            values.extend(
                [
                    row["total_extracted"],
                    row["public_effort"],
                    row["sanctions_given"],
                    row["sanctions_received"],
                ]
            )
            recent = [compact_index[entry] + 1 for entry in row["recent"]]
            values.extend([0] * (5 - len(recent)) + recent)

        if config.module == "cleanup":
            values.extend(
                [
                    public["apples"],
                    public["capacity"],
                    public["pollution"],
                    public["effective_regrowth"],
                    public["collapse_threshold"],
                    public["silt_rate"],
                    public["clean_power"],
                    int(public["dead"]),
                    public["cleaned_last_round"],
                ]
            )
        elif config.module == "harvest":
            values.extend([public["patch_capacity"], public["patch_regrowth"]])
            values.extend(
                int(public["property_rights"] == rights)
                for rights in ("open", "closed", "partnership")
            )
            for patch in public["patches"]:
                values.extend([patch["stock"], int(patch["dead"])])
                values.extend(
                    int(state.aliases[other] in patch["holders"])
                    for other in range(config.num_agents)
                )
                values.append(int(patch["id"] in public["your_patches"]))
        elif config.module == "allelopathic":
            values.extend(
                [public["field_size"], public["ripen_base"], public["favorite_bonus"]]
            )
            for color in COLORS:
                values.extend(
                    [
                        public["planted"][color],
                        public["ripe"][color],
                        int(public["your_favorite"] == color),
                    ]
                )
            values.append(int(public["barren"]))
        else:
            assert config.module == "mushrooms"
            values.extend(
                [
                    public["capacity"],
                    public["color_cap"],
                    public["spawn_per_round"],
                    public["frozen_until"],
                    int(public["you_may_eat"]),
                    config.red_value,
                    config.green_value,
                    config.blue_value,
                ]
            )
            for color in COLORS:
                values.extend([public["counts"][color], public["eaten_total"][color]])

        sanctions = [
            -1,
            *[other for other in range(config.num_agents) if other != seat],
        ]
        if not config.sanctions_enabled:
            sanctions = [-1, *([None] * (config.num_agents - 1))]
        return {
            "decision_id": decision_id,
            "values": values,
            "action_heads": [
                {"name": "module_action", "choices": list(range(len(catalog)))},
                {"name": "sanction", "choices": sanctions},
            ],
        }

    for line in sys.stdin:
        request = REQUEST.validate_json(line)
        if isinstance(request, Reset):
            assert request.players == config.num_agents
            config.seed = seed_of(request.seed)
            module = module_for(config)
            decider = LlmDecider(config, module, transport=None)
            state = new_game(config)
            policies = [
                make_baseline("steward", seed=config.seed * 1000 + slot)
                for slot in range(config.num_agents)
            ]
            pending = []
            seat = 0
            decision_id = 0
            open_round(state, config, module)
            response = decision()
        elif isinstance(request, Encode):
            assert state.round < config.rounds
            response = encoding()
        elif isinstance(request, Teacher):
            assert state.round < config.rounds
            raw = policies[seat].act(current_observation())
            parsed = parse_decision(
                raw, seat, config, state, module, "scripted:steward"
            )
            module_raw = {key: getattr(parsed, key) for key in catalog[0]}
            index = catalog_index[json.dumps(module_raw, sort_keys=True)]
            response = {
                "response": json.dumps(
                    {"module_action": index, "sanction": parsed.sanction}
                )
            }
            if parsed.sanction is None:
                response = {
                    "response": json.dumps({"module_action": index, "sanction": -1})
                }
        else:
            assert isinstance(request, Step)
            assert state.round < config.rounds and request.decision_id == decision_id
            action = json.loads(request.response)
            assert action["module_action"] in range(len(catalog))
            assert action["sanction"] in encoding()["action_heads"][1]["choices"]
            raw = dict(catalog[action["module_action"]])
            if action["sanction"] >= 0:
                raw["sanction"] = action["sanction"]
            pending.append(parse_decision(raw, seat, config, state, module, "numeric"))
            seat += 1
            if seat == config.num_agents:
                settle_round(state, pending, config, module)
                pending = []
                seat = 0
                if state.round < config.rounds:
                    open_round(state, config, module)
            decision_id += 1
            if state.round == config.rounds:
                outcome = results(
                    state,
                    config,
                    module,
                    "complete",
                    ["numeric"] * config.num_agents,
                    0,
                )
                obs = {
                    "kind": "terminal",
                    "scores": {
                        str(slot): score for slot, score in enumerate(outcome["scores"])
                    },
                }
            else:
                obs = decision()
            response = {"kind": "accepted", "action": action, "observation": obs}
        print(json.dumps(response, separators=(",", ":")), flush=True)


if __name__ == "__main__":
    main()
