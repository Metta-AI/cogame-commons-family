"""In-process episodes, no containers or websockets.

The tests run whole episodes through here, so what CI checks is exactly the
rules and baselines a hosted episode uses. Timing is the only thing missing:
headless rounds settle as soon as every seat has answered, with no pacing floor
and no round deadline.
"""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from coworld.examples.commons_family.game.baselines import make_baseline
from coworld.examples.commons_family.game.engine import (
    CommonsConfig,
    GameState,
    module_for,
    new_game,
    observation,
    open_round,
    parse_decision,
    replay_payload,
    results,
    settle_round,
)


def default_player_names(count: int) -> list[str]:
    return [f"seat-{slot}" for slot in range(count)]


def build_policies(names: list[str], seed: int = 0) -> list[Any]:
    """One baseline per seat, each with its own seed."""
    return [make_baseline(name, seed=seed * 1000 + slot) for slot, name in enumerate(names)]


def run_episode(
    config: CommonsConfig,
    policies: list[Any],
    parallel_seats: bool = False,
    decider: Any = None,
    prompts: dict[int, str] | None = None,
) -> GameState:
    """Run one full episode and return the settled state.

    `parallel_seats` mirrors a hosted round: every seat decides concurrently,
    which is the shape the LLM batch has. `prompts` names the seats the
    `decider` speaks for; every other seat plays its baseline from `policies`.
    """
    if len(policies) != config.num_agents:
        raise ValueError(f"expected {config.num_agents} policies, got {len(policies)}")
    module = module_for(config)
    state = new_game(config)
    prompts = prompts or {}
    state.events.append(
        {
            "kind": "episode_start",
            "r": 0,
            "module": config.module,
            "text": (
                f"{config.num_agents} cogs, {config.rounds} rounds, one "
                f"{config.module} commons."
            ),
        }
    )

    scripted_slots = [slot for slot in range(config.num_agents) if slot not in prompts]
    fallbacks = {slot: make_baseline(config.fallback_scripted, seed=slot) for slot in prompts}

    with ThreadPoolExecutor(max_workers=max(1, config.num_agents)) as executor:
        for _ in range(config.rounds):
            open_round(state, config, module)
            observations = {
                slot: observation(state, config, slot, module)
                for slot in range(config.num_agents)
            }

            def decide(slot: int) -> dict:
                return policies[slot].act(observations[slot])

            raw: dict[int, dict] = {}
            src: dict[int, str] = {}
            if parallel_seats:
                for slot, reply in zip(scripted_slots, executor.map(decide, scripted_slots)):
                    raw[slot] = reply
                    src[slot] = f"scripted:{getattr(policies[slot], 'name', 'baseline')}"
            else:
                for slot in scripted_slots:
                    raw[slot] = decide(slot)
                    src[slot] = f"scripted:{getattr(policies[slot], 'name', 'baseline')}"

            if prompts and decider is not None:
                requests = {slot: (observations[slot], prompts[slot]) for slot in prompts}
                answers = decider.decide(requests, time.monotonic() + config.round_seconds)
                for slot, (reply, cause) in answers.items():
                    if reply is not None:
                        raw[slot] = reply
                        src[slot] = "llm"
                        continue
                    raw[slot] = fallbacks[slot].act(observations[slot])
                    src[slot] = f"fallback:{cause}"
                    state.fallbacks[slot] += 1
                    state.events.append(
                        {
                            "kind": "fallback",
                            "r": state.round,
                            "slot": slot,
                            "alias": state.aliases[slot],
                            "cause": cause,
                            "text": (
                                f"{state.aliases[slot]} fell back to "
                                f"{config.fallback_scripted} — {cause}"
                            ),
                        }
                    )

            decisions = [
                parse_decision(raw.get(slot, {}), slot, config, state, module, src.get(slot, "pass"))
                for slot in range(config.num_agents)
            ]
            settle_round(state, decisions, config, module)

    state.events.append(
        {
            "kind": "episode_end",
            "r": max(0, state.round - 1),
            "reason": "complete",
            "text": f"Final — {state.round} rounds played.",
        }
    )
    return state


def write_artifacts(
    state: GameState,
    config: CommonsConfig,
    directory: Path,
    names: list[str] | None = None,
    reason: str = "complete",
    llm_requests: int = 0,
) -> tuple[Path, Path]:
    """Write `results.json` and `replay.json` exactly as the server writes them.

    Encoded UTF-8 exactly once, with `ensure_ascii=False`, so a rune that
    survived truncation also survives the bytes.
    """
    module = module_for(config)
    names = names or default_player_names(config.num_agents)
    payload = results(state, config, module, reason, names, llm_requests)
    kinds = ["scripted"] * config.num_agents
    scripted = [""] * config.num_agents
    replay = replay_payload(state, config, module, names, payload, kinds, scripted)

    directory.mkdir(parents=True, exist_ok=True)
    results_path = directory / "results.json"
    replay_path = directory / "replay.json"
    results_path.write_bytes(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    replay_path.write_bytes(json.dumps(replay, ensure_ascii=False).encode("utf-8"))
    return results_path, replay_path
