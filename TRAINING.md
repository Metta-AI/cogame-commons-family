# Commons Family training

The six certified variants share one Python simulator and hosted text player.
Export complete seeded games for Metta post-training with the hosted per-seat
prompt and game decision parser:

```bash
PYTHONPATH=src python tools/export_posttrain.py /tmp/commons-cleanup 10 --variant cleanup
PYTHONPATH=src python tools/export_posttrain.py /tmp/commons-open 10 --variant harvest-open
PYTHONPATH=src python tools/export_posttrain.py /tmp/commons-closed 10 --variant harvest-closed
PYTHONPATH=src python tools/export_posttrain.py /tmp/commons-partnership 10 --variant harvest-partnership
PYTHONPATH=src python tools/export_posttrain.py /tmp/commons-allelopathic 10 --variant allelopathic
PYTHONPATH=src python tools/export_posttrain.py /tmp/commons-mushrooms 10 --variant mushrooms
```

The exporter reads each published configuration and completes every game with
scripted steward proposals through the production parser and round settlement.
It requires clean committed source and a new output directory. The original
`train.jsonl`, `validation.jsonl`, and `manifest.json` format is preserved.
Proposals changed by normalization are excluded; the manifest counts exclusions.
These scripted targets are imitation data, not evidence of expert play.

Use `--prompt-variant canonical|plain|numbered|questions|reordered|mixed` and
`--prompt-seed`. Mixed selection requires an explicit seed and cycles wording
independently within each split and the requested game count. Held-out wording is rejected.
Families exclude game variant and wording; seeds divisible by five enter
validation, and all other seeds enter training. Preserve this assignment across
modules and wordings.

# Numeric reinforcement learning

The persistent bridge exposes all six certified variants to Metta RL and
native PufferLib. It uses each module's validated decision as a bounded action
catalog, plus a separate sanction head. The source engine settles all six
decisions together:

```bash
python tools/test_train_bridge.py tools/train_bridge.py
```

Pass the Python interpreter, absolute bridge path, manifest path, and variant
to Metta's `recipes.external.coworld_metta_rl.train` or
`recipes.external.coworld.train`. The action heads have 10/24/90/12 module
choices for Clean Up/Harvest/Allelopathic/Mushrooms, and six sanction slots.
The sanction head masks all but “no sanction” when sanctions are disabled.
Numeric observations use only the hosted per-seat observation: public module
state and ledger, own score, and own last gain. Their widths are 87 for Clean
Up, 137 for Harvest, 91 for Allelopathic, and 92 for Mushrooms. Public chat,
posted norms, and private notes remain available to the text post-training
path; the numeric policy does not emit text.

## Episode-stable prompt wording

Hosted configurations accept `promptVariant` and `promptSeed`. Five training
wordings are available: `canonical`, `plain`, `numbered`, `questions`, and
`reordered`. `mixed` requires an explicit nonnegative prompt seed and selects
training wording by `seed % 5`. Selection does not consume resource randomness
or change identities, sanctions, module rules, action schemas, or standing orders.
The canonical wording remains the default. Other variants rewrite simultaneous
settlement, effort allocation, shared-resource effects, and payoff explanations.
Module-specific rules and response schemas remain fixed. The shared score description
now states accumulated module payoff, including mushroom externalities, minus
sanctions; game scoring itself is unchanged.

`heldout-briefing` and `heldout-reference` are evaluation-only. Freeze training
choices before using these wordings. The language bridge rejects them under
`split: train`; use `split: validation` or `split: test`.

`PYTHONPATH=src:. python tools/text_bridge.py --variant cleanup` exposes production
text decisions through the JSONL reset/teacher/step protocol. Reset requires
`players: 6` and a scenario `seed`; it accepts the same prompt selector fields.
Each round queues six normalized decisions against unchanged private views, then
settles them together through the real engine. Notes stay private, chat becomes
visible next round, and accepted directives describe normalized submissions,
not guaranteed resource gains. The numeric bridge retains its existing actions
and observation widths. The text bridge's steward teacher is scripted imitation,
not an expert label or evidence of learned policy quality.

Private semantic views expose the prompt bank, selected wording, seed, and hashes
of the exact system and user messages. Keep all wordings and module variants of
a scenario family in one data split. Compare base, canonical-trained, and
mixed-trained checkpoints with identical scenario seeds, seats, partners,
modules, and inference budgets. Report malformed replies, normalized proposals,
fallbacks, incomplete games, per-seat scores, and welfare separately.

Run `PYTHONPATH=src:. python -m pytest tests/test_prompt_games.py -q` for complete
matched games across all six published variants, including private-note,
next-round chat, normalized-sanction, and held-out training checks.
