# Player protocol — `commons-family.player.v1`

JSON text frames over the websocket named by `COWORLD_PLAYER_WS_URL`
(`ws://<game>:8080/player?slot=N&token=T`). One connection per seat.

The player container's only job is to **register a policy**. Every decision is made in the game
container, so that all six seats' LLM calls can go out as one parallel batch per round and so a
seat that cannot answer falls back to a scripted baseline instead of silently passing.

## player → game, once, immediately after connect

```json
{"type": "prompt", "prompt": "<standing orders, at most 1200 runes>", "scripted": "<baseline name or empty>"}
```

`scripted` non-empty wins, and names one of
`steward, free_rider, cleaner, punisher, reciprocator, deterrable, random`.
An unknown name, a malformed frame, or no registration within 5 s of connect is treated as
`{"scripted": "steward"}` — never a disconnect.

The reference player (`/bin/commons-family-player`) sends `PLAYER_PROMPT` and `PLAYER_SCRIPTED`
from its environment and then spectates.

## game → player

**On connect:**

```json
{"type": "welcome", "protocol": "commons-family.player.v1", "slot": 2,
 "alias": "Cog-C", "module": "cleanup", "rounds": 20, "num_players": 6}
```

**After every settled round, one `observation` per seat:**

```json
{"type":"observation","protocol":"commons-family.player.v1",
 "slot":2,"alias":"Cog-C","round":7,"rounds":20,"round_seconds":20.0,
 "module":"allelopathic","num_players":6,"effort_budget":3,
 "ledger_public":true,"sanctions_enabled":true,"sanction_cost":1.0,"sanction_burn":3.0,
 "chat_enabled":true,"chat_max_chars":140,"norm_text":"Posted quota: one unit each.",
 "score":11.33,"your_last_gain":2.0,"sanctions_received_last_round":0,
 "last_round_total_extracted":9.0,
 "messages_last_round":[{"alias":"Cog-A","text":"everyone on green from now"}],
 "your_note":"Cog-A and Cog-E kept their word last round.",
 "ledger":[{"slot":0,"alias":"Cog-A","total_extracted":15.0,"public_effort":4,
            "recent":["e:g2","e:g2","p:g1 e:g1","e:g3","e:g2"],
            "sanctions_given":0,"sanctions_received":1}],
 "module_state":{ … }}
```

`module_state` per module:

| module | keys |
| --- | --- |
| `cleanup` | `apples, capacity, pollution, effective_regrowth, collapse_threshold, silt_rate, clean_power, dead, cleaned_last_round` |
| `harvest` | `property_rights, patch_capacity, patch_regrowth, patches[{id, stock, dead, holders}], dead_patches, your_patches` |
| `allelopathic` | `planted{red,green,blue}, ripe{red,green,blue}, field_size, ripen_base, favorite_bonus, barren, your_favorite` |
| `mushrooms` | `counts{red,green,blue}, eaten_total, frozen_until, you_may_eat, capacity, color_cap, spawn_per_round, payoff` |

**Visible:** the full public resource state, the norm, your own score and last gain, your own
private note, the aggregate extracted last round, everyone's signed chat from last round, and —
when `ledger_public` — every other cog's alias, cumulative extraction, cumulative maintenance
effort, last five compact actions and sanction counters. Property-rights assignments in `harvest`
are public by construction.

**Hidden:** every other cog's secret favourite colour in `allelopathic`, every other cog's private
note, every cog's decision for the current round until it settles, the episode seed, the real
policy name behind any alias (including your own), and the grader's optimum. With
`ledger_public: false` the entire `ledger` key and every per-cog attribution disappear.

**At the end, once:**

```json
{"type":"final","done":true,"reason":"complete","scores":[…],"names":[…],"aliases":[…], …}
```

after which the player exits 0.

## Optional: player-side decisions

A player may also send `{"type":"decision", …schema fields…}`. A decision that arrives before the
round deadline **overrides** the game-side decision for that seat. This keeps the browser player
client (which certification opens) working and is how a future non-prompt policy could play. None
of the bundled policies use it.

## Reply schema and caps

| field | modules | type | range | invalid → |
| --- | --- | --- | --- | --- |
| `harvest` | `cleanup`, `harvest` | int | 0..3 | 0 |
| `clean` | `cleanup` | int | 0..3, `harvest + clean ≤ 3` | 0 / reduced first |
| `patch` | `harvest` | int | 0..5 | 0 |
| `eat` | `allelopathic`, `mushrooms` | int | 0..3 | 0 |
| `eat_color` | `allelopathic`, `mushrooms` | enum | `red\|green\|blue` | `red` |
| `plant` | `allelopathic` | int | 0..3, `eat + plant ≤ 3` | 0 / reduced first |
| `plant_color` | `allelopathic` | enum | `red\|green\|blue` | `eat_color` |
| `sanction` | all | int or null | `0..5`, never yourself | null |
| `message` | all | text | ≤ 140 runes | truncated |
| `note` | all | text | ≤ 200 runes | truncated |

Every free-text field is truncated on **rune** boundaries, never byte boundaries, and `note` is
private: it is echoed back only to its own seat and never written to the replay.
