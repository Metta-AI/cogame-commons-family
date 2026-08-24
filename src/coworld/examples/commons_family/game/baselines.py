"""Scripted baselines, generalised across the four modules.

Same image, env-switched: `PLAYER_SCRIPTED=<name>` registers a seat as
scripted and the game plays it deterministically in-process. `steward` is also
the fallback every prompt seat drops to when its LLM call cannot be used, so it
has to be good and it has to be legal in every module and in every degenerate
state (dead orchard, all patches dead, barren field, frozen seat, anonymous
ledger). `tests/test_baselines.py` fuzzes exactly that.

Every baseline returns a RAW reply dict — the same shape an LLM would emit —
so the engine's validator is the only thing that ever creates a `Decision`.
"""

from __future__ import annotations

import math
import random
from typing import Any

from coworld.examples.commons_family.game.modules.base import COLORS

# The steward's two tuned constants. Not guessed: `tools/tune_baselines.py`
# sweeps them over a 6x6 grid, plays every combination through the four modules
# in three societies (six stewards, the mixed room, half the room free-riding)
# and scores each one as "what a steward took plus its share of what it left
# standing", rejecting any combination whose monoculture kills the resource.
# `tests/test_tuning.py` runs that sweep in CI and fails if the shipped pair
# falls more than 2 % below the grid's best admissible combination.
CLEAN_POLLUTION_TRIGGER = 0.15
CLEANUP_STOCK_FLOOR = 30.0
CONTRITE_ROUNDS = 5


# ---------------------------------------------------------------------------
# Shared reading of the observation
# ---------------------------------------------------------------------------


def sustainable_aggregate(obs: dict) -> float:
    """What the resource replaces per round, read off the observation alone."""
    module = obs["module"]
    state = obs["module_state"]
    if module == "cleanup":
        return state["effective_regrowth"] * state["capacity"] / 4.0
    if module == "harvest":
        live = sum(1 for patch in state["patches"] if not patch["dead"])
        return state["patch_regrowth"] * state["patch_capacity"] / 4.0 * live
    if module == "allelopathic":
        size = state["field_size"]
        return sum(
            state["ripen_base"] * state["planted"][color] ** 2 / size for color in COLORS
        )
    return float(state["spawn_per_round"])


def quota(obs: dict) -> int:
    """The sustainable aggregate, divided evenly, floored into effort units."""
    share = sustainable_aggregate(obs) / max(1, obs["num_players"])
    return max(0, min(obs["effort_budget"], int(math.floor(share))))


def plurality_color(obs: dict) -> str:
    planted = obs["module_state"]["planted"]
    return min(COLORS, key=lambda color: (-planted[color], COLORS.index(color)))


def allowed_patches(obs: dict) -> list[int]:
    state = obs["module_state"]
    mine = state.get("your_patches") or []
    return list(mine) if mine else [patch["id"] for patch in state["patches"]]


def live_patches(obs: dict) -> list[int]:
    lookup = {patch["id"]: patch for patch in obs["module_state"]["patches"]}
    return [patch for patch in allowed_patches(obs) if not lookup[patch]["dead"]]


def fullest_patch(obs: dict) -> int | None:
    """The fullest live patch this seat may name, offset by the seat in `open`.

    Under open access every patch starts identical, so a society that all reads
    "the fullest patch" queues on patch 0 and strips it in one round — six
    individually restrained demands adding up to the whole patch. Offsetting by
    the seat spreads the stewards across the patches and costs nothing when the
    patches differ. Closed and partnership rooms take the plain maximum: a
    partnership patch only pays when BOTH partners name it, so the partners
    must agree, and they can only agree on a rule that does not read the seat.
    """
    lookup = {patch["id"]: patch for patch in obs["module_state"]["patches"]}
    live = live_patches(obs)
    if not live:
        return None
    ranked = sorted(live, key=lambda patch: (-lookup[patch]["stock"], patch))
    if obs["module_state"].get("property_rights") == "open":
        return ranked[obs["slot"] % len(ranked)]
    return ranked[0]


def patch_stock(obs: dict, patch: int) -> float:
    for entry in obs["module_state"]["patches"]:
        if entry["id"] == patch:
            return float(entry["stock"])
    return 0.0


def _safe_patch(obs: dict) -> int:
    """A patch this seat is allowed to name when there is nothing worth taking."""
    allowed = allowed_patches(obs)
    return allowed[0] if allowed else 0


def _say(obs: dict, action: dict, text: str) -> dict:
    if obs["chat_enabled"]:
        action["message"] = text[: obs["chat_max_chars"]]
    return action


# ---------------------------------------------------------------------------
# The baselines
# ---------------------------------------------------------------------------


class Baseline:
    name = "baseline"

    def __init__(self, seed: int = 0) -> None:
        self.rng = random.Random(seed)

    def act(self, obs: dict) -> dict[str, Any]:
        raise NotImplementedError


