"""Allelopathic Harvest: three colours that inhibit each other.

Sixty plant slots hold three berry colours. A colour ripens in proportion to
the SQUARE of its own share of the field, so a three-way split starves
everybody and a monoculture feeds everybody. Every cog has a secret favourite
colour that pays it double — which is exactly why agreeing on one colour costs
four of the six cogs half their rate. Planting pays nothing; its price is the
effort unit it burns.
"""

from __future__ import annotations

from typing import Any

from coworld.examples.commons_family.game.modules.base import (
    COLORS,
    Decision,
    Module,
    clamp_color,
    clamp_int,
    pro_rata,
)


class AllelopathicModule(Module):
    name = "allelopathic"

    def new_state(self, config: Any, deal: dict) -> dict[str, Any]:
        return {
            "planted": {color: int(count) for color, count in zip(COLORS, config.planted_start)},
            "ripe": {color: float(count) for color, count in zip(COLORS, config.ripe_start)},
            "favorites": list(deal["favorites"]),
            "barren": False,
        }

    def parse_decision(self, raw: object, slot: int, config: Any, state: dict) -> Decision:
        if not isinstance(raw, dict):
            return Decision()
        eat = clamp_int(raw.get("eat", 0), 0, config.effort_budget)
        plant = clamp_int(raw.get("plant", 0), 0, config.effort_budget)
        eat_color = clamp_color(raw.get("eat_color"), "red")
        plant_color = clamp_color(raw.get("plant_color"), eat_color)
        if eat + plant > config.effort_budget:
            plant = max(0, config.effort_budget - eat)
        return Decision(eat=eat, eat_color=eat_color, plant=plant, plant_color=plant_color)

    def resolve(
        self, state: dict, decisions: list[Decision], config: Any, r: int
    ) -> tuple[list[float], list[float], list[dict]]:
        for decision in decisions:
            if decision.eat + decision.plant > config.effort_budget:
                decision.plant = max(0, config.effort_budget - decision.eat)

        gains = [0.0] * len(decisions)
        extracted = [0.0] * len(decisions)
        eaten_total = 0.0
        for color in COLORS:
            slots = [
                slot
                for slot, decision in enumerate(decisions)
                if decision.eat_color == color and decision.eat > 0
            ]
            if not slots:
                continue
            demands = [float(decisions[slot].eat) for slot in slots]
            berries = pro_rata(demands, state["ripe"][color])
            state["ripe"][color] = max(0.0, state["ripe"][color] - sum(berries))
            for slot, amount in zip(slots, berries):
                bonus = (
                    config.favorite_bonus
                    if state["favorites"][slot] == color
                    else config.favorite_base
                )
                gains[slot] += amount * bonus
                extracted[slot] += amount
                eaten_total += amount

        # Planting, ascending slot, one unit at a time: each unit pulls a slot
        # from the currently largest OTHER colour, and a converted slot takes
        # its ripe berry with it.
        planted_units = 0
        for slot, decision in enumerate(decisions):
            target = decision.plant_color
            for _ in range(decision.plant):
                sources = [color for color in COLORS if color != target]
                sources.sort(key=lambda color: (-state["planted"][color], COLORS.index(color)))
                source = sources[0]
                if state["planted"][source] <= 0:
                    continue
                state["planted"][source] -= 1
                state["planted"][target] += 1
                if state["ripe"][source] > state["planted"][source]:
                    state["ripe"][source] = float(state["planted"][source])
                planted_units += 1

        events = [
            {
                "kind": "resolve",
                "r": r,
                "extracted": round(eaten_total, 3),
                "planted": planted_units,
                "text": (
                    f"{eaten_total:.1f} berries eaten, {planted_units} slots replanted."
                ),
            }
        ]
        return gains, extracted, events

    def dynamics(self, state: dict, config: Any, r: int) -> list[dict]:
        for color in COLORS:
            planted = state["planted"][color]
            ripened = state["ripe"][color] + config.ripen_base * planted * planted / config.field_size
            state["ripe"][color] = min(float(planted), ripened)
        total = sum(state["ripe"].values())
        if total <= 0.0:
            state["barren"] = True
            return [
                {
                    "kind": "barren",
                    "r": r,
                    "text": "Not one ripe berry in the whole field.",
                }
            ]
        state["barren"] = False
        return []

    def public_state(self, state: dict, config: Any, aliases: list[str]) -> dict[str, Any]:
        return {
            "planted": dict(state["planted"]),
            "ripe": {color: round(value, 3) for color, value in state["ripe"].items()},
            "field_size": config.field_size,
            "ripen_base": config.ripen_base,
            "favorite_bonus": config.favorite_bonus,
            "barren": state["barren"],
        }

    def observe(
        self, state: dict, config: Any, slot: int, aliases: list[str], r: int
    ) -> dict[str, Any]:
        public = self.public_state(state, config, aliases)
        # A cog's own favourite is in its own observation and NOWHERE else.
        public["your_favorite"] = state["favorites"][slot]
        return public

    def seat_info(self, state: dict, config: Any, slot: int) -> dict[str, Any]:
        return {"favorite": state["favorites"][slot], "patches": []}

    def residual_value(self, state: dict) -> float:
        return float(sum(state["ripe"].values()))

    def public_effort(self, decision: Decision, config: Any) -> int:
        return decision.plant

    def compact(self, decision: Decision) -> str:
        parts = []
        if decision.eat:
            parts.append(f"e:{decision.eat_color[0]}{decision.eat}")
        if decision.plant:
            parts.append(f"p:{decision.plant_color[0]}{decision.plant}")
        return " ".join(parts) or "-"

    def describe(self, decision: Decision, gain: float, alias: str) -> str:
        parts = []
        if decision.eat:
            parts.append(f"eats {decision.eat} {decision.eat_color}")
        if decision.plant:
            parts.append(f"plants {decision.plant} {decision.plant_color}")
        if not parts:
            parts.append("does nothing")
        return f"{alias} {', '.join(parts)} — +{gain:.1f}"

    def series(self, state: dict, config: Any) -> dict[str, float]:
        planted = state["planted"]
        plurality = max(planted.values()) if planted else 0
        return {
            "total": round(sum(state["ripe"].values()), 3),
            "maintenance": round(plurality / config.field_size, 3),
        }

    def schema_line(self, config: Any) -> str:
        cap = config.effort_budget
        return (
            f'{{"eat": <int 0..{cap}>, "eat_color": "red|green|blue", '
            f'"plant": <int 0..{cap}>, "plant_color": "red|green|blue", '
            '"sanction": <cog slot int or null>, "message": "<one public line>", '
            '"note": "<private reminder to yourself>"}'
            f" — eat + plant must not exceed {cap}."
        )

    def rules_text(self, config: Any) -> str:
        split = config.ripen_base * (config.field_size / 3) ** 2 / config.field_size * 3
        mono = config.ripen_base * config.field_size
        return (
            f"The field has {config.field_size} plant slots holding red, green and blue berries.\n"
            f"- `eat` takes ripe berries of `eat_color`; over-demand splits them pro-rata.\n"
            f"- A berry pays you {config.favorite_base} normally and "
            f"{config.favorite_bonus} if it is YOUR secret favourite colour. Nobody else "
            f"knows your favourite; you do not know theirs.\n"
            f"- Every round each colour ripens by {config.ripen_base} x planted^2 / "
            f"{config.field_size}. That is QUADRATIC in the colour's share: an even "
            f"three-way split yields about {split:.0f} berries a round for the whole group, "
            f"a single-colour field yields about {mono:.0f}.\n"
            f"- `plant` converts slots to `plant_color`, taking them from the largest other "
            f"colour. Planting pays you NOTHING; it costs you the effort unit.\n"
            f"- Nothing here dies permanently, but a split field starves everyone."
        )

    def planner_optimum(self, config: Any) -> float:
        """Best-monoculture planner schedule (see the design note, v1 scope).

        A DP over the reduced state "slots planted to the target colour x ripe
        berries of that colour", maximised over the three target colours. Since
        exactly two cogs favour each colour, all three targets are symmetric at
        the default start, so the DP runs once. This is a lower bound on the
        exact joint optimum, and `grade.scale` says so.
        """
        import numpy as np  # noqa: PLC0415

        ripe_step = 1.0
        planted_axis = np.arange(0, config.field_size + 1, dtype=float)
        ripe_axis = np.arange(0.0, config.field_size + ripe_step, ripe_step)
        grid_planted, grid_ripe = np.meshgrid(planted_axis, ripe_axis, indexing="ij")
        budget = config.num_agents * config.effort_budget
        # Two cogs favour the target colour; each can eat at most effort_budget
        # berries a round at the doubled rate.
        favored_capacity = 2 * config.effort_budget

        value = grid_ripe.copy()

        def index_p(values: "np.ndarray") -> "np.ndarray":
            return np.clip(np.rint(values).astype(int), 0, len(planted_axis) - 1)

        def index_r(values: "np.ndarray") -> "np.ndarray":
            return np.clip(np.rint(values / ripe_step).astype(int), 0, len(ripe_axis) - 1)

        for _ in range(config.rounds):
            best = None
            for eat in range(budget + 1):
                berries = np.minimum(float(eat), grid_ripe)
                paid = config.favorite_bonus * np.minimum(berries, favored_capacity) + (
                    config.favorite_base * np.maximum(0.0, berries - favored_capacity)
                )
                left = grid_ripe - berries
                for plant in range(budget + 1 - eat):
                    planted = np.minimum(float(config.field_size), grid_planted + plant)
                    ripened = np.minimum(
                        planted,
                        left + config.ripen_base * planted * planted / config.field_size,
                    )
                    total = paid + value[index_p(planted), index_r(ripened)]
                    best = total if best is None else np.maximum(best, total)
            value = best

        start_planted = int(config.planted_start[0])
        start_ripe = float(config.ripe_start[0])
        return float(value[index_p(np.array(start_planted)), index_r(np.array(start_ripe))])
