"""The LLM seat — inside the GAME container, not the player container.

Meadow put its LLM policy in the player container, one Bedrock client per pod.
This game moves it server-side, adopting bullwhip's split wholesale, for four
load-bearing reasons: only the party that owns the round barrier can issue all
six seats' calls as ONE parallel batch; only that party can enforce
retry-once-then-fall-back-to-scripted (a hung player pod would otherwise
silently become a passing seat); one container needs the secret instead of six,
which is what `ANTHROPIC_API_KEY_URI` on the game runnable means; and the
scripted baselines are already pure `obs -> action` functions, so they become
the in-process fallback.

Transport ladder, in order: `AWS_ENDPOINT_URL_BEDROCK_RUNTIME` (the hosted
sidecar) -> Bedrock InvokeModel; else `ANTHROPIC_API_KEY`; else
`ANTHROPIC_API_KEY_URI`; else DISABLED, which makes zero network calls for the
whole episode and lets every prompt seat play `steward`.
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from coworld.examples.commons_family.game.engine import (
    PROMPT_MAX_RUNES,
    CommonsConfig,
    truncate_runes,
)
from coworld.examples.commons_family.game.modules.base import Module
from coworld.examples.commons_family.shared.log_shipper import get_logger

logger = get_logger("commons_family.llm")

DEFAULT_BEDROCK_MODEL = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
DEFAULT_ANTHROPIC_MODEL = "claude-haiku-4-5-20251001"

# Meadow's ladder was (1, 2, 4, 8, 16, 30, 60) — two minutes, far past our
# 20 s round deadline. Truncated to fit inside one round.
THROTTLE_SLEEPS = (0.5, 1.0, 2.0)

RETRY_HINT = (
    "Your last reply was not usable. Reply with ONE JSON object beginning with { "
    "and only the fields in the schema."
)

SYSTEM_TEMPLATE = """You are {alias}, one of {num_players} cogs sharing a commons for {rounds} rounds. Module: {module}.
{module_rules}
Every round all {num_players} cogs decide at the same time and the results settle together. You have
{effort_budget} effort units per round to split between taking and maintaining.
Your score is everything you take, minus punishment you pay or receive.{sanction_rules}{norm_line}
The resource is shared: what you take is not there next round for anyone, including you.
Each round you receive the game state as JSON. Reply with ONLY one JSON object, no other text.
Your reply MUST begin with the character {{.
Schema: {schema_line}"""

SANCTION_RULES = (
    "\nYou may also sanction one cog per round: you pay {cost}, they lose {burn}."
)


class LlmThrottled(Exception):
    """The provider asked us to slow down. Retryable."""


class LlmTimeout(Exception):
    """The call did not answer inside its deadline. Retryable."""


class LlmTransportError(Exception):
    """Anything else that is not the model's fault. Retryable once."""


class LlmRateBudget(Exception):
    """The rolling requests-per-minute budget is spent for this minute."""


_AUTO = object()


# ---------------------------------------------------------------------------
# Transports
# ---------------------------------------------------------------------------


class BedrockTransport:
    """Hosted path: the runner's sidecar endpoint, picked up by boto3."""

    def __init__(self, model: str) -> None:
        self.model = model
        self._client = None
        self._lock = threading.Lock()

    def _ensure(self, timeout: float):
        with self._lock:
            if self._client is None:
                import boto3  # noqa: PLC0415  # boto3 ships in the image, not the package
                from botocore.config import Config  # noqa: PLC0415

                self._client = boto3.client(
                    "bedrock-runtime",
                    config=Config(
                        connect_timeout=max(1.0, timeout / 2),
                        read_timeout=max(1.0, timeout),
                        retries={"max_attempts": 1},
                    ),
                )
        return self._client

    def complete(self, body: dict, timeout: float) -> str:
        import botocore.exceptions  # noqa: PLC0415

        client = self._ensure(timeout)
        try:
            response = client.invoke_model(modelId=self.model, body=json.dumps(body))
            content = json.loads(response["body"].read())["content"]
            return next((block["text"] for block in content if block["type"] == "text"), "")
        except botocore.exceptions.ReadTimeoutError as error:
            raise LlmTimeout(str(error)) from error
        except botocore.exceptions.ConnectTimeoutError as error:
            raise LlmTimeout(str(error)) from error
        except botocore.exceptions.ClientError as error:
            code = error.response.get("Error", {}).get("Code", "")
            if code in ("ThrottlingException", "ServiceUnavailableException", "ModelTimeoutException"):
                raise LlmThrottled(code) from error
            # Auth and validation errors are configuration bugs; they must be
            # loud at round 0, not silently played as a scripted baseline.
            raise
        except botocore.exceptions.EndpointConnectionError as error:
            raise LlmTransportError(str(error)) from error


class AnthropicTransport:
    """Direct API path, used locally and whenever a key is present."""

    def __init__(self, model: str, api_key: str) -> None:
        self.model = model
        self.api_key = api_key
        self.base = os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com").rstrip("/")

    def complete(self, body: dict, timeout: float) -> str:
        payload = {
            "model": self.model,
            "max_tokens": body["max_tokens"],
            "system": body["system"],
            "messages": body["messages"],
        }
        request = Request(
            f"{self.base}/v1/messages",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "content-type": "application/json",
                "anthropic-version": "2023-06-01",
                "x-api-key": self.api_key,
                "user-agent": "cogame-commons-family/0.1",
            },
        )
        try:
            with urlopen(request, timeout=timeout) as response:
                content = json.load(response)["content"]
                return next((block["text"] for block in content if block["type"] == "text"), "")
        except HTTPError as error:
            if error.code in (429, 529):
                raise LlmThrottled(str(error.code)) from error
            raise
        except TimeoutError as error:
            raise LlmTimeout(str(error)) from error
        except URLError as error:
            if isinstance(error.reason, TimeoutError):
                raise LlmTimeout(str(error)) from error
            raise LlmTransportError(str(error)) from error


def build_transport() -> tuple[object | None, str]:
    """The credential ladder. Returns `(transport, model)`; `(None, "")` disables."""
    if os.environ.get("AWS_ENDPOINT_URL_BEDROCK_RUNTIME"):
        model = os.environ.get("COMMONS_FAMILY_MODEL", DEFAULT_BEDROCK_MODEL)
        return BedrockTransport(model), model
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        uri = os.environ.get("ANTHROPIC_API_KEY_URI")
        if uri:
            try:
                from coworld.examples.commons_family.shared.artifact_io import (  # noqa: PLC0415
                    read_data,
                )

                key = read_data(uri).decode("utf-8").strip()
            except Exception as error:  # noqa: BLE001 - a bad URI must not kill the episode
                logger.warning("could not read ANTHROPIC_API_KEY_URI: %s", error)
                key = ""
    if key:
        model = os.environ.get("COMMONS_FAMILY_MODEL", DEFAULT_ANTHROPIC_MODEL)
        return AnthropicTransport(model, key), model
    return None, ""


# ---------------------------------------------------------------------------
# Reply extraction
# ---------------------------------------------------------------------------


def extract_json(raw: str) -> dict | None:
    """The first balanced `{...}` span, so leading or trailing prose is fine."""
    depth = 0
    start = -1
    in_string = False
    escaped = False
    for index, char in enumerate(raw):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                try:
                    parsed = json.loads(raw[start : index + 1])
                except json.JSONDecodeError:
                    return None
                return parsed if isinstance(parsed, dict) else None
    return None


# ---------------------------------------------------------------------------
# The decider
# ---------------------------------------------------------------------------


class LlmDecider:
    """One batch of LLM calls per round, one call per prompt seat.

    All prompt seats go out together on a single thread pool: this is a
    simultaneous-decision game, and issuing the calls sequentially is how an
    LLM coworld blows its play budget.
    """

    def __init__(
        self,
        config: CommonsConfig,
        module: Module,
        transport: object | None = _AUTO,  # type: ignore[assignment]
        model: str = "",
    ) -> None:
        if transport is _AUTO:
            transport, model = build_transport()
        self.config = config
        self.module = module
        self.transport = transport
        self.model = model or DEFAULT_ANTHROPIC_MODEL
        self.requests = 0
        self._systems: dict[int, str] = {}
        self._budget: deque[float] = deque()
        self._lock = threading.Lock()
        if self.transport is None:
            logger.info("no LLM credentials: every prompt seat plays the scripted fallback")

    @property
    def enabled(self) -> bool:
        return self.transport is not None

    # -- prompt ------------------------------------------------------------

    def system_prompt(self, obs: dict, standing_orders: str) -> str:
        sanction_rules = ""
        if obs["sanctions_enabled"]:
            sanction_rules = SANCTION_RULES.format(
                cost=obs["sanction_cost"], burn=obs["sanction_burn"]
            )
        norm_line = f"\nPosted norm: {obs['norm_text']}" if obs["norm_text"] else ""
        prompt = SYSTEM_TEMPLATE.format(
            alias=obs["alias"],
            num_players=obs["num_players"],
            rounds=obs["rounds"],
            module=obs["module"],
            module_rules=self.module.rules_text(self.config),
            effort_budget=obs["effort_budget"],
            sanction_rules=sanction_rules,
            norm_line=norm_line,
            schema_line=self.module.schema_line(self.config),
        )
        orders = truncate_runes(standing_orders, PROMPT_MAX_RUNES)
        if orders:
            prompt += f"\n\nSTANDING ORDERS\n{orders}"
        return prompt

    def user_message(self, obs: dict) -> str:
        payload = {key: value for key, value in obs.items() if key not in ("type", "round_seconds")}
        return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)

    # -- the batch ---------------------------------------------------------

    def decide(
        self, requests: dict[int, tuple[dict, str]], deadline: float
    ) -> dict[int, tuple[dict | None, str]]:
        """One parallel batch. Returns `{slot: (reply | None, cause)}`.

        `cause` is empty on success and one of `timeout, parse, rate_budget,
        transport, disabled` otherwise. Nothing here blocks past `deadline`.
        """
        if not requests:
            return {}
        if not self.enabled:
            return {slot: (None, "disabled") for slot in requests}
        slots = sorted(requests)
        with ThreadPoolExecutor(max_workers=max(1, len(slots))) as executor:
            answers = list(
                executor.map(
                    lambda slot: self._decide_seat(slot, *requests[slot], deadline), slots
                )
            )
        return dict(zip(slots, answers))

    def _decide_seat(
        self, slot: int, obs: dict, standing_orders: str, deadline: float
    ) -> tuple[dict | None, str]:
        if slot not in self._systems:
            self._systems[slot] = self.system_prompt(obs, standing_orders)
        system = self._systems[slot]
        user = self.user_message(obs)
        cause = "timeout"
        for attempt in range(2):
            text = user if attempt == 0 else f"{user}\n\n{RETRY_HINT}"
            try:
                raw = self._complete(system, text, deadline)
            except LlmRateBudget:
                return None, "rate_budget"
            except LlmTimeout:
                cause = "timeout"
                continue
            except LlmThrottled:
                cause = "timeout"
                continue
            except LlmTransportError as error:
                logger.warning("slot %d transport error: %s", slot, error)
                cause = "transport"
                continue
            if raw is None:
                cause = "timeout"
                continue
            parsed = extract_json(raw)
            if parsed is None:
                logger.warning("slot %d unusable reply: %r", slot, raw[:200])
                cause = "parse"
                continue
            return parsed, ""
        return None, cause

    def _complete(self, system: str, user: str, deadline: float) -> str | None:
        # Pre-4.6 models narrate their analysis and never reach the JSON unless
        # an assistant prefill forces the reply to BE the JSON object. 4.6+
        # models reject prefill and need max_tokens headroom instead.
        prefill = any(marker in self.model for marker in ("haiku-4-5", "sonnet-4-5"))
        messages: list[dict[str, Any]] = [{"role": "user", "content": user}]
        if prefill:
            messages.append({"role": "assistant", "content": "{"})
        body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 300 if prefill else 4000,
            "system": system,
            "messages": messages,
        }
        if not prefill:
            body["output_config"] = {"effort": "low"}

        for sleep_seconds in (*THROTTLE_SLEEPS, None):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise LlmTimeout("round deadline reached")
            timeout = min(self.config.decision_timeout_seconds, remaining)
            if not self._take_budget():
                raise LlmRateBudget("requests-per-minute budget spent")
            try:
                completion = self.transport.complete(body, timeout)  # type: ignore[union-attr]
            except LlmThrottled:
                if sleep_seconds is None:
                    raise
                time.sleep(min(sleep_seconds, max(0.0, deadline - time.monotonic())))
                continue
            if completion is None:
                return None
            return "{" + completion if prefill else completion
        raise LlmThrottled("throttled past the ladder")

    def _take_budget(self) -> bool:
        """A rolling requests-per-minute budget; retries draw from it too.

        A seat that cannot be called because the budget is exhausted plays its
        fallback baseline for that round rather than waiting for the window.
        """
        now = time.monotonic()
        with self._lock:
            while self._budget and now - self._budget[0] > 60.0:
                self._budget.popleft()
            if len(self._budget) >= self.config.llm_max_requests_per_minute:
                return False
            self._budget.append(now)
            self.requests += 1
            return True
