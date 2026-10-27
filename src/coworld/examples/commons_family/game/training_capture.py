"""Engine-owned private decision JSONL using the Coworld trajectory v1 contract."""

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from coworld.examples.commons_family.game.engine import parse_decision
from coworld.examples.commons_family.shared.artifact_io import (
    artifact_method,
    write_data,
)

_PHYSICAL_RULES = {
    "cleanup": ("stock_capacity", "regrowth_rate", "collapse_threshold", "silt_rate", "clean_power"),
    "harvest": ("patch_capacity", "patch_regrowth"),
    "allelopathic": ("field_size", "ripen_base", "favorite_bonus", "favorite_base"),
    "mushrooms": (
        "mushroom_capacity",
        "mushroom_color_cap",
        "spawn_per_round",
        "red_value",
        "green_value",
        "blue_value",
    ),
}


def physical_family(config: Any, state: Any) -> str:
    """Group actual physical state/rules, independent of alias and wording RNG."""
    initial = copy.deepcopy(state.module_state)
    if config.module == "harvest":
        rights = initial["property_rights"]
        if rights != "closed":
            initial.pop("owner")
        if rights != "partnership":
            initial.pop("pairs")
        if rights == "partnership":
            initial["pairs"] = [sorted(pair) for pair in initial["pairs"]]
        if rights not in {"closed", "partnership"}:
            initial["property_rights"] = "open"
    elif config.module == "allelopathic" and config.favorite_bonus == config.favorite_base:
        initial.pop("favorites")
    payload = {
        "module": config.module,
        "num_agents": config.num_agents,
        "effort_budget": config.effort_budget,
        "initial_state": initial,
        "rules": {name: getattr(config, name) for name in _PHYSICAL_RULES[config.module]},
        "sanctions": {"cost": config.sanction_cost, "burn": config.sanction_burn} if config.sanctions_enabled else None,
    }
    content = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "commons-family-physical-v1-" + hashlib.sha256(content.encode("utf-8")).hexdigest()


class TrainingCapture:
    def __init__(self) -> None:
        self.seed_family: str | None = None
        self.failure: str | None = None
        self.pending_attempts: dict[int, list[dict]] = {}
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

    def bind_episode(self, config: Any, state: Any) -> None:
        if not self.destination:
            return
        if state.round != 0 or state.history or self.decisions:
            raise ValueError("physical family must bind the actual initial episode state")
        self.seed_family = physical_family(config, state)

    @staticmethod
    def action(decision: Any) -> dict:
        return {key: getattr(decision, key) for key in decision.__slots__ if key != "src"}

    def parse_model_replies(self, answers: dict, config: Any, state: Any, module: Any) -> None:
        """Capture the real parser result before player overrides or settlement."""
        if not self.destination:
            return
        for slot, (reply, _cause) in answers.items():
            if reply is None or not self.pending_attempts.get(slot):
                continue
            attempt = self.pending_attempts[slot][-1]
            proposal = attempt.get("_proposal")
            if proposal is None:
                continue
            parsed = self.action(parse_decision(proposal, slot, config, state, module, "llm"))
            attempt["parsed_action"] = parsed
            changed = [
                key
                for key, value in proposal.items()
                if key in parsed and json.dumps(value, sort_keys=True) != json.dumps(parsed[key], sort_keys=True)
            ]
            if changed:
                attempt["rejection_reason"] = "game parser normalized supplied fields: " + ", ".join(sorted(changed))

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
            action = self.action(decision)
            evidence = copy.deepcopy(attempts.get(slot, []))
            selected = None
            for attempt in evidence:
                candidate = attempt.pop("_proposal", None)
                if candidate is not None:
                    if (
                        decision.src == "llm"
                        and attempt.get("parsed_action") == action
                        and not attempt.get("rejection_reason")
                    ):
                        attempt["accepted"] = True
                        selected = attempt["attempt_id"]
                    elif not attempt.get("rejection_reason"):
                        attempt.update(accepted=False, rejection_reason="overridden before round settlement")
            fallback = (
                decision.src.startswith("fallback:")
                or decision.src == "pass"
                or (decision.src == "llm" and selected is None)
            )
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
                event["fallback_origin"] = "engine-parser-normalization" if decision.src == "llm" else decision.src
            self.decisions.append(copy.deepcopy(event))
        self.pending_attempts = {}

    def finish(self, outcome: dict) -> None:
        if not self.destination:
            return
        if self.seed_family is None:
            raise ValueError("private trajectory requires the actual initial physical family")
        reason = outcome["reason"]
        if self.failure:
            outcome = dict(
                outcome, runtime_error=self.failure, unsettled_model_attempts=copy.deepcopy(self.pending_attempts)
            )
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
