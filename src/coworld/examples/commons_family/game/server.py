"""Commons Family game server.

Wraps the pure engine in the Coworld game-container contract: config via
`COGAME_CONFIG_URI`, results and replay written to the runner's URIs,
`/healthz`, the three browser clients, and the `/player`, `/global` and
`/admin` websocket routes. Replay viewing is the static wasm bundle declared in
the manifest (`static-replay-viewer/`), never a container route.

The round barrier is not meadow's "every connected player has submitted": the
decisions are made HERE. A round settles when the LLM batch for every prompt
seat is complete or when `round_seconds` elapses, whichever comes first, and
never before `min_round_seconds`. Nothing in the loop blocks on an unbounded
read, and no player-side problem can make this process exit non-zero.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, WebSocket
from fastapi.responses import HTMLResponse

from coworld.examples.commons_family.game.baselines import make_baseline
from coworld.examples.commons_family.game.engine import (
    PLAYER_PROTOCOL,
    PROMPT_MAX_RUNES,
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
    truncate_runes,
)
from coworld.examples.commons_family.game.llm import LlmDecider
from coworld.examples.commons_family.shared.artifact_io import (
    artifact_method,
    read_data,
    write_data,
)
from coworld.examples.commons_family.shared.log_shipper import get_logger

CLIENT_DIR = Path(__file__).parent / "client"
logger = get_logger("commons_family.game")
GAME_HOST = os.environ.get("COGAME_HOST", "0.0.0.0")
GAME_PORT = int(os.environ.get("COGAME_PORT", "8080"))

# Keep serving after the final round so viewers (and the hosted certifier's
# websocket probes) can still read the final state: an all-scripted episode can
# finish quickly, and exiting immediately races anything that connected while
# the game was live. Meadow's numbers, kept verbatim.
POST_GAME_LINGER_SECONDS = float(os.environ.get("COMMONS_FAMILY_POST_GAME_LINGER_SECONDS", "30"))
POST_GAME_MAX_LINGER_SECONDS = float(
    os.environ.get("COMMONS_FAMILY_POST_GAME_MAX_LINGER_SECONDS", "90")
)
# How long a connected socket has to send its `prompt` registration before the
# seat is treated as `{"scripted": "steward"}`. Never a disconnect.
REGISTRATION_GRACE_SECONDS = 5.0

RAW_CONFIG: dict[str, Any] = json.loads(read_data(os.environ["COGAME_CONFIG_URI"]))
RESULTS_URI = os.environ["COGAME_RESULTS_URI"]
REPLAY_URI = os.environ["COGAME_SAVE_REPLAY_URI"]

TOKENS: list[str] = list(RAW_CONFIG.get("tokens") or [])
PLAYER_NAMES: list[str] = [
    truncate_runes(player.get("name", f"seat-{index}"), 64)
    for index, player in enumerate(RAW_CONFIG.get("players") or [])
]
SEATS = len(TOKENS) or int(RAW_CONFIG.get("num_agents", 6))
while len(PLAYER_NAMES) < SEATS:
    PLAYER_NAMES.append(f"seat-{len(PLAYER_NAMES)}")
PLAYER_NAMES = PLAYER_NAMES[:SEATS]

CONFIG = CommonsConfig.model_validate({**RAW_CONFIG, "num_agents": SEATS})
MODULE = module_for(CONFIG)
# The game container does NOT receive COWORLD_TIMEOUT_SECONDS in hosted
# episodes (only the worker sidecar does), so the default is the platform's
# episodeTimeoutSeconds and play is bounded to a fraction of it.
EPISODE_TIMEOUT_SECONDS = float(
    os.environ.get("COWORLD_TIMEOUT_SECONDS", CONFIG.episode_timeout_seconds)
)
# The play budget is anchored at PROCESS START, not at the first round. The
# player-connect wait (180 s) and the registration grace (5 s) run before
# `_play_game` and the platform's `episodeTimeoutSeconds` pays for them too, so
# anchoring after them would have made the worst case
# 180 + 5 + 0.6 x 1200 = 905 s of a 1200 s episode (75 %) rather than the 60 %
# the design note promises. Anchored here, artifacts are written by
# 0.6 x 1200 = 720 s whatever the connect wait cost.
PROCESS_START = time.monotonic()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    timeout_task = asyncio.create_task(_start_after_player_connect_timeout())
    yield
    timeout_task.cancel()
    with suppress(asyncio.CancelledError):
        await timeout_task


app = FastAPI(lifespan=lifespan)
# Assigned in __main__. The tests drive `_play_game()` directly with no uvicorn
# around it, so the shutdown poke is guarded rather than assumed.
server: uvicorn.Server | None = None


class GameSession:
    def __init__(self) -> None:
        self.engine: GameState = new_game(CONFIG)
        self.players: dict[int, WebSocket] = {}
        self.registrations: dict[int, dict[str, str]] = {}
        self.connected_ever: set[int] = set()
        self.player_decisions: dict[int, dict] = {}
        self.policies: dict[int, Any] = {}
        self.started = False
        self.done = False
        self.paused = False
        self.reason = ""
        self.round_seconds = CONFIG.round_seconds
        self.global_viewers = 0
        self.decider = LlmDecider(CONFIG, MODULE)


session = GameSession()


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


@app.get("/healthz")
def healthz() -> dict[str, bool]:
    return {"ok": True}


@app.get("/client/global")
def global_client() -> HTMLResponse:
    return HTMLResponse((CLIENT_DIR / "global.html").read_text(encoding="utf-8"))


@app.get("/client/admin")
def admin_client() -> HTMLResponse:
    return HTMLResponse((CLIENT_DIR / "admin.html").read_text(encoding="utf-8"))


@app.get("/client/player")
def player_client() -> HTMLResponse:
    return HTMLResponse((CLIENT_DIR / "player.html").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Websockets
# ---------------------------------------------------------------------------


@app.websocket("/global")
async def global_viewer(websocket: WebSocket) -> None:
    await websocket.accept()
    session.global_viewers += 1
    try:
        sender = asyncio.create_task(_send_global_snapshots(websocket))
        receiver = asyncio.create_task(_drain_messages(websocket))
        done, pending = await asyncio.wait({sender, receiver}, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        for task in done:
            task.result()
    finally:
        session.global_viewers -= 1


GLOBAL_KEEPALIVE_SECONDS = 15.0
GLOBAL_MIN_SEND_INTERVAL_SECONDS = 1.0


async def _send_global_snapshots(websocket: WebSocket) -> None:
    # Send only on game progress (round/done), coalesced to at most one message
    # per second, plus a slow keepalive. The hosted certifier holds this socket
    # WITHOUT reading while it verifies player pods, and its websocket client
    # stops reading the transport — including Pong frames — once ~16 messages
    # sit unread. An unconditional 2Hz stream fills that budget during any
    # pod-start delay and the certification ping then times out against a
    # perfectly healthy server, so the total sent while a viewer isn't reading
    # must stay far below that queue limit.
    loop = asyncio.get_running_loop()
    await websocket.send_json(_snapshot())
    sent_at = loop.time()
    sent_progress = (session.engine.round, session.done)
    while True:
        await asyncio.sleep(0.5)
        now = loop.time()
        progress = (session.engine.round, session.done)
        changed = progress != sent_progress and now - sent_at >= GLOBAL_MIN_SEND_INTERVAL_SECONDS
        if changed or now - sent_at >= GLOBAL_KEEPALIVE_SECONDS:
            await websocket.send_json(_snapshot())
            sent_at = now
            sent_progress = progress


async def _drain_messages(websocket: WebSocket) -> None:
    async for _ in websocket.iter_json():
        pass


@app.websocket("/admin")
async def admin(websocket: WebSocket) -> None:
    await websocket.accept()
    await websocket.send_json(_snapshot())
    async for command in websocket.iter_json():
        if command.get("command") == "pause":
            session.paused = True
        elif command.get("command") == "resume":
            session.paused = False
        elif command.get("command") == "round_seconds":
            session.round_seconds = float(command["round_seconds"])
        await websocket.send_json(_snapshot())


@app.websocket("/player")
async def player(websocket: WebSocket) -> None:
    slot = int(websocket.query_params.get("slot", "-1"))
    token = websocket.query_params.get("token", "")
    if slot < 0 or slot >= len(TOKENS) or TOKENS[slot] != token:
        await websocket.close(code=1008)
        return

    await websocket.accept()
    session.players[slot] = websocket
    session.connected_ever.add(slot)
    logger.info("player slot %d connected (%d/%d)", slot, len(session.players), len(TOKENS))
    await websocket.send_json(
        {
            "type": "welcome",
            "protocol": PLAYER_PROTOCOL,
            "slot": slot,
            "alias": session.engine.aliases[slot],
            "module": CONFIG.module,
            "rounds": CONFIG.rounds,
            "num_players": CONFIG.num_agents,
        }
    )
    if len(session.players) == len(TOKENS) and not session.started:
        session.started = True
        logger.info("all players connected, starting game")
        asyncio.create_task(_play_game())

    try:
        async for message in websocket.iter_json():
            if not isinstance(message, dict):
                continue
            kind = message.get("type")
            if kind == "prompt":
                session.registrations[slot] = {
                    "prompt": truncate_runes(message.get("prompt"), PROMPT_MAX_RUNES),
                    "scripted": truncate_runes(message.get("scripted"), 32),
                }
                logger.info(
                    "slot %d registered: %s",
                    slot,
                    session.registrations[slot]["scripted"] or "prompt",
                )
            elif kind == "decision" and not session.done:
                # A player may drive its own seat; a decision that lands before
                # the round deadline overrides the game-side one.
                session.player_decisions[slot] = message
    except Exception:  # noqa: BLE001 - a dead player socket is lifecycle, not an error
        logger.info("player slot %d socket closed", slot)
    finally:
        if session.players.get(slot) is websocket:
            del session.players[slot]


async def _start_after_player_connect_timeout() -> None:
    await asyncio.sleep(CONFIG.player_connect_timeout_seconds)
    if not session.started and not session.done:
        session.started = True
        logger.info("player connect timeout elapsed, starting with the seats we have")
        asyncio.create_task(_play_game())


# ---------------------------------------------------------------------------
# The episode
# ---------------------------------------------------------------------------


def _policy_for(slot: int):
    if slot in session.policies:
        return session.policies[slot]
    registration = session.registrations.get(slot) or {}
    scripted = registration.get("scripted") or ""
    if scripted:
        policy = ("scripted", make_baseline(scripted, seed=CONFIG.seed * 1000 + slot), scripted)
    elif registration.get("prompt"):
        policy = ("prompt", registration["prompt"], "")
    else:
        policy = (
            "scripted",
            make_baseline(CONFIG.fallback_scripted, seed=CONFIG.seed * 1000 + slot),
            CONFIG.fallback_scripted,
        )
    session.policies[slot] = policy
    return policy


def _seat_kinds() -> list[str]:
    kinds = []
    for slot in range(CONFIG.num_agents):
        if slot not in session.connected_ever:
            kinds.append("none")
        else:
            kinds.append(_policy_for(slot)[0])
    return kinds


async def _play_game() -> None:
    """Run the episode; whatever happens, write artifacts and stop.

    Nobody awaits this task (it is created from a websocket handler and from
    the connect-timeout task), so an exception escaping it would be swallowed
    by the event loop and the container would sit there until the platform's
    episode timeout killed it, with no results.json and no replay.json. The
    guard turns any unexpected failure into a settled episode with the rounds
    that were played.
    """
    try:
        reason = await _run_episode()
    except Exception:  # noqa: BLE001 - degrade, never hang
        logger.exception("the round loop failed; settling on the rounds already played")
        reason = "complete"
    await _finish(reason)


async def _await_registrations(grace_seconds: float) -> None:
    """Return once every connected socket has registered, or when the grace ends.

    A fixed sleep here is 5 s added to every episode, and the certifier's local
    smoke budget is 60 s for the whole thing — game start, play, and the
    post-game linger included. Waiting only as long as there is something to
    wait for is both faster and the same "bound every wait" rule the round loop
    follows.
    """
    deadline = time.monotonic() + grace_seconds
    while time.monotonic() < deadline:
        connected = set(session.players)
        if connected and connected <= set(session.registrations):
            return
        await asyncio.sleep(0.05)


async def _run_episode() -> str:
    play_deadline = PROCESS_START + CONFIG.play_budget_fraction * EPISODE_TIMEOUT_SECONDS
    engine = session.engine

    if not session.connected_ever:
        logger.info("no player ever connected; settling as no_players")
        return "no_players"

    # Give every connected socket its registration window before round 0 —
    # and not one tick longer than it needs. The registration frame follows the
    # welcome immediately, so this almost always returns in milliseconds; the
    # grace is the bound for a socket that connected and then went quiet.
    await _await_registrations(REGISTRATION_GRACE_SECONDS)
    for slot in sorted(session.connected_ever):
        _policy_for(slot)
    for slot in range(CONFIG.num_agents):
        engine.disconnected[slot] = slot not in session.connected_ever

    engine.events.append(
        {
            "kind": "episode_start",
            "r": 0,
            "module": CONFIG.module,
            "text": (
                f"{CONFIG.num_agents} cogs, {CONFIG.rounds} rounds, one "
                f"{CONFIG.module} commons."
            ),
        }
    )

    fallback_seed_base = CONFIG.seed * 7919
    fallbacks = {
        slot: make_baseline(CONFIG.fallback_scripted, seed=fallback_seed_base + slot)
        for slot in range(CONFIG.num_agents)
    }

    def note_deadline() -> None:
        engine.events.append(
            {
                "kind": "deadline",
                "r": engine.round,
                "text": (
                    f"Wall-clock guard fired — scored on {engine.round} of "
                    f"{CONFIG.rounds} rounds."
                ),
            }
        )

    reason = "complete"
    while engine.round < CONFIG.rounds:
        if session.paused:
            # A pause is an admin convenience, not a licence to hold the
            # episode past its wall-clock budget: `/admin` takes no token, and
            # the platform kills the pod at `episodeTimeoutSeconds` with no
            # artifacts written. The guard is checked here too, so a pause that
            # outlives the budget settles and scores instead of spinning.
            if time.monotonic() > play_deadline:
                note_deadline()
                reason = "deadline"
                break
            await asyncio.sleep(0.1)
            continue
        # The wall-clock guard, between rounds so a deadline settle lands on a
        # clean boundary — and BEFORE a round rather than after it, so the
        # artifacts are written inside the budget instead of up to one
        # `round_seconds` past it. Round 0 always plays: a deadline episode is
        # still a scored episode.
        if engine.round > 0 and (
            time.monotonic() + max(session.round_seconds, CONFIG.min_round_seconds)
            > play_deadline
        ):
            note_deadline()
            reason = "deadline"
            break

        round_start = time.monotonic()
        round_deadline = round_start + session.round_seconds
        session.player_decisions.clear()
        open_round(engine, CONFIG, MODULE)
        observations = {
            slot: observation(engine, CONFIG, slot, MODULE) for slot in range(CONFIG.num_agents)
        }
        await _broadcast(observations)

        raw: dict[int, dict] = {}
        src: dict[int, str] = {}
        prompt_requests: dict[int, tuple[dict, str]] = {}
        for slot in range(CONFIG.num_agents):
            if engine.disconnected[slot]:
                continue
            kind, payload, label = _policy_for(slot)
            if kind == "scripted":
                raw[slot] = payload.act(observations[slot])
                src[slot] = f"scripted:{label}"
            else:
                prompt_requests[slot] = (observations[slot], payload)

        if prompt_requests:
            answers = await asyncio.to_thread(
                session.decider.decide, prompt_requests, round_deadline
            )
        else:
            answers = {}
        for slot, (reply, cause) in answers.items():
            if reply is not None:
                raw[slot] = reply
                src[slot] = "llm"
                continue
            raw[slot] = fallbacks[slot].act(observations[slot])
            src[slot] = f"fallback:{cause}"
            engine.fallbacks[slot] += 1
            engine.events.append(
                {
                    "kind": "fallback",
                    "r": engine.round,
                    "slot": slot,
                    "alias": engine.aliases[slot],
                    "cause": cause,
                    "text": (
                        f"{engine.aliases[slot]} fell back to "
                        f"{CONFIG.fallback_scripted} — {cause}"
                    ),
                }
            )

        # The pacing floor: an all-scripted episode would otherwise settle
        # twenty rounds in a fraction of a second, which gives the hosted
        # certifier's /global probes nothing to see and the viewer nothing to
        # soak.
        elapsed = time.monotonic() - round_start
        if elapsed < CONFIG.min_round_seconds:
            await asyncio.sleep(CONFIG.min_round_seconds - elapsed)

        for slot, message in list(session.player_decisions.items()):
            if 0 <= slot < CONFIG.num_agents:
                raw[slot] = message
                src[slot] = "player"

        decisions = []
        for slot in range(CONFIG.num_agents):
            if slot not in raw:
                engine.events.append(
                    {
                        "kind": "no_submission",
                        "r": engine.round,
                        "slot": slot,
                        "alias": engine.aliases[slot],
                        "text": f"{engine.aliases[slot]} is not here; it passes.",
                    }
                )
                decisions.append(
                    parse_decision({}, slot, CONFIG, engine, MODULE, "pass")
                )
                continue
            decisions.append(
                parse_decision(raw[slot], slot, CONFIG, engine, MODULE, src.get(slot, "pass"))
            )

        settle_round(engine, decisions, CONFIG, MODULE)

    return reason


async def _broadcast(observations: dict[int, dict]) -> None:
    for slot, websocket in list(session.players.items()):
        with suppress(Exception):
            await websocket.send_json(observations[slot])


async def _finish(reason: str) -> None:
    engine = session.engine
    session.reason = reason
    engine.events.append(
        {
            "kind": "episode_end",
            "r": max(0, engine.round - 1),
            "reason": reason,
            "text": _end_text(reason, engine),
        }
    )
    payload = results(
        engine, CONFIG, MODULE, reason, PLAYER_NAMES, session.decider.requests
    )
    logger.info("episode finished: reason=%s scores=%s", reason, payload["scores"])

    # Artifact writes are blocking HTTP; off the event loop so websocket pings
    # (the hosted certifier probes /global right around game end) still answer.
    await asyncio.to_thread(
        write_data,
        RESULTS_URI,
        json.dumps(payload, ensure_ascii=False),
        content_type="application/json",
        http_method=artifact_method("COGAME_RESULTS_METHOD"),
    )
    await asyncio.to_thread(
        write_data,
        REPLAY_URI,
        json.dumps(_replay_document(payload), ensure_ascii=False),
        content_type="application/json",
        http_method=artifact_method("COGAME_SAVE_REPLAY_METHOD"),
    )

    session.done = True
    for slot, websocket in list(session.players.items()):
        with suppress(Exception):
            await websocket.send_json(
                {
                    **observation(engine, CONFIG, slot, MODULE),
                    "type": "final",
                    "done": True,
                    "reason": reason,
                    "scores": payload["scores"],
                    "names": payload["names"],
                    "aliases": payload["aliases"],
                }
            )

    loop = asyncio.get_running_loop()
    linger_until = loop.time() + POST_GAME_LINGER_SECONDS
    hard_stop = loop.time() + POST_GAME_MAX_LINGER_SECONDS
    while loop.time() < hard_stop and (loop.time() < linger_until or session.global_viewers > 0):
        await asyncio.sleep(0.5)
    if server is not None:
        server.should_exit = True
    await asyncio.sleep(0.5)


def _end_text(reason: str, engine: GameState) -> str:
    if reason == "no_players":
        return "Nobody came. The commons keeps everything."
    if reason == "deadline":
        return f"Episode deadline — {engine.round} rounds stand as played."
    return f"Final — {engine.round} rounds played."


def _replay_document(payload: dict[str, Any]) -> dict[str, Any]:
    kinds = _seat_kinds()
    scripted = [
        truncate_runes((session.registrations.get(slot) or {}).get("scripted"), 32)
        for slot in range(CONFIG.num_agents)
    ]
    return replay_payload(
        session.engine,
        CONFIG,
        MODULE,
        PLAYER_NAMES,
        payload,
        kinds,
        scripted,
        variant=str(RAW_CONFIG.get("variant", CONFIG.module)),
    )


def _snapshot() -> dict[str, Any]:
    engine = session.engine
    last = engine.history[-1] if engine.history else None
    return {
        "type": "state",
        "protocol": PLAYER_PROTOCOL,
        "module": CONFIG.module,
        "round": engine.round,
        "rounds": CONFIG.rounds,
        "module_state": MODULE.public_state(engine.module_state, CONFIG, engine.aliases),
        "scores": [round(score, 2) for score in engine.scores],
        "total_extracted": [round(value, 2) for value in engine.total_extracted],
        "public_effort": list(engine.public_effort),
        "aliases": list(engine.aliases),
        "player_names": list(PLAYER_NAMES),
        "last_round": last.to_json() if last else None,
        "connected": sorted(session.players),
        "submitted": sorted(session.player_decisions),
        "started": session.started,
        "paused": session.paused,
        "round_seconds": session.round_seconds,
        "done": session.done,
        "reason": session.reason,
    }


if __name__ == "__main__":
    server = uvicorn.Server(uvicorn.Config(app, host=GAME_HOST, port=GAME_PORT))
    server.run()
