# cogame-commons-family

**Commons Family** — six cogs, one destructible commons, twenty simultaneous rounds, four
different resource physics on one institutional layer. A Softmax Coworld.

Watch: <https://softmax.com/commons-family>

Every round each cog splits three units of effort between taking from the commons and doing the
thing that keeps the commons alive, and everyone's choices settle at once. Taking pays you.
Maintaining pays everyone, including the cogs who did not maintain. Four modules put that
sentence under four different kinds of pressure:

- **Clean Up** — an apple orchard beside a river that silts up. Cleaning the river pays the
  cleaner nothing and is the only thing that keeps the apples regrowing. Let the orchard fall
  below ten apples and it is dead forever.
- **Commons Harvest** — six patches that regrow only while each keeps at least one apple. Strip
  one and it is a tombstone for the rest of the episode. Ships as a three-way property-rights
  A/B: open, one patch per cog, or two patches per pair that pay only when both partners hold
  them.
- **Allelopathic Harvest** — three berry colours that starve each other, because a colour ripens
  in proportion to the *square* of its share of the field. Every cog has a secret favourite that
  pays it double, so the outcome that feeds everybody needs four of six cogs to give up their
  preference.
- **Externality Mushrooms** — red pays you 1, green pays the whole group 2, and blue pays
  *everyone except you* 3. Eating freezes you for as many rounds as you ate. The control case,
  where institutions have the least excuse.

**A policy is just a prompt.** Both champions are `PLAYER_PROMPT` strings on the same image as
the scripted baselines; the game container makes every decision, issuing all six seats' calls as
one parallel batch per round.

- Game, engine and modules: [`src/coworld/examples/commons_family/`](src/coworld/examples/commons_family/)
- Player protocol: [`…/game/docs/player_protocol_spec.md`](src/coworld/examples/commons_family/game/docs/player_protocol_spec.md)
- Global protocol: [`…/game/docs/global_protocol_spec.md`](src/coworld/examples/commons_family/game/docs/global_protocol_spec.md)
- Design note: [`docs/plans/2026-08-24-commons-family-design.md`](docs/plans/2026-08-24-commons-family-design.md)

## Build and test

```bash
pip install -r requirements.txt pytest
PYTHONPATH=src python -m pytest tests/ -v

docker build -t coworld-commons-family:latest .
tools/ci/docker_smoke.sh coworld-commons-family:latest

tools/build_replay_viewer.sh "$PWD/dist/static-replay-viewer"
```

The replay viewer is a **static wasm bundle** (`replay-viewer/`, built by
`tools/build_replay_viewer.sh` and declared as `"replay_viewer": {"bundle":
"static-replay-viewer"}`), never a pod: the replay bytes are a single self-sufficient UTF-8 JSON
document and the browser contacts nothing else.

## Board art

`data/*.png` is generated from the nano-banana source sheets in `scripts/art/source/` by
`scripts/art/split_sheets.py` (`python3 scripts/art/split_sheets.py`). `arena_floor.png` and
`font.ttf` come from the starter and are not generated.

## Release

`.github/workflows/coworld-release.yml` (dispatch) runs build → certify → upload policies →
upload coworld → put secret, in that order, and uploads `release-result` as an artifact.
`.github/workflows/coworld-submit.yml` submits a policy to a league.
