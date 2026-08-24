"""Commons Family rules engine.

Pure, deterministic. No IO, no clocks: the server owns timing and artifacts,
the engine owns truth. Six cogs share one destructible resource for twenty
simultaneous rounds; the resource physics come from one of four modules
(`game/modules/`), and the institutions around them — a public ledger, costly
sanctions, a posted norm and one signed chat line per cog per round — are
meadow's, unchanged, and switched per variant.

The one deliberate change from meadow: the in-game name space is anonymous.
`observation()` takes ALIASES (`Cog-A` … `Cog-F`), never the runner's real
policy names, so a policy can never read who it is playing against.
"""

from __future__ import annotations

import datetime as dt
import random
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field, field_validator

from coworld.examples.commons_family.game.modules.allelopathic import AllelopathicModule
from coworld.examples.commons_family.game.modules.base import COLORS, Decision, Module
from coworld.examples.commons_family.game.modules.cleanup import CleanupModule
from coworld.examples.commons_family.game.modules.harvest import HarvestModule
from coworld.examples.commons_family.game.modules.mushrooms import MushroomsModule

PLAYER_PROTOCOL = "commons-family.player.v1"
REPLAY_PROTOCOL = "commons-family.replay.v1"
REPLAY_FORMAT = "commons-family/1"
COWORLD_NAME = "commons_family"
VERSION = "0.1.0"

ALIAS_LETTERS = "ABCDEFGHIJKL"
NOTE_MAX_CHARS = 200
PROMPT_MAX_RUNES = 1200
#: The posted norm comes from the manifest, not from a policy, but it reaches
#: both the system prompt and the replay's `config`, so it is truncated on rune
#: boundaries like every other string that gets that far.
NORM_MAX_RUNES = 400
RECENT_ACTIONS = 5

MODULES: dict[str, Module] = {
    "cleanup": CleanupModule(),
    "harvest": HarvestModule(),
    "allelopathic": AllelopathicModule(),
    "mushrooms": MushroomsModule(),
}

#: The complete event vocabulary. A replay may carry no other kind, and
#: `tests/test_replay_parse.py` and the wasm viewer both assert it.
EVENT_KINDS = (
    "episode_start",
    "round_open",
    "chat",
    "decision",
    "resolve",
    "sanction",
    "void",
    "trespass",
    "unheld",
    "patch_dead",
    "collapse",
    "barren",
    "digesting",
    "fallback",
    "no_submission",
    "deadline",
    "round_end",
    "episode_end",
)

#: The only legal `results.reason` values. The game emits nothing else.
END_REASONS = ("complete", "deadline", "no_players")


def truncate_runes(text: object, cap: int) -> str:
    """Trim free text to `cap` RUNES, never bytes.

    A Python `str` slice is already a code-point slice, so this is the whole
    truncator — but it has to be the ONLY one, applied to every string that can
    reach the replay (messages, notes, policy names, error text). Artifacts are
    written with `ensure_ascii=False` and encoded UTF-8 exactly once, so a half
    rune can never reach the replay bytes.
    """
    if not isinstance(text, str):
        return ""
    return text.strip()[:cap]


