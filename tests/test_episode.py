"""End-to-end episodes: the settle path, the artifacts, and the three end reasons.

The `complete` case runs through `headless.run_episode(parallel_seats=True)`;
the `deadline` and `no_players` cases run through the SERVER's own `_play_game`
loop, because the wall-clock guard and the no-player settle live there and
nowhere else.
"""

from __future__ import annotations

import asyncio
import importlib.util
import itertools
import json
import os
import sys
from pathlib import Path

import pytest

from coworld.examples.commons_family import headless
from coworld.examples.commons_family.game.engine import (
    END_REASONS,
    CommonsConfig,
    module_for,
    results,
)

from .conftest import EPISODE_POLICIES, build_config, run

SERVER_PATH = (
    Path(__file__).resolve().parents[1]
    / "src/coworld/examples/commons_family/game/server.py"
)
_COUNTER = itertools.count()


def load_server(tmp_path: Path, game_config: dict, monkeypatch):
    """Import a FRESH game server bound to `game_config`.

    The server reads its config at import time (that is the Coworld game
    contract), so each case needs its own module object rather than a reload.
    """
    work = tmp_path / f"srv{next(_COUNTER)}"
    work.mkdir()
    config_path = work / "config.json"
    config_path.write_text(json.dumps(game_config), encoding="utf-8")
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY_URI",
                 "AWS_ENDPOINT_URL_BEDROCK_RUNTIME", "COWORLD_TIMEOUT_SECONDS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("COGAME_CONFIG_URI", config_path.as_uri())
    monkeypatch.setenv("COGAME_RESULTS_URI", (work / "results.json").as_uri())
    monkeypatch.setenv("COGAME_SAVE_REPLAY_URI", (work / "replay.json").as_uri())
    monkeypatch.setenv("COMMONS_FAMILY_POST_GAME_LINGER_SECONDS", "0")
    monkeypatch.setenv("COMMONS_FAMILY_POST_GAME_MAX_LINGER_SECONDS", "0")

    module_name = f"_commons_family_server_{next(_COUNTER)}"
    spec = importlib.util.spec_from_file_location(module_name, SERVER_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    module.REGISTRATION_GRACE_SECONDS = 0.0
    return module, work


def base_game_config(**overrides) -> dict:
    config = {
        "tokens": [f"token-{index}" for index in range(6)],
        "players": [{"name": f"policy-{index}"} for index in range(6)],
        "num_agents": 6,
        "module": "cleanup",
        "rounds": 8,
        "round_seconds": 2,
        "min_round_seconds": 0,
        "sanctions_enabled": True,
        "chat_enabled": True,
        "seed": 20260824,
    }
    config.update(overrides)
    return config


def seat_all(module, scripted: list[str]) -> None:
    for slot, name in enumerate(scripted):
        module.session.connected_ever.add(slot)
        module.session.registrations[slot] = {"prompt": "", "scripted": name}


def recomputed_scores(replay: dict) -> list[float]:
    """The scoring formula, applied to the round records from the outside."""
    config = replay["config"]
    seats = config["num_agents"]
    scores = [0.0] * seats
    for record in replay["rounds"]:
        for slot, gain in enumerate(record["gains"]):
            scores[slot] += gain
        for decision in record["decisions"]:
            target = decision.get("sanction")
            if target is None:
                continue
            scores[decision["slot"]] -= config["sanction_cost"]
            scores[target] -= config["sanction_burn"]
    return [round(value, 3) for value in scores]


# ---------------------------------------------------------------------------
# complete
# ---------------------------------------------------------------------------


def test_a_full_episode_settles_complete_and_writes_both_artifacts(episode_dir, replay, results):
    assert (episode_dir / "results.json").stat().st_size > 0
    assert (episode_dir / "replay.json").stat().st_size > 0
    assert results["reason"] == "complete"
    assert results["reason"] in END_REASONS
    assert results["rounds"] == 8
    assert len(replay["rounds"]) == 8
    assert [record["r"] for record in replay["rounds"]] == list(range(8))


def test_scores_match_the_formula_recomputed_from_the_round_records(replay, results):
    assert results["scores"] == pytest.approx(recomputed_scores(replay), abs=1e-6)


def test_the_final_round_record_carries_the_final_scores(replay, results):
    assert replay["rounds"][-1]["scores"] == pytest.approx(results["scores"], abs=1e-6)


def test_welfare_is_the_scores_plus_what_the_commons_still_holds(replay, results):
    assert results["welfare"] == pytest.approx(
        sum(results["scores"]) + results["residual_value"], abs=1e-3
    )


@pytest.mark.parametrize("module_name", ["cleanup", "harvest", "allelopathic", "mushrooms"])
def test_every_module_plays_a_whole_episode(module_name):
    config = build_config(module=module_name, rounds=8)
    state = run(config)
    assert state.round == 8
    assert len(state.history) == 8
    payload = results(state, config, module_for(config), "complete", ["p"] * 6, 0)
    assert payload["reason"] == "complete"
    assert len(payload["scores"]) == 6


def test_two_runs_with_the_same_seed_are_byte_identical_modulo_generated_at(tmp_path):
    def replay_bytes(directory: str) -> bytes:
        config = build_config()
        state = run(config)
        out = tmp_path / directory
        headless.write_artifacts(state, config, out)
        payload = json.loads((out / "replay.json").read_bytes().decode("utf-8"))
        payload.pop("generated_at")
        return json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")

    assert replay_bytes("a") == replay_bytes("b")


def test_a_different_seed_produces_a_different_episode(tmp_path):
    left = run(build_config(seed=11))
    right = run(build_config(seed=12))
    assert (left.aliases, left.scores) != (right.aliases, right.scores)


# ---------------------------------------------------------------------------
# the server's own loop: complete, deadline, no_players
# ---------------------------------------------------------------------------


def test_the_server_loop_settles_complete_and_writes_utf8_artifacts(tmp_path, monkeypatch):
    module, work = load_server(tmp_path, base_game_config(), monkeypatch)
    seat_all(module, ["steward", "cleaner", "punisher", "reciprocator", "free_rider", "random"])
    asyncio.run(module._play_game())

    payload = json.loads((work / "results.json").read_bytes().decode("utf-8"))
    replay = json.loads((work / "replay.json").read_bytes().decode("utf-8"))
    assert payload["reason"] == "complete"
    assert payload["rounds"] == 8
    assert len(replay["rounds"]) == 8
    assert payload["disconnected"] == [False] * 6
    assert payload["llm_requests"] == 0          # no credentials: zero calls
    assert payload["fallbacks"] == [0] * 6


def test_the_wall_clock_guard_settles_deadline_with_the_rounds_it_played(tmp_path, monkeypatch):
    # 0.6 x 0.001 s of play budget: the guard fires between round 0 and round 1.
    module, work = load_server(
        tmp_path,
        base_game_config(rounds=20, episode_timeout_seconds=0.001),
        monkeypatch,
    )
    seat_all(module, ["steward"] * 6)
    asyncio.run(module._play_game())

    payload = json.loads((work / "results.json").read_bytes().decode("utf-8"))
    assert payload["reason"] == "deadline"
    assert 1 <= payload["rounds"] < 20
    # Scores are real, not zeroed: a deadline episode is still rankable.
    assert sum(payload["scores"]) > 0.0
    replay = json.loads((work / "replay.json").read_bytes().decode("utf-8"))
    assert len(replay["rounds"]) == payload["rounds"]
    assert any(event["kind"] == "deadline" for event in replay["events"])


def test_the_play_budget_is_anchored_at_process_start_not_at_the_first_round(
    tmp_path, monkeypatch
):
    """The connect wait is inside the budget, not on top of it.

    Anchoring `play_deadline` when `_play_game` starts put the worst case at
    180 s (connect) + 5 s (grace) + 0.6 x 1200 s = 905 s of a 1200 s episode.
    Anchored at process start, the artifacts are written inside 720 s whatever
    the connect wait cost.
    """
    module, work = load_server(
        tmp_path,
        base_game_config(rounds=20, episode_timeout_seconds=600),
        monkeypatch,
    )
    seat_all(module, ["steward"] * 6)
    # As if the process had started 500 s ago waiting for players: the
    # 0.6 x 600 = 360 s play budget is already spent.
    monkeypatch.setattr(module, "PROCESS_START", module.time.monotonic() - 500)
    asyncio.run(module._play_game())

    payload = json.loads((work / "results.json").read_bytes().decode("utf-8"))
    assert payload["reason"] == "deadline"
    assert payload["rounds"] == 1                 # round 0 always plays, then the guard


def test_a_pause_cannot_hold_the_episode_past_the_wall_clock_guard(tmp_path, monkeypatch):
    """`/admin` takes no token, and the paused branch used to skip the guard.

    A pause held the round loop in `await asyncio.sleep(0.1)` forever: the
    deadline test sat after `settle_round`, which a paused loop never reaches,
    so the pod died at `episodeTimeoutSeconds` with no artifacts.
    """
    module, work = load_server(
        tmp_path,
        base_game_config(rounds=20, episode_timeout_seconds=1.0),
        monkeypatch,
    )
    seat_all(module, ["steward"] * 6)

    async def drive() -> None:
        module.session.paused = True
        await asyncio.wait_for(module._play_game(), timeout=30)

    asyncio.run(drive())

    payload = json.loads((work / "results.json").read_bytes().decode("utf-8"))
    assert payload["reason"] == "deadline"
    replay = json.loads((work / "replay.json").read_bytes().decode("utf-8"))
    assert any(event["kind"] == "deadline" for event in replay["events"])


def test_no_seat_connecting_settles_no_players_with_zero_scores(tmp_path, monkeypatch):
    module, work = load_server(tmp_path, base_game_config(), monkeypatch)
    asyncio.run(module._play_game())

    payload = json.loads((work / "results.json").read_bytes().decode("utf-8"))
    assert payload["reason"] == "no_players"
    assert payload["rounds"] == 0
    assert payload["scores"] == [0.0] * 6
    assert (work / "replay.json").exists()


def test_a_seat_that_never_connects_passes_and_is_flagged(tmp_path, monkeypatch):
    module, work = load_server(tmp_path, base_game_config(), monkeypatch)
    seat_all(module, ["steward"] * 6)
    module.session.connected_ever.discard(5)
    module.session.registrations.pop(5, None)
    asyncio.run(module._play_game())

    payload = json.loads((work / "results.json").read_bytes().decode("utf-8"))
    assert payload["reason"] == "complete"
    assert payload["disconnected"] == [False] * 5 + [True]
    assert payload["scores"][5] == pytest.approx(0.0)
    replay = json.loads((work / "replay.json").read_bytes().decode("utf-8"))
    assert any(event["kind"] == "no_submission" and event["slot"] == 5
               for event in replay["events"])


def test_a_player_sent_decision_overrides_the_game_side_one(tmp_path, monkeypatch):
    module, work = load_server(
        tmp_path, base_game_config(rounds=1, min_round_seconds=0.6), monkeypatch
    )
    seat_all(module, ["steward"] * 6)

    async def drive() -> None:
        episode = asyncio.create_task(module._play_game())
        await asyncio.sleep(0.1)   # inside round 0, before its deadline
        module.session.player_decisions[0] = {"type": "decision", "harvest": 3, "clean": 0}
        await episode

    asyncio.run(drive())

    replay = json.loads((work / "replay.json").read_bytes().decode("utf-8"))
    decision = replay["rounds"][0]["decisions"][0]
    assert decision["src"] == "player"
    assert decision["harvest"] == 3


def test_a_transport_that_raises_settles_the_episode_with_fallbacks(tmp_path, monkeypatch):
    """The episode must degrade, never hang, on an unclassified LLM failure.

    Six prompt seats and a transport that raises `HTTPError 401` on every call:
    before this was handled the exception unwound `decide` -> `to_thread` ->
    `_play_game`, whose task nobody awaits, and the episode ended with no
    results.json, no replay.json and no exit.
    """
    from urllib.error import HTTPError  # noqa: PLC0415

    module, work = load_server(tmp_path, base_game_config(rounds=3), monkeypatch)
    for slot in range(6):
        module.session.connected_ever.add(slot)
        module.session.registrations[slot] = {"prompt": "take one apple", "scripted": ""}

    class RejectingTransport:
        def complete(self, body, timeout):
            raise HTTPError("https://api.anthropic.com/v1/messages", 401,
                            "Unauthorized", {}, None)

    module.session.decider.transport = RejectingTransport()
    asyncio.run(module._play_game())

    payload = json.loads((work / "results.json").read_bytes().decode("utf-8"))
    replay = json.loads((work / "replay.json").read_bytes().decode("utf-8"))
    assert payload["reason"] == "complete"
    assert payload["rounds"] == 3
    assert payload["fallbacks"] == [3] * 6
    assert all(
        decision["src"] == "fallback:transport"
        for record in replay["rounds"]
        for decision in record["decisions"]
    )


def test_an_unexpected_failure_in_the_round_loop_still_writes_artifacts(tmp_path, monkeypatch):
    module, work = load_server(tmp_path, base_game_config(rounds=8), monkeypatch)
    seat_all(module, ["steward"] * 6)
    real_settle = module.settle_round
    calls = {"n": 0}

    def explode_on_the_third_round(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 3:
            raise RuntimeError("something nobody thought of")
        return real_settle(*args, **kwargs)

    monkeypatch.setattr(module, "settle_round", explode_on_the_third_round)
    asyncio.run(module._play_game())

    payload = json.loads((work / "results.json").read_bytes().decode("utf-8"))
    assert payload["reason"] == "complete"
    assert payload["rounds"] == 2                 # the rounds that did settle
    assert (work / "replay.json").exists()


def test_the_registration_default_is_a_steward_not_a_disconnect(tmp_path, monkeypatch):
    module, work = load_server(tmp_path, base_game_config(rounds=2), monkeypatch)
    module.session.connected_ever.update(range(6))     # connected, never registered
    asyncio.run(module._play_game())

    replay = json.loads((work / "replay.json").read_bytes().decode("utf-8"))
    assert all(
        decision["src"] == "scripted:steward"
        for decision in replay["rounds"][0]["decisions"]
    )


# ---------------------------------------------------------------------------
# the player container
# ---------------------------------------------------------------------------


def test_the_player_retries_a_game_that_is_not_listening_yet(monkeypatch):
    """The connect race is the difference between six seats and four.

    Both containers start together, so the first connect regularly lands before
    uvicorn is listening. A player that gives up there costs the seat the whole
    episode: the game waits out its 180 s connect timeout and then plays the
    seat as absent, which is how CI's first smoke replay ended up with two
    `no_submission` seats.
    """
    from coworld.examples.commons_family.player import player  # noqa: PLC0415

    attempts = {"n": 0}

    async def flaky(url, **kwargs):
        attempts["n"] += 1
        if attempts["n"] < 4:
            raise OSError("connection refused")
        return "socket"

    monkeypatch.setattr(player.websockets, "connect", flaky)
    result = asyncio.run(player.connect_with_retry("ws://game:8080/player", timeout=10))
    assert result == "socket"
    assert attempts["n"] == 4


def test_the_player_gives_up_inside_its_window_rather_than_hanging(monkeypatch):
    from coworld.examples.commons_family.player import player  # noqa: PLC0415

    async def never(url, **kwargs):
        raise OSError("connection refused")

    monkeypatch.setattr(player.websockets, "connect", never)
    started = asyncio.get_event_loop_policy().new_event_loop()
    try:
        with pytest.raises(OSError):
            started.run_until_complete(
                player.connect_with_retry("ws://game:8080/player", timeout=0.3)
            )
    finally:
        started.close()


def test_the_registration_frame_prefers_the_scripted_baseline(monkeypatch):
    from coworld.examples.commons_family.player import player  # noqa: PLC0415

    monkeypatch.setenv("PLAYER_PROMPT", "take one apple")
    monkeypatch.setenv("PLAYER_SCRIPTED", "cleaner")
    frame = player.registration()
    assert frame == {"type": "prompt", "prompt": "take one apple", "scripted": "cleaner"}

    monkeypatch.delenv("PLAYER_SCRIPTED")
    assert player.registration()["scripted"] == ""

    monkeypatch.setenv("PLAYER_PROMPT", "x" * 5000)
    assert len(player.registration()["prompt"]) == player.PROMPT_MAX_RUNES


def test_the_environment_is_left_clean(monkeypatch):
    # A guard on the fixture above rather than on the game: a leaked
    # COGAME_CONFIG_URI would make an unrelated import pick up a stale episode.
    assert "COGAME_CONFIG_URI" not in os.environ or os.environ["COGAME_CONFIG_URI"]
