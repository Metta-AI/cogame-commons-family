# Global protocol — `/global`

A read-only spectator socket. Connect to `ws://<game>:8080/global`; the server sends one snapshot
immediately and then **only on game progress** (the round advanced or the episode finished),
coalesced to at most one message per second, plus a 15 s keepalive.

That gating is load-bearing and is meadow's, kept verbatim. The hosted certifier holds this socket
open **without reading** while it verifies player pods, and its websocket client stops reading the
transport — including Pong frames — once about sixteen messages sit unread. An unconditional 2 Hz
stream fills that budget during any pod-start delay and the certification ping then times out
against a perfectly healthy server.

## Snapshot

```json
{"type":"state","protocol":"commons-family.player.v1","module":"cleanup",
 "round":7,"rounds":20,
 "module_state":{ …the public resource state, same shape as the observation's… },
 "scores":[11.33, …],"total_extracted":[15.0, …],"public_effort":[4, …],
 "aliases":["Cog-A", …],"player_names":["commons-family-steward", …],
 "last_round":{ …the settled RoundRecord… },
 "connected":[0,1,2,3,4,5],"submitted":[],
 "started":true,"paused":false,"round_seconds":20.0,
 "done":false,"reason":""}
```

`aliases` is what the cogs call each other; `player_names` is spectator-side only and is the real
policy name for each seat. `last_round` is `null` until the first round settles. `reason` is empty
until the episode ends and is then one of `complete`, `deadline`, `no_players`.

## Admin socket

`ws://<game>:8080/admin` answers the same snapshot and accepts `{"command":"pause"}`,
`{"command":"resume"}` and `{"command":"round_seconds","round_seconds":N}`.

## HTTP

- `GET /healthz` → `{"ok": true}`
- `GET /client/global` — this view as a page
- `GET /client/player` — the human player client
- `GET /client/admin` — the admin page

There is no `/client/replay` route. Replays are the static wasm bundle declared in the manifest as
`"replay_viewer": {"bundle": "static-replay-viewer"}`, served by the platform from the replay
bytes alone.