class CommonsConfig(BaseModel):
    """Engine-facing configuration (game_config minus the runner-owned fields)."""

    num_agents: int = Field(default=6, ge=2, le=12)
    seed: int = 20260824
    module: str = "cleanup"
    rounds: int = Field(default=20, ge=1, le=100)
    round_seconds: float = Field(default=20.0, gt=0, le=120)
    min_round_seconds: float = Field(default=3.0, ge=0, le=120)
    decision_timeout_seconds: float = Field(default=8.0, gt=0, le=120)
    episode_timeout_seconds: float = Field(default=1200.0, gt=0)
    play_budget_fraction: float = Field(default=0.6, gt=0, le=1)
    player_connect_timeout_seconds: float = Field(default=180.0, ge=0)
    effort_budget: int = Field(default=3, ge=1, le=10)

    ledger_public: bool = True
    sanctions_enabled: bool = False
    sanction_cost: float = Field(default=1.0, ge=0)
    sanction_burn: float = Field(default=3.0, ge=0)
    chat_enabled: bool = True
    chat_max_chars: int = Field(default=140, ge=1, le=1000)
    norm_text: str = ""
    fallback_scripted: str = "steward"
    llm_max_requests_per_minute: int = Field(default=120, ge=1)

    @field_validator("norm_text")
    @classmethod
    def _cap_norm_text(cls, value: str) -> str:
        return truncate_runes(value, NORM_MAX_RUNES)

    # cleanup
    stock_start: float = Field(default=60.0, ge=0)
    stock_capacity: float = Field(default=100.0, gt=0)
    regrowth_rate: float = Field(default=0.35, ge=0, le=1)
    collapse_threshold: float = Field(default=10.0, ge=0)
    pollution_start: float = Field(default=0.30, ge=0, le=1)
    silt_rate: float = Field(default=0.12, ge=0, le=1)
    clean_power: float = Field(default=0.05, ge=0, le=1)

    # harvest
    patch_count: int = Field(default=6, ge=1, le=12)
    patch_capacity: float = Field(default=20.0, gt=0)
    patch_start: float = Field(default=12.0, ge=0)
    patch_regrowth: float = Field(default=0.40, ge=0, le=1)
    property_rights: str = "open"

    # allelopathic
    field_size: int = Field(default=60, ge=3, le=600)
    ripen_base: float = Field(default=0.5, ge=0, le=10)
    planted_start: list[int] = Field(default_factory=lambda: [20, 20, 20])
    ripe_start: list[float] = Field(default_factory=lambda: [6.0, 6.0, 6.0])
    favorite_bonus: float = Field(default=2.0, ge=0)
    favorite_base: float = Field(default=1.0, ge=0)

    # mushrooms
    mushroom_start: list[int] = Field(default_factory=lambda: [8, 8, 8])
    mushroom_capacity: int = Field(default=30, ge=1)
    mushroom_color_cap: int = Field(default=15, ge=1)
    spawn_per_round: int = Field(default=3, ge=0)
    red_value: float = Field(default=1.0, ge=0)
    green_value: float = Field(default=2.0, ge=0)
    blue_value: float = Field(default=3.0, ge=0)


@dataclass
class ChatRecord:
    alias: str
    text: str


@dataclass
class RoundRecord:
    """Everything that happened in one settled round — one replay frame."""

    r: int
    state_before: dict[str, Any]
    decisions: list[dict[str, Any]]
    gains: list[float]
    extracted: list[float]
    scores: list[float]
    state_after: dict[str, Any]
    total_extracted: float
    public_effort: int
    seat_public_effort: list[int]
    collapsed: bool
    series: dict[str, float]
    seat_frozen: list[int]
    messages: list[ChatRecord]

    def to_json(self) -> dict[str, Any]:
        return {
            "r": self.r,
            "state_before": self.state_before,
            "decisions": self.decisions,
            "gains": [round(value, 3) for value in self.gains],
            "extracted": [round(value, 3) for value in self.extracted],
            "scores": [round(value, 3) for value in self.scores],
            "state_after": self.state_after,
            "total_extracted": round(self.total_extracted, 3),
            "public_effort": self.public_effort,
            "seat_public_effort": list(self.seat_public_effort),
            "collapsed": self.collapsed,
            "series": self.series,
            "seat_frozen": self.seat_frozen,
            "messages": [{"alias": m.alias, "text": m.text} for m in self.messages],
        }


@dataclass
class GameState:
    aliases: list[str]
    module_state: dict[str, Any]
    scores: list[float]
    total_extracted: list[float]
    public_effort: list[int]
    sanctions_given: list[int]
    sanctions_received: list[int]
    notes: list[str]
    recent: list[list[str]]
    fallbacks: list[int]
    disconnected: list[bool]
    round: int = 0
    collapse_round: int | None = None
    history: list[RoundRecord] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)


def module_for(config: CommonsConfig) -> Module:
    if config.module not in MODULES:
        raise ValueError(f"unknown module {config.module!r}; known: {sorted(MODULES)}")
    return MODULES[config.module]


