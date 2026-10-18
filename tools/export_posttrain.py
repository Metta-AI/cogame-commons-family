"""Export complete games as private candidate evidence for shared review and admission."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path

from coworld.examples.commons_family.game.engine import results
from coworld.examples.commons_family.game.llm import logger
from coworld.examples.commons_family.game.prompts import (
    HELDOUT_VARIANTS,
    PromptSelection,
)
from tools.text_bridge import TextBridge


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("games", type=int)
    parser.add_argument("--first-seed", type=int, default=1)
    parser.add_argument("--variant", default="cleanup")
    parser.add_argument("--prompt-variant", default="canonical")
    parser.add_argument("--prompt-seed", type=int)
    args = parser.parse_args()
    if args.games < 10 or args.first_seed < 1:
        parser.error("at least ten games and a positive first seed are required")
    selector = {"promptVariant": args.prompt_variant}
    if args.prompt_seed is not None:
        selector["promptSeed"] = args.prompt_seed
    selection = PromptSelection.model_validate(selector)
    if selection.selected in HELDOUT_VARIANTS:
        parser.error("held-out wording is evaluation-only")
    root = Path(__file__).resolve().parents[1]
    if subprocess.check_output(
        ["git", "status", "--porcelain"], cwd=root, text=True
    ).strip():
        raise ValueError(
            "Commit qualified source before generating a pinned candidate corpus"
        )
    revision = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=root, text=True
    ).strip()
    bridge = TextBridge(args.variant)
    logger.disabled = True
    args.output.mkdir(parents=True, exist_ok=False, mode=0o700)
    descriptor = os.open(
        args.output / "trajectories.jsonl", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
    )
    runs = []
    with os.fdopen(descriptor, "w") as stream:
        for seed in range(args.first_seed, args.first_seed + args.games):
            family = f"commons-family-{seed}"
            episode = f"{family}-{args.variant}-{selection.selected}-{selection.seed}"
            shared = {
                "episode_id": episode,
                "game": "commons-family",
                "game_version": f"source-{revision}",
                "source_revision": revision,
            }
            decision = bridge.handle(
                {
                    "kind": "reset",
                    "seed": str(seed),
                    "players": 6,
                    **selection.model_dump(by_alias=True),
                }
            )
            count = 0
            while decision["kind"] == "decision":
                response = bridge.handle({"kind": "teacher"})["response"]
                proposal = json.loads(response)
                resolved = bridge.handle(
                    {
                        "kind": "step",
                        "decision_id": decision["decision_id"],
                        "response": response,
                    }
                )
                action = resolved["action"]
                accepted = all(
                    field in action and action[field] == value
                    for field, value in proposal.items()
                )
                attempt_id = f"{count}-teacher"
                rejection = None if accepted else "engine-normalized-submission"
                row = {
                    **shared,
                    "schema_version": "1",
                    "event_type": "decision",
                    "decision_id": str(count),
                    "decision_index": count,
                    "seat": str(decision["seat"]),
                    "visibility": "private",
                    "observation": decision["semantic_view"],
                    "prompt": decision["messages"],
                    "attempts": [
                        {
                            "attempt_id": attempt_id,
                            "policy": "scripted-steward",
                            "origin": "teacher",
                            "prompt": decision["messages"],
                            "response": response,
                            "parsed_action": action,
                            "accepted": accepted,
                            "rejection_reason": rejection,
                        }
                    ],
                    "selected_attempt_id": attempt_id if accepted else None,
                    "executed_action": action,
                    "action_status": "accepted" if accepted else "fallback",
                    "fallback_origin": rejection,
                    "terminal": resolved["observation"]["kind"] == "terminal",
                }
                stream.write(json.dumps(row, ensure_ascii=False) + "\n")
                decision = resolved["observation"]
                count += 1
            assert (
                bridge.state.round == bridge.config.rounds
                and count == bridge.config.rounds * 6
            )
            outcome = results(
                bridge.state,
                bridge.config,
                bridge.module,
                "complete",
                ["steward"] * 6,
                0,
            )
            stream.write(
                json.dumps(
                    {
                        **shared,
                        "schema_version": "1",
                        "event_type": "episode",
                        "seed_family": family,
                        "status": "completed",
                        "outcome": outcome,
                        "participant_outcomes": {
                            str(i): {"score": score}
                            for i, score in enumerate(outcome["scores"])
                        },
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
            runs.append(
                {
                    "seed": seed,
                    "decisions": count,
                    "scores": outcome["scores"],
                    "rounds": bridge.state.round,
                }
            )
    descriptor = os.open(
        args.output / "manifest.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
    )
    with os.fdopen(descriptor, "w") as stream:
        json.dump(
            {
                "schema_version": 1,
                "game": "commons-family",
                "variant": args.variant,
                "source_revision": revision,
                "teacher": "scripted-steward",
                "prompt_selection": selection.model_dump(by_alias=True),
                "split_authority": "shared Coworld SDK and application importer by seed_family",
                "runs": runs,
            },
            stream,
            indent=2,
        )
        stream.write("\n")
    print(f"complete candidate games={len(runs)}")


if __name__ == "__main__":
    main()
