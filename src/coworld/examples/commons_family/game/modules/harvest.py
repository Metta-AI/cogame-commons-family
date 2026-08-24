"""Commons Harvest: six independent patches, and property rights as an A/B.

A patch regrows logistically only while it keeps at least one apple. Strip it
and it is dead forever — a tombstone in the viewer and nothing else, for the
rest of the episode. `property_rights` decides who may name which patch:

- `open`        — anyone may name any patch. The pure commons.
- `closed`      — one patch per seat, publicly assigned. Single-cog excludability.
- `partnership` — two patches per seeded pair; a patch pays only if BOTH
                  partners name it this round.
"""

from __future__ import annotations

from typing import Any

from coworld.examples.commons_family.game.modules.base import (
    Decision,
    Module,
    clamp_int,
    pro_rata,
)


class HarvestModule(Module):
    name = "harvest"

    def new_state(self, config: Any, deal: dict) -> dict[str, Any]:
        # The engine draws the patch deal (the third and last draw of the
        # episode) so the RNG is consumed in the same order whatever module is
        # running; both the closed-room deal and the partnership deal come out
        # of that one permutation.
        patch_deal = list(deal["patch_deal"])
        owner = [patch_deal[patch] % config.num_agents for patch in range(config.patch_count)]
        pairs: list[list[int]] = []
        for index in range(0, config.patch_count, 2):
            left = patch_deal[index] % config.num_agents
            right = (
                patch_deal[index + 1] % config.num_agents
                if index + 1 < len(patch_deal)
                else left
            )
            pairs.append([left, right])
        return {
            "stocks": [float(config.patch_start)] * config.patch_count,
            "dead": [False] * config.patch_count,
            "owner": owner,
            "pairs": pairs,
            "property_rights": config.property_rights,
        }

    # -- who may name what ---------------------------------------------------

    def allowed_patches(self, state: dict, config: Any, slot: int) -> list[int]:
        rights = state["property_rights"]
        if rights == "closed":
            return [p for p in range(config.patch_count) if state["owner"][p] == slot]
        if rights == "partnership":
            return [p for p in range(config.patch_count) if slot in self._pair_of(state, p)]
        return list(range(config.patch_count))

    def _pair_of(self, state: dict, patch: int) -> list[int]:
        return state["pairs"][patch // 2]

    # -- the round -----------------------------------------------------------

    def parse_decision(self, raw: object, slot: int, config: Any, state: dict) -> Decision:
        if not isinstance(raw, dict):
            return Decision()
        return Decision(
            harvest=clamp_int(raw.get("harvest", 0), 0, config.effort_budget),
            patch=clamp_int(raw.get("patch", 0), 0, config.patch_count - 1),
        )

    def resolve(
        self, state: dict, decisions: list[Decision], config: Any, r: int
    ) -> tuple[list[float], list[float], list[dict]]:
        events: list[dict] = []
        rights = state["property_rights"]
        live_demand: list[float] = [0.0] * len(decisions)

        # A seat that did not answer names NOTHING. Its decision is the
        # all-zero default, and counting that as "holding patch 0" would let a
        # disconnected seat's partner harvest patch 0 alone every round while
        # patch 1 could never be held at all.
        answered = [decision.src != "pass" for decision in decisions]

        named: dict[int, set[int]] = {}
        for slot, decision in enumerate(decisions):
            if answered[slot]:
                named.setdefault(decision.patch, set()).add(slot)

        for slot, decision in enumerate(decisions):
            if not answered[slot]:
                continue
            patch = decision.patch
            if state["dead"][patch]:
                if decision.harvest:
                    events.append(
                        {
                            "kind": "void",
                            "r": r,
                            "slot": slot,
                            "cause": "dead",
                            "patch": patch,
                            "text": f"Patch {patch} is bare ground; the demand yields nothing.",
                        }
                    )
                continue
            if rights == "closed" and state["owner"][patch] != slot:
                events.append(
                    {
                        "kind": "trespass",
                        "r": r,
                        "slot": slot,
                        "patch": patch,
                        "text": f"Patch {patch} is not this cog's to pick — nothing taken.",
                    }
                )
                continue
            if rights == "partnership":
                partners = self._pair_of(state, patch)
                if not all(partner in named.get(patch, set()) for partner in partners):
                    events.append(
                        {
                            "kind": "unheld",
                            "r": r,
                            "slot": slot,
                            "patch": patch,
                            "text": f"Patch {patch} was not held by both partners — nothing taken.",
                        }
                    )
                    continue
            live_demand[slot] = float(decision.harvest)

        gains = [0.0] * len(decisions)
        for patch in range(config.patch_count):
            slots = [slot for slot in range(len(decisions)) if decisions[slot].patch == patch]
            demands = [live_demand[slot] for slot in slots]
            if not any(demands):
                continue
            paid = pro_rata(demands, state["stocks"][patch])
            for slot, amount in zip(slots, paid):
                gains[slot] = amount
            state["stocks"][patch] = max(0.0, state["stocks"][patch] - sum(paid))

        events.append(
            {
                "kind": "resolve",
                "r": r,
                "extracted": round(sum(gains), 3),
                "text": f"{sum(gains):.1f} apples taken across the patches.",
            }
        )
        return gains, list(gains), events

    def dynamics(self, state: dict, config: Any, r: int) -> list[dict]:
        events: list[dict] = []
        for patch in range(config.patch_count):
            if state["dead"][patch]:
                continue
            stock = state["stocks"][patch]
            if stock < 1.0:
                state["stocks"][patch] = 0.0
                state["dead"][patch] = True
                events.append(
                    {
                        "kind": "patch_dead",
                        "r": r,
                        "patch": patch,
                        "text": f"Patch {patch} stripped bare — it will never grow again.",
                    }
                )
                continue
            grown = stock + config.patch_regrowth * stock * (1.0 - stock / config.patch_capacity)
            state["stocks"][patch] = min(config.patch_capacity, grown)
        return events

    # -- views ---------------------------------------------------------------

    def public_state(self, state: dict, config: Any, aliases: list[str]) -> dict[str, Any]:
        return {
            "property_rights": state["property_rights"],
            "patch_capacity": config.patch_capacity,
            "patch_regrowth": config.patch_regrowth,
            "patches": [
                {
                    "id": patch,
                    "stock": round(state["stocks"][patch], 3),
                    "dead": state["dead"][patch],
                    "holders": self._holders(state, aliases, patch),
                }
                for patch in range(config.patch_count)
            ],
            "dead_patches": [p for p in range(config.patch_count) if state["dead"][p]],
        }

    def observe(
        self, state: dict, config: Any, slot: int, aliases: list[str], r: int
    ) -> dict[str, Any]:
        public = self.public_state(state, config, aliases)
        rights = state["property_rights"]
        public["your_patches"] = (
            [] if rights == "open" else self.allowed_patches(state, config, slot)
        )
        return public

    def _holders(self, state: dict, aliases: list[str], patch: int) -> list[str]:
        rights = state["property_rights"]
        if rights == "closed":
            return [aliases[state["owner"][patch]]]
        if rights == "partnership":
            return [aliases[slot] for slot in self._pair_of(state, patch)]
        return []

    def seat_info(self, state: dict, config: Any, slot: int) -> dict[str, Any]:
        if state["property_rights"] == "open":
            return {"favorite": "", "patches": []}
        return {"favorite": "", "patches": self.allowed_patches(state, config, slot)}

    def residual_value(self, state: dict) -> float:
        return float(sum(state["stocks"]))

    def public_effort(self, decision: Decision, config: Any) -> int:
        # The maintenance act here is restraint: every effort unit NOT spent
        # demanding is an apple left in a patch, which is the only thing that
        # keeps the patch alive.
        return max(0, config.effort_budget - decision.harvest)

    def compact(self, decision: Decision) -> str:
        return f"p{decision.patch} h:{decision.harvest}"

    def describe(self, decision: Decision, gain: float, alias: str) -> str:
        if decision.harvest == 0:
            return f"{alias} holds patch {decision.patch} and takes nothing — +{gain:.1f}"
        return f"{alias} takes {decision.harvest} from patch {decision.patch} — +{gain:.1f}"

    def series(self, state: dict, config: Any) -> dict[str, float]:
        return {
            "total": round(sum(state["stocks"]), 3),
            "maintenance": float(sum(1 for dead in state["dead"] if dead)),
        }

    def schema_line(self, config: Any) -> str:
        return (
            f'{{"patch": <int 0..{config.patch_count - 1}>, '
            f'"harvest": <int 0..{config.effort_budget}>, '
            '"sanction": <cog slot int or null>, "message": "<one public line>", '
            '"note": "<private reminder to yourself>"}'
        )

    def rules_text(self, config: Any) -> str:
        sustainable = config.patch_regrowth * config.patch_capacity / 4.0
        rights = {
            "open": "Any cog may name any patch.",
            "closed": (
                "Each patch belongs to one cog, and the assignment is public. A demand on a "
                "patch you do not own yields NOTHING."
            ),
            "partnership": (
                "Patches are dealt to pairs, publicly. A patch pays this round only if BOTH "
                "partners name it this round; either partner may demand 0 and still be holding it."
            ),
        }[config.property_rights]
        return (
            f"There are {config.patch_count} apple patches, each holding up to "
            f"{config.patch_capacity:.0f} apples.\n"
            f"- `patch` names the one patch you pick from this round; `harvest` is how much.\n"
            f"- A live patch regrows by {config.patch_regrowth} x stock x "
            f"(1 - stock/{config.patch_capacity:.0f}) every round.\n"
            f"- A patch left with LESS THAN 1 apple is DEAD FOREVER. It never regrows.\n"
            f"- Sustainable per patch is about {sustainable:.1f} a round, so about "
            f"{sustainable * config.patch_count:.0f} across all patches for "
            f"{config.num_agents} cogs.\n"
            f"- Property rights: {rights}"
        )

    def planner_optimum(self, config: Any) -> float:
        """Per-patch 1-D DP, summed.

        Exact here because the planner's optimal aggregate demand (~12 a round)
        never reaches the society's 18-unit effort cap, so the patches do not
        compete for effort and each one can be solved on its own.
        """
        import numpy as np  # noqa: PLC0415

        step = 0.05
        stocks = np.arange(0.0, config.patch_capacity + step, step)
        budget = config.num_agents * config.effort_budget
        value = stocks.copy()

        def index(values: "np.ndarray") -> "np.ndarray":
            return np.clip(np.rint(values / step).astype(int), 0, len(stocks) - 1)

        for _ in range(config.rounds):
            best = None
            for demand in range(budget + 1):
                taken = np.minimum(float(demand), stocks)
                remaining = stocks - taken
                dead = remaining < 1.0
                grown = np.minimum(
                    config.patch_capacity,
                    remaining
                    + config.patch_regrowth * remaining * (1.0 - remaining / config.patch_capacity),
                )
                future = np.where(dead, 0.0, value[index(grown)])
                total = taken + future
                best = total if best is None else np.maximum(best, total)
            value = best

        start = min(max(int(round(config.patch_start / step)), 0), len(stocks) - 1)
        return float(value[start]) * config.patch_count
