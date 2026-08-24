"""Commons Family grader: the social planner's view of one episode.

Consumes an episode bundle (`COGAME_EPISODE_BUNDLE_URI`), reads results and
replay, and writes a grade to `COGAME_GRADE_URI`. `score` is group welfare as a
fraction of the module's planner optimum — meadow's framing, per module. It is
NOT what the league ranks by: the league ranks by `results.scores`.
"""

from __future__ import annotations

import io
import json
import os
import zipfile
from itertools import combinations

from pydantic import BaseModel

from coworld.examples.commons_family.game.engine import CommonsConfig, module_for
from coworld.examples.commons_family.shared.artifact_io import (
    JSON_CONTENT_TYPE,
    read_data,
    write_data,
)

GRADER_ID = "commons-grader"

SCALE = {
    "cleanup": (
        "group welfare (all scores + the orchard still standing) as a fraction of the exact "
        "2-D DP planner optimum over apples x pollution"
    ),
    "harvest": (
        "group welfare (all scores + every patch still standing) as a fraction of the exact "
        "per-patch DP planner optimum"
    ),
    "allelopathic": (
        "group welfare (all scores + the ripe field) as a fraction of the BEST-MONOCULTURE "
        "planner schedule; the exact joint optimum over heterogeneous colour schedules is out "
        "of scope for v1, so this denominator is a lower bound and the ratio an upper one"
    ),
    "mushrooms": (
        "group welfare (all scores + the standing mushrooms) as a fraction of the DP optimum "
        "under the never-freeze planner schedule (every seat eats at most one a round)"
    ),
}


class GraderInputs(BaseModel):
    episode_bundle_uri: str
    grade_uri: str


class CommonsGrade(BaseModel):
    grader_id: str
    score: float
    scale: str
    module: str
    welfare: float
    optimum_welfare: float
    survived: bool
    collapse_round: int | None
    dead_patches: list[int]
    synchrony_same_action_rate: float | None
    harvest_gini: float | None
    public_effort_share: float | None


def load_grader_inputs() -> GraderInputs:
    return GraderInputs(
        episode_bundle_uri=os.environ["COGAME_EPISODE_BUNDLE_URI"],
        grade_uri=os.environ["COGAME_GRADE_URI"],
    )


def load_bundle(bundle_uri: str) -> tuple[dict, dict]:
    bundle = zipfile.ZipFile(io.BytesIO(read_data(bundle_uri)))
    manifest = json.loads(bundle.read("manifest.json"))
    results = json.loads(bundle.read(manifest["files"]["results"]))
    replay = json.loads(bundle.read(manifest["files"]["replay"]))
    return results, replay


def synchrony_same_action_rate(demand_rows: list[list[int]]) -> float | None:
    """Mean over rounds and cog pairs of "did the pair demand identically"."""
    if not demand_rows or len(demand_rows[0]) < 2:
        return None
    pairs = list(combinations(range(len(demand_rows[0])), 2))
    matches = sum(1 for row in demand_rows for left, right in pairs if row[left] == row[right])
    return matches / (len(demand_rows) * len(pairs))


def harvest_gini(totals: list[float]) -> float | None:
    if not totals or sum(totals) == 0:
        return None
    values = sorted(totals)
    n = len(values)
    cumulative = sum((2 * index - n - 1) * value for index, value in enumerate(values, start=1))
    return cumulative / (n * sum(values))


def extractive_demand(module: str, decision: dict) -> int:
    return int(decision.get("harvest", 0) if module in ("cleanup", "harvest") else decision.get("eat", 0))


def public_effort_share(results: dict, config: CommonsConfig, rounds: int) -> float | None:
    """Maintenance units divided by total effort units — what the family is about."""
    capacity = rounds * config.num_agents * config.effort_budget
    if capacity <= 0:
        return None
    return sum(results.get("public_effort") or []) / capacity


def build_grade(results: dict, replay: dict) -> CommonsGrade:
    config = CommonsConfig.model_validate(replay["config"])
    module = module_for(config)
    optimum = module.planner_optimum(config)
    rounds = replay.get("rounds") or []
    demand_rows = [
        [extractive_demand(config.module, decision) for decision in record["decisions"]]
        for record in rounds
    ]
    dead_patches = list(results.get("dead_patches") or [])
    return CommonsGrade(
        grader_id=GRADER_ID,
        score=results["welfare"] / optimum if optimum else 0.0,
        scale=SCALE[config.module],
        module=config.module,
        welfare=results["welfare"],
        optimum_welfare=round(optimum, 3),
        survived=results.get("collapse_round") is None and not dead_patches,
        collapse_round=results.get("collapse_round"),
        dead_patches=dead_patches,
        synchrony_same_action_rate=synchrony_same_action_rate(demand_rows),
        harvest_gini=harvest_gini(list(results.get("total_extracted") or [])),
        public_effort_share=public_effort_share(results, config, len(rounds)),
    )


def run(inputs: GraderInputs) -> CommonsGrade:
    results, replay = load_bundle(inputs.episode_bundle_uri)
    grade = build_grade(results, replay)
    write_data(inputs.grade_uri, grade.model_dump_json(indent=2), content_type=JSON_CONTENT_TYPE)
    return grade


if __name__ == "__main__":
    print(run(load_grader_inputs()).model_dump_json(indent=2))
