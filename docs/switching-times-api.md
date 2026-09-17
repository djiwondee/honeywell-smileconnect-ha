# Switching Times (Schedules) — API Reference

Consolidated technical reference for `/api/room/switchingtimes/get2` and
`/api/room/switchingtimes/set2`, based on live investigation against a real
SCN-10 gateway (2026-09-09). Intended for a developer implementing or
extending schedule read/write functionality — see "Implementation
guidance" at the end for what this implies concretely.

For the narrative of how these findings were derived (mitmproxy capture,
failed attempts, etc.), see `CLAUDE.md`'s "Still untested / open" section
and `docs/mitmproxy-setup.md`. This document only states the confirmed
result, not the investigation story.

## Endpoints

| Endpoint | Method | Purpose |
|---|---|---|
| `/api/room/switchingtimes/get2` | POST | Read a room's full weekly schedule |
| `/api/room/switchingtimes/set2` | POST | Write a room's full weekly schedule |

Both use the same authenticated/signed request mechanism as every other
endpoint in this project (`api_request.py`) — nothing endpoint-specific
there.

## Reading a schedule: `get2`

### Request

```
roomid   = <room id>
roomname = <room name>
```

### Response

```json
{
  "success": true,
  "message": "",
  "loginRejected": false,
  "switchingtimes": [
    {"from": "04:30", "to": "07:30", "type": "H"},
    null,
    null,
    {"from": "04:30", "to": "07:30", "type": "H"},
    ...
  ],
  "language": "en",
  "performance": 0.093
}
```

`switchingtimes` is a **flat array**, one entry per slot, either `null`
(unused slot) or an object with `from`/`to` (zero-padded `"HH:MM"`, 24h)
and `type` (single-letter code, see "Type codes" below).

### Indexing scheme

The array is **day-major**: `index = slots_per_day * weekday + slot`,
where `weekday` is `0` = Monday .. `6` = Sunday, and `slot` is `0`-based
within the day.

`slots_per_day` was empirically observed as `3` on this hardware (giving a
21-element array), but **this is not declared anywhere in the protocol** —
no field says "3 slots per day". Do not hardcode `3` or `21`; derive
`slots_per_day` from `len(switchingtimes) // 7` at read time, so the code
adapts automatically if another gateway/firmware exposes a different
count.

### Type codes

| Code | Meaning |
|---|---|
| `H` | Comfort Hi |
| `L` | Comfort Lo |

