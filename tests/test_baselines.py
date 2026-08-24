"""Bounded orders: every scripted baseline is legal in every module, always.

The fuzz covers the degenerate states a long episode actually reaches — a dead
orchard, every patch stripped, a barren field, a frozen seat, an anonymous
ledger — because those are where a baseline that "works" starts emitting
out-of-range demands. A baseline that produces an illegal or unbounded order
fails CI; it is also the fallback every prompt seat drops to, so an illegal
order here would be an illegal order for an LLM seat too.
"""

from __future__ import annotations

import random

import pytest

from coworld.examples.commons_family.game.baselines import BASELINES, make_baseline
from coworld.examples.commons_family.game.engine import (
    NOTE_MAX_CHARS,
    CommonsConfig,
    module_for,
    new_game,
    observation,
    parse_decision,
    settle_round,
)
from coworld.examples.commons_family.game.modules.base import COLORS

CASES_PER_MODULE = 400
MODULES = ["cleanup", "harvest", "allelopathic", "mushrooms"]


def _warm(config, state, module, rounds: int, rng: random.Random) -> None:
    """Play a few rounds so the ledger, the chat and the counters are populated."""
    for _ in range(rounds):
        raws = []
        for slot in range(config.num_agents):
            raws.append(
                {
                    "harvest": rng.randint(0, config.effort_budget),
                    "clean": rng.randint(0, 1),
                    "patch": rng.randrange(config.patch_count),
                    "eat": rng.randint(0, config.effort_budget),
                    "eat_color": rng.choice(COLORS),
                    "plant": rng.randint(0, 1),
                    "plant_color": rng.choice(COLORS),
                    "sanction": rng.choice([None, (slot + 1) % config.num_agents]),
                    "message": "warm-up",
                }
            )
        decisions = [
            parse_decision(raw, slot, config, state, module, "scripted:fuzz")
            for slot, raw in enumerate(raws)
        ]
        settle_round(state, decisions, config, module)


def _degrade(module_name: str, module_state: dict, config, rng: random.Random) -> None:
    """Push the module state somewhere a long, badly played episode would."""
    if module_name == "cleanup":
        module_state["apples"] = rng.choice(
            [0.0, 0.4, 5.0, 9.99, 30.0, 61.5, config.stock_capacity]
        )
        module_state["pollution"] = rng.choice([0.0, 0.2, 0.35, 0.5, 0.99, 1.0])
        module_state["dead"] = rng.random() < 0.3
    elif module_name == "harvest":
        all_dead = rng.random() < 0.25
        for patch in range(config.patch_count):
            dead = all_dead or rng.random() < 0.3
            module_state["dead"][patch] = dead
            module_state["stocks"][patch] = 0.0 if dead else rng.choice(
                [0.0, 0.5, 0.99, 1.0, 3.0, 12.0, config.patch_capacity]
            )
    elif module_name == "allelopathic":
        if rng.random() < 0.2:
            module_state["planted"] = {"red": 0, "green": 0, "blue": 0}
            module_state["ripe"] = {color: 0.0 for color in COLORS}
        else:
            left = config.field_size
            planted = {}
            for index, color in enumerate(COLORS):
                take = left if index == len(COLORS) - 1 else rng.randint(0, left)
                planted[color] = take
                left -= take
            module_state["planted"] = planted
            module_state["ripe"] = {
                color: rng.uniform(0.0, planted[color]) for color in COLORS
            }
    else:
        module_state["counts"] = {
            color: float(rng.choice([0, 0, 1, 4, 9, config.mushroom_color_cap]))
            for color in COLORS
        }
        for slot in range(config.num_agents):
            module_state["frozen_until"][slot] = rng.choice([0, 0, 5, 40])


def fuzz_observations(module_name: str, count: int) -> list[dict]:
    rng = random.Random(f"{module_name}-fuzz")
    out: list[dict] = []
    while len(out) < count:
        rights = rng.choice(["open", "closed", "partnership"])
        config = CommonsConfig(
            module=module_name,
            num_agents=6,
            rounds=30,
            seed=rng.randrange(1_000_000),
            property_rights=rights,
            ledger_public=rng.random() < 0.7,
            sanctions_enabled=rng.random() < 0.6,
            chat_enabled=rng.random() < 0.8,
            norm_text=rng.choice(["", "Posted quota: one unit each."]),
        )
        module = module_for(config)
        state = new_game(config)
        _warm(config, state, module, rng.randint(0, 3), rng)
        _degrade(module_name, state.module_state, config, rng)
        for slot in range(config.num_agents):
            if len(out) >= count:
                break
            out.append(observation(state, config, slot, module))
    return out


