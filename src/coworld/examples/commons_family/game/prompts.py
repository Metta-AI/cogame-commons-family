"""Episode-stable wording independent of resource and alias randomness."""

from __future__ import annotations

import hashlib
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

TRAINING_VARIANTS = ("canonical", "plain", "numbered", "questions", "reordered")
HELDOUT_VARIANTS = ("heldout-briefing", "heldout-reference")
BANK_VERSION = "commons-family-rules-v1"


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
