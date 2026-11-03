"""Optional registered checkpoint route shared by player, server and transport."""

import re


def checkpoint_model(value: str) -> str:
    if (
        not isinstance(value, str)
        or re.fullmatch(r"checkpoint/[0-9a-f]{64}", value) is None
    ):
        raise ValueError(
            "Expected a registered checkpoint/<64-character weight hash> model route"
        )
    return value
