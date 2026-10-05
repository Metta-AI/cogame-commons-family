"""Engine-issued Commons decision windows and bounded player evidence frames."""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import ConfigDict, Field, JsonValue, StrictBool, StrictInt, TypeAdapter

from commons_family.game.engine import PLAYER_PROTOCOL
from commons_family.game.prompt import NativeProfile
from commons_family.shared.native import Attempt, PrivateModel, Request

MAX_FRAME_BYTES = 16 * 1024 * 1024
MAX_PENDING_FRAMES = 4


class ScriptedProfile(PrivateModel):
    kind: Literal["scripted"] = "scripted"
    policy: Literal["steward", "cleaner", "punisher", "free_rider", "random"]


Profile = Annotated[NativeProfile | ScriptedProfile, Field(discriminator="kind")]


class Register(PrivateModel):
    type: Literal["register"] = "register"
    protocol: Literal["commons-family.player.v2"] = PLAYER_PROTOCOL
    slot: StrictInt = Field(ge=0, le=11)
    profile: Profile


class Welcome(PrivateModel):
    type: Literal["welcome"] = "welcome"
    protocol: Literal["commons-family.player.v2"] = PLAYER_PROTOCOL
    slot: StrictInt = Field(ge=0, le=11)
    alias: str
    module: str
    rounds: StrictInt = Field(gt=0)
    num_players: StrictInt = Field(ge=2, le=12)


class DecisionWindow(PrivateModel):
    type: Literal["window"] = "window"
    window_id: UUID
    round: StrictInt = Field(gt=0)
    slot: StrictInt = Field(ge=0, le=11)
    observation: dict[str, JsonValue]
    request: Request | None
    remaining_seconds: float = Field(gt=0, allow_inf_nan=False)


class CallGrant(PrivateModel):
    type: Literal["grant"] = "grant"
    window_id: UUID
    grant_id: UUID
    slot: StrictInt = Field(ge=0, le=11)
    semantic_attempt: StrictInt = Field(ge=0, le=1)
    transport_attempt: StrictInt = Field(ge=0, le=3)
    request: Request
    timeout_seconds: float = Field(gt=0, allow_inf_nan=False)


class RequestGrant(PrivateModel):
    type: Literal["request_grant"] = "request_grant"
    window_id: UUID
    slot: StrictInt = Field(ge=0, le=11)
    semantic_attempt: StrictInt = Field(ge=0, le=1)
    transport_attempt: StrictInt = Field(ge=0, le=3)


class AttemptProgress(PrivateModel):
    type: Literal["attempt_progress"] = "attempt_progress"
    window_id: UUID
    grant_id: UUID
    attempt: Attempt


class Submit(PrivateModel):
    type: Literal["submit"] = "submit"
    window_id: UUID
    slot: StrictInt = Field(ge=0, le=11)
    selected_attempt_id: str | None
    action: dict[str, JsonValue]


class Stop(PrivateModel):
    type: Literal["stop"] = "stop"
    nonce: UUID
    remaining_seconds: float = Field(gt=0, allow_inf_nan=False)


class Stopped(PrivateModel):
    type: Literal["stopped"] = "stopped"
    nonce: UUID
    slot: StrictInt = Field(ge=0, le=11)
    joined: StrictBool


class Final(PrivateModel):
    type: Literal["final"] = "final"
    results: dict[str, JsonValue]


PlayerFrame = Annotated[Register | RequestGrant | AttemptProgress | Submit | Stopped, Field(discriminator="type")]
GameFrame = Annotated[Welcome | DecisionWindow | CallGrant | Stop | Final, Field(discriminator="type")]
PLAYER_FRAMES = TypeAdapter(PlayerFrame, config=ConfigDict(hide_input_in_errors=True))
GAME_FRAMES = TypeAdapter(GameFrame, config=ConfigDict(hide_input_in_errors=True))


def read_player_frame(raw: str | bytes) -> PlayerFrame:
    if len(raw.encode("utf-8") if isinstance(raw, str) else raw) > MAX_FRAME_BYTES:
        raise ValueError("player frame exceeds its bounded protocol capacity")
    return PLAYER_FRAMES.validate_json(raw)


def read_game_frame(raw: str | bytes) -> GameFrame:
    if len(raw.encode("utf-8") if isinstance(raw, str) else raw) > MAX_FRAME_BYTES:
        raise ValueError("game frame exceeds its bounded protocol capacity")
    return GAME_FRAMES.validate_json(raw)
