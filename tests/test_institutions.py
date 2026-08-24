"""The institutional layer: ledger, sanctions, norm, chat — and the name spaces.

Meadow's invariants, kept, plus the one thing this fork changes: meadow's
`observation()` puts `player_names[other]` — the runner's REAL policy names —
straight into the ledger. Here the in-game name space is anonymous, and the
last test in this file is what keeps it that way.
"""

from __future__ import annotations

import json

import pytest

from coworld.examples.commons_family.game.engine import (
    NOTE_MAX_CHARS,
    CommonsConfig,
    module_for,
    new_game,
    observation,
    parse_decision,
    settle_round,
    truncate_runes,
)

REAL_NAMES = [
    "commons-family-steward",
    "commons-family-warden",
    "daveey-1",
    "baseline",
    "commons-family-freerider",
    "commons-family-cleaner",
]


def setup(**overrides):
    config = CommonsConfig(num_agents=6, rounds=5, **overrides)
    state = new_game(config)
    return config, state, module_for(config)


def play(state, config, module, raws):
    decisions = [
        parse_decision(raw, slot, config, state, module, "scripted:test")
        for slot, raw in enumerate(raws)
    ]
    return settle_round(state, decisions, config, module)


def test_sanction_costs_the_payer_and_burns_the_target():
    config, state, module = setup(sanctions_enabled=True)
    play(state, config, module, [{"harvest": 1, "sanction": 3}] + [{"harvest": 1}] * 5)
    # slot 0 gained 1.0 and paid 1.0; slot 3 gained 1.0 and lost 3.0.
    assert state.scores[0] == pytest.approx(0.0)
    assert state.scores[3] == pytest.approx(-2.0)
    assert state.sanctions_given[0] == 1
    assert state.sanctions_received[3] == 1
    assert any(event["kind"] == "sanction" for event in state.events)


def test_a_sanction_is_dropped_when_the_dial_is_off():
    config, state, module = setup(sanctions_enabled=False)
    play(state, config, module, [{"harvest": 1, "sanction": 3}] + [{"harvest": 1}] * 5)
    assert state.sanctions_given == [0] * 6
    assert state.scores[3] == pytest.approx(1.0)


@pytest.mark.parametrize("target", [0, 6, -1, 99, "two", None, True])
def test_a_sanction_is_dropped_unless_it_names_a_real_other_seat(target):
    config, state, module = setup(sanctions_enabled=True)
    decision = parse_decision({"sanction": target}, 0, config, state, module, "x")
    assert decision.sanction is None


def test_anonymous_ledger_removes_every_per_cog_attribution():
    config, state, module = setup(ledger_public=False)
    play(state, config, module, [{"harvest": 2}] + [{"harvest": 1}] * 5)
    obs = observation(state, config, 0, module)
    assert "ledger" not in obs
    # Only the aggregate survives, plus the seat's own facts.
    assert obs["last_round_total_extracted"] == pytest.approx(7.0)
    assert obs["your_last_gain"] == pytest.approx(2.0)
    body = json.dumps(obs)
    assert "total_extracted" not in body.replace("last_round_total_extracted", "")


def test_public_ledger_carries_the_recent_actions_and_the_counters():
    config, state, module = setup(ledger_public=True, sanctions_enabled=True)
    for _ in range(6):
        play(state, config, module, [{"harvest": 1, "sanction": 1}] + [{"harvest": 1}] * 5)
    obs = observation(state, config, 2, module)
    entry = obs["ledger"][0]
    assert entry["alias"] == state.aliases[0]
    assert entry["slot"] == 0
    assert len(entry["recent"]) == 5           # the last five, never more
    assert entry["sanctions_given"] == 6
    assert obs["ledger"][1]["sanctions_received"] == 6


def test_chat_is_truncated_on_rune_boundaries():
    config, state, module = setup(chat_enabled=True, chat_max_chars=10)
    long_line = "みどり" * 20
    decision = parse_decision({"message": long_line}, 0, config, state, module, "x")
    assert decision.message is not None
    assert len(decision.message) == 10
    assert decision.message == long_line[:10]
    # A rune boundary, not a byte boundary: it still round-trips through UTF-8.
    assert decision.message.encode("utf-8").decode("utf-8") == decision.message


def test_a_note_is_truncated_and_never_reaches_the_replay():
    config, state, module = setup()
    long_note = "私" * 400
    record = play(state, config, module, [{"note": long_note}] + [{}] * 5)
    assert len(state.notes[0]) == NOTE_MAX_CHARS
    assert all("note" not in decision for decision in record.decisions)


def test_a_note_is_echoed_back_only_to_its_own_seat():
    config, state, module = setup()
    play(state, config, module, [{"note": "watch Cog-B"}] + [{}] * 5)
    assert observation(state, config, 0, module)["your_note"] == "watch Cog-B"
    assert observation(state, config, 1, module)["your_note"] == ""


def test_chat_is_visible_in_the_next_round_and_never_inside_its_own():
    config, state, module = setup(chat_enabled=True)
    assert observation(state, config, 1, module)["messages_last_round"] == []
    play(state, config, module, [{"message": "one each"}] + [{}] * 5)
    messages = observation(state, config, 1, module)["messages_last_round"]
    assert messages == [{"alias": state.aliases[0], "text": "one each"}]
    play(state, config, module, [{}] * 6)
    assert observation(state, config, 1, module)["messages_last_round"] == []


def test_chat_is_dropped_when_the_dial_is_off():
    config, state, module = setup(chat_enabled=False)
    record = play(state, config, module, [{"message": "hello"}] + [{}] * 5)
    assert record.messages == []
    assert not any(event["kind"] == "chat" for event in state.events)


def test_the_norm_is_carried_into_every_observation():
    config, state, module = setup(norm_text="Posted quota: one unit each.")
    for slot in range(6):
        assert observation(state, config, slot, module)["norm_text"] == \
            "Posted quota: one unit each."


def test_no_real_policy_name_appears_in_any_observation():
    """The two-name-spaces assertion.

    The runner's real policy names exist spectator-side only. A prompt seat is
    composed from exactly this object, so if a real name were reachable here it
    would be reachable by a policy.
    """
    config, state, module = setup(ledger_public=True, chat_enabled=True, sanctions_enabled=True)
    for _ in range(3):
        play(state, config, module, [{"harvest": 2, "message": "hello"}] * 6)
    for slot in range(6):
        body = json.dumps(observation(state, config, slot, module), ensure_ascii=False)
        for name in REAL_NAMES:
            assert name not in body
        assert state.aliases[slot] in body


@pytest.mark.parametrize("module_name", ["cleanup", "harvest", "allelopathic", "mushrooms"])
def test_an_observation_never_leaks_another_cogs_secret(module_name):
    config, state, module = setup(module=module_name)
    for slot in range(6):
        obs = observation(state, config, slot, module)
        module_state = obs["module_state"]
        assert "favorites" not in module_state
        assert "seed" not in obs
        if module_name == "allelopathic":
            assert module_state["your_favorite"] == state.module_state["favorites"][slot]


def test_truncate_runes_handles_non_strings_and_whitespace():
    assert truncate_runes(None, 10) == ""
    assert truncate_runes(12, 10) == ""
    assert truncate_runes("  padded  ", 10) == "padded"
    assert truncate_runes("🌲🌲🌲", 2) == "🌲🌲"
