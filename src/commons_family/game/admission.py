"""The engine owns simultaneous windows, request budgets and action admission."""

from __future__ import annotations

import base64
import time
from collections import deque
from dataclasses import dataclass, field
from uuid import UUID, uuid4

from commons_family.game.engine import CommonsConfig, GameState, observation, parse_decision
from commons_family.game.modules.base import Decision, Module
from commons_family.game.prompt import DecisionRenderer, NativeProfile
from commons_family.game.llm import extract_json
from commons_family.shared.attempt_store import AttemptStore
from commons_family.shared.native import Response, TemperedSamplingEvidence
from commons_family.shared.protocol import (
    AttemptProgress,
    CallGrant,
    DecisionWindow,
    Register,
    RequestGrant,
    ScriptedProfile,
    Submit,
)

THROTTLE_SLEEPS = (0.5, 1.0, 2.0)


@dataclass
class WindowOwner:
    window: DecisionWindow
    deadline: float
    profile: NativeProfile | ScriptedProfile
    grants: list[CallGrant] = field(default_factory=list)
    attempts: AttemptStore = field(default_factory=AttemptStore)
    selected_attempt_id: UUID | None = None
    installed: Decision | None = None
    closed: bool = False
    rejection: str | None = None
    retry_at: float = 0.0


class Roster:
    """Player inputs freeze once, before any private observation leaves the engine."""

    def __init__(self, seats: int):
        self.seats = seats
        self.registrations: dict[int, Register] = {}
        self.frozen = False

    def register(self, assigned_slot: int, frame: Register) -> None:
        if frame.slot != assigned_slot or not 0 <= assigned_slot < self.seats:
            raise ValueError("registration differs from the authenticated assigned seat")
        if self.frozen or assigned_slot in self.registrations:
            raise ValueError("registration is already frozen for this seat")
        self.registrations[assigned_slot] = frame.model_copy(deep=True)

    def freeze(self) -> None:
        if self.frozen or set(self.registrations) != set(range(self.seats)):
            raise ValueError("private windows require one frozen registration per seat")
        self.frozen = True


