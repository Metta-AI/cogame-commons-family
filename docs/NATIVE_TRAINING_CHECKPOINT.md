# Unfinished native training checkpoint

This draft is an implementation checkpoint, not a ready runtime or release.

Completed source work:
- Move the game namespace from coworld.examples.commons_family to commons_family, so the installed Coworld SDK is not shadowed.
- Declare normal package dependencies, including the unreleased stable coworld>=0.1.57 floor.
- Add engine-issued typed decision/grant/STOP frames, immutable pre-window profile registration, bounded actual native transport and private attempt spooling.
- Preserve the ordinary Haiku assistant-prefill grammar, max-token controls and omitted temperature/top_p defaults.
- Independently compare received native completion with submitted action before engine parsing.

Bounded diagnostic evidence: three source-boundary tests passed on Titan in 7.31 seconds. They verify ordinary renderer/prefill roundtrip, hidden favorites/notes permutation, registration immutability and response/action mismatch rejection. The diagnostic reused the read-only Collab dependency closure; Commons normal dependency resolution is not qualified. Initial pytest plugin-autoload failure and macOS AppleDouble Ruff failure remain preserved.

Required remaining work:
- Replace the old game-owned LlmDecider/provider ladder and every active caller. The legacy server, player and exporter are still live in this draft.
- Finish shared grant retry/throttle admission, truthful native partial evidence, cancellation/STOP acknowledgements and bounded joins before sealing.
- Implement authoritative round journals and a common direct-SDK CompleteEpisode producer for runtime and teacher collection.
- Remove exporter-issued train/validation labels; require external content-bound review.
- Finish ordinary scripted/native player profiles, all browser/protocol clients and public privacy paths.
- Resolve normal Commons dependencies, then run full tests/type/lint, six ordinary variant cohorts, independent engine review/import, real six-player native HTTP/WebSocket failure/signal gates and exact-source image/cert/browser checks.

No model-serving authenticity, training strength, completed runtime, release or production upload is claimed. Protected histories and previous captures remain untouched.
