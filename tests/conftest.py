"""Shared fixtures: one real episode, written to disk exactly as the game writes it.

`tests/test_replay_parse.py` parses the artifact this fixture produced rather
than a hand-written fixture, so the strict-UTF-8 assertions are made against
the writer that ships.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from coworld.examples.commons_family import headless
from coworld.examples.commons_family.game.engine import CommonsConfig

EPISODE_POLICIES = ["steward", "cleaner", "punisher", "reciprocator", "free_rider", "random"]


def build_config(**overrides) -> CommonsConfig:
    base = {
        "num_agents": 6,
        "module": "cleanup",
        "rounds": 8,
        "sanctions_enabled": True,
        "chat_enabled": True,
        "ledger_public": True,
        "norm_text": "Posted norm: one apple each, and someone cleans every round.",
    }
    base.update(overrides)
    return CommonsConfig(**base)


def run(config: CommonsConfig, policies: list[str] | None = None, seed: int = 3):
    return headless.run_episode(
        config,
        headless.build_policies(policies or EPISODE_POLICIES, seed=seed),
        parallel_seats=True,
    )


@pytest.fixture(scope="session")
def episode_dir(tmp_path_factory) -> Path:
    """An eight-round episode with results.json and replay.json beside it."""
    directory = tmp_path_factory.mktemp("episode")
    config = build_config()
    state = run(config)
    headless.write_artifacts(
        state,
        config,
        directory,
        names=[
            "commons-family-steward",
            "commons-family-cleaner",
            "baseline",
            "commons-family-warden",
            "baseline (2)",
            "commons-family-freerider",
        ],
    )
    return directory


@pytest.fixture(scope="session")
def replay(episode_dir: Path) -> dict:
    return json.loads((episode_dir / "replay.json").read_bytes().decode("utf-8"))


@pytest.fixture(scope="session")
def results(episode_dir: Path) -> dict:
    return json.loads((episode_dir / "results.json").read_bytes().decode("utf-8"))