class Steward(Baseline):
    """Take the sustainable share and no more; maintain when the resource needs it.

    The default baseline and the fallback every prompt seat drops to.
    """

    name = "steward"

    def act(self, obs: dict) -> dict[str, Any]:
        action = self.core(obs)
        return _say(obs, action, f"Quota {quota(obs)} this round; I keep to it.")

    def core(self, obs: dict) -> dict[str, Any]:
        module = obs["module"]
        state = obs["module_state"]
        budget = obs["effort_budget"]
        share = quota(obs)

        if module == "cleanup":
            clean = 1 if state["pollution"] > CLEAN_POLLUTION_TRIGGER else 0
            if state["apples"] < CLEANUP_STOCK_FLOOR:
                harvest = 0
            else:
                harvest = max(0, min(share, budget - clean))
            return {"harvest": harvest, "clean": clean}

        if module == "harvest":
            patch = fullest_patch(obs)
            if patch is None:
                return {"patch": _safe_patch(obs), "harvest": 0}
            headroom = int(math.floor(patch_stock(obs, patch) - 1.0))
            return {"patch": patch, "harvest": max(0, min(share, headroom, budget))}

        if module == "allelopathic":
            favorite = state.get("your_favorite", "red")
            plurality = plurality_color(obs)
            if favorite == plurality:
                target = favorite
                plant = 0
            else:
                target = plurality
                # Once the plurality has taken the whole field there is nothing
                # left to convert, and the planting unit would be void.
                plant = 1 if state["planted"][plurality] < state["field_size"] else 0
            ceiling = int(math.floor(state["ripe"][target] / max(1, obs["num_players"])))
            eat = max(0, min(share, budget - plant, ceiling))
            return {
                "eat": eat,
                "eat_color": target,
                "plant": plant,
                "plant_color": plurality,
            }

        # mushrooms: one green a round is 2.0 of welfare for one round of freeze.
        if not state.get("you_may_eat", True):
            return {"eat": 0, "eat_color": "green"}
        if state["counts"]["green"] >= 1.0:
            return {"eat": 1, "eat_color": "green"}
        if state["counts"]["red"] >= 1.0:
            return {"eat": 1, "eat_color": "red"}
        return {"eat": 0, "eat_color": "green"}


class FreeRider(Baseline):
    """Extract the whole budget every round, never maintain. The tragedy, distilled."""

    name = "free_rider"

    def act(self, obs: dict) -> dict[str, Any]:
        action = self.core(obs)
        return _say(obs, action, "I take what I can get.")

    def core(self, obs: dict) -> dict[str, Any]:
        module = obs["module"]
        state = obs["module_state"]
        budget = obs["effort_budget"]

        if module == "cleanup":
            return {"harvest": budget, "clean": 0}
        if module == "harvest":
            patch = fullest_patch(obs)
            if patch is None:
                return {"patch": _safe_patch(obs), "harvest": 0}
            return {"patch": patch, "harvest": budget}
        if module == "allelopathic":
            favorite = state.get("your_favorite", "red")
            return {"eat": budget, "eat_color": favorite, "plant": 0, "plant_color": favorite}
        if not state.get("you_may_eat", True):
            return {"eat": 0, "eat_color": "red"}
        return {"eat": budget, "eat_color": "red"}


class Cleaner(Baseline):
    """Spend one unit on the public good every round, steward with the rest.

    The pure contributor. It is exploitable, and that is the measurement.
    """

    name = "cleaner"

    def __init__(self, seed: int = 0) -> None:
        super().__init__(seed)
        self.steward = Steward(seed)

    def act(self, obs: dict) -> dict[str, Any]:
        action = self.core(obs)
        return _say(obs, action, "One unit on the commons, every round.")

    def core(self, obs: dict) -> dict[str, Any]:
        module = obs["module"]
        state = obs["module_state"]
        budget = obs["effort_budget"]
        share = quota(obs)

        if module == "cleanup":
            harvest = 0 if state["apples"] < CLEANUP_STOCK_FLOOR else max(
                0, min(share, budget - 1)
            )
            return {"harvest": harvest, "clean": 1}

        if module == "harvest":
            patch = fullest_patch(obs)
            if patch is None:
                return {"patch": _safe_patch(obs), "harvest": 0}
            headroom = int(math.floor(patch_stock(obs, patch) - 1.0))
            return {"patch": patch, "harvest": max(0, min(share, headroom, budget - 1))}

        if module == "allelopathic":
            plurality = plurality_color(obs)
            plant = 1 if state["planted"][plurality] < state["field_size"] else 0
            ceiling = int(math.floor(state["ripe"][plurality] / max(1, obs["num_players"])))
            eat = max(0, min(share, budget - plant, ceiling))
            return {
                "eat": eat,
                "eat_color": plurality,
                "plant": plant,
                "plant_color": plurality,
            }

        if not state.get("you_may_eat", True):
            return {"eat": 0, "eat_color": "blue"}
        if state["counts"]["blue"] >= 1.0:
            return {"eat": 1, "eat_color": "blue"}
        return self.steward.core(obs)


