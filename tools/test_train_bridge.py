"""Play every certified Commons Family variant through its numeric bridge."""

import json
import subprocess
import sys
from pathlib import Path


manifest = Path(__file__).resolve().parents[1] / "coworld_manifest_template.json"
variants = (
    "cleanup",
    "harvest-open",
    "harvest-closed",
    "harvest-partnership",
    "allelopathic",
    "mushrooms",
)
for variant in variants:
    with subprocess.Popen(
        [sys.executable, sys.argv[1], str(manifest), variant],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    ) as bridge:
        assert bridge.stdin is not None and bridge.stdout is not None

        def request(payload):
            bridge.stdin.write(json.dumps(payload) + "\n")
            bridge.stdin.flush()
            return json.loads(bridge.stdout.readline())

        obs = request({"kind": "reset", "seed": "commons-bridge-test", "players": 6})
        dimensions = None
        decisions = 0
        while obs["kind"] == "decision":
            view = obs["semantic_view"]
            assert view["slot"] == obs["seat"]
            assert view["module"] in ("cleanup", "harvest", "allelopathic", "mushrooms")
            encoded = request({"kind": "encode"})
            assert encoded["decision_id"] == obs["decision_id"]
            dimensions = dimensions or len(encoded["values"])
            assert len(encoded["values"]) == dimensions
            assert [head["name"] for head in encoded["action_heads"]] == [
                "module_action",
                "sanction",
            ]
            assert len(encoded["action_heads"][1]["choices"]) == 6
            action = json.loads(request({"kind": "teacher"})["response"])
            for head in encoded["action_heads"]:
                assert action[head["name"]] in head["choices"]
            result = request(
                {
                    "kind": "step",
                    "decision_id": obs["decision_id"],
                    "response": json.dumps(action),
                }
            )
            assert result["kind"] == "accepted" and result["action"] == action
            next_obs = result["observation"]
            if next_obs["kind"] == "decision" and decisions % 6 != 5:
                assert next_obs["turn"] == obs["turn"]
            obs = next_obs
            decisions += 1
        assert decisions == 120 and len(obs["scores"]) == 6
        bridge.stdin.close()
        assert bridge.wait() == 0
    print(variant, decisions, dimensions, obs["scores"])
