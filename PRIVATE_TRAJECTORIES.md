# Private runtime trajectories

Set `COGAME_SAVE_TRAJECTORY_URI` to a private `file://` or signed upload destination.
The game records canonical Coworld decision events and a terminal episode summary,
separately from the spectator replay. Capture requires dispatcher-provided
`COWORLD_EPISODE_ID`, `COWORLD_GAME_VERSION`, and `COWORLD_SOURCE_REVISION`.
Missing identity pins fail visibly; they are never inferred from replay bytes.

The engine retains exact native request bodies, prompts, response bytes, response
headers, native `X-Softmax-Llm-Call-Id`, rejected attempts, and its normalized applied
action. Scripted policies and engine fallbacks retain distinct origins. A player submission that overrides a model proposal rejects that proposal in
the evidence; the selected action retains its actual origin. Physical families hash the actual initial module state and active resource/institution
rules, without alias permutations, prompt wording, clocks, or unused deals. Open harvest
ownership/pairs and neutral favorite assignments are excluded; consequential closed
ownership, partnership pairs, and favorite roles remain. Cleanup and mushrooms therefore
group replicas across seeds. The namespace is `commons-family-physical-v1-SHA256`.
Historical `commons-family-SEED` artifacts are unchanged and may falsely split alias-only
replicas; they require explicit review before any family-sensitive training export. A deadline is truncated,
and only normal completion qualifies as a completed episode.

File artifacts use exclusive creation and mode 0600. Use a new destination for
each episode. Upload errors propagate; capture is never silently disabled after
being configured. Private trajectories must not be included in public replay uploads.
The canonical SDK `read_trajectory_jsonl` and `complete_episodes` validate the output.
Local protocol fixtures do not establish hosted source authority or model quality.

The headless whole-episode runner uses the same capture path. Runtime round-loop
failures mark private episodes failed even when the historical public result
contract settles already-played rounds.

The engine records each model proposal through the real parser before settlement or
player overrides. A changed supplied field (including a clamp) is conservatively
rejected as a model label, while its parser output and original response remain
available. Private fallback counts therefore also include parser normalization,
without changing public gameplay. Failed protocol steps retain pending native
attempts under the failed episode outcome rather than inventing applied decisions.
