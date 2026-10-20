"""Episode-stable wording independent of resource and alias randomness."""

from __future__ import annotations

import hashlib
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

TRAINING_VARIANTS = ("canonical", "plain", "numbered", "questions", "reordered")
HELDOUT_VARIANTS = ("heldout-briefing", "heldout-reference")
BANK_VERSION = "commons-family-rules-v2"


class PromptSelection(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    variant: Literal[
        "canonical",
        "plain",
        "numbered",
        "questions",
        "reordered",
        "heldout-briefing",
        "heldout-reference",
        "mixed",
    ] = Field(default="canonical", alias="promptVariant")
    seed: int = Field(default=0, alias="promptSeed", strict=True, ge=0)

    @model_validator(mode="after")
    def explicit_mixed_seed(self) -> PromptSelection:
        if self.variant == "mixed" and "seed" not in self.model_fields_set:
            raise ValueError("mixed wording requires an explicit promptSeed")
        return self

    @property
    def selected(self) -> str:
        return (
            TRAINING_VARIANTS[self.seed % len(TRAINING_VARIANTS)]
            if self.variant == "mixed"
            else self.variant
        )

    def metadata(self, system: str, user: str) -> dict:
        return {
            "bank": BANK_VERSION,
            "variant": self.selected,
            "selection": self.variant,
            "seed": self.seed,
            "system_sha256": hashlib.sha256(system.encode()).hexdigest(),
            "user_sha256": hashlib.sha256(user.encode()).hexdigest(),
        }

    def introduction(self, alias: str, players: int, rounds: int, module: str) -> str:
        introductions = {
            "canonical": f"You are {alias}, one of {players} cogs sharing a commons for {rounds} rounds. Module: {module}.\n",
            "plain": f"Your name is {alias}. You and {players - 1} other cogs share a commons for {rounds} rounds under the {module} module.\n",
            "numbered": f"1. You are {alias}.\n2. {players} cogs share this commons.\n3. Play lasts {rounds} rounds.\n4. Module: {module}.\n",
            "questions": f"Who are you? {alias}. Who shares the commons? {players} cogs. How long? {rounds} rounds. Which module? {module}.\n",
            "reordered": f"In the {module} module, {players} cogs share a commons for {rounds} rounds. You are {alias}.\n",
            "heldout-briefing": f"Take the role of {alias} among {players} cogs sharing one commons over {rounds} rounds of {module}.\n",
            "heldout-reference": f"Commons reference\nIdentity: {alias}.\nParticipants: {players} cogs.\nDuration: {rounds} rounds.\nModule: {module}.\n",
        }
        return introductions[self.selected]


CANONICAL_SELECTION = PromptSelection()


SYSTEM_TEMPLATES = {
    "canonical": """{module_rules}
Every round all {num_players} cogs decide at the same time and the results settle together. You have
{effort_budget} effort units per round to split between taking and maintaining.
Your score is your accumulated module payoff, minus punishment you pay or receive.{sanction_rules}{norm_line}
The resource is shared: what you take is not there next round for anyone, including you.
Each round you receive the game state as JSON. Reply with ONLY one JSON object, no other text.
Your reply MUST begin with the character {{.
Schema: {schema_line}""",
    "plain": """{module_rules}
All {num_players} cogs choose simultaneously each round, and their choices settle together. Divide your
{effort_budget} effort units between taking and maintaining. Score is accumulated module payoff less
punishment you pay or receive.{sanction_rules}{norm_line}
Taking depletes a shared resource: what you take will be unavailable next round to everybody, yourself
included.
Each round you receive the game state as JSON. Reply with ONLY one JSON object, no other text.
Your reply MUST begin with the character {{.
Schema: {schema_line}""",
    "numbered": """{module_rules}
Round procedure:
1. All {num_players} cogs choose simultaneously; outcomes settle together.
2. Allocate {effort_budget} effort units to taking and maintaining each round.
3. Accumulate module payoff, subtracting punishment you pay or receive.{sanction_rules}{norm_line}
4. The resource is shared. Taking removes resources from next round for all cogs, including you.
Each round you receive the game state as JSON. Reply with ONLY one JSON object, no other text.
Your reply MUST begin with the character {{.
Schema: {schema_line}""",
    "questions": """{module_rules}
When do choices happen? All {num_players} cogs decide simultaneously every round; results settle together.
What can you allocate? {effort_budget} effort units between taking and maintaining per round. What is your
score? Accumulated module payoff minus punishment paid or received.{sanction_rules}{norm_line}
Why does taking affect later rounds? The resource is shared; what you take cannot be there next round for
anyone, including yourself.
Each round you receive the game state as JSON. Reply with ONLY one JSON object, no other text.
Your reply MUST begin with the character {{.
Schema: {schema_line}""",
    "reordered": """Your score is accumulated module payoff minus punishment you pay or receive.{sanction_rules}{norm_line}
{module_rules}
The commons is shared. Resources you take are unavailable next round to every cog, including you. Each round
split {effort_budget} effort units between taking and maintaining. All {num_players} cogs decide at once and
their results settle together.
Each round you receive the game state as JSON. Reply with ONLY one JSON object, no other text.
Your reply MUST begin with the character {{.
Schema: {schema_line}""",
    "heldout-briefing": """{module_rules}
Decision briefing: {num_players} cogs choose at the same time each round, followed by joint settlement.
Allocate {effort_budget} effort units per round between taking and maintaining.
Payoff briefing: score accumulates module payoff, less punishment you pay or
receive.{sanction_rules}{norm_line}
Resource briefing: the commons is shared. What you take is missing next round for others and for you.
Each round you receive the game state as JSON. Reply with ONLY one JSON object, no other text.
Your reply MUST begin with the character {{.
Schema: {schema_line}""",
    "heldout-reference": """{module_rules}
Timing: simultaneous decisions by all {num_players} cogs; results settle together. Effort: {effort_budget}
units per round, divided between taking and maintaining. Score: accumulated module payoff less punishment paid
or received.{sanction_rules}{norm_line}
Shared-resource consequence: anything you take is absent next round for everyone, including yourself.
Each round you receive the game state as JSON. Reply with ONLY one JSON object, no other text.
Your reply MUST begin with the character {{.
Schema: {schema_line}""",
}
