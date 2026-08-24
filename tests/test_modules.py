"""The four resource physics, one exact-arithmetic case per numbered rule."""

from __future__ import annotations

import math

import pytest

from coworld.examples.commons_family.game.engine import (
    CommonsConfig,
    module_for,
    new_game,
    parse_decision,
)
from coworld.examples.commons_family.game.modules.base import Decision

APPROX = 1e-9


def setup(module: str, **overrides):
    config = CommonsConfig(module=module, num_agents=6, rounds=20, **overrides)
    state = new_game(config)
    return config, state, module_for(config)


# ---------------------------------------------------------------------------
# cleanup
# ---------------------------------------------------------------------------


def test_cleanup_over_demand_splits_pro_rata():
    config, state, module = setup("cleanup")
    state.module_state["apples"] = 5.0
    decisions = [Decision(harvest=3) for _ in range(6)]
    gains, extracted, _ = module.resolve(state.module_state, decisions, config, 0)
    assert gains == pytest.approx([3 * 5.0 / 18.0] * 6)
    assert extracted == pytest.approx(gains)
    assert state.module_state["apples"] == pytest.approx(0.0, abs=APPROX)


def test_cleanup_under_demand_pays_in_full():
    config, state, module = setup("cleanup")
    decisions = [Decision(harvest=1) for _ in range(6)]
    gains, _, _ = module.resolve(state.module_state, decisions, config, 0)
    assert gains == pytest.approx([1.0] * 6)
    assert state.module_state["apples"] == pytest.approx(54.0)


def test_cleanup_effort_budget_reduces_cleaning_first():
    config, state, module = setup("cleanup")
    decisions = [Decision(harvest=3, clean=3)] + [Decision() for _ in range(5)]
    module.resolve(state.module_state, decisions, config, 0)
    assert decisions[0].harvest == 3
    assert decisions[0].clean == 0


def test_cleanup_pollution_clamps_at_zero_and_one():
    config, state, module = setup("cleanup", pollution_start=0.02)
    module.resolve(state.module_state, [Decision(clean=3) for _ in range(6)], config, 0)
    assert state.module_state["pollution"] == 0.0

    config, state, module = setup("cleanup", pollution_start=0.95)
    module.resolve(state.module_state, [Decision() for _ in range(6)], config, 0)
    assert state.module_state["pollution"] == 1.0


@pytest.mark.parametrize(
    ("pollution", "effective"),
    [(0.0, 0.35), (0.3, 0.245), (1.0, 0.0)],
)
def test_cleanup_effective_regrowth_scales_with_pollution(pollution, effective):
    config, state, module = setup("cleanup", pollution_start=pollution)
    public = module.public_state(state.module_state, config, [""] * 6)
    assert public["effective_regrowth"] == pytest.approx(effective)


def test_cleanup_regrowth_is_logistic_and_scaled():
    config, state, module = setup("cleanup", pollution_start=0.0, silt_rate=0.0)
    state.module_state["apples"] = 50.0
    module.dynamics(state.module_state, config, 0)
    assert state.module_state["apples"] == pytest.approx(50.0 + 0.35 * 50.0 * 0.5)


def test_cleanup_collapse_latches_and_never_regrows():
    config, state, module = setup("cleanup")
    state.module_state["apples"] = 9.0
    events = module.dynamics(state.module_state, config, 4)
    assert [event["kind"] for event in events] == ["collapse"]
    assert state.module_state["dead"] is True
    assert state.module_state["collapse_round"] == 4
    assert state.module_state["apples"] == 9.0

    # Dead means dead: no regrowth for the rest of the episode, and no second
    # collapse event.
    for round_index in range(5, 10):
        assert module.dynamics(state.module_state, config, round_index) == []
    assert state.module_state["apples"] == 9.0
    # Remaining apples can still be scavenged.
    gains, _, _ = module.resolve(state.module_state, [Decision(harvest=3)] * 6, config, 5)
    assert sum(gains) == pytest.approx(9.0)


# ---------------------------------------------------------------------------
# harvest
# ---------------------------------------------------------------------------


