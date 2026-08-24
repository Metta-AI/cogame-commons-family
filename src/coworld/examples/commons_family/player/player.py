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
from typing import Any, cast

import websockets

from coworld.examples.commons_family.shared.log_shipper import get_logger

logger = get_logger("commons_family.player")

PROMPT_MAX_RUNES = 1200
SCRIPTED_MAX_RUNES = 32


def registration() -> dict[str, str]:
    prompt = (os.environ.get("PLAYER_PROMPT") or "").strip()[:PROMPT_MAX_RUNES]
    scripted = (os.environ.get("PLAYER_SCRIPTED") or "").strip()[:SCRIPTED_MAX_RUNES]
    return {"type": "prompt", "prompt": prompt, "scripted": scripted}


async def main() -> None:
    url = os.environ["COWORLD_PLAYER_WS_URL"]
    frame = registration()
    logger.info(
        "registering as %s and connecting to %s",
        frame["scripted"] or ("prompt" if frame["prompt"] else "default steward"),
        url,
    )
    try:
        async with websockets.connect(url, ping_timeout=None) as websocket:
            await websocket.send(json.dumps(frame, ensure_ascii=False))
            while True:
                message = cast(dict[str, Any], json.loads(await websocket.recv()))
                if message.get("type") == "final":
                    logger.info("received final message, exiting")
                    return
    except websockets.exceptions.ConnectionClosed:
        logger.info("server closed the connection, exiting")
    except OSError as error:
        # A refused or dropped connection must not fail the episode.
        logger.info("player socket unavailable (%s), exiting", error)


asyncio.run(main())
