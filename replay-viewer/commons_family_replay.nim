## Commons Family static replay viewer, wasm side.
##
## JS hands the raw replay bytes to cf_load_replay; this module parses them,
## validates the required keys and the event vocabulary, and EXPANDS the round
## records into one renderer state per event, so `scrub.update(index)`
## addresses `events[i]` and `states[i]` together.
##
## It deliberately does NOT re-run the physics. The game is Python and the four
## resource modules live there in exactly one place; a Nim reimplementation
## would be a second source of truth for the rules. It does not need one: the
## replay records each round's fully settled `state_before`, `state_after`,
## `gains` and `scores`, so every frame is RECORDED, not derived.

import
  std/json

var
  payload: string
  lastError: string

const RequiredKeys = [
  "format", "protocol", "config", "seed", "names", "policyNames",
  "seats", "rounds", "events", "results"
]

const EventKinds = [
  "episode_start", "round_open", "chat", "decision", "resolve", "sanction",
  "void", "trespass", "unheld", "patch_dead", "collapse", "barren",
  "digesting", "fallback", "no_submission", "deadline", "round_end",
  "episode_end"
]

proc bytesFromPointer(data: ptr uint8, length: int): string =
  result = newString(length)
  if length > 0:
    copyMem(result[0].addr, data, length)

proc floatsToJson(values: seq[float]): JsonNode =
  result = newJArray()
  for value in values:
    result.add(%value)

proc orEmptyArray(node: JsonNode): JsonNode =
  ## A missing optional list must not put a nil node into the payload.
  if node.isNil or node.kind != JArray:
    newJArray()
  else:
    node

proc orEmptyObject(node: JsonNode): JsonNode =
  if node.isNil or node.kind != JObject:
    newJObject()
  else:
    node

proc moduleNameOf(replay: JsonNode): string =
  replay{"module"}.getStr("cleanup")

proc buildStates(replay: JsonNode): JsonNode =
  let events = replay["events"]
  let rounds = replay["rounds"]
  let seats = replay["seats"]
  let config = replay["config"]
  let results = replay["results"]
  let seatCount = seats.len
  let moduleName = moduleNameOf(replay)
  let totalRounds = config{"rounds"}.getInt(0)
  let reason = results{"reason"}.getStr("")

  var
    score = newSeq[float](seatCount)
    gain = newSeq[float](seatCount)
    extracted = newSeq[float](seatCount)
    effort = newSeq[float](seatCount)
    frozen = newSeq[bool](seatCount)
    pending = newSeq[bool](seatCount)
    say = newSeq[string](seatCount)
    seriesTotal: seq[float] = @[]
    seriesMaint: seq[float] = @[]
    resource: JsonNode = newJObject()
    flow: JsonNode = newJArray()
    phase = "open"
    done = false
    endReason = ""
    current = 0

  if rounds.len > 0:
    resource = rounds[0]{"state_before"}
  if resource.isNil:
    resource = newJObject()

  result = newJArray()
  for event in events:
    let kind = event["kind"].getStr()
    let r = event{"r"}.getInt(0)
    case kind
    of "round_open":
      current = r
      phase = "open"
      flow = newJArray()
      for slot in 0 ..< seatCount:
        pending[slot] = true
        gain[slot] = 0.0
        say[slot] = ""
      if event.hasKey("state"):
        resource = event["state"]
    of "chat":
      let slot = event{"slot"}.getInt(-1)
      if slot >= 0 and slot < seatCount:
        say[slot] = event{"message"}.getStr("")
    of "decision":
      let slot = event{"slot"}.getInt(-1)
      if slot >= 0 and slot < seatCount:
        pending[slot] = false
        if r < rounds.len:
          gain[slot] = rounds[r]["gains"][slot].getFloat()
    of "resolve":
      phase = "resolve"
      if event.hasKey("flow"):
        flow = event["flow"]
      if r < rounds.len:
        for slot in 0 ..< seatCount:
          gain[slot] = rounds[r]["gains"][slot].getFloat()
    of "round_end":
      phase = "settled"
      if event.hasKey("state"):
        resource = event["state"]
      if r < rounds.len:
        let record = rounds[r]
        for slot in 0 ..< seatCount:
          score[slot] = record["scores"][slot].getFloat()
          extracted[slot] = extracted[slot] + record["extracted"][slot].getFloat()
          let frozenList = record{"seat_frozen"}
          if not frozenList.isNil and slot < frozenList.len:
            frozen[slot] = frozenList[slot].getInt(0) > r
          let decisions = record{"decisions"}
          if not decisions.isNil and slot < decisions.len:
            let decision = decisions[slot]
            var spent = 0.0
            case moduleName
            of "cleanup": spent = decision{"clean"}.getFloat(0.0)
            of "allelopathic": spent = decision{"plant"}.getFloat(0.0)
            of "mushrooms":
              if decision{"eat_color"}.getStr("red") != "red":
                spent = decision{"eat"}.getFloat(0.0)
            else:
              spent = config{"effort_budget"}.getFloat(3.0) -
                decision{"harvest"}.getFloat(0.0)
            effort[slot] = effort[slot] + spent
        let series = record{"series"}
        if not series.isNil:
          seriesTotal.add(series{"total"}.getFloat(0.0))
          seriesMaint.add(series{"maintenance"}.getFloat(0.0))
    of "episode_end":
      done = true
      phase = "final"
      endReason = event{"reason"}.getStr(reason)
    else:
      discard

    var seatsOut = newJArray()
    for slot in 0 ..< seatCount:
      let seat = seats[slot]
      seatsOut.add(%*{
        "slot": slot,
        "alias": seat{"alias"}.getStr(""),
        "name": seat{"name"}.getStr(""),
        "score": score[slot],
        "gain": gain[slot],
        "extracted": extracted[slot],
        "public_effort": effort[slot],
        "favorite": seat{"favorite"}.getStr(""),
        "frozen": frozen[slot],
        "patches": orEmptyArray(seat{"patches"}),
        "disconnected": seat{"disconnected"}.getBool(false),
        "pending": pending[slot],
        "say": say[slot]
      })

    var resourceOut = copy(resource)
    if resourceOut.kind != JObject:
      resourceOut = newJObject()
    resourceOut["kind"] = %moduleName

    result.add(%*{
      "r": current,
      "rounds": totalRounds,
      "module": moduleName,
      "phase": phase,
      "done": done,
      "reason": endReason,
      "seats": seatsOut,
      "resource": resourceOut,
      "series": {
        "total": floatsToJson(seriesTotal),
        "maintenance": floatsToJson(seriesMaint)
      },
      "flow": flow
    })