class DecisionAdmission:
    """Game code issues grants; only player containers own native HTTP."""

    def __init__(self, config: CommonsConfig, module: Module, roster: Roster):
        self.config = config
        self.module = module
        self.roster = roster
        self.renderer = DecisionRenderer(config, module)
        self.windows: dict[UUID, WindowOwner] = {}
        self.active: dict[int, UUID] = {}
        self.requests: deque[float] = deque()
        self.closed = False
        self.sealed = False

    def begin(self, state: GameState, deadline: float) -> list[DecisionWindow]:
        if (
            not self.roster.frozen
            or self.closed
            or any(not self.windows[identity].closed for identity in self.active.values())
        ):
            raise ValueError("simultaneous round is not available for admission")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ValueError("round has no remaining original decision budget")
        self.active.clear()
        issued = []
        for slot in range(self.config.num_agents):
            profile = self.roster.registrations[slot].profile.model_copy(deep=True)
            private = observation(state, self.config, slot, self.module)
            window = DecisionWindow(
                window_id=uuid4(),
                round=state.round + 1,
                slot=slot,
                observation=private,
                request=self.renderer.request(private, profile) if isinstance(profile, NativeProfile) else None,
                remaining_seconds=remaining,
            )
            self.windows[window.window_id] = WindowOwner(window.model_copy(deep=True), deadline, profile)
            self.active[slot] = window.window_id
            issued.append(window)
        return issued

    def grant(self, assigned_slot: int, frame: RequestGrant) -> CallGrant:
        entry = self.windows[frame.window_id]
        now = time.monotonic()
        if (
            self.closed
            or entry.closed
            or frame.slot != assigned_slot
            or entry.window.slot != assigned_slot
            or self.active[assigned_slot] != frame.window_id
            or now >= entry.deadline
            or not isinstance(entry.profile, NativeProfile)
        ):
            raise ValueError("native grant has no active issued private window")
        if entry.grants:
            prior_grant = entry.grants[-1]
            prior = entry.attempts[prior_grant.grant_id]
            if prior.response_reader_joined is not True:
                raise ValueError("retry requires the actual prior native reader joined")
            throttled = prior.http_status in (429, 529)
            expected = (
                (prior_grant.semantic_attempt, prior_grant.transport_attempt + 1)
                if throttled
                else (prior_grant.semantic_attempt + 1, 0)
            )
            if (frame.semantic_attempt, frame.transport_attempt) != expected or now < entry.retry_at:
                raise ValueError("retry changes the source-owned semantic or throttle ladder")
            if not throttled and entry.rejection is None:
                raise ValueError("semantic retry requires independently observed rejection")
        elif (frame.semantic_attempt, frame.transport_attempt) != (0, 0):
            raise ValueError("first call must use the first original attempt")
        while self.requests and now - self.requests[0] > 60:
            self.requests.popleft()
        if len(self.requests) >= self.config.llm_max_requests_per_minute:
            entry.rejection = "rate_budget"
            raise ValueError("original shared requests-per-minute budget exhausted")
        grant = CallGrant(
            window_id=frame.window_id,
            grant_id=uuid4(),
            slot=assigned_slot,
            semantic_attempt=frame.semantic_attempt,
            transport_attempt=frame.transport_attempt,
            request=self.renderer.request(entry.window.observation, entry.profile, frame.semantic_attempt > 0),
            timeout_seconds=min(self.config.decision_timeout_seconds, entry.deadline - now),
        )
        entry.grants.append(grant)
        self.requests.append(now)
        return grant.model_copy(deep=True)

    def progress(self, assigned_slot: int, frame: AttemptProgress) -> None:
        entry = self.windows[frame.window_id]
        matching = [grant for grant in entry.grants if grant.grant_id == frame.grant_id]
        if self.sealed or entry.window.slot != assigned_slot or len(matching) != 1:
            raise ValueError("native progress has no issued call owner")
        grant = matching[0]
        attempt = frame.attempt
        if (
            attempt.attempt_id != str(grant.grant_id)
            or attempt.slot != assigned_slot
            or attempt.request != grant.request
            or attempt.timeout_ms != grant.timeout_seconds * 1000
        ):
            raise ValueError("native progress rewrites the engine-issued caller request")
        entry.attempts.receive(grant.grant_id, attempt)
        if attempt.http_status in (429, 529) and attempt.response_reader_joined is True:
            if grant.transport_attempt < len(THROTTLE_SLEEPS):
                entry.retry_at = time.monotonic() + THROTTLE_SLEEPS[grant.transport_attempt]

    def submit(self, assigned_slot: int, frame: Submit, state: GameState) -> Decision:
        """Received completion is parsed independently before the ordinary engine acts."""
        entry = self.windows[frame.window_id]
        if (
            self.closed
            or entry.closed
            or frame.slot != assigned_slot
            or entry.window.slot != assigned_slot
            or self.active[assigned_slot] != frame.window_id
            or time.monotonic() >= entry.deadline
        ):
            raise ValueError("submitted action has no current issued private window")
        if isinstance(entry.profile, ScriptedProfile):
            if frame.selected_attempt_id is not None:
                raise ValueError("scripted player cannot assert a native call or teacher authority")
            parsed = parse_decision(frame.action, assigned_slot, self.config, state, self.module, "external:scripted")
        else:
            if frame.selected_attempt_id is None:
                raise ValueError("native submission requires its observed issued call identity")
            identity = UUID(frame.selected_attempt_id)
            attempt = entry.attempts[identity]
            if (
                attempt.response_reader_joined is not True
                or attempt.response_complete is not True
                or attempt.http_status != 200
                or attempt.response_body_b64 is None
            ):
                raise ValueError("selected native response is not complete and owned-joined")
            raw = base64.b64decode(attempt.response_body_b64, validate=True).decode("utf-8")
            if raw != attempt.raw_response:
                raise ValueError("selected response differs from actual received byte evidence")
            received = Response.model_validate_json(raw)
            stop_reason = (
                received.stop_reason if received.sampling_evidence is None else received.sampling_evidence.stop_reason
            )
            if (
                received.text != attempt.response
                or received.model != attempt.model
                or received.usage != attempt.usage
                or received.sampling_evidence != attempt.sampling_evidence
                or stop_reason != attempt.stop_reason
            ):
                raise ValueError("selected response metadata differs from its actual native body")
            sample = received.sampling_evidence
            if sample is not None:
                temperature = (
                    sample.temperature
                    if isinstance(sample, TemperedSamplingEvidence)
                    else 0
                    if sample.sampling is None
                    else 1
                )
                if (
                    attempt.request.temperature != temperature
                    or attempt.request.top_p != 1
                    or sample.max_new_tokens != attempt.request.max_tokens
                    or sample.response != received.text
                    or received.usage.input_tokens != len(sample.prompt_token_ids)
                    or received.usage.output_tokens != len(sample.completion_token_ids)
                    or received.stop_reason != ("end_turn" if sample.stop_reason == "eos" else "max_tokens")
                ):
                    raise ValueError("selected sampling evidence differs from its issued request")
            authored = extract_json(entry.profile.parser_input(received.text))
            if authored is None:
                entry.rejection = "parse"
                raise ValueError("selected native completion has no ordinary decision object")
            if authored != frame.action:
                raise ValueError("submitted action differs from independently parsed native completion")
            parsed = parse_decision(authored, assigned_slot, self.config, state, self.module, "model")
            entry.selected_attempt_id = identity
        entry.installed = parsed
        entry.closed = True
        return parsed

    def stop(self) -> None:
        self.closed = True
        for entry in self.windows.values():
            entry.closed = True

    def seal(self) -> None:
        if not self.closed or any(
            attempt.response_reader_joined is False
            for entry in self.windows.values()
            for attempt in entry.attempts.values()
        ):
            raise ValueError("private decision evidence cannot seal before owned reader joins")
        self.sealed = True
        for entry in self.windows.values():
            entry.attempts.close_joined()
