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

The exporter reads each certified `game_config` from
`coworld_manifest_template.json`, runs ten complete games with the published
steward player, and writes `train.jsonl`, `validation.jsonl`, and
`manifest.json`. Seeds divisible by five go to validation, keeping each game
entirely in one split. Every teacher reply passes the same parser and round
settlement as hosted play. An existing output directory is rejected.

Train the text policy with Metta's post-training CLI:

```bash
uv run python -m metta_posttrain.train --dataset /tmp/commons-cleanup \
  --output /tmp/commons-model --model Qwen/Qwen2.5-0.5B-Instruct \
  --max-steps 100 --max-length 4096
```

The dataset imitates scripted play; its loss does not measure policy quality.

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
