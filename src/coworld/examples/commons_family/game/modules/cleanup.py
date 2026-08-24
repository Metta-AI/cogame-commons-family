"""Clean Up: one apple stock, one river that silts up.

Public goods with a physical opportunity cost. Apples regrow logistically, but
the growth rate is scaled by `(1 - pollution)`, and pollution rises every round
whether or not anyone cleans. Cleaning pays the cleaner nothing; it pays
everyone. Below the collapse threshold the orchard is dead forever — meadow's
latch, unchanged.
"""

from __future__ import annotations

from typing import Any

from coworld.examples.commons_family.game.modules.base import (
    Decision,
    Module,
    clamp_int,
    pro_rata,
)


class CleanupModule(Module):
    name = "cleanup"

    def new_state(self, config: Any, deal: dict) -> dict[str, Any]:
        return {
            "apples": float(config.stock_start),
            "pollution": float(config.pollution_start),
            "dead": False,
            "collapse_round": None,
            "cleaned_last_round": 0,
        }

    def parse_decision(self, raw: object, slot: int, config: Any, state: dict) -> Decision:
        if not isinstance(raw, dict):
            return Decision()
        harvest = clamp_int(raw.get("harvest", 0), 0, config.effort_budget)
        clean = clamp_int(raw.get("clean", 0), 0, config.effort_budget)
        # Maintenance yields first: the extractive field is what the seat asked
        # for, and an over-budget reply loses its clean units, not its harvest.
        if harvest + clean > config.effort_budget:
            clean = max(0, config.effort_budget - harvest)
        return Decision(harvest=harvest, clean=clean)

    def resolve(
        self, state: dict, decisions: list[Decision], config: Any, r: int
    ) -> tuple[list[float], list[float], list[dict]]:
        for decision in decisions:
            if decision.harvest + decision.clean > config.effort_budget:
                decision.clean = max(0, config.effort_budget - decision.harvest)
        demands = [float(decision.harvest) for decision in decisions]
        gains = pro_rata(demands, state["apples"])
        state["apples"] = max(0.0, state["apples"] - sum(gains))

        cleaned = sum(decision.clean for decision in decisions)
        state["cleaned_last_round"] = cleaned
        state["pollution"] = min(
            1.0,
            max(0.0, state["pollution"] + config.silt_rate - config.clean_power * cleaned),
        )
        events = [
            {
                "kind": "resolve",
                "r": r,
                "extracted": round(sum(gains), 3),
                "cleaned": cleaned,
                "text": f"{sum(gains):.1f} apples taken, {cleaned} effort on the river.",
            }
        ]
        return gains, list(gains), events

    def dynamics(self, state: dict, config: Any, r: int) -> list[dict]:
        if not state["dead"] and state["apples"] < config.collapse_threshold:
            state["dead"] = True
            state["collapse_round"] = r
            return [
                {
                    "kind": "collapse",
                    "r": r,
                    "text": "The orchard is stripped below recovery — nothing regrows again.",
                }
            ]
        if state["dead"]:
            return []
        apples = state["apples"]
        grown = apples + config.regrowth_rate * (1.0 - state["pollution"]) * apples * (
            1.0 - apples / config.stock_capacity
        )
        state["apples"] = min(config.stock_capacity, grown)
        return []

    def public_state(self, state: dict, config: Any, aliases: list[str]) -> dict[str, Any]:
        return {
            "apples": round(state["apples"], 3),
            "capacity": config.stock_capacity,
            "pollution": round(state["pollution"], 3),
            "effective_regrowth": round(config.regrowth_rate * (1.0 - state["pollution"]), 4),
            "collapse_threshold": config.collapse_threshold,
            "silt_rate": config.silt_rate,
            "clean_power": config.clean_power,
            "dead": state["dead"],
            "cleaned_last_round": state["cleaned_last_round"],
        }

    def observe(
        self, state: dict, config: Any, slot: int, aliases: list[str], r: int
    ) -> dict[str, Any]:
        return self.public_state(state, config, aliases)

    def residual_value(self, state: dict) -> float:
        return float(state["apples"])

    def public_effort(self, decision: Decision, config: Any) -> int:
        return decision.clean

    def compact(self, decision: Decision) -> str:
        return f"h:{decision.harvest} c:{decision.clean}"

    def describe(self, decision: Decision, gain: float, alias: str) -> str:
        parts = []
        if decision.harvest:
            parts.append(f"picks {decision.harvest}")
        if decision.clean:
            parts.append(f"cleans the river with {decision.clean}")
        if not parts:
            parts.append("rests")
        return f"{alias} {' and '.join(parts)} — +{gain:.1f}"

    def series(self, state: dict, config: Any) -> dict[str, float]:
        return {"total": round(state["apples"], 3), "maintenance": round(state["pollution"], 3)}

    def schema_line(self, config: Any) -> str:
        cap = config.effort_budget
        return (
            f'{{"harvest": <int 0..{cap}>, "clean": <int 0..{cap}>, '
            '"sanction": <cog slot int or null>, "message": "<one public line>", '
            '"note": "<private reminder to yourself>"}'
            f" — harvest + clean must not exceed {cap}."
        )

    def rules_text(self, config: Any) -> str:
        sustainable = (
            config.regrowth_rate * (1.0 - config.pollution_start) * config.stock_capacity / 4.0
        )
        return (
            f"There is ONE orchard, currently around {config.stock_start:.0f} apples out of a "
            f"capacity of {config.stock_capacity:.0f}, and ONE river.\n"
            f"- `harvest` takes apples; over-demand splits what is there pro-rata.\n"
            f"- Apples regrow by {config.regrowth_rate} x (1 - pollution) x apples x "
            f"(1 - apples/{config.stock_capacity:.0f}) every round.\n"
            f"- Pollution rises {config.silt_rate} every round no matter what; each `clean` "
            f"effort unit removes {config.clean_power}. Holding it steady costs "
            f"{config.silt_rate / config.clean_power:.1f} clean units a round across the whole group.\n"
            f"- Cleaning pays you NOTHING. It only keeps the apples regrowing, for everyone.\n"
            f"- If apples ever fall below {config.collapse_threshold:.0f}, the orchard is DEAD "
            f"FOREVER: no regrowth for the rest of the game.\n"
            f"- The sustainable total for the whole group is about {sustainable:.1f} apples a "
            f"round, i.e. about {sustainable / max(1, config.num_agents):.1f} each."
        )

    def planner_optimum(self, config: Any) -> float:
        """Exact 2-D DP over discretised (apples, pollution).

        The planner picks ONE aggregate effort split per round: how many of the
        N x effort_budget units go to harvesting and how many to cleaning.
        Terminal value is the residual orchard.
        """
        import numpy as np  # noqa: PLC0415  # numpy is an image/test dependency

        apple_step = 0.5
        poll_step = 0.01
        apples = np.arange(0.0, config.stock_capacity + apple_step, apple_step)
        polls = np.arange(0.0, 1.0 + poll_step, poll_step)
        budget = config.num_agents * config.effort_budget

        grid_a, grid_p = np.meshgrid(apples, polls, indexing="ij")
        value = grid_a.copy()  # after the last round, welfare is the residual stock

        actions = [
            (harvest, clean)
            for harvest in range(budget + 1)
            for clean in range(budget + 1 - harvest)
        ]

        def index_a(values: "np.ndarray") -> "np.ndarray":
            return np.clip(np.rint(values / apple_step).astype(int), 0, len(apples) - 1)

        def index_p(values: "np.ndarray") -> "np.ndarray":
            return np.clip(np.rint(values / poll_step).astype(int), 0, len(polls) - 1)

        for _ in range(config.rounds):
            best = None
            for harvest, clean in actions:
                taken = np.minimum(float(harvest), grid_a)
                remaining = grid_a - taken
                next_poll = np.clip(
                    grid_p + config.silt_rate - config.clean_power * clean, 0.0, 1.0
                )
                dead = remaining < config.collapse_threshold
                grown = np.minimum(
                    config.stock_capacity,
                    remaining
                    + config.regrowth_rate
                    * (1.0 - next_poll)
                    * remaining
                    * (1.0 - remaining / config.stock_capacity),
                )
                next_a = np.where(dead, remaining, grown)
                future = np.where(dead, remaining, value[index_a(next_a), index_p(next_poll)])
                total = taken + future
                best = total if best is None else np.maximum(best, total)
            value = best

        start_a = int(round(config.stock_start / apple_step))
        start_p = int(round(config.pollution_start / poll_step))
        start_a = min(max(start_a, 0), len(apples) - 1)
        start_p = min(max(start_p, 0), len(polls) - 1)
        return float(value[start_a, start_p])