def test_harvest_splits_per_patch_not_across_patches():
    config, state, module = setup("harvest")
    state.module_state["stocks"] = [3.0, 20.0, 12.0, 12.0, 12.0, 12.0]
    decisions = [
        Decision(patch=0, harvest=3),
        Decision(patch=0, harvest=3),
        Decision(patch=1, harvest=3),
    ] + [Decision(patch=index, harvest=0) for index in range(3, 6)]
    gains, _, _ = module.resolve(state.module_state, decisions, config, 0)
    assert gains[0] == pytest.approx(1.5)
    assert gains[1] == pytest.approx(1.5)
    assert gains[2] == pytest.approx(3.0)
    assert state.module_state["stocks"][0] == pytest.approx(0.0, abs=APPROX)
    assert state.module_state["stocks"][1] == pytest.approx(17.0)


def test_harvest_patch_below_one_is_dead_forever():
    config, state, module = setup("harvest")
    state.module_state["stocks"][0] = 1.5
    module.resolve(state.module_state, [Decision(patch=0, harvest=1)] + [Decision(patch=5)] * 5,
                   config, 0)
    assert state.module_state["stocks"][0] == pytest.approx(0.5)
    events = module.dynamics(state.module_state, config, 0)
    assert any(event["kind"] == "patch_dead" and event["patch"] == 0 for event in events)
    assert state.module_state["dead"][0] is True
    assert state.module_state["stocks"][0] == 0.0

    for round_index in range(1, 5):
        module.dynamics(state.module_state, config, round_index)
    assert state.module_state["stocks"][0] == 0.0

    gains, _, events = module.resolve(
        state.module_state, [Decision(patch=0, harvest=3)] * 6, config, 5
    )
    assert gains == [0.0] * 6
    assert all(event["cause"] == "dead" for event in events if event["kind"] == "void")


def test_harvest_live_patch_regrows_logistically():
    config, state, module = setup("harvest")
    state.module_state["stocks"][0] = 10.0
    module.dynamics(state.module_state, config, 0)
    assert state.module_state["stocks"][0] == pytest.approx(10.0 + 0.4 * 10.0 * 0.5)


def test_harvest_closed_voids_a_non_owner():
    config, state, module = setup("harvest", property_rights="closed")
    owner = state.module_state["owner"]
    patch = 0
    trespasser = next(slot for slot in range(6) if slot != owner[patch])
    decisions = [Decision(patch=patch if slot == trespasser else 5, harvest=2) for slot in range(6)]
    decisions[owner[patch]] = Decision(patch=5, harvest=0)
    gains, _, events = module.resolve(state.module_state, decisions, config, 0)
    assert gains[trespasser] == 0.0
    assert any(
        event["kind"] == "trespass" and event["slot"] == trespasser for event in events
    )


def test_harvest_closed_pays_the_owner():
    config, state, module = setup("harvest", property_rights="closed")
    owner = state.module_state["owner"][2]
    decisions = [Decision(patch=2 if slot == owner else 5, harvest=2) for slot in range(6)]
    gains, _, _ = module.resolve(state.module_state, decisions, config, 0)
    assert gains[owner] == pytest.approx(2.0)


def test_harvest_partnership_pays_only_when_both_hold_the_patch():
    config, state, module = setup("harvest", property_rights="partnership")
    left, right = state.module_state["pairs"][0]
    patch = 0

    # Only one partner names it: nothing yields, and the event says why.
    decisions = [Decision(patch=5, harvest=0) for _ in range(6)]
    decisions[left] = Decision(patch=patch, harvest=2)
    gains, _, events = module.resolve(state.module_state, decisions, config, 0)
    assert gains[left] == 0.0
    assert any(event["kind"] == "unheld" for event in events)

    # Both name it — one of them may demand 0; naming it is holding it.
    decisions = [Decision(patch=5, harvest=0) for _ in range(6)]
    decisions[left] = Decision(patch=patch, harvest=2)
    decisions[right] = Decision(patch=patch, harvest=0)
    gains, _, events = module.resolve(state.module_state, decisions, config, 1)
    assert gains[left] == pytest.approx(2.0)
    assert not any(event["kind"] == "unheld" for event in events)


