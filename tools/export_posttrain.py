"""Export complete certified Commons Family games for Metta post-training."""

from __future__ import annotations

import argparse
import hashlib
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
from coworld.examples.commons_family.game.prompts import (
    BANK_VERSION,
    HELDOUT_VARIANTS,
    PromptSelection,
)
from coworld.examples.commons_family.game.training_capture import physical_family

OPERATOR_PROMPT = "Keep the shared resource alive while maximizing your own score."


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("games", type=int)
    parser.add_argument("--first-seed", type=int, default=1)
    parser.add_argument("--variant", default="cleanup")
    parser.add_argument("--prompt-variant", default="canonical")
    parser.add_argument("--prompt-seed", type=int)
    args = parser.parse_args()
    selector = {"promptVariant": args.prompt_variant}
    if args.prompt_seed is not None:
        selector["promptSeed"] = args.prompt_seed
    initial_selection = PromptSelection.model_validate(selector)
    if initial_selection.selected in HELDOUT_VARIANTS:
        parser.error("held-out wording is evaluation-only")
    if subprocess.check_output(["git", "status", "--porcelain"], text=True).strip():
        raise ValueError("commit source before export")
    if args.games < 10 or args.first_seed < 1:
        parser.error("at least ten games and a positive first seed are required")

    manifest = json.loads(Path("coworld_manifest_template.json").read_text())
    variants = {entry["id"]: entry["game_config"] for entry in manifest["variants"]}
    if args.variant not in variants:
        parser.error(f"unknown variant: {args.variant}")
    args.output.mkdir(parents=True, exist_ok=False, mode=0o700)
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    train_rows: list[str] = []
    validation_rows: list[str] = []
    runs: list[dict] = []
    wording_counts = [0, 0]
    families = {"train": set(), "validation": set()}
    family_splits = {}

    for seed in range(args.first_seed, args.first_seed + args.games):
        config = CommonsConfig.model_validate(variants[args.variant] | {"seed": seed})
        module = module_for(config)
        state = new_game(config)
        family = physical_family(config, state)
        fraction = int(hashlib.sha256(family.encode()).hexdigest()[:8], 16) / 2**32
        split = "validation" if fraction < 0.2 else "train"
        partition = int(split == "validation")
        family_splits[family] = split
        selection = initial_selection
        if selection.variant == "mixed":
            selection = selection.model_copy(
                update={"seed": selection.seed + wording_counts[partition]}
            )
            wording_counts[partition] += 1
        decider = LlmDecider(config, module, transport=None, prompt_selection=selection)
        policies = [
            make_baseline("steward", seed=seed * 1000 + slot)
            for slot in range(config.num_agents)
        ]
        rows: list[str] = []
        normalized = 0
        for _ in range(config.rounds):
            open_round(state, config, module)
            decisions = []
            for slot, policy in enumerate(policies):
                obs = observation(state, config, slot, module)
                completion = policy.act(obs)
                decision = parse_decision(
                    completion, slot, config, state, module, "scripted:steward"
                )
                decisions.append(decision)
                action = {
                    field: getattr(decision, field)
                    for field in decision.__slots__
                    if field != "src"
                }
                if any(
                    field not in action or action[field] != value
                    for field, value in completion.items()
                ):
                    normalized += 1
                    continue
                rows.append(
                    json.dumps(
                        {
                            "episode_id": f"commons-family-{args.variant}-{seed}-{selection.selected}-{selection.seed}",
                            "seed": family,
                            "decision_id": state.round * config.num_agents + slot,
                            "prompt": [
                                {
                                    "role": "system",
                                    "content": decider.system_prompt(
                                        obs, OPERATOR_PROMPT
                                    ),
                                },
                                {"role": "user", "content": decider.user_message(obs)},
                            ],
                            "completion": [
                                {"role": "assistant", "content": json.dumps(completion)}
                            ],
                            "game": "commons-family",
                            "action_schema_revision": "commons-family-decision-v1",
                        },
                        ensure_ascii=False,
                    )
                )
            settle_round(state, decisions, config, module)
        assert state.round == config.rounds
        outcome = results(
            state, config, module, "complete", ["steward"] * config.num_agents, 0
        )
        (validation_rows if partition else train_rows).extend(rows)
        if rows:
            families[split].add(family)
        runs.append(
            {
                "prompt_selection": {
                    "bank": BANK_VERSION,
                    "variant": selection.selected,
                    "selection": selection.variant,
                    "seed": selection.seed,
                },
                "normalized_proposals": normalized,
                "seed": seed,
                "seed_family": family,
                "split": split,
                "decisions": len(rows),
                "scores": outcome["scores"],
                "rounds": state.round,
            }
        )

    (args.output / "train.jsonl").write_text(
        "".join(line + "\n" for line in train_rows)
    )
    (args.output / "validation.jsonl").write_text(
        "".join(line + "\n" for line in validation_rows)
    )
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
                "split_scheme": "physical-family-sha256-first32-validation20-v1",
                "seed_family_splits": family_splits,
                "physical_families": {
                    split: sorted(values) for split, values in families.items()
                },
                "independent_split_coverage": bool(
                    families["train"] and families["validation"]
                ),
                "runs": runs,
            },
            indent=2,
        )
        + "\n"
    )
    print(f"train={len(train_rows)} validation={len(validation_rows)}")


if __name__ == "__main__":
    main()
