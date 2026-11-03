"""Ordinary native HTTP/WebSocket games exercise an opt-in checkpoint seat."""

import base64
import json
import os
import socket
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Barrier, Thread
from uuid import uuid4

import httpx
import pytest

from coworld.examples.commons_family.game.baselines import make_baseline
from coworld.examples.commons_family.game.engine import (
    CommonsConfig,
    module_for,
    new_game,
    observation,
    open_round,
    parse_decision,
    settle_round,
)

ROOT = Path(__file__).resolve().parents[1]
TEACHER = "anthropic/claude-haiku-4.5"
CHECKPOINT = "checkpoint/" + "a" * 64


@pytest.mark.parametrize("checkpoint_seat", [False, True, "wrong-identity"])
def test_ordinary_native_checkpoint_seat_keeps_teacher_scaffold(
    tmp_path, checkpoint_seat
):
    manifest = json.loads((ROOT / "coworld_manifest_template.json").read_text())
    values = dict(
        next(v["game_config"] for v in manifest["variants"] if v["id"] == "cleanup")
    )
    values["tokens"] = [str(uuid4()) for _ in range(6)]
    config = CommonsConfig.model_validate(values)
    assert (config.rounds, config.round_seconds, config.min_round_seconds) == (
        20,
        20,
        3,
    )
    (tmp_path / "config.json").write_text(json.dumps(values))
    calls = []
    wrong_identity = checkpoint_seat == "wrong-identity"
    attempts_per_round = {}
    batch_barrier = Barrier(2)
    teachers = {
        slot: make_baseline("steward", seed=config.seed * 1000 + slot)
        for slot in range(2)
    }

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            slot = int(self.headers["X-Coworld-Player-Slot"])
            view = json.JSONDecoder().raw_decode(body["messages"][0]["content"])[0]
            proposal = teachers[slot].act(view)
            # Synthetic provider response, never an authenticated platform receipt.
            text = json.dumps(proposal)[1:]
            call_id = str(uuid4())
            record = {"slot": slot, "body": body, "response": text, "call_id": call_id}
            calls.append(record)
            # A serial seat loop cannot pass the ordinary parallel batch contract.
            key = (slot, view["round"])
            attempts_per_round[key] = attempts_per_round.get(key, 0) + 1
            if attempts_per_round[key] == 1:
                batch_barrier.wait(timeout=5)
            raw = json.dumps(
                {
                    "model": body["model"],
                    "content": [{"type": "text", "text": text}],
                    "stop_reason": "end_turn",
                    "usage": {"input_tokens": 1, "output_tokens": 1},
                }
            ).encode()
            record["raw_response"] = raw
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("X-Softmax-Llm-Call-Id", call_id)
            self.send_header("request-id", "fixture-" + call_id)
            if body["model"] == CHECKPOINT:
                for header, digest in [
                    ("Checkpoint", ("f" if wrong_identity else "a") * 64),
                    ("Tokenizer", "b" * 64),
                    ("Chat-Template", "c" * 64),
                ]:
                    self.send_header("X-Coworld-" + header + "-Sha256", digest)
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *_args):
            pass

    native = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=native.serve_forever, daemon=True)
    thread.start()
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    env = {
        **os.environ,
        "PYTHONPATH": str(ROOT / "src"),
        "COWORLD_LLM_ENDPOINT": f"http://127.0.0.1:{native.server_port}",
        "COWORLD_LLM_MODEL": TEACHER,
        "COGAME_CONFIG_URI": (tmp_path / "config.json").as_uri(),
        "COGAME_RESULTS_URI": (tmp_path / "results.json").as_uri(),
        "COGAME_SAVE_REPLAY_URI": (tmp_path / "replay.json").as_uri(),
        "COGAME_SAVE_TRAJECTORY_URI": (tmp_path / "private/episode.jsonl").as_uri(),
        "COWORLD_EPISODE_ID": "fixture-no-platform-receipts-" + str(uuid4()),
        "COWORLD_GAME_VERSION": "fixture-native-checkpoint",
        "COWORLD_SOURCE_REVISION": "fixture-working-source",
        "COGAME_HOST": "127.0.0.1",
        "COGAME_PORT": str(port),
        "COMMONS_FAMILY_POST_GAME_LINGER_SECONDS": "0",
        "COMMONS_FAMILY_POST_GAME_MAX_LINGER_SECONDS": "0",
    }
    for key in ("ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY_URI", "COGAME_LOG_URI"):
        env.pop(key, None)
    processes, logs = [], []
    try:
        out = (tmp_path / "server.log").open("wb")
        logs.append(out)
        server = subprocess.Popen(
            [sys.executable, "-m", "coworld.examples.commons_family.game.server"],
            cwd=ROOT,
            env=env,
            stdout=out,
            stderr=subprocess.STDOUT,
        )
        processes.append(server)
        deadline = time.monotonic() + 15
        with httpx.Client(timeout=1) as client:
            while True:
                assert server.poll() is None, (tmp_path / "server.log").read_text()
                try:
                    if (
                        client.get(f"http://127.0.0.1:{port}/healthz").status_code
                        == 200
                    ):
                        break
                except httpx.TransportError:
                    pass
                assert time.monotonic() < deadline
                time.sleep(0.05)
        for slot in range(6):
            player_env = {
                **env,
                "COWORLD_PLAYER_WS_URL": f"ws://127.0.0.1:{port}/player?slot={slot}&token={values['tokens'][slot]}",
            }
            if slot < 2:
                player_env["PLAYER_PROMPT"] = (
                    "Take the sustainable share, maintain the resource and use integer effort."
                )
                player_env.pop("PLAYER_SCRIPTED", None)
            else:
                player_env["PLAYER_SCRIPTED"] = "steward"
                player_env.pop("PLAYER_PROMPT", None)
            if checkpoint_seat and slot == 1:
                player_env["COWORLD_LLM_MODEL"] = CHECKPOINT
            out = (tmp_path / f"player-{slot}.log").open("wb")
            logs.append(out)
            processes.append(
                subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "coworld.examples.commons_family.player.player",
                    ],
                    cwd=ROOT,
                    env=player_env,
                    stdout=out,
                    stderr=subprocess.STDOUT,
                )
            )
        deadline = time.monotonic() + 105
        while any(p.poll() is None for p in processes):
            assert time.monotonic() < deadline, (tmp_path / "server.log").read_text()
            time.sleep(0.1)
        assert all(p.returncode == 0 for p in processes), (
            tmp_path / "server.log"
        ).read_text()
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
        for process in processes:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        native.shutdown()
        native.server_close()
        thread.join(timeout=5)
        for stream in logs:
            stream.close()
    assert len(calls) == (60 if wrong_identity else 40)
    assert {slot: sum(c["slot"] == slot for c in calls) for slot in range(2)} == {
        0: 20,
        1: 40 if wrong_identity else 20,
    }
    records = [
        json.loads(line)
        for line in (tmp_path / "private/episode.jsonl").read_text().splitlines()
    ]
    decisions, episode = records[:-1], records[-1]
    assert len(decisions) == 120 and episode["status"] == "completed"
    assert episode["outcome"]["reason"] == "complete"
    state, module = new_game(config), module_for(config)
    joined = {c["call_id"]: c for c in calls}
    for index in range(20):
        open_round(state, config, module)
        actions = []
        for slot, decision in enumerate(decisions[index * 6 : (index + 1) * 6]):
            assert decision["observation"] == observation(state, config, slot, module)
            rejected = wrong_identity and slot == 1
            assert decision["action_status"] == ("fallback" if rejected else "accepted")
            assert CHECKPOINT not in json.dumps(decision["observation"])
            attempts = [a for a in decision["attempts"] if a.get("platform_call_id")]
            if slot < 2:
                assert len(attempts) == (2 if rejected else 1)
                for selected in attempts:
                    call = joined[selected["platform_call_id"]]
                    body = call["body"]
                    expected = CHECKPOINT if checkpoint_seat and slot == 1 else TEACHER
                    assert body["model"] == expected and body["max_tokens"] == 300
                    assert "checkpoint_model" not in body
                    assert body["messages"][-1] == {"role": "assistant", "content": "{"}
                    assert selected["prompt"] == [
                        {"role": "system", "content": body["system"]},
                        *body["messages"],
                    ]
                    assert (
                        selected["request"] == body
                        and selected["response"] == call["response"]
                    )
                    assert (
                        base64.b64decode(selected["response_body_b64"])
                        == call["raw_response"]
                    )
                    if rejected:
                        assert not selected["accepted"]
                        assert (
                            selected["rejection_reason"]
                            == "checkpoint serving identity mismatch"
                        )
                        assert selected["attempt_id"] != decision["selected_attempt_id"]
                    else:
                        assert selected["parsed_action"] == decision["executed_action"]
                        assert selected["attempt_id"] == decision["selected_attempt_id"]
                    if expected == CHECKPOINT:
                        assert body["thinking"] == {"type": "disabled"}
                        assert body["temperature"] == 0 and body["top_p"] == 1
                        assert selected["decoder"]["thinking"] == body["thinking"]
                        assert (
                            selected["decoder"]["temperature"] == 0
                            and selected["decoder"]["top_p"] == 1
                        )
                        assert (
                            selected["model_identity"],
                            selected["tokenizer_identity"],
                            selected["chat_template_identity"],
                        ) == (("f" if rejected else "a") * 64, "b" * 64, "c" * 64)
                    else:
                        assert not {"thinking", "temperature", "top_p"} & body.keys()
            else:
                assert not attempts
            actions.append(
                parse_decision(
                    decision["executed_action"],
                    slot,
                    config,
                    state,
                    module,
                    "scripted:steward",
                )
            )
        settle_round(state, actions, config, module)
    assert state.round == 20
    assert all(
        abs(a - b) < 1e-9
        for a, b in zip(state.scores, episode["participant_outcomes"], strict=True)
    )
    assert CHECKPOINT not in (tmp_path / "replay.json").read_text()
    assert not thread.is_alive()