def test_a_seat_that_never_answered_holds_no_patch():
    """A pass names nothing; the default `patch=0` is not a claim.

    Every seat's decision carries `patch`, and a seat that passes gets the
    all-zero default. Counted as "named", that let the partner of a
    disconnected seat harvest patch 0 alone every round — the pair's other
    patch could never be held at all — and it made a passing seat a trespasser
    in a closed room.
    """
    config, state, module = setup("harvest", property_rights="partnership")
    left, right = state.module_state["pairs"][0]
    other = next(slot for slot in range(6) if slot not in (left, right))

    passing = [
        parse_decision({}, slot, config, state, module, "pass") for slot in range(6)
    ]
    decisions = list(passing)
    decisions[left] = parse_decision({"patch": 0, "harvest": 2}, left, config, state,
                                     module, "llm")
    gains, _, events = module.resolve(state.module_state, decisions, config, 0)
    assert gains[left] == 0.0, "patch 0 is not held by a partner that never answered"
    assert any(event["kind"] == "unheld" and event["slot"] == left for event in events)
    # And the passing seats produce no events of their own.
    assert not any(event.get("slot") == other for event in events)

    # Patch 1 belongs to the same pair, and is just as unholdable while its
    # partner is absent — the point being that neither patch is silently held.
    decisions = list(passing)
    decisions[left] = parse_decision({"patch": 1, "harvest": 2}, left, config, state,
                                     module, "llm")
    gains, _, _ = module.resolve(state.module_state, decisions, config, 1)
    assert gains[left] == 0.0

    # Both partners answer: the patch pays, either may demand 0.
    decisions = list(passing)
    decisions[left] = parse_decision({"patch": 0, "harvest": 2}, left, config, state,
                                     module, "llm")
    decisions[right] = parse_decision({"patch": 0, "harvest": 0}, right, config, state,
                                      module, "llm")
    gains, _, events = module.resolve(state.module_state, decisions, config, 2)
    assert gains[left] == pytest.approx(2.0)
    assert not any(event["kind"] == "unheld" for event in events)


def test_a_passing_seat_is_not_a_trespasser_in_a_closed_room():
    config, state, module = setup("harvest", property_rights="closed")
    decisions = [
        parse_decision({}, slot, config, state, module, "pass") for slot in range(6)
    ]
    _, _, events = module.resolve(state.module_state, decisions, config, 0)
    assert not any(event["kind"] == "trespass" for event in events)


def test_harvest_deals_are_a_public_permutation():
    config, state, module = setup("harvest", property_rights="closed")
    assert sorted(state.module_state["owner"]) == list(range(6))
    public = module.public_state(state.module_state, config, [f"Cog-{i}" for i in range(6)])
    assert all(len(patch["holders"]) == 1 for patch in public["patches"])


@pytest.mark.parametrize("patch_count", [3, 6, 12])
@pytest.mark.parametrize("rights", ["open", "closed", "partnership"])
def test_patch_ownership_stays_defined_when_the_counts_differ(patch_count, rights):
    """The 1:1 deal is the shipped case; the wrap is what makes the rest legal.

    `owner[p] = patch_deal[p] % num_agents` is a permutation only because the
    shipped `patch_count` equals `num_agents` (the manifest pins both, and the
    schema pins num_agents 6..6). A hand-edited `game_config` can set
    `patch_count` anywhere in 1..12, and every patch must still have exactly
    one owner, every allowed set must stay inside the patch range, and the
    episode must still play.
    """
    from coworld.examples.commons_family import headless  # noqa: PLC0415

    config = CommonsConfig(module="harvest", num_agents=6, rounds=8,
                           patch_count=patch_count, property_rights=rights)
    module = module_for(config)
    state = new_game(config)
    owner = state.module_state["owner"]
    assert len(owner) == patch_count
    assert all(0 <= seat < config.num_agents for seat in owner)
    if patch_count == config.num_agents:
        assert sorted(owner) == list(range(config.num_agents))
    for slot in range(config.num_agents):
        allowed = module.allowed_patches(state.module_state, config, slot)
        assert all(0 <= patch < patch_count for patch in allowed)
        assert len(set(allowed)) == len(allowed)

    played = headless.run_episode(
        config,
        headless.build_policies(["steward", "free_rider", "cleaner", "punisher",
                                 "random", "reciprocator"]),
    )
    assert played.round == 8
    for record in played.history:
        for decision in record.decisions:
            assert 0 <= decision["patch"] < patch_count
            assert 0 <= decision["harvest"] <= config.effort_budget


# ---------------------------------------------------------------------------
# allelopathic
# ---------------------------------------------------------------------------


def test_allelopathic_favorite_pays_double():
    config, state, module = setup("allelopathic")
    state.module_state["favorites"] = ["green", "red", "red", "green", "blue", "blue"]
    decisions = [Decision(eat=1, eat_color="green") for _ in range(2)] + [Decision()] * 4
    gains, extracted, _ = module.resolve(state.module_state, decisions, config, 0)
    assert gains[0] == pytest.approx(2.0)   # green is slot 0's favourite
    assert gains[1] == pytest.approx(1.0)   # slot 1 favours red
    assert extracted[0] == pytest.approx(1.0)


