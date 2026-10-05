"""Source-owned ordinary Commons prompt and assistant-prefill grammar.

Profiles are player input frozen before the first private decision window.
Their requested model never establishes the identity of a serving checkpoint.
"""

from __future__ import annotations

import json
from typing import Literal

from pydantic import ConfigDict, Field, model_validator

from commons_family.game.engine import PROMPT_MAX_RUNES, CommonsConfig, truncate_runes
from commons_family.game.modules.base import Module
from commons_family.shared.native import Message, OutputConfig, PrivateModel, Request

RETRY_HINT = (
    "Your last reply was not usable. Reply with ONE JSON object beginning with { and only the fields in the schema."
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

SANCTION_RULES = "\nYou may also sanction one cog per round: you pay {cost}, they lose {burn}."


class NativeProfile(PrivateModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True, allow_inf_nan=False)
    kind: Literal["native_messages"] = "native_messages"
    model: str = Field(default="anthropic/claude-haiku-4.5", min_length=1)
    grammar: Literal["assistant_prefill", "json_object"] = "assistant_prefill"
    standing_orders: str = Field(max_length=PROMPT_MAX_RUNES)
    temperature: float | None = Field(default=None, strict=True, ge=0, le=1)
    top_p: float | None = Field(default=None, strict=True, gt=0, le=1)

    @model_validator(mode="after")
    def original_model_grammar(self) -> NativeProfile:
        prefill = any(marker in self.model for marker in ("haiku-4-5", "sonnet-4-5", "haiku-4.5", "sonnet-4.5"))
        if prefill != (self.grammar == "assistant_prefill"):
            raise ValueError("model profile must declare its ordinary assistant-prefill grammar")
        return self

    def parser_input(self, completion: str) -> str:
        return "{" + completion if self.grammar == "assistant_prefill" else completion

    def teacher_completion(self, action_json: str) -> str:
        if self.grammar == "assistant_prefill":
            if not action_json.startswith("{"):
                raise ValueError("teacher action must begin with the ordinary JSON object prefix")
            return action_json[1:]
        return action_json


class DecisionRenderer:
    """The same prompt renderer serves issued windows, native callers and teachers."""

    def __init__(self, config: CommonsConfig, module: Module) -> None:
        self.config = config
        self.module = module

    def system_prompt(self, obs: dict, standing_orders: str) -> str:
        sanction_rules = ""
        if obs["sanctions_enabled"]:
            sanction_rules = SANCTION_RULES.format(cost=obs["sanction_cost"], burn=obs["sanction_burn"])
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

    def request(self, obs: dict, profile: NativeProfile, retry: bool = False) -> Request:
        user = self.user_message(obs)
        if retry:
            user += f"\n\n{RETRY_HINT}"
        messages = [Message(role="user", content=user)]
        prefill = profile.grammar == "assistant_prefill"
        if prefill:
            messages.append(Message(role="assistant", content="{"))
        return Request(
            model=profile.model,
            max_tokens=300 if prefill else 4000,
            temperature=profile.temperature,
            top_p=profile.top_p,
            system=self.system_prompt(obs, profile.standing_orders),
            messages=messages,
            output_config=None if prefill else OutputConfig(effort="low"),
        )