Only these two have been observed. A third category ("Night" in the Smile
App's own vocabulary) was investigated: it could not be assigned an
explicit type through the app UI, and the working hypothesis is that
"Night" is not a slot type at all but the implicit state that applies
outside any defined slot — i.e. it has no wire-level code and is not
represented in `switchingtimes` as a `type` value. This has not been
independently confirmed against a third letter code, since the app gives
no way to attempt one; treat it as a reasonably strong but not airtight
inference.

## Writing a schedule: `set2`

### Request

```
roomid   = <room id>
roomname = <room name>
from     = <comma-joined string, one value per slot>
to       = <comma-joined string, one value per slot>
type     = <comma-joined string, one value per slot>
```

`from`/`to`/`type` each contain exactly `len(switchingtimes)` comma-
separated values, in the same day-major order as `get2`'s response. An
unused slot is an **empty string** at that position (not omitted — the
position itself must exist, so all three CSV strings always have the same
number of comma-separated fields as the schedule length).

Example (21-slot schedule, only slot 1 of each day populated):
```
from = 4:30,,,4:30,,,4:30,,,4:30,,,4:30,,,6:00,,,6:00,,
to   = 7:30,,,7:30,,,7:30,,,7:30,,,7:30,,,9:00,,,9:00,,
type = H,,,H,,,H,,,H,,,H,,,H,,,H,,
```

### Critical wire-format details

**1. The field name is `from`, not `from_`.**
`from` is a reserved word in Python, so it cannot be assigned as an
attribute via dot notation (`params.from = x` is a `SyntaxError`). The
original implementation worked around this by naming the Python attribute
`from_` — but that attribute name went straight onto the wire unchanged,
so the gateway received a field called `from_`, not `from`, and rejected
every call with `success:false, "The input format is invalid: 1"`.

Fix: use `setattr(params, "from", value)` instead of dot notation.
`ApiRequest.request()` builds the request from `vars(data_object)`, which
picks up `setattr`-assigned keys identically to normal attributes — no
change needed in `api_request.py` itself, the fix is entirely local to
however `set2`'s params object is built.

**2. Hours must be sent WITHOUT a leading zero.**
`get2` returns times zero-padded (`"04:30"`, `"06:00"`). The real Smile
App's own `set2` request (captured via mitmproxy, `User-Agent:
RestSharp/106.6.9.0`) sends them **without** the leading zero on the hour
(`"4:30"`, `"6:00"`). Two-digit hours are unaffected (`"14:50"` either
way). Convert on write; the gateway continues to return zero-padded values
on subsequent `get2` reads regardless of how they were written.

**3. Slots within a day must be filled contiguously from slot 1 upward.**
The gateway does **not** reject a gap (e.g. writing slot 3 while slot 2 is
still empty for that day) with an error. Instead it silently:
- shifts the sent value to the first free slot in that day, and
- **drops the sent `type`**, replacing it with the day's existing first
  slot's `type` instead.

This is a silent data-corrupting write (`success:true`, wrong actual
result), the same failure class as several other bugs found earlier in
this project's `api_request.py` work (booleans, empty arrays). There is no
server-side validation to rely on — this rule must be enforced
client-side before a request is ever sent.

**4. Every write is a full-schedule replacement.** There is no partial-
update endpoint. To change one slot: read the full schedule via `get2`,
modify the one entry in the resulting Python structure, and send the
**entire** modified list back via `set2` — every other slot must be
included unchanged, or it will be cleared.

### Response

```json
{
  "success": true,
  "message": "",
  "loginRejected": false,
  "language": "en",
  "performance": 0.078
}
```

No confirmation of what was actually written — the only way to verify a
write took effect as intended is to call `get2` again afterward and
compare. This was true even for the contiguous-slot violation above:
`set2` itself returned `success:true` in that case too.

### Operations confirmed to work correctly (when the rules above are followed)

- **Editing** an existing slot's `from`, `to`, or `type` individually.
- **Creating** a new slot in a currently-empty position, provided it is
  the next contiguous slot for that day.
- **Deleting** a slot (setting its `from`/`to`/`type` back to empty
  strings), returning that position to `null` on the next `get2`.

All three were verified via `get2` after the write, not just via `set2`'s
`success:true` — see `scripts/manual_probe_switchingtimes_slotops_v2.py`.

## Reference implementation

The confirmed-correct implementation lives in
`custom_components/honeywell_smileconnect/api/api_methods.py`
(`get_switching_times()` / `set_switching_times()` /
`_strip_leading_zero_hour()` / `_validate_switching_times()`) — see that
file's own change-log header for the fix history. In summary, the write
path:

1. Accepts a `switchingtimes` list in the same shape `get_switching_times()`
   returns (list of `{"from","to","type"}` dicts or `None`).
2. Validates the contiguous-slot rule client-side, raising `ValueError`
   before building any request if violated.
3. Converts to three comma-joined CSV strings, stripping leading zeros
   from hours.
4. Builds the request with `setattr(params, "from", ...)` for the
   reserved-word field, plain attributes for `to`/`type`.

## Testing / verification pattern

Diagnostic scripts under `scripts/manual_probe_switchingtimes*.py`
established and then locked in the above findings, in this order:

1. **Echo test** — read the current schedule, write it back completely
   unchanged, then read again and diff. Validates the wire format
   round-trips without side effects, with no risk to the real schedule.
2. **Deliberate single-field change** — change exactly one value, verify
   via `get2` that only that value changed (not just that `set2` returned
   `success:true`), then revert.
3. **Slot create/edit/delete** — the same pattern applied to slots
   transitioning between `null` and populated, which behave differently
   from editing an already-populated slot (see contiguous-slot rule
   above).

The final versions of these scripts (`*_echo_v3.py`, `*_slotops_v2.py`)
call `ApiMethods.set_switching_times()` directly rather than building a
request by hand, so they double as regression tests against the actual
integration code path, not just the protocol format in isolation.

## Implementation guidance

For a developer building on top of this (e.g. the planned HA Action/
service — see `CLAUDE.md`'s "Next planned work"):

- **Never construct a `set2` request by hand outside `api_methods.py`.**
  Always go through `ApiMethods.set_switching_times()`, so the
  wire-format quirks (field name, zero-padding, contiguous-slot
  validation) stay in exactly one place.
- **A "set one slot" convenience helper, if built, must read-modify-write
  internally** — there is no shortcut around the full-schedule-replacement
  constraint.
- **Any user-facing or service-call interface should validate before
  calling `set_switching_times()`**, not rely on the gateway to catch
  mistakes — the gateway's own error handling for this endpoint has
  already been shown to fail silently at least once (the contiguous-slot
  case). `_validate_switching_times()` already does this at the
  `api_methods.py` layer; a higher-level UI/service should surface that
  `ValueError` clearly rather than let it propagate as an opaque
  exception.
- **Don't assume `slots_per_day == 3` anywhere new.** Derive it from the
  schedule length, consistent with how `api_methods.py` already does it,
  in case a future installation or firmware version differs.
- **A service-call parameter shape should probably not be the raw
  21-element list directly** — that is an internal wire format, not a
  natural interface for automations. A day/slot/time/type structure (or a
  simpler "set this room's slot N on day D" convenience call that does the
  read-modify-write internally) is likely more ergonomic; this still needs
  a design decision before implementation, per the project's Session
  Workflow rules.
