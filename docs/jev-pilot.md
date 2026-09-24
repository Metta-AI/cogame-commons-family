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

A `linux/amd64` image built and completed the raw Docker smoke with a temporary certification fixture that put Jev in slot 0 and kept the five bundled scripted opponents. The eight-round cleanup replay records eight `src: player` decisions for slot 0 and zero fallbacks. All six player containers exited 0. Slot 0 scored 5 against the other seats' 8, 8, 1, 3, and 11. The Jev player made a real TypeSafe call in each round; the hosted sidecar remains untested.

Validation: `PYTHONPATH=src python -m pytest -q tests/` passed 280 tests, including the Jev choice and registration cases. Ruff check and format passed on changed Python files. A private production Experience Request with a relh-owned player policy can test the current canonical Coworld version; no game version change is needed because that version already accepts player decision frames.