def test_allelopathic_eating_splits_pro_rata_per_colour():
    config, state, module = setup("allelopathic")
    state.module_state["favorites"] = ["red"] * 6
    state.module_state["ripe"]["green"] = 3.0
    decisions = [Decision(eat=3, eat_color="green") for _ in range(6)]
    gains, _, _ = module.resolve(state.module_state, decisions, config, 0)
    assert gains == pytest.approx([3.0 * 3.0 / 18.0] * 6)
    assert state.module_state["ripe"]["green"] == pytest.approx(0.0, abs=APPROX)


def test_allelopathic_plant_takes_from_the_largest_other_colour_canonically():
    config, state, module = setup("allelopathic")
    state.module_state["planted"] = {"red": 20, "green": 20, "blue": 20}
    decisions = [Decision(plant=1, plant_color="blue")] + [Decision()] * 5
    module.resolve(state.module_state, decisions, config, 0)
    # red and green are tied at 20; the canonical order breaks it towards red.
    assert state.module_state["planted"] == {"red": 19, "green": 20, "blue": 21}


def test_allelopathic_a_converted_slot_takes_its_ripe_berry_with_it():
    config, state, module = setup("allelopathic")
    state.module_state["planted"] = {"red": 5, "green": 1, "blue": 0}
    state.module_state["ripe"] = {"red": 5.0, "green": 0.0, "blue": 0.0}
    decisions = [Decision(plant=1, plant_color="blue")] + [Decision()] * 5
    module.resolve(state.module_state, decisions, config, 0)
    assert state.module_state["planted"] == {"red": 4, "green": 1, "blue": 1}
    assert state.module_state["ripe"]["red"] == pytest.approx(4.0)


def test_allelopathic_a_plant_unit_with_no_source_is_void():
    config, state, module = setup("allelopathic")
    state.module_state["planted"] = {"red": 0, "green": 0, "blue": 60}
    decisions = [Decision(plant=3, plant_color="blue")] + [Decision()] * 5
    module.resolve(state.module_state, decisions, config, 0)
    assert state.module_state["planted"] == {"red": 0, "green": 0, "blue": 60}


def test_allelopathic_ripening_is_quadratic_in_the_colour_share():
    config, state, module = setup("allelopathic")
    state.module_state["planted"] = {"red": 20, "green": 20, "blue": 20}
    state.module_state["ripe"] = {"red": 0.0, "green": 0.0, "blue": 0.0}
    module.dynamics(state.module_state, config, 0)
    per_colour = 0.5 * 20 * 20 / 60
    assert state.module_state["ripe"]["red"] == pytest.approx(per_colour)
    assert sum(state.module_state["ripe"].values()) == pytest.approx(10.0)

    config, state, module = setup("allelopathic")
    state.module_state["planted"] = {"red": 60, "green": 0, "blue": 0}
    state.module_state["ripe"] = {"red": 0.0, "green": 0.0, "blue": 0.0}
    module.dynamics(state.module_state, config, 0)
    assert sum(state.module_state["ripe"].values()) == pytest.approx(30.0)


def test_allelopathic_ripe_never_exceeds_planted_and_reports_barren():
    config, state, module = setup("allelopathic")
    state.module_state["planted"] = {"red": 0, "green": 0, "blue": 0}
    state.module_state["ripe"] = {"red": 0.0, "green": 0.0, "blue": 0.0}
    events = module.dynamics(state.module_state, config, 2)
    assert [event["kind"] for event in events] == ["barren"]
    assert sum(state.module_state["ripe"].values()) == 0.0


# ---------------------------------------------------------------------------
# mushrooms
# ---------------------------------------------------------------------------


def test_mushrooms_red_pays_only_the_eater():
    config, state, module = setup("mushrooms")
    decisions = [Decision(eat=1, eat_color="red")] + [Decision()] * 5
    gains, extracted, _ = module.resolve(state.module_state, decisions, config, 0)
    assert gains[0] == pytest.approx(1.0)
    assert gains[1:] == pytest.approx([0.0] * 5)
    assert sum(gains) == pytest.approx(1.0)
    assert extracted[0] == pytest.approx(1.0)


