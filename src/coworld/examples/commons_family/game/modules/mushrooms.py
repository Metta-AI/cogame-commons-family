"""Externality Mushrooms: the instant-externality control case.

Red pays only the eater, green pays the whole group, blue pays everyone except
the eater. Eating freezes you for as many rounds as you ate, so gorging costs
turns. Nothing here can collapse permanently: this is the module where the
institutions have the least excuse.
"""

from __future__ import annotations

import math
from typing import Any

from coworld.examples.commons_family.game.modules.base import (
    COLORS,
    Decision,
    Module,
    clamp_color,
    clamp_int,
    pro_rata,
)


class MushroomsModule(Module):
    name = "mushrooms"

    def new_state(self, config: Any, deal: dict) -> dict[str, Any]:
        return {
            "counts": {color: float(count) for color, count in zip(COLORS, config.mushroom_start)},
            "eaten_total": {color: 0.0 for color in COLORS},
            "frozen_until": [0] * config.num_agents,
        }

    def parse_decision(self, raw: object, slot: int, config: Any, state: dict) -> Decision:
        if not isinstance(raw, dict):
            return Decision()
        return Decision(
            eat=clamp_int(raw.get("eat", 0), 0, config.effort_budget),
            eat_color=clamp_color(raw.get("eat_color"), "red"),
        )

    def _values(self, config: Any) -> dict[str, float]:
        return {
            "red": config.red_value,
            "green": config.green_value,
            "blue": config.blue_value,
        }

    def resolve(
        self, state: dict, decisions: list[Decision], config: Any, r: int
    ) -> tuple[list[float], list[float], list[dict]]:
        n = config.num_agents
        events: list[dict] = []
        live = [0.0] * len(decisions)
        for slot, decision in enumerate(decisions):
            if r < state["frozen_until"][slot]:
                if decision.eat:
                    events.append(
                        {
                            "kind": "digesting",
                            "r": r,
                            "slot": slot,
                            "text": "Still digesting — the bite does not happen.",
                        }
                    )
                continue
            live[slot] = float(decision.eat)

        eaten: list[dict[str, float]] = [{color: 0.0 for color in COLORS} for _ in decisions]
        for color in COLORS:
            slots = [slot for slot in range(len(decisions)) if decisions[slot].eat_color == color]
            demands = [live[slot] for slot in slots]
            if not any(demands):
                continue
            paid = pro_rata(demands, state["counts"][color])
            state["counts"][color] = max(0.0, state["counts"][color] - sum(paid))
            for slot, amount in zip(slots, paid):
                eaten[slot][color] = amount

        gains = [0.0] * len(decisions)
        flow: list[dict] = []
        values = self._values(config)
        for slot in range(len(decisions)):
            for color in COLORS:
                k = eaten[slot][color]
                if k <= 0.0:
                    continue
                if color == "red":
                    gains[slot] += values["red"] * k
                    flow.append(
                        {"from": slot, "to": slot, "amount": round(values["red"] * k, 3), "kind": "red"}
                    )
                elif color == "green":
                    share = values["green"] * k / n
                    for other in range(n):
                        gains[other] += share
                        flow.append(
                            {"from": slot, "to": other, "amount": round(share, 3), "kind": "green"}
                        )
                else:
                    share = values["blue"] * k / (n - 1) if n > 1 else 0.0
                    for other in range(n):
                        if other == slot:
                            continue
                        gains[other] += share
                        flow.append(
                            {"from": slot, "to": other, "amount": round(share, 3), "kind": "blue"}
                        )

        for slot in range(len(decisions)):
            total = sum(eaten[slot].values())
            if total > 0.0:
                state["frozen_until"][slot] = r + int(math.ceil(total))
        for color in COLORS:
            state["eaten_total"][color] += sum(row[color] for row in eaten)

        events.append(
            {
                "kind": "resolve",
                "r": r,
                "extracted": round(sum(sum(row.values()) for row in eaten), 3),
                "flow": flow,
                "text": (
                    f"{sum(sum(row.values()) for row in eaten):.1f} mushrooms eaten; "
                    f"{sum(entry['amount'] for entry in flow):.1f} of value paid out."
                ),
            }
        )
        return gains, [sum(row.values()) for row in eaten], events

    def dynamics(self, state: dict, config: Any, r: int) -> list[dict]:
        weights = {color: 1.0 + state["eaten_total"][color] for color in COLORS}
        total_weight = sum(weights.values())
        exact = {color: config.spawn_per_round * weights[color] / total_weight for color in COLORS}
        alloc = {color: int(math.floor(exact[color])) for color in COLORS}
        remainder = config.spawn_per_round - sum(alloc.values())
        order = sorted(COLORS, key=lambda color: (-(exact[color] - alloc[color]), COLORS.index(color)))
        for color in order[:remainder]:
            alloc[color] += 1

        for color in COLORS:
            state["counts"][color] = min(
                float(config.mushroom_color_cap), state["counts"][color] + alloc[color]
            )
        excess = sum(state["counts"].values()) - config.mushroom_capacity
        if excess > 0:
            for color in sorted(
                COLORS, key=lambda color: (-state["counts"][color], COLORS.index(color))
            ):
                if excess <= 0:
                    break
                drop = min(excess, state["counts"][color])
                state["counts"][color] -= drop
                excess -= drop
        return []

    def public_state(self, state: dict, config: Any, aliases: list[str]) -> dict[str, Any]:
        return {
            "counts": {color: round(value, 3) for color, value in state["counts"].items()},
            "eaten_total": {color: round(value, 3) for color, value in state["eaten_total"].items()},
            "frozen_until": list(state["frozen_until"]),
            "capacity": config.mushroom_capacity,
            "color_cap": config.mushroom_color_cap,
            "spawn_per_round": config.spawn_per_round,
        }

    def observe(
        self, state: dict, config: Any, slot: int, aliases: list[str], r: int
    ) -> dict[str, Any]:
        public = self.public_state(state, config, aliases)
        mine = state["frozen_until"][slot]
        public["frozen_until"] = mine
        public["you_may_eat"] = mine <= r
        n = config.num_agents
        public["payoff"] = {
            "red": f"{config.red_value} to you",
            "green": f"{config.green_value} split among all {n}",
            "blue": f"{config.blue_value} split among the other {n - 1}",
        }
        return public

    def residual_value(self, state: dict) -> float:
        return float(sum(state["counts"].values()))

    def public_effort(self, decision: Decision, config: Any) -> int:
        return decision.eat if decision.eat_color in ("green", "blue") else 0

    def compact(self, decision: Decision) -> str:
        return f"e:{decision.eat_color[0]}{decision.eat}" if decision.eat else "-"

    def describe(self, decision: Decision, gain: float, alias: str) -> str:
        if not decision.eat:
            return f"{alias} eats nothing — +{gain:.1f}"
        return f"{alias} eats {decision.eat} {decision.eat_color} — +{gain:.1f}"

    def series(self, state: dict, config: Any) -> dict[str, float]:
        total = sum(state["counts"].values())
        public_share = (state["counts"]["green"] + state["counts"]["blue"]) / total if total else 0.0
        return {"total": round(total, 3), "maintenance": round(public_share, 3)}

    def schema_line(self, config: Any) -> str:
        return (
            f'{{"eat": <int 0..{config.effort_budget}>, "eat_color": "red|green|blue", '
            '"sanction": <cog slot int or null>, "message": "<one public line>", '
            '"note": "<private reminder to yourself>"}'
        )

    def rules_text(self, config: Any) -> str:
        n = config.num_agents
        return (
            f"Red, green and blue mushrooms grow in one patch (at most "
            f"{config.mushroom_color_cap} of a colour, {config.mushroom_capacity} in total).\n"
            f"- RED pays {config.red_value} to YOU alone.\n"
            f"- GREEN pays {config.green_value} split equally among all {n} cogs "
            f"({config.green_value / n:.2f} each, you included).\n"
            f"- BLUE pays {config.blue_value} split among the {n - 1} cogs who did NOT eat it "
            f"({config.blue_value / (n - 1):.2f} each). The eater gets nothing.\n"
            f"- Eating k mushrooms freezes you for k rounds: eat 1 and you can eat again next "
            f"round, eat 3 and you skip two. A frozen cog may still talk and sanction.\n"
            f"- {config.spawn_per_round} new mushrooms appear each round, weighted towards the "
            f"colours that have been eaten most.\n"
            f"- Blue is worth the most to the group; red is worth the most to you."
        )

    def planner_optimum(self, config: Any) -> float:
        """DP over the three mushroom counts, under the never-freeze schedule.

        Welfare per mushroom is 1/2/3 by colour no matter who is paid, and a
        seat that eats more than one loses turns, so an optimal planner has
        every seat eat at most one per round and nobody is ever frozen. The
        state is then just the three counts and the DP is exact over that
        schedule; `grade.scale` says which schedule it is.
        """
        import numpy as np  # noqa: PLC0415

        size = int(config.mushroom_color_cap) + 1
        seats = config.num_agents
        values = self._values(config)
        shape = (size, size, size)

        # Where each post-eat state lands after the spawn + caps, as a flat index.
        grown_flat = np.zeros(shape, dtype=np.int64)
        for red in range(size):
            for green in range(size):
                for blue in range(size):
                    key = self._spawn_key((red, green, blue), config)
                    grown_flat[red, green, blue] = (key[0] * size + key[1]) * size + key[2]

        actions = [
            (eat_red, eat_green, eat_blue)
            for eat_red in range(seats + 1)
            for eat_green in range(seats + 1 - eat_red)
            for eat_blue in range(seats + 1 - eat_red - eat_green)
        ]
        table = []
        for eat_red, eat_green, eat_blue in actions:
            target = np.zeros(shape, dtype=np.int64)
            legal = np.zeros(shape, dtype=bool)
            target[eat_red:, eat_green:, eat_blue:] = grown_flat[
                : size - eat_red, : size - eat_green, : size - eat_blue
            ]
            legal[eat_red:, eat_green:, eat_blue:] = True
            gain = (
                values["red"] * eat_red
                + values["green"] * eat_green
                + values["blue"] * eat_blue
            )
            table.append((target, legal, gain))

        counts = np.indices(shape).sum(axis=0).astype(float)
        value = counts
        for _ in range(config.rounds):
            flat = value.ravel()
            best = None
            for target, legal, gain in table:
                candidate = np.where(legal, gain + flat[target], -np.inf)
                best = candidate if best is None else np.maximum(best, candidate)
            value = best

        start = tuple(min(int(count), size - 1) for count in config.mushroom_start)
        return float(value[start])

    def _spawn_key(self, counts: tuple[int, int, int], config: Any) -> tuple[int, int, int]:
        """Planner-side spawn: every new mushroom in the most valuable colour.

        Spawn weights follow appetite (`w[c] = 1 + eaten_total[c]`), so a diet
        concentrated on one colour drives the spawn to that colour; the planner
        is therefore granted the most valuable spawn its own diet could induce,
        spilling to the next colour as caps bind. That keeps this DP an UPPER
        bound on any real episode's welfare, which is what a grading
        denominator has to be.
        """
        values = self._values(config)
        order = sorted(range(3), key=lambda index: (-values[COLORS[index]], index))
        alloc = [0, 0, 0]
        left = config.spawn_per_round
        for index in order:
            room = max(0, config.mushroom_color_cap - counts[index])
            take = min(left, room)
            alloc[index] = take
            left -= take
        grown = [
            min(config.mushroom_color_cap, counts[index] + alloc[index]) for index in range(3)
        ]
        excess = sum(grown) - config.mushroom_capacity
        if excess > 0:
            for index in sorted(range(3), key=lambda i: (-grown[i], i)):
                if excess <= 0:
                    break
                drop = min(excess, grown[index])
                grown[index] -= drop
                excess -= drop
        return (grown[0], grown[1], grown[2])
