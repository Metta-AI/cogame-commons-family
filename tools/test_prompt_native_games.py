"""Full published episodes with real host, players, and failed local inference.

This verifies transport/fallback provenance, not model quality. No game budgets
are shortened. --smoke selects two complete episodes for the ordinary CI job.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import subprocess
import sys
import time
from contextlib import ExitStack
from pathlib import Path

from coworld.examples.commons_family.game.evidence import (
    EpisodeContext,
    EpisodeEnd,
    InferenceAttempt,
    HostDecision,
)
from coworld.examples.commons_family.game.prompts import (
    HELDOUT_VARIANTS,
    TRAINING_VARIANTS,
    PromptSelection,
)

ROOT = Path(__file__).resolve().parents[1]


def run_episode(output: Path, variant: dict, wording: str) -> dict:
    case = output / f"{variant['id']}-{wording}"
    case.mkdir(parents=True)
    selection = PromptSelection(promptVariant=wording, promptSeed=3)
    config = {
        **variant["game_config"],
        "seed": 47,
        "tokens": [f"seat-{i}" for i in range(6)],
        **selection.model_dump(by_alias=True),
    }
    (case / "config.json").write_text(json.dumps(config))
    processes: list[subprocess.Popen] = []
    with ExitStack() as stack:
        unavailable = stack.enter_context(socket.socket())
        unavailable.bind(
            ("127.0.0.1", 0)
        )  # Reserved but never listening: real transport failure.
        with socket.socket() as reserved:
            reserved.bind(("127.0.0.1", 0))
            port = reserved.getsockname()[1]
        env = dict(
            os.environ,
            COGAME_CONFIG_URI=(case / "config.json").as_uri(),
            COGAME_RESULTS_URI=(case / "results.json").as_uri(),
            COGAME_SAVE_REPLAY_URI=(case / "replay.json").as_uri(),
            COGAME_HOST="127.0.0.1",
            COGAME_PORT=str(port),
            COWORLD_LLM_ENDPOINT=f"http://127.0.0.1:{unavailable.getsockname()[1]}",
            COMMONS_FAMILY_TRACE_PATH=str(case / "private.jsonl"),
            COWORLD_EPISODE_ID=case.name,
            COWORLD_SOURCE_REVISION="uncommitted-commons-family-validation",
        )
        try:
            host_log = stack.enter_context((case / "host.log").open("w"))
            host = subprocess.Popen(
                [sys.executable, "-m", "coworld.examples.commons_family.game.server"],
                cwd=ROOT,
                env=env,
                stdout=host_log,
                stderr=subprocess.STDOUT,
            )
            processes.append(host)
            deadline = time.monotonic() + 60
            while True:
                assert host.poll() is None, case / "host.log"
                with socket.socket() as probe:
                    if probe.connect_ex(("127.0.0.1", port)) == 0:
                        break
                assert time.monotonic() < deadline, "host readiness timeout"
                time.sleep(0.1)
            for slot in range(6):
                player_log = stack.enter_context(
                    (case / f"player-{slot}.log").open("w")
                )
                env.pop("PLAYER_SCRIPTED", None)
                player_env = dict(
                    env,
                    PLAYER_PROMPT=f"OPERATOR-SEAT-{slot}",
                    COWORLD_PLAYER_WS_URL=f"ws://127.0.0.1:{port}/player?slot={slot}&token=seat-{slot}",
                )
                processes.append(
                    subprocess.Popen(
                        [
                            sys.executable,
                            "-m",
                            "coworld.examples.commons_family.player.player",
                        ],
                        cwd=ROOT,
                        env=player_env,
                        stdout=player_log,
                        stderr=subprocess.STDOUT,
                    )
                )
            assert host.wait(timeout=1200) == 0, case / "host.log"
            for player in processes[1:]:
                assert player.wait(timeout=30) == 0, case
        finally:
            for process in processes:
                if process.poll() is None:
                    process.terminate()
            for process in processes:
                process.wait(timeout=30)

    results = json.loads((case / "results.json").read_text())
    replay = json.loads((case / "replay.json").read_text())
    assert (
        results["reason"] == "complete" and results["rounds"] == config["rounds"] == 20
    ), results
    assert results["disconnected"] == [False] * 6
    assert len(replay["rounds"]) == 20 and replay["results"] == results
    assert all(seat["kind"] == "prompt" for seat in replay["seats"])
    assert (case / "private.jsonl").stat().st_mode & 0o777 == 0o600
    models = {
        "episode_context": EpisodeContext,
        "episode_end": EpisodeEnd,
        "inference_started": InferenceAttempt,
        "inference_finished": InferenceAttempt,
        "host_decision": HostDecision,
    }
    starts, finishes, decisions, ends = {}, {}, [], []
    with (case / "private.jsonl").open() as stream:
        for line in stream:
            raw = json.loads(line)
            record = models[raw["kind"]].model_validate(raw)
            if isinstance(record, EpisodeContext):
                assert (
                    record.episode_id == case.name
                    and record.source_revision == env["COWORLD_SOURCE_REVISION"]
                )
            elif isinstance(record, InferenceAttempt):
                key = record.round, record.slot, record.attempt
                system, user = [message["content"] for message in record.prompt[:2]]
                assert record.prompt_template == selection.metadata(system, user)
                assert (
                    hashlib.sha256(system.encode()).hexdigest()
                    == record.prompt_template["system_sha256"]
                )
                assert f"OPERATOR-SEAT-{record.slot}" in system
                assert all(
                    f"OPERATOR-SEAT-{other}" not in system
                    for other in range(6)
                    if other != record.slot
                )
                assert record.request["system"] == system
                assert record.request["messages"] == record.prompt[1:]
                assert record.prompt[-1] == {"role": "assistant", "content": "{"}
                assert (
                    record.platform_call_id is None and record.behavior_logprobs is None
                )
                target = starts if record.kind == "inference_started" else finishes
                assert key not in target
                target[key] = record
            elif isinstance(record, HostDecision):
                assert record.selected_attempt is None and record.source.startswith(
                    "fallback:"
                )
                decisions.append(record)
            else:
                assert isinstance(record, EpisodeEnd)
                ends.append(record)
    assert (
        len(starts) == results["llm_requests"] > 0 and starts.keys() == finishes.keys()
    )
    for key, finish in finishes.items():
        assert (
            finish.prompt == starts[key].prompt
            and finish.request == starts[key].request
        )
        assert finish.outcome == "transport" and finish.parsed_reply is None
        assert finish.raw_response is None and finish.response is None
    assert (
        len(ends) == 1 and ends[0].status == "completed" and ends[0].results == results
    )
    assert len(decisions) == sum(results["fallbacks"]) == 120
    for record in decisions:
        public = replay["rounds"][record.round]["decisions"][record.slot]
        assert public["src"] == record.source and public["slot"] == record.slot
        for field, value in record.action.items():
            if field != "note":
                assert public[field] == (
                    value or "" if field == "message" else value
                ), (field, public, record)
    assert "OPERATOR-SEAT-" not in (case / "replay.json").read_text()
    print(
        f"{case.name}: 20 rounds, {len(starts)} actual attempts, 120 normalized host decisions PASS",
        flush=True,
    )
    # Fall-back cause depends on request timing, but all seats run the same steward.
    rounds = replay["rounds"]
    for round_record in rounds:
        for decision in round_record["decisions"]:
            decision.pop("src")
    return {"rounds": rounds, "scores": results["scores"]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args()
    variants = json.loads((ROOT / "coworld_manifest_template.json").read_text())[
        "variants"
    ]
    for variant in variants:
        if args.smoke and variant["id"] != "mushrooms":
            continue
        wordings = ["canonical", "mixed"]
        if variant["id"] in ("allelopathic", "mushrooms"):
            wordings = [*TRAINING_VARIANTS, *HELDOUT_VARIANTS, "mixed"]
        if args.smoke:
            wordings = ["canonical", "mixed"]
        baseline = None
        for wording in wordings:
            result = run_episode(args.output.resolve(), variant, wording)
            if baseline is None:
                baseline = result
            assert result == baseline, (
                f"wording changed fallback gameplay: {variant['id']} {wording}"
            )


if __name__ == "__main__":
    main()