@pytest.mark.parametrize("invalid_route", [False, True])
def test_checkpoint_player_refuses_unsupported_game_or_invalid_identity(
    tmp_path, invalid_route
):
    import asyncio

    import websockets

    async def run():
        received = []

        async def legacy_game(websocket):
            await websocket.send(
                json.dumps({"type": "welcome", "protocol": "commons-family.player.v1"})
            )
            try:
                received.append(await asyncio.wait_for(websocket.recv(), timeout=2))
            except (TimeoutError, websockets.exceptions.ConnectionClosed):
                pass

        async with websockets.serve(legacy_game, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            env = {
                **os.environ,
                "PYTHONPATH": str(ROOT / "src"),
                "PLAYER_PROMPT": "Take the sustainable share.",
                "COWORLD_LLM_MODEL": "checkpoint/not-an-immutable-hash"
                if invalid_route
                else CHECKPOINT,
                "COWORLD_PLAYER_WS_URL": f"ws://127.0.0.1:{port}/player?slot=0&token=fixture",
            }
            env.pop("PLAYER_SCRIPTED", None)
            env.pop("COGAME_LOG_URI", None)
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "coworld.examples.commons_family.player.player",
                cwd=ROOT,
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                out, err = await asyncio.wait_for(process.communicate(), timeout=10)
            finally:
                if process.returncode is None:
                    process.kill()
                    await process.wait()
            (tmp_path / "client.stdout").write_bytes(out)
            (tmp_path / "client.stderr").write_bytes(err)
            assert process.returncode != 0
            assert (
                b"64-character weight hash"
                if invalid_route
                else b"does not advertise immutable checkpoint routing"
            ) in err
        assert received == []

    asyncio.run(run())