OBSERVATIONS = {name: fuzz_observations(name, CASES_PER_MODULE) for name in MODULES}


def assert_legal(raw: dict, obs: dict) -> None:
    assert isinstance(raw, dict), raw
    budget = obs["effort_budget"]
    module = obs["module"]
    state = obs["module_state"]

    def integer(field: str, low: int, high: int) -> int:
        value = raw.get(field, 0)
        assert isinstance(value, int) and not isinstance(value, bool), (field, value)
        assert low <= value <= high, (field, value, low, high)
        return value

    if module == "cleanup":
        assert integer("harvest", 0, budget) + integer("clean", 0, budget) <= budget
    elif module == "harvest":
        patch = integer("patch", 0, len(state["patches"]) - 1)
        harvest = integer("harvest", 0, budget)
        lookup = {entry["id"]: entry for entry in state["patches"]}
        assert patch in lookup
        if lookup[patch]["dead"]:
            assert harvest == 0, "a baseline demanded from a dead patch"
    elif module == "allelopathic":
        assert integer("eat", 0, budget) + integer("plant", 0, budget) <= budget
        assert raw.get("eat_color", "red") in COLORS
        assert raw.get("plant_color", "red") in COLORS
    else:
        integer("eat", 0, budget)
        assert raw.get("eat_color", "red") in COLORS

    sanction = raw.get("sanction")
    if sanction is None:
        pass
    else:
        assert obs["sanctions_enabled"], "a baseline sanctioned with the dial off"
        assert isinstance(sanction, int) and not isinstance(sanction, bool)
        assert 0 <= sanction < obs["num_players"]
        assert sanction != obs["slot"], "a baseline sanctioned itself"

    message = raw.get("message")
    if message is not None:
        assert isinstance(message, str)
        assert len(message) <= obs["chat_max_chars"]
        assert obs["chat_enabled"], "a baseline spoke with chat off"
    note = raw.get("note")
    if note is not None:
        assert isinstance(note, str) and len(note) <= NOTE_MAX_CHARS


@pytest.mark.parametrize("baseline_name", sorted(BASELINES))
@pytest.mark.parametrize("module_name", MODULES)
def test_every_baseline_emits_a_legal_bounded_order(baseline_name, module_name):
    baseline = make_baseline(baseline_name, seed=17)
    for obs in OBSERVATIONS[module_name]:
        raw = baseline.act(obs)
        assert_legal(raw, obs)


@pytest.mark.parametrize("baseline_name", sorted(BASELINES))
@pytest.mark.parametrize("module_name", MODULES)
def test_every_baseline_survives_the_validator_unchanged_in_bounds(baseline_name, module_name):
    """What the baseline emitted is what the engine will play.

    The validator clamps, so an out-of-range order would be silently corrected
    rather than rejected; this asserts there is nothing to correct.
    """
    config = CommonsConfig(module=module_name, num_agents=6)
    module = module_for(config)
    state = new_game(config)
    baseline = make_baseline(baseline_name, seed=5)
    for obs in OBSERVATIONS[module_name][:120]:
        raw = baseline.act(obs)
        decision = parse_decision(raw, obs["slot"], config, state, module, "scripted:test")
        for field in ("harvest", "clean", "eat", "plant", "patch"):
            if field in raw:
                assert getattr(decision, field) == raw[field], (field, raw)


def test_the_registry_names_are_exactly_the_documented_seven():
    assert sorted(BASELINES) == [
        "cleaner",
        "deterrable",
        "free_rider",
        "punisher",
        "random",
        "reciprocator",
        "steward",
    ]


def test_an_unknown_scripted_name_degrades_to_the_default_rather_than_raising():
    baseline = make_baseline("no-such-policy", seed=1)
    assert baseline.name == "steward"


@pytest.mark.parametrize("module_name", MODULES)
def test_the_steward_never_kills_the_resource_on_its_own(module_name):
    """Six stewards leave the commons standing at the end of a full episode."""
    from coworld.examples.commons_family import headless  # noqa: PLC0415

    config = CommonsConfig(module=module_name, num_agents=6, rounds=20)
    state = headless.run_episode(config, headless.build_policies(["steward"] * 6))
    module = module_for(config)
    assert module.residual_value(state.module_state) > 0.0
    assert state.collapse_round is None
    assert not any(state.module_state.get("dead") or [])
