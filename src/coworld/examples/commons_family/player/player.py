"""Commons Family websocket player entrypoint.

Prompt and scripted policies register once, then spectate. A `PLAYER_JEV=1`
policy sends one System One choice decision for each observed round. The game
uses its steward baseline if a decision misses the round deadline.

Every wait here is bounded: the connect retries inside a 150 s window, the
socket carries a ping timeout so a game that died without closing its socket is
noticed, and the spectate loop itself has a wall-clock deadline past the game's
own worst case.

`PLAYER_SCRIPTED` wins when both are set. A dead socket is lifecycle, not an
error: this process exits 0 whatever the game does to it, because a player
container that exits non-zero fails the whole episode.
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import time
from typing import Any, Literal, cast
from urllib.request import Request, urlopen

import websockets
from pydantic import BaseModel, Field

from coworld.examples.commons_family.game.baselines import make_baseline
from coworld.examples.commons_family.shared.log_shipper import get_logger

logger = get_logger("commons_family.player")

PROMPT_MAX_RUNES = 1200
SCRIPTED_MAX_RUNES = 32
# The player container and the game container start together, so the first
# connect regularly lands before uvicorn is listening. Giving up there costs
# the seat the whole episode: the game waits out its 180 s connect timeout and
# then plays the seat as absent. Retry inside that window instead.
CONNECT_TIMEOUT_SECONDS = float(
    os.environ.get("COMMONS_FAMILY_CONNECT_TIMEOUT_SECONDS", "150")
)
CONNECT_RETRY_MAX_SECONDS = 2.0
# Spectating is bounded too. The game's own worst case is the 0.6 x 1200 s play
# budget (anchored at ITS process start, so the connect wait is inside it) plus
# the 90 s hard-cap linger; past that the game is gone and this process should
# be too, rather than sitting on a socket until the platform kills the pod.
SPECTATE_TIMEOUT_SECONDS = float(
    os.environ.get("COMMONS_FAMILY_SPECTATE_TIMEOUT_SECONDS", "1080")
)
# A dead game that never closed its socket is the case a blocking read cannot
# see: without a ping timeout the recv() below waits forever on a peer that
# will never speak again.
PING_INTERVAL_SECONDS = 20.0
PING_TIMEOUT_SECONDS = 30.0
JEV_BASELINES = (
    "steward",
    "free_rider",
    "cleaner",
    "punisher",
    "reciprocator",
    "deterrable",
)


class ChoiceAnswer(BaseModel):
    type: Literal["choice"]
    choice: str
    probabilities: dict[str, float]
    confidence: float = Field(ge=0, le=1)


class JevResponse(BaseModel):
    answers: dict[str, ChoiceAnswer]


def jev_action(observation: dict[str, Any]) -> dict[str, Any]:
    actions: dict[str, dict[str, Any]] = {}
    criteria: dict[str, str] = {}
    seen: set[str] = set()
    for name in JEV_BASELINES:
        action = make_baseline(
            name, seed=observation["round"] * 1000 + observation["slot"]
        ).act(observation)
        signature = json.dumps(action, sort_keys=True)
        if signature in seen:
            continue
        seen.add(signature)
        actions[name] = action
        criteria[name] = f"{name}: {signature}"

    sidecar = os.environ.get("AWS_ENDPOINT_URL_BEDROCK_RUNTIME")
    if sidecar:
        endpoint = sidecar
        model = "typesafe/jev-1.13"
        headers = {"X-Coworld-Player-Slot": str(observation["slot"])}
    else:
        endpoint = os.environ.get("TYPESAFE_BASE_URL", "https://api.typesafe.ai")
        model = os.environ.get("TYPESAFE_DEFAULT_MODEL", "jev-latest")
        headers = {"Authorization": f"Bearer {os.environ['TYPESAFE_API_KEY']}"}
    body = {
        "model": model,
        "state": json.dumps(observation, sort_keys=True),
        "questions": {
            "decision": {
                "type": "choice",
                "instructions": "Choose the action that maximizes your final score while accounting for the shared resource and other cogs' behavior.",
                "criteria": criteria,
            }
        },
    }
    request = Request(
        f"{endpoint.rstrip('/')}/v1/systemone",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", **headers},
    )
    started = time.monotonic()
    with urlopen(
        request, timeout=min(8.0, observation["round_seconds"] * 0.8)
    ) as response:
        payload = JevResponse.model_validate_json(response.read())
    answer = payload.answers["decision"]
    if answer.choice not in actions or set(answer.probabilities) != set(actions):
        raise ValueError("Jev returned the wrong choice set")
    if not all(
        math.isfinite(value) and 0 <= value <= 1
        for value in answer.probabilities.values()
    ):
        raise ValueError("Jev returned an invalid probability")
    if abs(sum(answer.probabilities.values()) - 1) > len(actions) * 0.005 + 1e-6:
        raise ValueError("Jev probabilities do not sum to one")
    choice = max(actions, key=answer.probabilities.__getitem__)
    logger.info(
        "Jev round %d choice %s reported %s latency_ms %d",
        observation["round"],
        choice,
        answer.choice,
        round((time.monotonic() - started) * 1000),
    )
    return actions[choice]


def registration() -> dict[str, str]:
    prompt = (os.environ.get("PLAYER_PROMPT") or "").strip()[:PROMPT_MAX_RUNES]
    scripted = (os.environ.get("PLAYER_SCRIPTED") or "").strip()[:SCRIPTED_MAX_RUNES]
    if os.environ.get("PLAYER_JEV") == "1":
        scripted = "steward"
    return {"type": "prompt", "prompt": prompt, "scripted": scripted}


async def connect_with_retry(url: str, timeout: float = CONNECT_TIMEOUT_SECONDS):
    """Connect, retrying a not-yet-listening game until `timeout` runs out.

    Bounded, never unbounded: when the window closes the last error is raised
    and `main` turns it into a clean exit 0.
    """
    deadline = time.monotonic() + timeout
    delay = 0.5
    attempt = 0
    while True:
        attempt += 1
        try:
            return await websockets.connect(
                url,
                ping_interval=PING_INTERVAL_SECONDS,
                ping_timeout=PING_TIMEOUT_SECONDS,
            )
        except Exception as error:  # noqa: BLE001 - any startup race is retryable
            if time.monotonic() >= deadline:
                logger.info(
                    "could not reach the game after %d attempts: %s", attempt, error
                )
                raise
            if attempt == 1:
                logger.info("game not listening yet (%s); retrying", error)
            await asyncio.sleep(delay)
            delay = min(CONNECT_RETRY_MAX_SECONDS, delay * 1.5)


async def main() -> None:
    url = os.environ["COWORLD_PLAYER_WS_URL"]
    frame = registration()
    logger.info(
        "registering as %s and connecting to %s",
        frame["scripted"] or ("prompt" if frame["prompt"] else "default steward"),
        url,
    )
    try:
        websocket = await connect_with_retry(url)
    except Exception as error:  # noqa: BLE001 - an absent seat must not fail the episode
        logger.info("giving up on the game socket (%s), exiting", error)
        return
    try:
        await websocket.send(json.dumps(frame, ensure_ascii=False))
        logger.info("registered; receiving rounds until the game says final")
        deadline = time.monotonic() + SPECTATE_TIMEOUT_SECONDS
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                logger.info("spectate deadline reached without a final frame, exiting")
                return
            raw = await asyncio.wait_for(websocket.recv(), timeout=remaining)
            message = cast(dict[str, Any], json.loads(raw))
            if message["type"] == "observation" and os.environ.get("PLAYER_JEV") == "1":
                await websocket.send(
                    json.dumps({"type": "decision", **jev_action(message)})
                )
            if message.get("type") == "final":
                logger.info("received final message, exiting")
                return
    except asyncio.TimeoutError:
        logger.info("no frame from the game inside the spectate window, exiting")
    except websockets.exceptions.ConnectionClosed:
        # The server exiting after the last round is the episode-over signal for
        # a seat still waiting; a closed socket here is lifecycle, not an error.
        logger.info("server closed the connection, exiting")
    except OSError as error:
        logger.info("player socket dropped (%s), exiting", error)
    finally:
        await websocket.close()


if __name__ == "__main__":
    asyncio.run(main())
