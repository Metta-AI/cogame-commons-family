"""The replay bytes: strict UTF-8, complete keys, and a closed event vocabulary.

Parsed with no error handler at all — a string truncated on a BYTE boundary
mid-rune renders fine in a browser and fails here, which is the point.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from coworld.examples.commons_family import headless
from coworld.examples.commons_family.game.engine import (
    END_REASONS,
    EVENT_KINDS,
    REPLAY_FORMAT,
    REPLAY_PROTOCOL,
    CommonsConfig,
    module_for,
    new_game,
    parse_decision,
    settle_round,
)

REQUIRED_KEYS = (
    "format",
    "protocol",
    "config",
    "seed",
    "names",
    "policyNames",
    "seats",
    "rounds",
    "events",
    "results",
)


def test_the_replay_is_strict_utf8_json(episode_dir: Path):
    data = (episode_dir / "replay.json").read_bytes()
    text = data.decode("utf-8")          # no errors= handler, deliberately
    assert json.loads(text)


def test_every_required_key_is_present(replay):
    for key in REQUIRED_KEYS:
        assert key in replay, key
    assert replay["format"] == REPLAY_FORMAT
    assert replay["protocol"] == REPLAY_PROTOCOL
    assert replay["coworld"] == "commons_family"


def test_the_three_name_lists_are_the_seat_count_long(replay):
    seats = replay["config"]["num_agents"]
    assert len(replay["names"]) == seats
    assert len(replay["policyNames"]) == seats
    assert len(replay["seats"]) == seats
    assert [seat["slot"] for seat in replay["seats"]] == list(range(seats))


def test_the_replay_carries_both_name_spaces(replay):
    assert replay["names"] == [seat["alias"] for seat in replay["seats"]]
    assert all(name.startswith("Cog-") for name in replay["names"])
    assert "commons-family-steward" in replay["policyNames"]


def test_the_end_reason_is_in_the_legal_enum(replay, results):
    assert replay["results"]["reason"] in END_REASONS
    assert results["reason"] in END_REASONS


def test_every_event_carries_a_kind_inside_the_vocabulary_and_a_round(replay):
    assert replay["events"], "an episode with no events is not a replay"
    for event in replay["events"]:
        assert event["kind"] in EVENT_KINDS, event
        assert isinstance(event["r"], int), event
        if "slot" in event:
            assert "alias" in event, event


def test_the_engine_can_emit_no_other_event_kind(replay):
    emitted = {event["kind"] for event in replay["events"]}
    assert emitted <= set(EVENT_KINDS)
    # The kinds a normal settled episode must show, or the viewer has nothing
    # to draw and the scrubber has no beats.
    assert {"episode_start", "round_open", "decision", "resolve", "round_end",
            "episode_end"} <= emitted


def test_every_round_record_has_the_shape_the_viewer_expands(replay):
    seats = replay["config"]["num_agents"]
    for record in replay["rounds"]:
        for key in ("r", "state_before", "state_after", "decisions", "gains",
                    "extracted", "scores", "series", "seat_frozen",
                    "public_effort", "seat_public_effort"):
            assert key in record, key
        assert len(record["gains"]) == seats
        assert len(record["scores"]) == seats
        assert len(record["decisions"]) == seats
        assert set(record["series"]) == {"total", "maintenance"}


def test_the_maintenance_effort_the_viewer_shows_is_recorded_per_seat(replay):
    """The viewer must not re-derive `public_effort` from the decisions.

    It is the one quantity the wasm module used to recompute, per module, from
    each recorded decision — a second implementation of `Module.public_effort`
    in another language. The engine books it in step 8 and records it here.
    """
    config = CommonsConfig(**replay["config"])
    module = module_for(config)
    seats = config.num_agents
    running = [0] * seats
    for record in replay["rounds"]:
        per_seat = record["seat_public_effort"]
        assert len(per_seat) == seats
        assert sum(per_seat) == record["public_effort"]
        for slot, decision in enumerate(record["decisions"]):
            expected = module.public_effort(
                parse_decision(decision, slot, config, new_game(config), module, "replay"),
                config,
            )
            assert per_seat[slot] == expected, (record["r"], slot)
            running[slot] += per_seat[slot]
    assert running == replay["results"]["public_effort"]


def test_note_is_absent_from_the_replay_entirely(episode_dir: Path):
    text = (episode_dir / "replay.json").read_bytes().decode("utf-8")
    payload = json.loads(text)
    for record in payload["rounds"]:
        for decision in record["decisions"]:
            assert "note" not in decision
    assert '"note"' not in text


def test_a_message_at_the_rune_cap_survives_as_valid_utf8(tmp_path: Path):
    """A 140-RUNE multi-byte line, truncated and written, still decodes."""
    config = CommonsConfig(module="cleanup", rounds=2, chat_enabled=True, chat_max_chars=140)
    module = module_for(config)
    state = new_game(config)
    # 200 runes of four-byte emoji plus CJK: byte-truncating this at 140 bytes
    # would split a rune and the strict decode below would fail.
    line = ("🌲森" * 100)[:200]
    for _ in range(config.rounds):
        raws = [{"harvest": 1, "message": line} for _ in range(config.num_agents)]
        decisions = [
            parse_decision(raw, slot, config, state, module, "scripted:test")
            for slot, raw in enumerate(raws)
        ]
        settle_round(state, decisions, config, module)

    headless.write_artifacts(state, config, tmp_path)
    data = (tmp_path / "replay.json").read_bytes()
    payload = json.loads(data.decode("utf-8"))
    recorded = payload["rounds"][0]["decisions"][0]["message"]
    assert len(recorded) == 140
    assert recorded == line[:140]
    assert recorded.encode("utf-8").decode("utf-8") == recorded
    assert any(event["kind"] == "chat" for event in payload["events"])


def test_the_replay_is_self_sufficient(replay):
    """Everything the viewer needs is in the bytes: no server is ever contacted."""
    assert replay["seed"] == replay["config"]["seed"]
    assert replay["module"] == replay["config"]["module"]
    assert "tokens" not in replay["config"]
    assert replay["config"]["rounds"] >= len(replay["rounds"])
    assert set(replay["results"]) >= {
        "reason", "rounds", "scores", "total_extracted", "public_effort",
        "sanctions_given", "sanctions_received", "welfare", "residual_value",
        "collapse_round", "dead_patches", "fallbacks", "llm_requests",
        "names", "aliases", "disconnected",
    }


@pytest.mark.parametrize("module_name", ["cleanup", "harvest", "allelopathic", "mushrooms"])
def test_every_module_writes_a_parseable_replay(module_name, tmp_path: Path):
    config = CommonsConfig(module=module_name, rounds=4, sanctions_enabled=True)
    state = headless.run_episode(
        config, headless.build_policies(["steward", "free_rider", "cleaner",
                                        "punisher", "reciprocator", "random"])
    )
    out = tmp_path / module_name
    headless.write_artifacts(state, config, out)
    payload = json.loads((out / "replay.json").read_bytes().decode("utf-8"))
    assert payload["module"] == module_name
    assert all(event["kind"] in EVENT_KINDS for event in payload["events"])
