"""The manifest's shape, checked where a reviewer can see it.

`coworld build` fills in the image and the version; everything else in
`coworld_manifest_template.json` ships as written, so the parts the acceptance
checklist spells out literally are asserted here rather than discovered in
hosted certification.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = json.loads((ROOT / "coworld_manifest_template.json").read_text(encoding="utf-8"))
README = ROOT / "src/coworld/examples/commons_family/README.md"


def test_the_readme_and_every_page_are_inline_text():
    docs = MANIFEST["game"]["docs"]
    assert docs["readme"]["type"] == "text"
    assert docs["readme"]["value"] == README.read_text(encoding="utf-8")
    assert docs["pages"]
    for page in docs["pages"]:
        assert set(page) == {"id", "title", "content"}
        assert page["content"]["type"] == "text"
        assert page["content"]["value"].strip()


def test_both_protocols_are_declared():
    protocols = MANIFEST["game"]["protocols"]
    assert set(protocols) == {"player", "global"}
    for protocol in protocols.values():
        assert protocol["type"] in ("text", "uri")
        assert protocol["value"].strip()


def test_the_static_replay_bundle_is_declared():
    assert MANIFEST["game"]["replay_viewer"] == {"bundle": "static-replay-viewer"}


@pytest.mark.parametrize("variant", MANIFEST["variants"])
def test_every_variant_seats_six(variant):
    assert variant["game_config"]["num_agents"] == 6
    assert len(variant["game_config"]["players"]) == 6


def test_the_certification_fixture_seats_six():
    cert = MANIFEST["certification"]
    assert cert["game_config"]["num_agents"] == 6
    assert len(cert["players"]) == 6
    assert len(cert["game_config"]["players"]) == 6
