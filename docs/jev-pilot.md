# Commons Family Jev pilot

`PLAYER_JEV=1` uses the existing player decision frame. The player registers a scripted steward fallback, then asks System One to rank the current actions of steward, free rider, cleaner, punisher, reciprocator, and deterrable baselines. Duplicate actions are removed. It sends the selected action before the round deadline. The action may contain the baseline's public message; Jev does not generate new chat or private notes.

## Paired local episodes

Each arm used the same seed 7, eight rounds, seat 0, and the same five scripted opponents: cleaner, punisher, reciprocator, free rider, and deterrable. Local calls used TypeSafe `jev-latest`, which resolved to `jev-1.13.0` in a separate response. The direct response does not report dollar spend.

| Module | Jev seat 0 | Steward seat 0 | Jev calls | Observed choice pattern |
| --- | ---: | ---: | ---: | --- |
| Cleanup | 6.0 | 6.0 | 8 | Cleaner four rounds, then steward four |
| Harvest | 17.0 | 15.0 | 8 | Reciprocator seven rounds, then free rider |
| Allelopathic | 28.275 | 10.857 | 8 | Free rider all eight rounds |
| Mushrooms | 9.8 | 9.8 | 8 | Reciprocator once, then steward seven |

Across 32 calls, mean response time was 345 ms, maximum 406 ms; two reported choices differed from the probability maximum, which the player applied. The allelopathic score gain came from choosing free riding every round. These four single-seed episodes measure neither general performance nor socially helpful behavior.

## Container proof

A `linux/amd64` image built and completed the raw Docker smoke with a temporary certification fixture that put Jev in slot 0 and kept the five bundled scripted opponents. The eight-round cleanup replay records eight `src: player` decisions for slot 0 and zero fallbacks. All six player containers exited 0. Slot 0 scored 5 against the other seats' 8, 8, 1, 3, and 11. The Jev player made a real TypeSafe call in each round; the hosted sidecar result is below.

Validation: `PYTHONPATH=src python -m pytest -q tests/` passed 280 tests, including the Jev choice and registration cases. Ruff check and format passed on changed Python files. The current canonical Coworld version accepts player decision frames; the private production result below needed no game version change.

## Private production canary

The relh-owned `commons-family-jev:v1` policy ran in private Experience Request `xreq_44849cce-beb6-4841-a229-5d8cedabee37` on canonical Coworld 0.1.3. It used the cleanup variant for 20 rounds. The league had two active versions of the same relh ledger-warden policy, so those versions filled the five opponent seats. Jev scored 20; opponent seats scored 22, 20, 22, 20, and 22. This is a protocol smoke test, not a paired performance comparison.

The Jev player log records 20 completed choices, zero fallback, 31,708 input tokens, 1,441 output tokens, and 271 ms mean latency (755 ms maximum). It chose cleaner in 19 rounds and steward in one. The sidecar's cumulative Jev spend was $0.001332. The episode reported $0.017006 of Kubernetes execution cost; it is not a model-inclusive total. The combined player LLM cap was $0.05. No ladder submission or game-version change was made.
