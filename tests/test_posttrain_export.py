"""Public exporter games must partition actual physical families, never seed aliases."""

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from coworld.examples.commons_family.game.engine import CommonsConfig, new_game
from coworld.examples.commons_family.game.training_capture import physical_family

ROOT = Path(__file__).resolve().parents[1]
VARIANTS = (
    "cleanup",
    "harvest-open",
    "harvest-closed",
    "harvest-partnership",
    "allelopathic",
    "mushrooms",
)


@pytest.fixture(scope="module")
def committed_source(tmp_path_factory):
    # Commit only current game/tool source to exercise the actual clean-source CLI.
    # Never copy a checkout's Git history, runtime artifacts or private evidence.
    root = tmp_path_factory.mktemp("source")
    names = (
        subprocess.check_output(
            [
                "git",
                "ls-files",
                "-z",
                "src",
                "tools",
                "coworld_manifest_template.json",
                ".gitignore",
            ],
            cwd=ROOT,
        )
        .decode()
        .split("\0")
    )
    for name in filter(None, names):
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    subprocess.run(
        ["git", "-c", "init.templateDir=", "init", "-q", str(root)], check=True
    )
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=System Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-qm",
            "Exact exporter system fixture",
        ],
        cwd=root,
        check=True,
    )
    return root


@pytest.mark.parametrize("variant", VARIANTS)
@pytest.mark.parametrize("wording", ("canonical", "mixed"))
def test_complete_exports_keep_physical_families_together(
    committed_source, tmp_path, variant, wording
):
    root = committed_source
    output = tmp_path / "export"
    result = subprocess.run(
        [
            sys.executable,
            "tools/export_posttrain.py",
            str(output),
            "10",
            "--variant",
            variant,
            "--prompt-variant",
            wording,
            *(["--prompt-seed", "42"] if wording == "mixed" else []),
        ],
        cwd=root,
        env={**os.environ, "PYTHONPATH": str(root / "src")},
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    manifest = json.loads((output / "manifest.json").read_text())
    assert len(manifest["runs"]) == 10
    assert all(
        run["rounds"] == 20 and len(run["scores"]) == 6 for run in manifest["runs"]
    )
    assert all(
        run["prompt_selection"]["selection"] == wording for run in manifest["runs"]
    )
    assert manifest["teacher"] == "scripted-steward"
    variants = json.loads((root / "coworld_manifest_template.json").read_text())[
        "variants"
    ]
    config = next(entry["game_config"] for entry in variants if entry["id"] == variant)
    for run in manifest["runs"]:
        native_config = CommonsConfig.model_validate(config | {"seed": run["seed"]})
        expected = physical_family(native_config, new_game(native_config))
        assert run["seed_family"] == expected
    assert manifest["split_scheme"] == "physical-family-sha256-first32-validation20-v1"
    seen = {}
    for split in ("train", "validation"):
        rows = [
            json.loads(line)
            for line in (output / f"{split}.jsonl").read_text().splitlines()
        ]
        assert len(rows) == manifest[f"{split}_examples"]
        seen[split] = {row["seed"] for row in rows}
        assert seen[split] == set(manifest["physical_families"][split])
        for family in seen[split]:
            assert family.startswith("commons-family-physical-v1-")
            expected = (
                "validation"
                if int(hashlib.sha256(family.encode()).hexdigest()[:8], 16) / 2**32
                < 0.2
                else "train"
            )
            assert split == expected == manifest["seed_family_splits"][family]
    assert not seen["train"] & seen["validation"]
    assert manifest["independent_split_coverage"] == bool(
        seen["train"] and seen["validation"]
    )
    if variant in {"cleanup", "harvest-open", "mushrooms"}:
        assert len(seen["train"] | seen["validation"]) == 1
        assert not manifest["independent_split_coverage"]
        assert any((output / f"{split}.jsonl").stat().st_size == 0 for split in seen)