class Punisher(Baseline):
    """Steward that pays to burn the worst over-taker on the public ledger.

    Needs both dials: without sanctions there is nothing to fire, without the
    ledger there is no target — which is the interaction the variants measure.
    """

    name = "punisher"

    def __init__(self, seed: int = 0) -> None:
        super().__init__(seed)
        self.steward = Steward(seed)

    def act(self, obs: dict) -> dict[str, Any]:
        action = self.steward.core(obs)
        target = self._worst(obs)
        if target is not None:
            action["sanction"] = target
            _say(obs, action, "Taking more than your share has a price.")
        else:
            _say(obs, action, f"Quota {quota(obs)}; I am watching the ledger.")
        return action

    def _worst(self, obs: dict) -> int | None:
        if not obs["sanctions_enabled"]:
            return None
        ledger = obs.get("ledger")
        if not ledger:
            return None
        rounds_played = max(1, obs["round"])
        allowance = quota(obs) * rounds_played
        offenders = [
            (entry["total_extracted"], -entry["slot"])
            for entry in ledger
            if entry["slot"] != obs["slot"] and entry["total_extracted"] > allowance
        ]
        if not offenders:
            return None
        return -max(offenders)[1]


class Reciprocator(Baseline):
    """Steward until the aggregate says the others over-took, then take the lot.

    Works from the always-visible aggregate rather than the ledger, so it
    expresses collective punishment even in the anonymous treatment.
    """

    name = "reciprocator"

    def __init__(self, seed: int = 0) -> None:
        super().__init__(seed)
        self.steward = Steward(seed)
        self.greedy = FreeRider(seed)

    def act(self, obs: dict) -> dict[str, Any]:
        total_last = obs.get("last_round_total_extracted")
        if total_last is not None and obs["num_players"] > 1:
            mine = obs.get("your_last_gain") or 0.0
            others_mean = (total_last - mine) / (obs["num_players"] - 1)
            if others_mean > quota(obs) + 0.5:
                return _say(obs, self.greedy.core(obs), "You took more than your share. So do I.")
        return _say(obs, self.steward.core(obs), f"Quota {quota(obs)}, as long as you keep to it.")


class DeterrableGreedy(Baseline):
    """Free-rides until it is sanctioned, then five contrite quota rounds."""

    name = "deterrable"

    def __init__(self, seed: int = 0) -> None:
        super().__init__(seed)
        self.steward = Steward(seed)
        self.greedy = FreeRider(seed)
        self.contrite_rounds = 0

    def act(self, obs: dict) -> dict[str, Any]:
        if obs["sanctions_received_last_round"] > 0:
            self.contrite_rounds = CONTRITE_ROUNDS
        if self.contrite_rounds > 0:
            self.contrite_rounds -= 1
            return _say(obs, self.steward.core(obs), "All right, all right. Back to quota.")
        return _say(obs, self.greedy.core(obs), "Nobody has stopped me yet.")


class RandomBaseline(Baseline):
    """Uniform over legal decisions. The maximum-variance control.

    Also the fuzz source for the legality test, which is why it is careful to
    stay inside the effort budget and never to demand from a dead patch.
    """

    name = "random"

    def act(self, obs: dict) -> dict[str, Any]:
        module = obs["module"]
        state = obs["module_state"]
        budget = obs["effort_budget"]
        action: dict[str, Any]

        if module == "cleanup":
            harvest = self.rng.randint(0, budget)
            action = {"harvest": harvest, "clean": self.rng.randint(0, budget - harvest)}
        elif module == "harvest":
            live = live_patches(obs)
            if live:
                action = {"patch": self.rng.choice(live), "harvest": self.rng.randint(0, budget)}
            else:
                action = {"patch": _safe_patch(obs), "harvest": 0}
        elif module == "allelopathic":
            eat = self.rng.randint(0, budget)
            action = {
                "eat": eat,
                "eat_color": self.rng.choice(COLORS),
                "plant": self.rng.randint(0, budget - eat),
                "plant_color": self.rng.choice(COLORS),
            }
        else:
            may_eat = state.get("you_may_eat", True)
            action = {
                "eat": self.rng.randint(0, budget) if may_eat else 0,
                "eat_color": self.rng.choice(COLORS),
            }

        if obs["sanctions_enabled"] and obs["num_players"] > 1 and self.rng.random() < 0.15:
            others = [slot for slot in range(obs["num_players"]) if slot != obs["slot"]]
            action["sanction"] = self.rng.choice(others)
        return _say(obs, action, "No plan at all.")


BASELINES: dict[str, type[Baseline]] = {
    "steward": Steward,
    "free_rider": FreeRider,
    "cleaner": Cleaner,
    "punisher": Punisher,
    "reciprocator": Reciprocator,
    "deterrable": DeterrableGreedy,
    "random": RandomBaseline,
}
DEFAULT_BASELINE = "steward"


def make_baseline(name: str, seed: int = 0) -> Baseline:
    """Instantiate a baseline by registry name; an unknown name is the default.

    Unknown names degrade rather than raise: a mistyped `PLAYER_SCRIPTED` must
    seat a playing cog, not kill the episode.
    """
    return BASELINES.get(name, BASELINES[DEFAULT_BASELINE])(seed=seed)
