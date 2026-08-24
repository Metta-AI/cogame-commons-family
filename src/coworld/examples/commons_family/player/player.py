"""Commons Family websocket player entrypoint.

The player container's only job is to register its policy. Every decision is
made in the game container (see `game/llm.py` for why), so this process
connects, sends one `prompt` frame naming either its standing orders
(`PLAYER_PROMPT`) or a scripted baseline (`PLAYER_SCRIPTED`), and then
spectates until the game sends `final`.

`PLAYER_SCRIPTED` wins when both are set. A dead socket is lifecycle, not an
error: this process exits 0 whatever the game does to it, because a player
container that exits non-zero fails the whole episode.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any, cast

import websockets

from coworld.examples.commons_family.shared.log_shipper import get_logger

logger = get_logger("commons_family.player")

PROMPT_MAX_RUNES = 1200
SCRIPTED_MAX_RUNES = 32
# The player container and the game container start together, so the first
# connect regularly lands before uvicorn is listening. Giving up there costs
# the seat the whole episode: the game waits out its 180 s connect timeout and
# then plays the seat as absent. Retry inside that window instead.
CONNECT_TIMEOUT_SECONDS = float(os.environ.get("COMMONS_FAMILY_CONNECT_TIMEOUT_SECONDS", "150"))
CONNECT_RETRY_MAX_SECONDS = 2.0


def registration() -> dict[str, str]:
    prompt = (os.environ.get("PLAYER_PROMPT") or "").strip()[:PROMPT_MAX_RUNES]
    scripted = (os.environ.get("PLAYER_SCRIPTED") or "").strip()[:SCRIPTED_MAX_RUNES]
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
            return await websockets.connect(url, ping_timeout=None)
        except Exception as error:  # noqa: BLE001 - any startup race is retryable
            if time.monotonic() >= deadline:
                logger.info("could not reach the game after %d attempts: %s", attempt, error)
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
        logger.info("registered; spectating until the game says final")
        while True:
            message = cast(dict[str, Any], json.loads(await websocket.recv()))
            if message.get("type") == "final":
                logger.info("received final message, exiting")
                return
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