def test_mushrooms_green_pays_the_whole_group_including_the_eater():
    config, state, module = setup("mushrooms")
    decisions = [Decision(eat=1, eat_color="green")] + [Decision()] * 5
    gains, _, _ = module.resolve(state.module_state, decisions, config, 0)
    assert gains == pytest.approx([2.0 / 6] * 6)
    assert sum(gains) == pytest.approx(2.0)


def test_mushrooms_blue_pays_everyone_except_the_eater():
    config, state, module = setup("mushrooms")
    decisions = [Decision(eat=1, eat_color="blue")] + [Decision()] * 5
    gains, _, _ = module.resolve(state.module_state, decisions, config, 0)
    assert gains[0] == pytest.approx(0.0)
    assert gains[1:] == pytest.approx([3.0 / 5] * 5)
    assert sum(gains) == pytest.approx(3.0)


def test_mushrooms_digestion_freezes_for_ceil_k_rounds():
    config, state, module = setup("mushrooms")
    decisions = [Decision(eat=3, eat_color="red"), Decision(eat=1, eat_color="red")] + \
        [Decision()] * 4
    module.resolve(state.module_state, decisions, config, 4)
    assert state.module_state["frozen_until"][0] == 4 + math.ceil(3)
    assert state.module_state["frozen_until"][1] == 4 + 1

    # A frozen seat's eat demand is voided, and it is told why.
    gains, _, events = module.resolve(
        state.module_state, [Decision(eat=1, eat_color="red")] * 6, config, 5
    )
    assert gains[0] == 0.0
    assert any(event["kind"] == "digesting" and event["slot"] == 0 for event in events)
    assert gains[1] > 0.0   # slot 1 is free again at round 5


def test_mushrooms_spawn_is_uniform_when_nothing_has_been_eaten():
    config, state, module = setup("mushrooms")
    module.dynamics(state.module_state, config, 0)
    assert state.module_state["counts"] == {"red": 9.0, "green": 9.0, "blue": 9.0}


def test_mushrooms_spawn_follows_appetite_with_a_canonical_tie_break():
    config, state, module = setup("mushrooms")
    state.module_state["counts"] = {"red": 0.0, "green": 0.0, "blue": 0.0}
    state.module_state["eaten_total"] = {"red": 2.0, "green": 0.0, "blue": 0.0}
    module.dynamics(state.module_state, config, 0)
    # weights 3/1/1 -> exact 1.8/0.6/0.6 -> floors 1/0/0, two left over; the
    # largest remainder is red, then green wins the green/blue tie canonically.
    assert state.module_state["counts"] == {"red": 2.0, "green": 1.0, "blue": 0.0}


def test_mushrooms_counts_are_capped_per_colour_and_in_total():
    config, state, module = setup("mushrooms")
    state.module_state["counts"] = {"red": 15.0, "green": 15.0, "blue": 0.0}
    state.module_state["eaten_total"] = {"red": 10.0, "green": 0.0, "blue": 0.0}
    module.dynamics(state.module_state, config, 0)
    assert state.module_state["counts"]["red"] <= 15.0
    assert sum(state.module_state["counts"].values()) <= 30.0


def test_mushrooms_residual_value_counts_every_standing_mushroom():
    config, state, module = setup("mushrooms")
    assert module.residual_value(state.module_state) == pytest.approx(24.0)


# ---------------------------------------------------------------------------
# determinism
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("module_name", ["cleanup", "harvest", "allelopathic", "mushrooms"])
def test_seed_fixes_the_aliases_the_favourites_and_the_deal(module_name):
    first = new_game(CommonsConfig(module=module_name, seed=99))
    second = new_game(CommonsConfig(module=module_name, seed=99))
    third = new_game(CommonsConfig(module=module_name, seed=100))
    assert first.aliases == second.aliases
    assert first.module_state == second.module_state
    assert sorted(first.aliases) == [f"Cog-{letter}" for letter in "ABCDEF"]
    assert (first.aliases, first.module_state) != (third.aliases, third.module_state) or \
        module_name in ("cleanup", "mushrooms")


def test_the_favourite_deal_is_two_cogs_per_colour():
    config = CommonsConfig(module="allelopathic", seed=7)
    state = new_game(config)
    favorites = state.module_state["favorites"]
    assert sorted(favorites) == ["blue", "blue", "green", "green", "red", "red"]
