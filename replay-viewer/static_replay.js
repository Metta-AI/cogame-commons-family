// Commons Family static replay shell: fetches the replay named by ?replay=<url>,
// hands the bytes to the wasm module (which validates the replay and
// expands its round records into one renderer state per event), then drives the shared
// renderer with the resulting payload.
//
// The shell is the only thing on screen until the replay is in, so it has to
// be honest about waiting: the caption names what it is doing, a stalled
// fetch gives up after FETCH_TIMEOUT_MS instead of sitting on "LOADING"
// forever, and every failure offers a Retry that refetches without a page
// reload (the wasm module, once compiled, is reused).
(function () {
  "use strict";

  var FETCH_TIMEOUT_MS = 20000;

  // VIEWER -> HOST READINESS. An embedding page (the softmax.com theater, the
  // Observatory episode page) can only see this document's `load` event,
  // which fires long before the wasm module has compiled and the replay has
  // come back from S3. So the shell tells the parent what it is doing:
  // `loading` as soon as this script runs (before `load`, so the host never
  // mistakes document-load for a picture), `ready` once the renderer has
  // drawn its first frame, `error` when the replay cannot be shown. Same
  // envelope shape as the ctf-shell Escape bridge ({src, type}); no secrets
  // ride on it, so the target origin is "*".
  function tell(type, message) {
    if (window.parent === window) return;
    var envelope = { src: "coworld-replay", type: type };
    if (message) envelope.message = message;
    try { window.parent.postMessage(envelope, "*"); } catch (ignore) {}
  }
  tell("loading");
  var modulePromise = null;
  var attempt = 0;

  function caption(text) {
    var loading = document.getElementById("loading");
    if (!loading) return;
    loading.style.display = "";
    loading.textContent = text;
    var retry = document.getElementById("loading-retry");
    if (retry) retry.remove();
  }

  function fail(message) {
    var loading = document.getElementById("loading");
    if (loading) {
      loading.style.display = "";
      loading.textContent = "Replay failed: " + message + " ";
      var retry = document.createElement("button");
      retry.id = "loading-retry";
      retry.type = "button";
      retry.textContent = "Retry";
      retry.onclick = function () { load(); };
      loading.appendChild(retry);
    }
    document.documentElement.setAttribute("data-replay-error", message);
    tell("error", message);
  }

  function readString(module, ptr, len) {
    if (!ptr || !len) return "";
    return new TextDecoder().decode(
      module.HEAPU8.subarray(ptr, ptr + len)
    );
  }

  function fetchReplay(url) {
    // AbortController bounds the wait; a fetch that never answers (a dead
    // CDN edge, a proxy holding the socket) is otherwise indistinguishable
    // from a slow one, and the caption would say LOADING until the tab died.
    var controller = typeof AbortController === "function" ?
      new AbortController() : null;
    var timer = window.setTimeout(function () {
      if (controller) controller.abort();
    }, FETCH_TIMEOUT_MS);
    return fetch(url, controller ? { signal: controller.signal } : {})
      .then(function (response) {
        if (!response.ok) throw new Error("replay fetch " + response.status);
        return response.arrayBuffer();
      })
      .catch(function (error) {
        if (error && error.name === "AbortError") {
          throw new Error("replay fetch timed out after " +
            Math.round(FETCH_TIMEOUT_MS / 1000) + "s");
        }
        throw error;
      })
      .finally(function () { window.clearTimeout(timer); });
  }

  function start(module, bytes) {
    var ptr = module._malloc(bytes.length);
    module.HEAPU8.set(bytes, ptr);
    var ok = module._cf_load_replay(ptr, bytes.length);
    module._free(ptr);
    if (!ok) {
      fail(readString(module, module._cf_error_ptr(),
        module._cf_error_len()) || "wasm rejected the replay");
      return;
    }
    var payload = JSON.parse(
      readString(module, module._cf_payload_ptr(),
        module._cf_payload_len())
    );
    var loading = document.getElementById("loading");
    if (loading) loading.style.display = "none";
    document.documentElement.removeAttribute("data-replay-error");
    CommonsRenderer.attachReplay({
      canvas: document.getElementById("table"),
      feed: document.getElementById("feed"),
      scrub: document.getElementById("scrub"),
      playButton: document.getElementById("play"),
      label: document.getElementById("pos"),
      clock: document.getElementById("clock"),
      scorebug: document.getElementById("scorebug"),
      endscreen: document.getElementById("endscreen"),
      assetBase: "./assets",
      payload: payload
    });
    // `ready` must mean a PICTURE, so it is posted from a callback that fires
    // after the renderer has set data-replay-loaded="true" — which it does at
    // the end of its own ready callback, i.e. after the first real draw.
    // Posting on rAF timing at this call site instead can beat that first
    // paint, and an embedding page that samples on `ready` then screenshots an
    // unpainted shell; viewer_smoke.mjs cannot catch it because it accepts
    // either signal, whichever arrives first (chorus 3c11c953, 2026-08-24).
    whenDrawn(function () { tell("ready"); });
  }

  // Calls `done` once `<html data-replay-loaded="true">` is set, or straight
  // away if the renderer beat us to it.
  function whenDrawn(done) {
    var root = document.documentElement;
    if (root.getAttribute("data-replay-loaded") === "true") {
      done();
      return;
    }
    var observer = new MutationObserver(function () {
      if (root.getAttribute("data-replay-loaded") !== "true") return;
      observer.disconnect();
      done();
    });
    observer.observe(root, {
      attributes: true,
      attributeFilter: ["data-replay-loaded"]
    });
  }

  function load() {
    var replayUrl = new URLSearchParams(location.search).get("replay");
    if (!replayUrl) {
      fail("missing required ?replay= URL");
      return;
    }
    attempt += 1;
    document.documentElement.removeAttribute("data-replay-error");
    caption(attempt > 1 ? "RETRYING REPLAY… (attempt " + attempt + ")" :
      "LOADING REPLAY…");
    if (!modulePromise) {
      modulePromise = CommonsReplayModule().catch(function (error) {
        modulePromise = null;   // a failed compile is retried from scratch
        throw error;
      });
    }
    Promise.all([fetchReplay(replayUrl), modulePromise])
      .then(function (results) {
        start(results[1], new Uint8Array(results[0]));
      })
      .catch(function (error) {
        fail(String(error && error.message || error));
      });
  }

  window.addEventListener("load", load);
})();
