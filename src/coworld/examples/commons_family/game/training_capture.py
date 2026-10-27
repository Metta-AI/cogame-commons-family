"""Engine-owned private decision JSONL using the Coworld trajectory v1 contract."""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from coworld.examples.commons_family.shared.artifact_io import (
    artifact_method,
    write_data,
)


class TrainingCapture:
    def __init__(self, seed: int) -> None:
        self.failure: str | None = None
        self.destination = os.environ.get("COGAME_SAVE_TRAJECTORY_URI", "")
        self.identity: dict[str, Any] = {}
        self.decisions: list[dict[str, Any]] = []
        if not self.destination:
            return
        for field, env in (
            ("episode_id", "COWORLD_EPISODE_ID"),
            ("game_version", "COWORLD_GAME_VERSION"),
            ("source_revision", "COWORLD_SOURCE_REVISION"),
        ):
            value = os.environ.get(env, "")
            if not value:
                raise ValueError(f"private trajectory requires {env}")
            self.identity[field] = value
        self.identity.update(
            schema_version="1",
            game=os.environ.get("COWORLD_GAME_NAME", "commons-family"),
        )
        self.seed_family = f"commons-family-{seed}"

    def record_round(
        self,
        observations: dict,
        decisions: list,
        attempts: dict[int, list[dict]],
        registered: set[int],
    ) -> None:
        if not self.destination:
            return
        for slot, decision in enumerate(decisions):
            action = {key: getattr(decision, key) for key in decision.__slots__ if key != "src"}
            evidence = copy.deepcopy(attempts.get(slot, []))
            selected = None
            for attempt in evidence:
                candidate = attempt.pop("_proposal", None)
                if candidate is not None:
                    if decision.src == "llm":
                        attempt.update(accepted=True, parsed_action=action)
                        selected = attempt["attempt_id"]
                    else:
                        attempt.update(
                            accepted=False,
                            rejection_reason="overridden before round settlement",
                        )
            fallback = decision.src.startswith("fallback:") or decision.src == "pass"
            if selected is None:
                origin = (
                    "fallback"
                    if fallback
                    else "teacher"
                    if decision.src.startswith("scripted:") and slot in registered
                    else "unknown"
                )
                selected = f"{observations[slot]['round']}-{slot}-applied"
                evidence.append(
                    dict(
                        attempt_id=selected,
                        policy=decision.src or "missing",
                        origin=origin,
                        accepted=True,
                        parsed_action=action,
                    )
                )
            event = dict(
                self.identity,
                event_type="decision",
                decision_index=len(self.decisions),
                decision_id=f"{observations[slot]['round']}-{slot}",
                seat=str(slot),
                visibility="private",
                observation=observations[slot],
                attempts=evidence,
                selected_attempt_id=selected,
                executed_action=action,
                action_status="fallback" if fallback else "accepted",
            )
            if fallback:
                event["fallback_origin"] = decision.src
            self.decisions.append(copy.deepcopy(event))

    def finish(self, outcome: dict) -> None:
        if not self.destination:
            return
        reason = outcome["reason"]
        if self.failure:
            outcome = dict(outcome, runtime_error=self.failure)
        summary = dict(
            self.identity,
            event_type="episode",
            seed_family=self.seed_family,
            status="failed"
            if self.failure
            else "completed"
            if reason == "complete"
            else "truncated"
            if reason == "deadline"
            else "failed",
            outcome=outcome,
            participant_outcomes=outcome["scores"],
        )
        content = "".join(json.dumps(event, ensure_ascii=False) + "\n" for event in [*self.decisions, summary])
        parsed = urlparse(self.destination)
        if parsed.scheme == "file":
            target = Path(unquote(parsed.path))
            target.parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(content)
        else:
            write_data(
                self.destination,
                content,
                content_type="application/x-ndjson",
                http_method=artifact_method("COGAME_SAVE_TRAJECTORY_METHOD"),
            )
