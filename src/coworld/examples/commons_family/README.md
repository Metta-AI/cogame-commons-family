# Commons Family

Six cogs share one destructible resource for twenty simultaneous rounds. Every round each cog
splits **three units of effort** between taking from the resource and doing the thing that keeps
the resource alive, and then everyone's choices settle at once. Taking pays you. Maintaining pays
everyone, including the cogs who did not maintain.

The resource physics come from one of four **modules**; the institutions around them — a public
ledger of who took what, costly punishment, a posted norm and one signed chat line per cog per
round — are the same in all four, and are switched per variant. That is the experiment: the
physics are the treatment, the institutions are the control.

| module | the resource | take | maintain | the trap |
| --- | --- | --- | --- | --- |
| `cleanup` | one apple stock + one river | `harvest` | `clean` | cleaning pays nothing; unclean → apples stop regrowing |
| `harvest` | six independent patches | `harvest` from a named `patch` | leaving ≥ 1 apple in a patch | a stripped patch is dead **forever** |
| `allelopathic` | 60 slots in three colours | `eat` ripe berries | `plant` a slot | ripening is quadratic in a colour's share, and your favourite pays double |
| `mushrooms` | red / green / blue mushrooms | `eat` a colour | eating green or blue | red pays you 1, green pays the group 2, blue pays *everyone but you* 3 |

Everything runs at an abstract round, not on a grid: a Clean Up cog does not walk to the river, it
spends one of its three effort units on `clean`. That is what keeps the planner optimum computable
and the game exactly solvable.

## Running one

```bash
docker build -t coworld-commons-family:latest .
tools/ci/docker_smoke.sh coworld-commons-family:latest   # one episode, six seats, raw docker
```

In-process, no containers:

```python
from coworld.examples.commons_family.game.engine import CommonsConfig
from coworld.examples.commons_family import headless

config = CommonsConfig(module="mushrooms", rounds=20)
state = headless.run_episode(
    config,
    headless.build_policies(["steward"] * 3 + ["free_rider"] * 3),
    parallel_seats=True,
)
```

## Fielding a policy

One image, env-switched:

```bash
PLAYER_PROMPT="Take the sustainable share and no more…"   # an LLM seat
PLAYER_SCRIPTED=steward                                   # a scripted seat
```

`PLAYER_SCRIPTED` wins when both are set. Baselines: `steward`, `free_rider`, `cleaner`,
`punisher`, `reciprocator`, `deterrable`, `random`. See `game/docs/player_protocol_spec.md` for the
reply schema, and the manifest's `policies.md` page for the short version.

## Where the LLM lives

In the **game** container, not the player container. Only the party that owns the round barrier
can issue all six seats' calls as one parallel batch, and only that party can enforce
retry-once-then-fall-back-to-scripted — otherwise a hung player pod silently becomes a passing
seat. The player container registers a policy and spectates.

## Layout

```
game/engine.py            the round loop, the institutions, the ledger, the replay document
game/modules/base.py      the module protocol: new_state / parse_decision / resolve /
                          dynamics / observe / residual_value / planner_optimum
game/modules/*.py         the four physics
game/llm.py               the LLM seat: credential ladder, one parallel batch, retry once
game/baselines.py         the seven scripted baselines, generalised across the modules
game/server.py            the Coworld game-container contract
player/player.py          registers a policy, then spectates
grader/commons_grader.py  welfare against the module's planner optimum
headless.py               in-process episodes, for the tests
shared/                   artifact IO and the log shipper, from coworld-meadow verbatim
```

Two name spaces, always: cogs address each other as `Cog-A` … `Cog-F` and can never read who they
are playing against; the real policy names live in the replay, the results and the viewer.
