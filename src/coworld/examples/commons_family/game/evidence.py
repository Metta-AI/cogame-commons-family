"""Private request lifecycle and host-selected decisions; never spectator data."""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


class EvidenceRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EpisodeContext(EvidenceRecord):
    kind: Literal["episode_context"] = "episode_context"
    schema_version: Literal[1] = 1
    game: Literal["commons-family"] = "commons-family"
    episode_id: str | None
    source_revision: str | None


class InferenceAttempt(EvidenceRecord):
    kind: Literal["inference_started", "inference_finished"] = "inference_started"
    round: int
    slot: int
    attempt: int
    prompt: list[dict[str, str]]
    prompt_template: dict[str, Any]
    model: str
    request: dict[str, Any] | None = None
    raw_response: str | None = None
    response: str | None = None
    response_with_prefill: str | None = None
    parsed_reply: dict[str, Any] | None = None
    outcome: str = "unknown_failure"
    platform_call_id: None = None
    behavior_logprobs: None = None


class HostDecision(EvidenceRecord):
    kind: Literal["host_decision"] = "host_decision"
    round: int
    slot: int
    source: str
    selected_attempt: int | None
    proposal: dict[str, Any]
    action: dict[str, Any]


class EpisodeEnd(EvidenceRecord):
    kind: Literal["episode_end"] = "episode_end"
    status: Literal["completed", "truncated"]
    results: dict[str, Any]


class PrivateEvidence:
    def __init__(self) -> None:
        self.path = os.environ.get("COMMONS_FAMILY_TRACE_PATH", "")
        self.lock = threading.Lock()
        if self.path:
            descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(descriptor)
            self.write(
                EpisodeContext(
                    episode_id=os.environ.get("COWORLD_EPISODE_ID"),
                    source_revision=os.environ.get("COWORLD_SOURCE_REVISION"),
                )
            )

    def write(self, record: EvidenceRecord) -> None:
        if self.path:
            with self.lock, Path(self.path).open("a") as stream:
                stream.write(record.model_dump_json() + "\n")
