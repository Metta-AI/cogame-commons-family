"""The resource-module protocol.

The institutional layer (ledger, sanctions, posted norm, chat) lives in
`game/engine.py` and is identical for every module. Everything that differs
between Clean Up, Commons Harvest, Allelopathic Harvest and Externality
Mushrooms is resource *physics*, and physics lives here: one class per module,
registered by name, driven entirely by the engine's round loop.

A module never touches time, sockets or artifacts, and never reads the RNG
except in `new_state`. Every resolution rule is a closed-form function of the
decisions, so two runs with the same seed and the same decisions produce
identical state.
"""

from __future__ import annotations

from typing import Any

COLORS = ("red", "green", "blue")
"""Canonical colour order. Every tie in the game breaks in this order."""


class Decision:
    """One seat's validated decision for one round.

    The union of every module's fields; a module reads only its own. `src`
    records where the decision came from and is the only field the replay's
    `decision` event needs beyond the numbers — and it is load-bearing in one
    place: `src == "pass"` means the seat did not answer at all, so `harvest`
    does not count its default `patch` as a patch it named.
    """

    __slots__ = (
        "harvest",
        "clean",
        "patch",
        "eat",
        "eat_color",
        "plant",
        "plant_color",
        "sanction",
        "message",
        "note",
        "src",
    )

    def __init__(
        self,
        harvest: int = 0,
        clean: int = 0,
        patch: int = 0,
        eat: int = 0,
        eat_color: str = "red",
        plant: int = 0,
        plant_color: str = "red",
        sanction: int | None = None,
        message: str | None = None,
        note: str | None = None,
        src: str = "",
    ) -> None:
        self.harvest = harvest
        self.clean = clean
        self.patch = patch
        self.eat = eat
        self.eat_color = eat_color
        self.plant = plant
        self.plant_color = plant_color
        self.sanction = sanction
        self.message = message
        self.note = note
        self.src = src

    def to_json(self, slot: int) -> dict[str, Any]:
        """The replay's per-decision record. `note` is private and never here."""
        return {
            "slot": slot,
            "harvest": self.harvest,
            "clean": self.clean,
            "eat": self.eat,
            "eat_color": self.eat_color,
            "plant": self.plant,
            "plant_color": self.plant_color,
            "patch": self.patch,
            "sanction": self.sanction,
            "message": self.message or "",
            "src": self.src,
        }


def clamp_int(raw: object, low: int, high: int, default: int = 0) -> int:
    """Coerce anything to an int inside [low, high]; nonsense becomes `default`."""
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return default
    try:
        value = int(raw)
    except (ValueError, OverflowError):
        return default
    return min(max(value, low), high)


def clamp_color(raw: object, default: str = "red") -> str:
    if isinstance(raw, str) and raw.strip().lower() in COLORS:
        return raw.strip().lower()
    return default


def pro_rata(demands: list[float], available: float) -> list[float]:
    """Split `available` over `demands`, in full when it fits, pro-rata when not."""
    total = sum(demands)
    if total <= 0.0:
        return [0.0] * len(demands)
    if total <= available:
        return list(demands)
    scale = available / total
    return [demand * scale for demand in demands]


class Module:
    """Base class: the seven hooks the engine calls, in the order it calls them."""

    name = "base"

    def new_state(self, config: Any, deal: dict) -> dict[str, Any]:
        raise NotImplementedError

    def parse_decision(self, raw: object, slot: int, config: Any, state: dict) -> Decision:
        raise NotImplementedError

    def resolve(
        self, state: dict, decisions: list[Decision], config: Any, r: int
    ) -> tuple[list[float], list[float], list[dict]]:
        """Settle the extractive step.

        Returns `(gains, extracted, events)`: what each seat is PAID, what each
        seat physically removed from the resource (the two differ in
        `mushrooms`, where a blue bite pays everyone else), and the module's
        events for this round.
        """
        raise NotImplementedError

    def dynamics(self, state: dict, config: Any, r: int) -> list[dict]:
        raise NotImplementedError

    def observe(
        self, state: dict, config: Any, slot: int, aliases: list[str], r: int
    ) -> dict[str, Any]:
        raise NotImplementedError

    def public_state(self, state: dict, config: Any, aliases: list[str]) -> dict[str, Any]:
        """The module state as the replay and the /global snapshot carry it."""
        raise NotImplementedError

    def residual_value(self, state: dict) -> float:
        raise NotImplementedError

    def planner_optimum(self, config: Any) -> float:
        raise NotImplementedError

    def seat_info(self, state: dict, config: Any, slot: int) -> dict[str, Any]:
        """Per-seat facts the replay's `seats[]` carries (favourite, patches)."""
        return {"favorite": "", "patches": []}

    def public_effort(self, decision: Decision, config: Any) -> int:
        """Effort units this decision spent on the module's maintenance act."""
        raise NotImplementedError

    def compact(self, decision: Decision) -> str:
        """The one-token ledger notation for `recent`."""
        raise NotImplementedError

    def describe(self, decision: Decision, gain: float, alias: str) -> str:
        """Spectator English for the feed, e.g. "Cog-C eats 2 green — +2.0"."""
        raise NotImplementedError

    def series(self, state: dict, config: Any) -> dict[str, float]:
        """The chart's two traces at this instant: primary resource, maintenance."""
        raise NotImplementedError

    def schema_line(self, config: Any) -> str:
        """The reply schema, as the LLM system prompt states it."""
        raise NotImplementedError

    def rules_text(self, config: Any) -> str:
        """The module physics in words, with this episode's actual numbers."""
        raise NotImplementedError