def new_game(config: CommonsConfig) -> GameState:
    """Draw the episode's three seeded facts, then build the module state.

    ONE `random.Random(seed)` is drawn from exactly three times, in this order:
    the alias permutation, the allelopathic favourite deal, the harvest
    ownership/partnership deal. All three are drawn whatever module is running,
    so a seed means the same thing across variants. Nothing else in the sim is
    stochastic.
    """
    n = config.num_agents
    rng = random.Random(config.seed)

    alias_order = list(range(n))
    rng.shuffle(alias_order)
    aliases = [f"Cog-{ALIAS_LETTERS[alias_order[slot] % len(ALIAS_LETTERS)]}" for slot in range(n)]

    favorites = [COLORS[index % len(COLORS)] for index in range(n)]
    rng.shuffle(favorites)

    patch_deal = list(range(config.patch_count))
    rng.shuffle(patch_deal)

    deal = {"aliases": aliases, "favorites": favorites, "patch_deal": patch_deal}
    module = module_for(config)
    return GameState(
        aliases=aliases,
        module_state=module.new_state(config, deal),
        scores=[0.0] * n,
        total_extracted=[0.0] * n,
        public_effort=[0] * n,
        sanctions_given=[0] * n,
        sanctions_received=[0] * n,
        notes=[""] * n,
        recent=[[] for _ in range(n)],
        fallbacks=[0] * n,
        disconnected=[False] * n,
    )


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def parse_decision(
    raw: object, slot: int, config: CommonsConfig, state: GameState, module: Module, src: str
) -> Decision:
    """Validate one raw reply into a bounded `Decision`.

    Every integer is clamped to its range, every colour to the enum, the effort
    budget is enforced by reducing the MAINTENANCE field first, a sanction is
    dropped unless sanctions are on and the target is a real other seat, and
    every free-text field is truncated on rune boundaries.
    """
    decision = module.parse_decision(raw, slot, config, state.module_state)
    decision.src = src
    if not isinstance(raw, dict):
        return decision

    if config.sanctions_enabled:
        sanction_raw = raw.get("sanction")
        if (
            isinstance(sanction_raw, (int, float))
            and not isinstance(sanction_raw, bool)
            and int(sanction_raw) != slot
            and 0 <= int(sanction_raw) < config.num_agents
        ):
            decision.sanction = int(sanction_raw)

    if config.chat_enabled:
        message = truncate_runes(raw.get("message"), config.chat_max_chars)
        decision.message = message or None
    decision.note = truncate_runes(raw.get("note"), NOTE_MAX_CHARS) or None
    return decision


# ---------------------------------------------------------------------------
# Observation
# ---------------------------------------------------------------------------


def observation(
    state: GameState, config: CommonsConfig, slot: int, module: Module
) -> dict[str, Any]:
    """The per-seat observation. Aliases only — never a real policy name.

    An LLM seat's prompt is composed from exactly this object and nothing else,
    so a prompt seat can never see further than a scripted seat.
    """
    last = state.history[-1] if state.history else None
    obs: dict[str, Any] = {
        "type": "observation",
        "protocol": PLAYER_PROTOCOL,
        "slot": slot,
        "alias": state.aliases[slot],
        "round": state.round,
        "rounds": config.rounds,
        "round_seconds": config.round_seconds,
        "module": config.module,
        "num_players": config.num_agents,
        "effort_budget": config.effort_budget,
        "ledger_public": config.ledger_public,
        "sanctions_enabled": config.sanctions_enabled,
        "sanction_cost": config.sanction_cost,
        "sanction_burn": config.sanction_burn,
        "chat_enabled": config.chat_enabled,
        "chat_max_chars": config.chat_max_chars,
        "norm_text": config.norm_text,
        "score": round(state.scores[slot], 2),
        "your_last_gain": round(last.gains[slot], 2) if last else None,
        "sanctions_received_last_round": _sanctions_received_last_round(state, slot),
        "last_round_total_extracted": round(last.total_extracted, 2) if last else None,
        "messages_last_round": (
            [{"alias": m.alias, "text": m.text} for m in last.messages] if last else []
        ),
        "your_note": state.notes[slot],
        "module_state": module.observe(state.module_state, config, slot, state.aliases, state.round),
    }
    if config.ledger_public:
        obs["ledger"] = [
            {
                "slot": other,
                "alias": state.aliases[other],
                "total_extracted": round(state.total_extracted[other], 2),
                "public_effort": state.public_effort[other],
                "recent": list(state.recent[other][-RECENT_ACTIONS:]),
                "sanctions_given": state.sanctions_given[other],
                "sanctions_received": state.sanctions_received[other],
            }
            for other in range(config.num_agents)
        ]
    return obs


def _sanctions_received_last_round(state: GameState, slot: int) -> int:
    if not state.history:
        return 0
    last = state.history[-1]
    return sum(1 for decision in last.decisions if decision.get("sanction") == slot)


# ---------------------------------------------------------------------------
# The round
# ---------------------------------------------------------------------------


