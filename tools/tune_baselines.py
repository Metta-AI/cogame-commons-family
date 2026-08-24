#!/usr/bin/env python3
"""Grid harness for the steward's tuned constants.

The steward is the default baseline AND the fallback every prompt seat drops to
when its LLM call cannot be used, so its two free parameters are load-bearing:

    CLEAN_POLLUTION_TRIGGER   clean the river when pollution is above this
    CLEANUP_STOCK_FLOOR       take nothing while the orchard is below this

They are not guessed. This file sweeps them over a grid, plays every
combination through the four modules in three societies, and scores each one;
`tests/test_tuning.py` runs the same sweep in CI and fails if the shipped
constants fall outside the tolerance below.

    python3 tools/tune_baselines.py            # the whole table, best first

**The objective.** For one combination, one module and one society, the
steward's payoff is

    mean(score of every steward seat) + residual_value / num_agents

— what a steward took, plus its equal share of what the commons still holds at
the end. That is the quantity a sustainable policy is trying to maximise: a
policy that strips the resource scores well on the first term and zero on the
second, and one that never takes anything scores zero on the first. The
combination's value is that payoff summed over the four modules and the three
societies (12 episodes), and a combination is **inadmissible** if six stewards
kill the resource in any module, whatever it scores.

Everything is deterministic: one seed, no sampling, `headless.run_episode` with
the same policies in the same order, so the table is reproducible and a
regression in the baselines shows up as a moved optimum.
"""

from __future__ import annotations

import itertools
import sys
from pathlib import Path

if __name__ == "__main__" and "coworld" not in sys.modules:  # hand runs
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from coworld.examples.commons_family import headless  # noqa: E402
from coworld.examples.commons_family.game import baselines  # noqa: E402
from coworld.examples.commons_family.game.engine import (  # noqa: E402
    CommonsConfig,
    module_for,
)

MODULES = ("cleanup", "harvest", "allelopathic", "mushrooms")
ROOMS: dict[str, list[str]] = {
    # Six stewards: does the policy keep its own commons alive, and what is it
    # worth when everyone plays it?
    "monoculture": ["steward"] * 6,
    # The mixed room the variants actually run: a contributor, an enforcer, a
    # free rider and a random cog around two stewards.
    "mixed": ["steward", "steward", "cleaner", "punisher", "free_rider", "random"],
    # Under pressure: half the room is taking everything it can.
    "pressure": ["steward"] * 3 + ["free_rider"] * 3,
}

TRIGGERS = (0.05, 0.15, 0.25, 0.35, 0.45, 0.55)
FLOORS = (0.0, 10.0, 20.0, 30.0, 40.0, 50.0)

#: The shipped values, and the tolerance the test enforces against the grid's
#: best admissible combination.
SHIPPED = (baselines.CLEAN_POLLUTION_TRIGGER, baselines.CLEANUP_STOCK_FLOOR)
TOLERANCE = 0.02
"""Why a tolerance and not "must be the argmax": the top of this grid is
`trigger = 0.05`, which makes the steward clean in every round the river is
dirty at all — an unconditional rule that is the `cleaner` baseline, and the
difference between a conditional steward and an unconditional contributor is
one of the things this coworld exists to measure. The shipped trigger is the
best *conditional* value and lands inside 2 % of that corner.
"""

ROUNDS = 20
SEED = 20260824


def payoff(module_name: str, seats: list[str]) -> tuple[float, int]:
    """One episode. Returns `(steward payoff, dead resource count)`."""
    config = CommonsConfig(
        module=module_name,
        num_agents=6,
        rounds=ROUNDS,
        sanctions_enabled=True,
        seed=SEED,
    )
    state = headless.run_episode(config, headless.build_policies(seats))
    module = module_for(config)
    mine = [score for slot, score in enumerate(state.scores) if seats[slot] == "steward"]
    share = module.residual_value(state.module_state) / config.num_agents
    dead = state.module_state.get("dead")
    dead_count = (
        sum(1 for is_dead in dead if is_dead)
        if isinstance(dead, list)
        else int(bool(dead))
    )
    return sum(mine) / len(mine) + share, dead_count


def evaluate(trigger: float, floor: float) -> tuple[float, bool]:
    """Score one combination over the four modules and the three societies."""
    original = (baselines.CLEAN_POLLUTION_TRIGGER, baselines.CLEANUP_STOCK_FLOOR)
    baselines.CLEAN_POLLUTION_TRIGGER = trigger
    baselines.CLEANUP_STOCK_FLOOR = floor
    try:
        total = 0.0
        admissible = True
        for module_name in MODULES:
            for room, seats in ROOMS.items():
                value, dead_count = payoff(module_name, seats)
                total += value
                if room == "monoculture" and dead_count:
                    admissible = False
        return total, admissible
    finally:
        baselines.CLEAN_POLLUTION_TRIGGER, baselines.CLEANUP_STOCK_FLOOR = original


def sweep() -> list[tuple[float, bool, float, float]]:
    """The whole grid, best first: `(value, admissible, trigger, floor)`."""
    rows = [
        (*evaluate(trigger, floor), trigger, floor)
        for trigger, floor in itertools.product(TRIGGERS, FLOORS)
    ]
    rows.sort(key=lambda row: -row[0])
    return rows


def main() -> None:
    rows = sweep()
    best = max(value for value, admissible, _, _ in rows if admissible)
    print(f"{len(rows)} combinations, {len(MODULES) * len(ROOMS)} episodes each")
    print(f"{'value':>9}  {'vs best':>8}  admissible  trigger  floor")
    for value, admissible, trigger, floor in rows:
        marker = "  <- shipped" if (trigger, floor) == SHIPPED else ""
        print(
            f"{value:9.2f}  {(value / best - 1) * 100:7.2f}%  "
            f"{str(admissible):>10}  {trigger:7.2f}  {floor:5.0f}{marker}"
        )


if __name__ == "__main__":
    main()