proc cfLoadReplay(data: ptr uint8, length: cint): cint
    {.exportc: "cf_load_replay", cdecl.} =
  try:
    lastError = ""
    payload = ""
    let replay = parseJson(bytesFromPointer(data, int(length)))
    if replay.kind != JObject:
      raise newException(ValueError, "replay is not a JSON object")
    for key in RequiredKeys:
      if not replay.hasKey(key):
        raise newException(ValueError, "replay is missing the key " & key)
    for event in replay["events"]:
      if not event.hasKey("kind"):
        raise newException(ValueError, "an event has no kind")
      if not event.hasKey("r"):
        raise newException(ValueError, "event " & event["kind"].getStr() &
          " has no round")
      if event["kind"].getStr() notin EventKinds:
        raise newException(ValueError, "unknown event kind " &
          event["kind"].getStr())
    payload = $ %*{
      "type": "replay",
      "protocol": replay{"protocol"}.getStr("commons-family.replay.v1"),
      "module": moduleNameOf(replay),
      "names": orEmptyArray(replay["names"]),
      "policyNames": orEmptyArray(replay["policyNames"]),
      "seats": orEmptyArray(replay["seats"]),
      "config": orEmptyObject(replay["config"]),
      "events": orEmptyArray(replay["events"]),
      "results": orEmptyObject(replay["results"]),
      "states": buildStates(replay)
    }
    return 1
  except CatchableError as error:
    lastError = error.msg
    return 0

proc cfPayloadPointer(): ptr uint8 {.exportc: "cf_payload_ptr", cdecl.} =
  if payload.len == 0:
    nil
  else:
    cast[ptr uint8](payload[0].addr)

proc cfPayloadLength(): cint {.exportc: "cf_payload_len", cdecl.} =
  cint(payload.len)

proc cfErrorPointer(): ptr uint8 {.exportc: "cf_error_ptr", cdecl.} =
  if lastError.len == 0:
    nil
  else:
    cast[ptr uint8](lastError[0].addr)

proc cfErrorLength(): cint {.exportc: "cf_error_len", cdecl.} =
  cint(lastError.len)

when defined(emscripten):
  proc emscriptenExitWithLiveRuntime() {.
    importc: "emscripten_exit_with_live_runtime", cdecl.}

when isMainModule and defined(emscripten):
  ## Nim's generated main would run module-global destructors on return,
  ## freeing `payload` and friends while JS keeps calling into the module.
  ## Exiting with a live runtime skips the destructor epilogue so globals
  ## stay valid for the life of the page.
  emscriptenExitWithLiveRuntime()