def open_round(state: GameState, config: CommonsConfig, module: Module) -> dict[str, Any]:
    """Step 1: snapshot the pre-round state and write `round_open`."""
    event = {
        "kind": "round_open",
        "r": state.round,
        "state": module.public_state(state.module_state, config, state.aliases),
        "text": f"Round {state.round + 1} of {config.rounds} — everyone decides at once.",
    }
    state.events.append(event)
    return event


def settle_round(
    state: GameState, decisions: list[Decision], config: CommonsConfig, module: Module
) -> RoundRecord:
    """Steps 4-8, in exactly the order the design note fixes them."""
    if len(decisions) != config.num_agents:
        raise ValueError(f"expected {config.num_agents} decisions, got {len(decisions)}")
    r = state.round
    state_before = module.public_state(state.module_state, config, state.aliases)

    # Step 4 — publish chat. Messages attach to round r and become visible in
    # round r+1's observation; they are NEVER visible inside their own round,
    # because the decisions are simultaneous.
    messages: list[ChatRecord] = []
    for slot, decision in enumerate(decisions):
        if decision.message:
            messages.append(ChatRecord(alias=state.aliases[slot], text=decision.message))
            state.events.append(
                {
                    "kind": "chat",
                    "r": r,
                    "slot": slot,
                    "alias": state.aliases[slot],
                    "text": f"{state.aliases[slot]} says: {decision.message}",
                    "message": decision.message,
                }
            )
        if decision.note is not None:
            state.notes[slot] = decision.note

    # Step 5 — resolve the module.
    gains, extracted, module_events = module.resolve(state.module_state, decisions, config, r)

    for slot, decision in enumerate(decisions):
        state.events.append(
            {
                "kind": "decision",
                "r": r,
                "slot": slot,
                "alias": state.aliases[slot],
                "src": decision.src,
                "text": module.describe(decision, gains[slot], state.aliases[slot]),
            }
        )
    state.events.extend(_stamp(module_events, state))

    # Step 6 — apply sanctions, ascending slot.
    if config.sanctions_enabled:
        for slot, decision in enumerate(decisions):
            if decision.sanction is None:
                continue
            target = decision.sanction
            state.scores[slot] -= config.sanction_cost
            state.scores[target] -= config.sanction_burn
            state.sanctions_given[slot] += 1
            state.sanctions_received[target] += 1
            state.events.append(
                {
                    "kind": "sanction",
                    "r": r,
                    "slot": slot,
                    "alias": state.aliases[slot],
                    "target": target,
                    "target_alias": state.aliases[target],
                    "text": (
                        f"{state.aliases[slot]} burns {state.aliases[target]}: "
                        f"-{config.sanction_cost:.1f} / -{config.sanction_burn:.1f}"
                    ),
                }
            )

    # Step 7 — resource dynamics, latching any permanent death.
    dynamics_events = module.dynamics(state.module_state, config, r)
    for event in dynamics_events:
        if event["kind"] == "collapse" and state.collapse_round is None:
            state.collapse_round = r
    state.events.extend(_stamp(dynamics_events, state))

    # Step 8 — book the round. The per-seat maintenance effort is computed
    # HERE, once, and recorded per round: it is the one derived quantity the
    # viewer shows, and a viewer that recomputed it would be a second
    # implementation of `Module.public_effort` in another language.
    efforts = [module.public_effort(decision, config) for decision in decisions]
    for slot in range(config.num_agents):
        state.scores[slot] += gains[slot]
        state.total_extracted[slot] += extracted[slot]
        state.public_effort[slot] += efforts[slot]
        state.recent[slot].append(module.compact(decisions[slot]))
        state.recent[slot] = state.recent[slot][-RECENT_ACTIONS:]

    state_after = module.public_state(state.module_state, config, state.aliases)
    record = RoundRecord(
        r=r,
        state_before=state_before,
        decisions=[decision.to_json(slot) for slot, decision in enumerate(decisions)],
        gains=list(gains),
        extracted=list(extracted),
        scores=list(state.scores),
        state_after=state_after,
        total_extracted=sum(extracted),
        public_effort=sum(efforts),
        seat_public_effort=list(efforts),
        collapsed=_collapsed(state_after) or state.collapse_round is not None,
        series=module.series(state.module_state, config),
        seat_frozen=list(state.module_state.get("frozen_until", [0] * config.num_agents)),
        messages=messages,
    )
    state.history.append(record)
    state.events.append(
        {
            "kind": "round_end",
            "r": r,
            "state": state_after,
            "scores": [round(score, 3) for score in state.scores],
            "text": _round_end_text(record, config),
        }
    )
    state.round += 1
    return record


