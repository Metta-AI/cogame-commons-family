"""Export complete certified Commons Family games for Metta post-training."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from coworld.examples.commons_family.game.baselines import make_baseline
from coworld.examples.commons_family.game.engine import (
    CommonsConfig,
    module_for,
    new_game,
    observation,
    open_round,
    parse_decision,
    results,
    settle_round,
)
from coworld.examples.commons_family.game.llm import LlmDecider

OPERATOR_PROMPT = "Keep the shared resource alive while maximizing your own score."


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("games", type=int)
    parser.add_argument("--first-seed", type=int, default=1)
    parser.add_argument("--variant", default="cleanup")
    args = parser.parse_args()
    if args.games < 10 or args.first_seed < 1:
        parser.error("at least ten games and a positive first seed are required")

    manifest = json.loads(Path("coworld_manifest_template.json").read_text())
    variants = {entry["id"]: entry["game_config"] for entry in manifest["variants"]}
    if args.variant not in variants:
        parser.error(f"unknown variant: {args.variant}")
    args.output.mkdir(parents=True, exist_ok=False)
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    train_rows: list[str] = []
    validation_rows: list[str] = []
    runs: list[dict] = []

    for seed in range(args.first_seed, args.first_seed + args.games):
        config = CommonsConfig.model_validate(variants[args.variant] | {"seed": seed})
        module = module_for(config)
        decider = LlmDecider(config, module, transport=None)
        state = new_game(config)
        policies = [make_baseline("steward", seed=seed * 1000 + slot) for slot in range(config.num_agents)]
        rows: list[str] = []
        for _ in range(config.rounds):
            open_round(state, config, module)
            decisions = []
            for slot, policy in enumerate(policies):
                obs = observation(state, config, slot, module)
                completion = policy.act(obs)
                decisions.append(parse_decision(completion, slot, config, state, module, "scripted:steward"))
                rows.append(
                    json.dumps(
                        {
                            "episode_id": f"commons-family-{args.variant}-{seed}",
                            "seed": f"commons-family-{args.variant}-{seed}",
                            "decision_id": len(rows),
                            "prompt": [
                                {"role": "system", "content": decider.system_prompt(obs, OPERATOR_PROMPT)},
                                {"role": "user", "content": decider.user_message(obs)},
                            ],
                            "completion": [{"role": "assistant", "content": json.dumps(completion)}],
                            "game": "commons-family",
                            "action_schema_revision": "commons-family-decision-v1",
                        },
                        ensure_ascii=False,
                    )
                )
            settle_round(state, decisions, config, module)
        assert state.round == config.rounds
        outcome = results(state, config, module, "complete", ["steward"] * config.num_agents, 0)
        (validation_rows if seed % 5 == 0 else train_rows).extend(rows)
        runs.append({"seed": seed, "decisions": len(rows), "scores": outcome["scores"], "rounds": state.round})

    (args.output / "train.jsonl").write_text("\n".join(train_rows) + "\n")
    (args.output / "validation.jsonl").write_text("\n".join(validation_rows) + "\n")
    (args.output / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "game": "commons-family",
                "variant": args.variant,
                "source_revision": revision,
                "teacher": "scripted-steward",
                "operator_prompt": OPERATOR_PROMPT,
                "train_examples": len(train_rows),
                "validation_examples": len(validation_rows),
                "runs": runs,
            },
            indent=2,
        )
        + "\n"
    )
    print(f"train={len(train_rows)} validation={len(validation_rows)}")


if __name__ == "__main__":
    main()
