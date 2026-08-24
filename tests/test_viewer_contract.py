"""The viewer payload contract, checked without a browser.

This is the static half of the viewer gate — `ci.yml`'s `wasm-viewer` job runs
the other half in real chromium. What is checked here is what a browser cannot
tell you quickly: that the four viewer files and the shared chrome name the
SAME module symbols. cogame-lantern (2026-08-23) shipped a viewer whose
emscripten link flags and whose JS bootstrap came from two different starters —
every file present, every asset 200, the factory never called, and the page sat
on "Loading replay…" forever. A statically checkable symbol match would have
caught it before it was built.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from coworld.examples.commons_family import headless
from coworld.examples.commons_family.game.engine import EVENT_KINDS, CommonsConfig, module_for

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "replay-viewer/index.html"
STATIC_JS = ROOT / "replay-viewer/static_replay.js"
CONFIG_NIMS = ROOT / "replay-viewer/config.nims"
WASM_NIM = ROOT / "replay-viewer/commons_family_replay.nim"
RENDERER = ROOT / "client/renderer.js"
CHROME = ROOT / "client/chrome.css"

EXPORTS = ("cf_load_replay", "cf_payload_ptr", "cf_payload_len", "cf_error_ptr", "cf_error_len")
# Everything renderer.js exports or uses as a global. The appended game block
# in index.html may not declare a top-level function with any of these names:
# hoisting would shadow them and the affected chrome renders as dead nodes
# (tandem, 2026-08-23).
RENDERER_GLOBALS = (
    "CommonsRenderer", "attachReplay", "attachLive", "renderFeed", "bindFeedToggle",
    "makeNameMap", "buildScrub", "updateScorebug", "updateEndscreen", "fit",
    "draw", "makeRenderer", "stateToView", "paint",
)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# the four files name one module
# ---------------------------------------------------------------------------


def test_all_four_viewer_files_exist():
    for path in (INDEX, STATIC_JS, CONFIG_NIMS, WASM_NIM, RENDERER, CHROME):
        assert path.is_file() and path.stat().st_size > 0, path


def test_the_emscripten_export_name_matches_the_js_factory_call():
    nims = read(CONFIG_NIMS)
    assert "-s MODULARIZE=1" in nims
    assert "-s EXPORT_NAME=CommonsReplayModule" in nims
    # MODULARIZE + EXPORT_NAME is a matched pair with the bootstrap: the shell
    # must call the factory by exactly that name.
    assert "CommonsReplayModule()" in read(STATIC_JS)


def test_the_exported_functions_are_renamed_on_both_sides_together():
    nims = read(CONFIG_NIMS)
    nim = read(WASM_NIM)
    shell = read(STATIC_JS)
    exported_line = next(
        line for line in nims.splitlines() if "EXPORTED_FUNCTIONS" in line
    )
    for name in EXPORTS:
        assert f"_{name}" in exported_line, name
        assert f'exportc: "{name}"' in nim, name
        assert f"_{name}(" in shell, name
    assert "_main,_malloc,_free" in exported_line


def test_no_starter_symbol_survived_the_rename():
    """Provenance may be named in a comment; a SYMBOL may not survive.

    A half-renamed pair (`CommonsReplayModule` in the link flags,
    `_bw_load_replay` in the bootstrap) is exactly the lantern failure, and it
    is invisible to every file-presence check.
    """
    for path in (INDEX, STATIC_JS, CONFIG_NIMS, WASM_NIM, RENDERER, CHROME):
        body = read(path)
        for symbol in ("_bw_", "BullwhipRenderer", "BullwhipReplayModule",
                       "bullwhip_replay"):
            assert symbol not in body, (path.name, symbol)


def test_the_output_module_name_matches_the_script_tag_and_the_build_hook():
    assert 'commons_family_replay.js' in read(CONFIG_NIMS)
    assert './commons_family_replay.js' in read(INDEX)
    hook = read(ROOT / "tools/build_replay_viewer.sh")
    assert "commons_family_replay.wasm" in hook
    assert "commons_family_replay.js" in hook
    assert "replay-viewer/commons_family_replay.nim" in hook


def test_the_wasm_module_imports_only_the_json_stdlib():
    body = read(WASM_NIM)
    imports = re.search(r"^import\n((?:[ \t]+\S.*\n)+)", body, re.M)
    assert imports, "no import block found"
    assert imports.group(1).strip() == "std/json"


def test_the_shell_owns_the_error_attribute_and_the_renderer_owns_the_loaded_one():
    shell = read(STATIC_JS)
    renderer = read(RENDERER)
    assert 'setAttribute("data-replay-error"' in shell
    assert 'removeAttribute("data-replay-error")' in shell
    assert 'setAttribute("data-replay-loaded", "true")' in renderer
    # It is written from inside makeRenderer's ready callback, i.e. after the
    # first real draw — not at the call site, where it would mean "parsed".
    tail = renderer[renderer.index("function attachReplay"):]
    assert tail.index('data-replay-loaded') > tail.index("requestAnimationFrame(frame)")


def test_the_ready_bridge_is_posted_only_after_the_first_painted_frame():
    """`ready` must mean a picture, not a parsed payload.

    `viewer_smoke.mjs` accepts `data-replay-loaded` OR the bridge `ready`,
    whichever arrives first, so CI cannot see the wrong order — but an
    embedding page that samples on `ready` screenshots an unpainted shell
    (chorus 3c11c953, 2026-08-24). The only writer of the attribute is the
    renderer, at the end of its first draw, so the bridge waits on it.
    """
    shell = read(STATIC_JS)
    assert shell.count('tell("ready")') == 1
    assert 'whenDrawn(function () { tell("ready"); });' in shell
    assert 'attributeFilter: ["data-replay-loaded"]' in shell
    assert 'requestAnimationFrame(function () { tell("ready"); })' not in shell


def test_the_fetch_is_bounded_and_retryable():
    shell = read(STATIC_JS)
    assert "AbortController" in shell
    assert "FETCH_TIMEOUT_MS" in shell
    assert 'retry.id = "loading-retry"' in shell


# ---------------------------------------------------------------------------
# the appended block
# ---------------------------------------------------------------------------


def test_the_page_is_the_starters_page_plus_a_marked_block():
    body = read(INDEX)
    assert "commons-family additions to the inherited cogame-bullwhip chrome" in body
    # Zero starter elements removed: every id renderer.js and static_replay.js
    # resolve by id must still be there.
    for element_id in ("layout", "stage", "topband", "wordmark", "clock", "topright",
                       "statuschip", "feedtoggle", "scorebug", "board-wrap", "table",
                       "lightpool", "grain", "endscreen", "transport", "scrub", "play",
                       "pos", "feed", "loading"):
        assert f'id="{element_id}"' in body, element_id
    # The lineage has no zoom bar and no minimap, and this is a fixed arena.
    assert "viewpanel" not in body
    assert "minimap" not in body


def test_the_appended_nodes_are_exactly_modulebar_and_patchgrid():
    body = read(INDEX)
    assert 'id="modulebar"' in body
    assert 'id="patchgrid"' in body
    # Outside #transport and outside #board-wrap.
    transport = body.index('<div id="transport">')
    board = body.index('<div id="board-wrap">')
    assert body.index('id="modulebar"') < board < transport
    assert body.index('id="patchgrid"') < board


def test_the_appended_block_declares_no_colliding_top_level_function():
    body = read(INDEX)
    # Top-level of an inline <script> is two spaces in; anything deeper is a
    # helper inside another function and cannot shadow a global.
    declared = set(re.findall(r"^ {0,2}function\s+([A-Za-z_$][\w$]*)\s*\(", body, re.M))
    assert {"cfModuleBar", "cfPatchGrid"} <= declared
    collisions = declared & set(RENDERER_GLOBALS) - {"fit"}
    assert not collisions, collisions
    # `fit` is the STARTER's own inline function, not ours; ours are the two
    # cf* builders and nothing else.
    assert declared - {"fit", "relayout", "cfModuleBar", "cfPatchGrid"} == set()


def test_relayout_is_the_only_writer_of_the_band_and_hudscale_variables():
    body = read(INDEX)
    assert body.count('setProperty("--band"') == 1
    assert body.count('setProperty("--hudscale"') == 1
    relayout = body[body.index("function relayout()"):body.index("window.addEventListener(\"resize\", relayout)")]
    assert 'setProperty("--band"' in relayout
    assert 'setProperty("--hudscale"' in relayout


def test_no_overlay_sits_in_the_transport_band():
    css = read(CHROME)
    endscreen = css[css.index("#endscreen {"):css.index("#endscreen.show")]
    assert "bottom: var(--band, 0px);" in endscreen


def test_every_seek_dismisses_the_endcard():
    renderer = read(RENDERER)
    seek = renderer[renderer.index("var scrub = buildScrub("):renderer.index("if (options.playButton)")]
    assert 'options.endscreen.classList.remove("show")' in seek
    assert "setIndex(next, true)" in seek


def test_the_say_band_is_reserved_and_sized_from_the_servers_rune_cap():
    """A remark gets a band, not whatever room is left above the cog.

    `drawBubble` used to wrap into a hard TWO lines and ellipsize the rest, at
    a width of `pitch * 1.5` — about 86 px in a 360 px frame, so roughly 25 of
    a 140-rune remark's characters survived and the rest became "…". The band
    is now computed from `chat_max_chars` in the bubble's own font
    (`sayMetrics`), reserved in the layout whether or not anyone is speaking,
    and the bubble wraps to as many lines as the text needs.
    """
    renderer = read(RENDERER)
    assert f"var SAY_CAP_RUNES = {CommonsConfig().chat_max_chars};" in renderer
    assert "function sayMetrics(" in renderer
    # The band is part of the layout, and the board is measured after it.
    layout = renderer[renderer.index("function computeLayout("):
                      renderer.index("function sayMetrics(")]
    assert "sayBand" in layout
    assert "say: {" in layout
    assert "computeLayout(w, h, say.band)" in renderer
    # No line cap for a sentence, and no ellipsis anywhere in the bubble.
    bubble = renderer[renderer.index("function drawBubble("):]
    bubble = bubble[:bubble.index("// ---- Chart")]
    assert "wrapLines(ctx, text, width - pad * 2, 0)" in bubble
    assert "ellipsize" not in bubble
    # A word wider than the box is broken on rune boundaries, not cut.
    wrap = renderer[renderer.index("function wrapLines("):
                    renderer.index("function drawBubble(")]
    assert "Array.from(word)" in wrap


# ---------------------------------------------------------------------------
# the scrubber beats
# ---------------------------------------------------------------------------


def beat_kinds() -> dict[str, str]:
    renderer = read(RENDERER)
    block = renderer[renderer.index("var BEAT_KIND = {"):]
    block = block[:block.index("};")]
    return dict(re.findall(r"(\w+):\s*\"(\w+)\"", block))


def test_every_beat_kind_the_scrubber_emits_has_a_css_rule():
    css = read(CHROME)
    for event_kind, beat in beat_kinds().items():
        assert f".beat-marker.{beat}" in css, (event_kind, beat)


def test_no_beat_kind_is_put_on_the_scrubber_that_the_engine_cannot_emit():
    for event_kind in beat_kinds():
        assert event_kind in EVENT_KINDS, event_kind


def test_scrubber_beats_are_clickable_labelled_buttons():
    renderer = read(RENDERER)
    build = renderer[renderer.index("function buildScrub("):renderer.index("function attachReplay(")]
    assert 'document.createElement("button")' in build
    assert 'marker.type = "button"' in build
    assert 'marker.setAttribute("aria-label", label)' in build
    assert "marker.title = label" in build
    assert "onSeek(i + 1)" in build
    # Spectator English, never internal notation.
    labels = read(RENDERER)
    assert '"Round " + ((event.r || 0) + 1)' in labels
    assert "stripped bare" in labels


# ---------------------------------------------------------------------------
# the payload the wasm module promises the renderer
# ---------------------------------------------------------------------------


def test_the_wasm_module_validates_the_same_vocabulary_the_engine_emits():
    nim = read(WASM_NIM)
    block = nim[nim.index("const EventKinds = ["):]
    block = block[:block.index("]")]
    declared = tuple(re.findall(r'"(\w+)"', block))
    assert declared == EVENT_KINDS


def test_the_wasm_module_requires_every_key_the_replay_writer_emits():
    nim = read(WASM_NIM)
    block = nim[nim.index("const RequiredKeys = ["):]
    block = block[:block.index("]")]
    required = set(re.findall(r'"(\w+)"', block))

    config = CommonsConfig(module="cleanup", rounds=2)
    state = headless.run_episode(config, headless.build_policies(["steward"] * 6))
    from coworld.examples.commons_family.game.engine import replay_payload  # noqa: PLC0415
    from coworld.examples.commons_family.game.engine import results  # noqa: PLC0415

    module = module_for(config)
    payload = results(state, config, module, "complete", ["p"] * 6, 0)
    replay = replay_payload(
        state, config, module, ["p"] * 6, payload, ["scripted"] * 6, [""] * 6
    )
    assert required <= set(replay)


def test_the_payload_keys_the_renderer_reads_are_the_ones_the_wasm_emits():
    nim = read(WASM_NIM)
    renderer = read(RENDERER)
    # The top-level payload contract. `config` rides along for the record and
    # for the endcard; the renderer reads the other five.
    assert '"config"' in nim
    for key in ("names", "policyNames", "events", "results", "states"):
        assert f'"{key}"' in nim, key
        assert f"payload.{key}" in renderer or f'payload["{key}"]' in renderer, key
    # One state per event, and the fields the board and the scorebug read.
    for key in ("seats", "resource", "series", "flow", "phase", "module", "rounds"):
        assert f'"{key}"' in nim, key
    for field in ("score", "gain", "extracted", "public_effort", "favorite", "frozen",
                  "patches", "pending", "say", "alias"):
        assert f'"{field}"' in nim, field
        assert f"seat.{field}" in renderer or f'seat["{field}"]' in renderer, field


def test_the_wasm_module_reads_the_recorded_effort_instead_of_recomputing_it():
    """One source of truth for `public_effort`: the engine.

    The expander used to switch on the module name and re-derive each seat's
    maintenance effort from its recorded decision (`clean`, `plant`,
    `effort_budget - harvest`, a non-red `eat`) — a second implementation of
    `Module.public_effort` in Nim. It now reads `seat_public_effort`, which the
    engine books in step 8 and the round record carries.
    """
    nim = read(WASM_NIM)
    assert '"seat_public_effort"' in nim
    for recomputation in ('decision{"clean"}', 'decision{"plant"}',
                          'decision{"harvest"}', 'decision{"eat_color"}'):
        assert recomputation not in nim, recomputation


@pytest.mark.parametrize("module_name", ["cleanup", "harvest", "allelopathic", "mushrooms"])
def test_the_module_bar_reads_keys_the_module_actually_publishes(module_name):
    """`cfModuleBar` in index.html reads resource fields; they must exist."""
    config = CommonsConfig(module=module_name, num_agents=6)
    module = module_for(config)
    public = module.public_state(
        module.new_state(config, {"aliases": [], "favorites": ["red"] * 6,
                                  "patch_deal": list(range(config.patch_count))}),
        config,
        [f"Cog-{i}" for i in range(6)],
    )
    expected = {
        "cleanup": ["apples", "pollution", "collapse_threshold", "dead"],
        "harvest": ["patches", "property_rights"],
        "allelopathic": ["planted", "ripe"],
        "mushrooms": ["counts"],
    }[module_name]
    for key in expected:
        assert key in public, key
    body = read(INDEX)
    for key in expected:
        assert f"res.{key}" in body or f'"{key}"' in body or f"(res.{key}" in body, key


def test_the_worst_case_text_fixture_is_shipped_and_driven_by_ci():
    """The gate that covers model-authored text, and CI's own use of it.

    Every replay CI can produce is text-free in the sense that matters: the
    smoke episode runs with no ANTHROPIC_API_KEY, so every seat plays a
    scripted baseline and the longest remark in it is 40 runes of a fixed
    string. The fixture is the only thing that draws a full-cap remark, and a
    fixture nothing runs is not a gate.
    """
    fixture = ROOT / "tools/ci/text_fixture/index.html"
    body = read(fixture)
    assert "./renderer.js" in body
    assert "CommonsRenderer.attachReplay" in body
    assert 'setAttribute("data-replay-loaded", "true")' in body
    assert 'setAttribute("data-replay-error"' in body
    # It asserts its own strings are full length: one quietly shortened remark
    # would leave it passing while testing nothing.
    assert "var CAP = 140;" in body
    assert CommonsConfig().chat_max_chars == 140
    assert "the fixture shortened its own string" in body
    assert "was not drawn in full" in body
    assert body.count("[360, 640]") == 1        # the embedded featured-match width

    workflow = read(ROOT / ".github/workflows/ci.yml")
    step = workflow[workflow.index("Render the worst-case text fixture"):]
    step = step[:step.index("Upload the worst-case text evidence")]
    assert "tools/ci/text_fixture/index.html" in step
    assert "client/renderer.js" in step
    assert "--strict-text-bounds" in step
    assert "continue-on-error" not in step


def test_the_bundle_carries_the_renderer_the_css_and_the_board_art():
    hook = read(ROOT / "tools/build_replay_viewer.sh")
    for asset in ("renderer.js", "chrome.css", "index.html", "static_replay.js"):
        assert asset in hook, asset
    for sprite in ("cog_red.png", "cog_violet.png", "apple.png", "mushroom_blue.png",
                   "arena_floor.png", "font.ttf"):
        assert sprite in hook, sprite
        assert (ROOT / "data" / sprite).is_file(), sprite


def test_the_scorebug_plate_keeps_its_embedded_width_rules():
    css = read(CHROME)
    plate = css[css.index(".plate-name {"):css.index(".plate.dead")]
    assert "min-width: 3.2em;" in plate
    assert "flex: 1 1 auto;" in plate
    # Secondary labels go under 640px; the alias and the score never do.
    narrow = css[css.index("@media (max-width: 640px)"):]
    assert ".plate-label { display: none; }" in narrow
    assert ".plate-badge { display: none; }" in narrow
    assert ".plate-name" not in narrow.split("@media (max-width: 420px)")[0]