def _collapsed(public_state: dict[str, Any]) -> bool:
    """True when the module's resource has taken permanent damage."""
    dead = public_state.get("dead")
    if isinstance(dead, bool):
        return dead
    return bool(public_state.get("dead_patches"))


def _round_end_text(record: RoundRecord, config: CommonsConfig) -> str:
    return (
        f"Round {record.r + 1} settled — {record.total_extracted:.1f} taken, "
        f"{record.public_effort} of {config.num_agents * config.effort_budget} effort units "
        f"spent on the commons."
    )


def _stamp(events: list[dict[str, Any]], state: GameState) -> list[dict[str, Any]]:
    """Give every per-seat event its alias, and reject anything off-vocabulary."""
    for event in events:
        if event["kind"] not in EVENT_KINDS:
            raise ValueError(f"module emitted an event outside the vocabulary: {event['kind']!r}")
        if "slot" in event and "alias" not in event:
            event["alias"] = state.aliases[event["slot"]]
    return events


# ---------------------------------------------------------------------------
# Accounting
# ---------------------------------------------------------------------------


def welfare(state: GameState, config: CommonsConfig, module: Module) -> float:
    """Group welfare: every score plus what the commons still holds.

    Sanction costs and burns already flow through the scores, so punishment is
    welfare-negative for the group.
    """
    return sum(state.scores) + module.residual_value(state.module_state)


def results(
    state: GameState,
    config: CommonsConfig,
    module: Module,
    reason: str,
    names: list[str],
    llm_requests: int,
) -> dict[str, Any]:
    if reason not in END_REASONS:
        raise ValueError(f"illegal end reason {reason!r}")
    dead_patches = state.module_state.get("dead")
    if isinstance(dead_patches, list):
        dead = [index for index, is_dead in enumerate(dead_patches) if is_dead]
    else:
        dead = []
    return {
        "reason": reason,
        "rounds": state.round,
        "scores": [round(score, 3) for score in state.scores],
        "total_extracted": [round(value, 3) for value in state.total_extracted],
        "public_effort": list(state.public_effort),
        "sanctions_given": list(state.sanctions_given),
        "sanctions_received": list(state.sanctions_received),
        "welfare": round(welfare(state, config, module), 3),
        "residual_value": round(module.residual_value(state.module_state), 3),
        "collapse_round": state.collapse_round,
        "dead_patches": dead,
        "fallbacks": list(state.fallbacks),
        "llm_requests": llm_requests,
        "names": [truncate_runes(name, 64) for name in names],
        "aliases": list(state.aliases),
        "disconnected": list(state.disconnected),
    }


def replay_payload(
    state: GameState,
    config: CommonsConfig,
    module: Module,
    names: list[str],
    payload: dict[str, Any],
    seat_kinds: list[str],
    seat_scripted: list[str],
    variant: str = "",
) -> dict[str, Any]:
    """The replay document: one UTF-8 JSON object, self-sufficient.

    `docker_smoke.sh` parses it, the wasm module parses it in the browser, and
    nothing else is ever contacted — no server, no config lookup, no name
    service. Everything the viewer needs is in here: the aliases, the real
    policy names, the resolved config, the seed, the per-round state before and
    after, every decision, every event, and the results.
    """
    seats = []
    for slot in range(config.num_agents):
        info = module.seat_info(state.module_state, config, slot)
        seats.append(
            {
                "slot": slot,
                "alias": state.aliases[slot],
                "name": truncate_runes(names[slot], 64),
                "kind": seat_kinds[slot],
                "scripted": truncate_runes(seat_scripted[slot], 32),
                "color": slot,
                "favorite": info["favorite"],
                "patches": info["patches"],
                "disconnected": state.disconnected[slot],
            }
        )
    return {
        "format": REPLAY_FORMAT,
        "protocol": REPLAY_PROTOCOL,
        "version": VERSION,
        "coworld": COWORLD_NAME,
        "module": config.module,
        "variant": variant or config.module,
        "generated_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "seed": config.seed,
        "config": config.model_dump(),
        "names": list(state.aliases),
        "policyNames": [truncate_runes(name, 64) for name in names],
        "seats": seats,
        "rounds": [record.to_json() for record in state.history],
        "events": state.events,
        "results": payload,
    }
