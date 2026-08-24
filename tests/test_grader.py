"""The grader: welfare against the module's planner optimum.

`planner_optimum` is a normalisation constant, so the property that matters is
that it BOUNDS what play can achieve. A random population is the cheapest
adversary for that: 500 sampled episodes per module must all score below it.
"""

from __future__ import annotations

import pytest

from coworld.examples.commons_family import headless
from coworld.examples.commons_family.game.engine import (
    CommonsConfig,
    module_for,
    new_game,
    parse_decision,
    results,
    settle_round,
    welfare,
)
from coworld.examples.commons_family.game.modules.base import Decision
from coworld.examples.commons_family.grader.commons_grader import (
    SCALE,
    build_grade,
    harvest_gini,
    public_effort_share,
    synchrony_same_action_rate,
)

MODULES = ["cleanup", "harvest", "allelopathic", "mushrooms"]
RANDOM_SAMPLES = 500
ROUNDS = 20


def episode(module_name: str, policies: list[str], seed: int = 20260824, rounds: int = ROUNDS):
    config = CommonsConfig(module=module_name, num_agents=6, rounds=rounds, seed=seed)
    state = headless.run_episode(config, headless.build_policies(policies, seed=seed % 1000))
    module = module_for(config)
    payload = results(state, config, module, "complete", [f"p{i}" for i in range(6)], 0)
    return config, state, module, payload


def bundle(module_name: str, policies: list[str], seed: int = 20260824):
    from coworld.examples.commons_family.game.engine import replay_payload  # noqa: PLC0415

    config, state, module, payload = episode(module_name, policies, seed)
    replay = replay_payload(
        state, config, module, [f"p{i}" for i in range(6)], payload,
        ["scripted"] * 6, list(policies),
    )
    return payload, replay


@pytest.fixture(scope="module")
def optima() -> dict[str, float]:
    out = {}
    for name in MODULES:
        config = CommonsConfig(module=name, num_agents=6, rounds=ROUNDS)
        out[name] = module_for(config).planner_optimum(config)
    return out


# ---------------------------------------------------------------------------
# welfare accounting
# ---------------------------------------------------------------------------


def test_welfare_is_the_scores_plus_the_residual():
    config = CommonsConfig(module="cleanup", num_agents=6, rounds=6)
    module = module_for(config)
    state = headless.run_episode(config, headless.build_policies(["steward"] * 6))
    assert welfare(state, config, module) == pytest.approx(
        sum(state.scores) + module.residual_value(state.module_state)
    )


def test_sanctions_are_welfare_negative():
    config = CommonsConfig(module="cleanup", num_agents=6, rounds=1, sanctions_enabled=True)
    module = module_for(config)

    quiet = new_game(config)
    settle_round(quiet, [Decision(harvest=1) for _ in range(6)], config, module)

    burning = new_game(config)
    decisions = [
        parse_decision({"harvest": 1, "sanction": (slot + 1) % 6}, slot, config, burning,
                       module, "test")
        for slot in range(6)
    ]
    settle_round(burning, decisions, config, module)

    assert welfare(burning, config, module) < welfare(quiet, config, module)
    lost = 6 * (config.sanction_cost + config.sanction_burn)
    assert welfare(quiet, config, module) - welfare(burning, config, module) == \
        pytest.approx(lost)


@pytest.mark.parametrize("module_name", MODULES)
def test_residual_value_is_what_the_commons_still_holds(module_name):
    config = CommonsConfig(module=module_name, num_agents=6)
    module = module_for(config)
    state = new_game(config)
    residual = module.residual_value(state.module_state)
    expected = {
        "cleanup": config.stock_start,
        "harvest": config.patch_start * config.patch_count,
        "allelopathic": sum(config.ripe_start),
        "mushrooms": float(sum(config.mushroom_start)),
    }[module_name]
    assert residual == pytest.approx(expected)


# ---------------------------------------------------------------------------
# the grade
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("module_name", MODULES)
def test_a_steward_population_scores_above_zero_and_beats_a_free_rider_population(module_name):
    steward = build_grade(*bundle(module_name, ["steward"] * 6))
    free = build_grade(*bundle(module_name, ["free_rider"] * 6))
    assert steward.score > 0.0
    assert free.score < steward.score
    assert steward.module == module_name
    assert steward.scale == SCALE[module_name]
    assert steward.optimum_welfare > 0.0


@pytest.mark.parametrize("module_name", MODULES)
def test_the_optimum_is_finite_and_bounds_a_500_sample_random_population(
    module_name, optima
):
    optimum = optima[module_name]
    assert optimum == optimum and optimum not in (float("inf"), float("-inf"))
    best = max(
        episode(module_name, ["random"] * 6, seed=20260824 + sample)[3]["welfare"]
        for sample in range(RANDOM_SAMPLES)
    )
    assert best <= optimum, f"{module_name}: random welfare {best} beat the optimum {optimum}"


def test_the_grade_reports_survival_and_the_collapse_round():
    config = CommonsConfig(module="cleanup", num_agents=6, rounds=ROUNDS)
    module = module_for(config)
    state = headless.run_episode(config, headless.build_policies(["free_rider"] * 6))
    assert state.collapse_round is not None
    payload, replay = bundle("cleanup", ["free_rider"] * 6)
    grade = build_grade(payload, replay)
    assert grade.survived is False
    assert grade.collapse_round is not None
    assert module.residual_value(state.module_state) >= 0.0


def test_the_grade_reports_dead_patches():
    payload, replay = bundle("harvest", ["free_rider"] * 6)
    grade = build_grade(payload, replay)
    assert grade.dead_patches
    assert grade.survived is False


def test_the_public_effort_share_is_the_number_the_family_is_about():
    payload, replay = bundle("cleanup", ["cleaner"] * 6)
    grade = build_grade(payload, replay)
    assert grade.public_effort_share is not None
    # Six cleaners spend exactly one of their three units on the river.
    assert grade.public_effort_share == pytest.approx(1 / 3, abs=0.02)

    greedy = build_grade(*bundle("cleanup", ["free_rider"] * 6))
    assert greedy.public_effort_share == pytest.approx(0.0)


def test_public_effort_share_handles_a_zero_round_episode():
    config = CommonsConfig(module="cleanup", num_agents=6)
    assert public_effort_share({"public_effort": []}, config, 0) is None


def test_synchrony_is_one_for_a_uniform_population_and_none_for_a_single_seat():
    assert synchrony_same_action_rate([[1, 1, 1], [2, 2, 2]]) == pytest.approx(1.0)
    assert synchrony_same_action_rate([[1, 2], [1, 1]]) == pytest.approx(0.5)
    assert synchrony_same_action_rate([[1]]) is None
    assert synchrony_same_action_rate([]) is None


def test_the_gini_is_zero_for_an_even_split_and_none_for_an_empty_one():
    assert harvest_gini([5.0, 5.0, 5.0, 5.0]) == pytest.approx(0.0)
    assert harvest_gini([0.0, 0.0]) is None
    assert harvest_gini([]) is None
    assert harvest_gini([0.0, 0.0, 0.0, 12.0]) > 0.5


def test_the_grade_scale_names_the_v1_approximation_for_allelopathic():
    assert "BEST-MONOCULTURE" in SCALE["allelopathic"]
    assert "never-freeze" in SCALE["mushrooms"]
