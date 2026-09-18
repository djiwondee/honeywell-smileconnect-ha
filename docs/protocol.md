# Honeywell Smile Connect — Protocol Reference

> Reverse-engineered against an SCN-10 gateway (server version 1.6.32687,
> relay SCN-10 V1.6 Rev. 12). This document is the working basis for the
> API layer in `custom_components/honeywell_smileconnect/api/`.

## 1. Login Flow

```
1. POST /api/user/token/challenge   Body: udid=web
   → { devicetoken: "<challenge-token>" }

2. Client computes:
   hashed = hash_auth_token(password, challenge_token)
   # Standard HeatApp: MD5(password + challenge_token)
   # Honeywell: PBKDF2/SHA512, with "stringToCharcodes" pre-processing
   #            -> TODO: exact parameters (iterations, salt, key length)
   #               not yet fully verified.

3. POST /api/user/token/response
   Body: {
     udid: "web",
     login: "<username>",
     token: "<challenge-token>",
     hashed: "<hash from step 2>",
     devicename: "Computer"
   }
   → { userid, devicetoken_encrypted }

4. devicetoken (plaintext) = AES-256-CBC-Decrypt(
     ciphertext = devicetoken_encrypted (Base64),
     key = SHA-256(password),
     iv  = Base64-decode("D3GC5NQEFH13is04KD2tOg==")   # TODO: verify on
                                                          # Honeywell whether
                                                          # this matches the
                                                          # standard HeatApp IV
   )
   Padding: standard HeatApp strips "\x10" padding manually
            (no standard PKCS7 handling in the reference implementation).
```

After a successful login, the client holds:
- `userid`
- `devicetoken` (plaintext, decrypted)
- `udid` = `"web"` (fixed, not generated)

## 2. Request Signing (for every authenticated call)

```
1. Sort all body parameters alphabetically by key.
2. Serialize values to strings:
   - Booleans: lowercase "true"/"false" — NOT Python's capitalized
     "True"/"False" and NOT "1"/"0". Mirrors JavaScript's own string-
     coercion (`"active=" + false` → `"active=false"` in JS). Getting
     this wrong caused a real production bug: `/api/scene/set(active=
     False)` always returned `success:true` while the gateway silently
     never actually changed the scene's active state - see CLAUDE.md for
     the full story.
   - Arrays with 2+ elements: "[a,b,c]" (brackets, comma-separated, no spaces)
   - single-element array: just the value, no brackets
   - EMPTY array: the literal string "undefined" — NOT an empty string.
     Mirrors the gateway's own JS exactly: `g.length<2 ? f+"="+g[0] : ...`
     - for an empty array, `g[0]` is JavaScript's `undefined`, and string-
     concatenation coerces it into the literal text "undefined". Getting
     this wrong (sending a genuinely empty value) caused a real production
     bug: the gateway's firmware hangs/times out (10s ReadTimeout observed)
     rather than returning a clean error when `/api/scene/setrooms`
     receives an empty `rooms=` value - see CLAUDE.md for the full story.
   - scalars (numbers, strings): plain string representation
3. Build the data string: "key1=val1|key2=val2|...|"   (pipe, NOT &, with
   a trailing pipe)
4. Signature = Base64(PBKDF2-HMAC-SHA512(...)) — see §1 above for the full
   scheme; this is NOT plain MD5(data_string + devicetoken), which was an
   early, incorrect assumption carried over from the generic HeatApp
   reference project.
5. Insert additional required fields BEFORE computing the signature:
   udid, userid, reqcount  (reqcount incremented per request)
6. Final request body: all original parameters + udid + userid + reqcount
   + request_signature=<signature>, standard URL-encoded (& as the
   separator in the actual HTTP body — the pipe variant is ONLY used for
   the signature computation).
```

> ⚠️ Difference from the generic HeatApp protocol: standard HeatApp replaces
> `&` with `|` for the signature computation (see `api_request.py` in the
> legacy code: `.replace('&', '|')`), which is structurally equivalent to
> "building with pipes from the start". On Honeywell this was observed as
> pipe-native directly in the admin console code — functionally equivalent,
> but noted here as an implementation detail.

## 3. Known Endpoints

See `CLAUDE.md` for the full list. Additional raw data examples (fixtures)
will live under `tests/fixtures/` once added.

### `/api/scene/status` — example response (generic HeatApp, as a structural
template; the Honeywell response still needs to be captured 1:1)

```json
{
  "success": true,
  "scenes": [
    { "name": "Party", "min": 0, "max": 12, "step": 1, "isActive": false },
    { "name": "Boost", "min": 0, "max": 120, "step": 30, "isActive": false },
    { "name": "Holiday", "min": false, "max": false, "step": false, "isActive": false },
    { "name": "Shower", "min": 0, "max": 1440, "step": 1, "isActive": false },
    { "name": "Leave", "min": 0, "max": 12, "step": 1, "isActive": false },
    { "name": "Standby", "min": 0, "max": 1, "step": 1, "isActive": false },
    { "name": "Towel", "min": false, "max": false, "step": false }
  ]
}
```

> **Cross-check (2026-09-01):** the live duration investigation in §4d below
> independently confirmed this template's `min`/`max` for Party (0-12,
> hours) and Boost (0-120, minutes) exactly, and Leave's `max` (12, hours)
> exactly — this generic-project template turned out to be accurate for
> those three. **Holiday is the one exception:** the template shows
> `min`/`max`/`step` as `false` (implying "unbounded"), but live testing
> found a real, hard bound of 0-30 **days** — the generic template is
> wrong/incomplete for Holiday specifically, consistent with this
> project's general finding that Holiday keeps being the outlier scene
> that needs the most live re-verification (see also §4f).
>
> **Second cross-check (2026-09-09):** Leave's `max` (12h) is confirmed
> correct for the READ side (`get_scene_duration()` reliably reports
> `raw_value × 12 = remaining_hours`). The WRITE side is a different
> story entirely — see the corrected Leave entry in §4d below. Despite
> sharing the identical `min`/`max`/`step` template values with Party,
> Leave's write formula turned out NOT to match Party's.

The test installation has only one room ("Alle"/"All") and no Shower/Towel
usage — these scenes are kept in code as constants but remain untested.

## 4. Room Status Codes (live-verified against a real gateway)

> ⚠️ The table below **replaces** an earlier version that was carried over
> from the generic HeatApp reference project (values in the 40-140 range).
> Those were confirmed **completely wrong** for this Honeywell variant once
> actually tested - the real codes are single/double digits. Verified via
> `scripts/manual_probe_roomstatus_via_app.py`: each mode was set through
> the Smile App itself (not our own code), then `roomstatus` and
> `/api/scene/status` were read passively.

| Code | Scene | Confidence |
|---|---|---|
| 3 | Party | Confirmed |
| 6 | Boost | Confirmed |
| 7 | Holiday | Confirmed (see disambiguation note below) |
| 10 | Leave | Confirmed (see disambiguation note below) |
| 12 | Standby | Confirmed |

**Disambiguation note (Leave vs. Holiday):** this took three attempts to
settle, worth recording so it isn't re-litigated. Run 1 (scenes not reset
between tests, so Standby was still stacked underneath): Leave=10,
Holiday=7. Run 2 (also not reset between tests): the opposite, Leave=7,
Holiday=10. A third, deliberately controlled run — explicitly resetting to
a clean Standby-only baseline before testing EACH of Leave and Holiday
individually, specifically to eliminate the stacking confound — reproduced
Run 1's values (Leave=10, Holiday=7), with only one active scene reported
each time (no stacking). 2-out-of-3 agreement, with the third run's
deviation plausibly explained by the (since-understood) stacking confound,
is the basis for treating **Holiday=7, Leave=10** as the final, confirmed
mapping.

Scene names themselves (`Party`, `Boost`, `Holiday`, `Shower`, `Leave`,
`Standby`, `Towel`), as returned by `/api/scene/status`, matched the
generic reference project exactly and needed no correction — only the
*numeric room-status codes* were wrong, not the scene name strings.

**Naming note:** the Smile App's own UI labels the `Leave` scene as
"Economy". This is a cosmetic, vendor-app-only display label — the actual
API scene name string is still `"Leave"`, which is what this project uses
internally (see `const.SceneName`).

**Overlapping/stacked scenes:** a real gateway can report multiple scenes
simultaneously active. Observed: activating `Boost` or `Party` did NOT
turn off an already-active `Standby` (both showed `isActive: true`
together) — they behave like temporary overrides layered on top of a
baseline. Activating `Leave` or `Holiday`, by contrast, DID make `Standby`
disappear from the active list — these behave like full alternate modes
that replace the baseline rather than layering on top of it. Despite this,
`roomstatus` itself always resolved to a single, priority-appropriate
value in every observation, so the integration's single-preset model
(`climate.py`) did not need to change to accommodate this.

## 4b. Field availability differences vs. generic HeatApp

Confirmed on a live Honeywell gateway: `/api/room/list` room objects do
**not** always include `actualTemperature` - observed missing entirely on a
single-zone installation with no dedicated room sensor ("Regler MK1"/relay
controller). The integration treats it as optional (falls back to unknown)
rather than assuming it is always present, unlike the generic HeatApp
reference code this was originally ported from.

## 4c. What "Standby" actually means (and why hvac_mode ≠ preset_mode)

Clarified by the user, who knows this hardware's real-world behavior
(confirmed against their own regler configuration, not just API
observation):

- **Frost protection is always active at the regler itself** and is
  **not controllable via the gateway/API at all**. There is no "fully off,
  no frost protection" state reachable through this integration, by
  design of the hardware — nor should there be.
- **`Standby` scene ON** = the room's configured schedule
  (Schaltzeiten, set per-room in the Smile App's time profile) is
  **ignored**, and the regler does not heat to any schedule-driven
  setpoint (frost protection floor still applies, per the point above).
- **`Standby` scene OFF** = the regler follows the configured schedule,
  heating to the programmed setpoint at the programmed times.
- **Confirmed (2026-08-30): `/api/room/settemperature` calls are silently
  rejected while `Standby` is active** — matches the real Smile App's own
  behavior (temperature cannot be changed for a room in Standby there
  either). The gateway does not return an error for this; the request
  appears to succeed, but `desiredTemperature` simply does not change.
  Any future manual testing (or an eventual "why didn't my temperature
  change stick" support question) should check `roomstatus` for Standby
  first before suspecting anything else.

This means `Standby` is fundamentally a **mode toggle** (schedule-following
vs. schedule-ignoring), not a "preset" alongside Boost/Party/Leave/Holiday.
The integration reflects this by mapping it to HA's `hvac_mode` concept
instead of `preset_mode`:

| roomstatus = Standby | HA `hvac_mode` |
|---|---|
| active | `OFF` |
| inactive | `AUTO` (schedule-following — there is no `HEAT` mode here, since there's no "hold a fixed manual setpoint, ignore the schedule" concept on this hardware) |

`preset_mode` is therefore driven *exclusively* by Boost/Party/Leave/
Holiday, entirely independent of the Standby-driven `hvac_mode` — a room
can in principle report a Boost/Party/Leave/Holiday preset regardless of
whatever `hvac_mode` currently shows, since they answer different
questions (schedule-following vs. schedule-ignoring, vs. which temporary
scene override is layered on top). `preset_modes` deliberately has no
"none" entry; when no scene is active, `preset_mode` returns Python `None`
rather than a string, which HA renders natively as "no preset selected".

## 4d. Scene `duration` parameter — the value you send is NOT the real
duration (2026-09-01, corrected 2026-09-09, Leave RESOLVED 2026-09-11)

**Root cause of the original production symptom** ("selecting a preset in
the climate entity shows briefly, then reverts on the next poll — the
gateway apparently never actually enabled it"): `scene_manager.py`'s
`add_member_to_scene()` read the CURRENT `duration` via
`ApiMethods.get_scene_duration()` and resent that value unchanged when
activating a scene. For an **inactive** scene this call returns `0` (or,
for Holiday, a tiny near-zero fractional leftover, e.g. `0.013` days ≈ 19
minutes — never anything resembling a real default). `set_scene(active=
True, duration=0)` is then **silently rejected by the gateway** — the
response reports `success: true`, but `scene/status.isActive` for that
scene never actually flips to `true`. Same bug class as the four
`api_request.py` bugs documented in CLAUDE.md (a `success:true` response
that does nothing).

**Second, independent discovery once a real (non-zero) duration was sent:**
the number you send is **not** the real-world duration in the scene's
documented unit for most scenes — three of the four scenes apply their own
multiplicative factor and/or a different wire format, and (as of
2026-09-09) one of them — Leave — could not be fully characterized despite
extensive effort. This was only found by testing multiple distinct,
deliberately small send-values per scene and reading the *actually
configured* duration back — originally from the Smile App/physical regler
display (see the warning below about `get_scene_duration()`'s reliability
at the time), later from `get_scene_duration()` directly once a
`duration=0` bug that had made it look unreliable was fixed (see
`scene_manager.py`'s change log, 2026-09-01).

**A methodological trap worth recording (2026-09-01):** the first two
Holiday and Party data points (`3→30d`, `1.5→30d` for Holiday; `3→12h`,
`1.5→12h` for Party) each showed the *same* output for *different*
inputs — which looks like strong evidence for a specific factor (e.g.
`×10` was the first, wrong, conclusion for Holiday) but is actually the
signature of **both inputs having already saturated a cap**, revealing
nothing about the real factor below it. Only a *third*, deliberately
smaller test value per scene (which landed below the cap) revealed the
true factor. Lesson: two data points that agree do not by themselves
prove linearity — check whether they might both be capped before trusting
a factor derived from them. **This exact trap resurfaced for Leave in the
2026-09-09 investigation below, in a more insidious form:** the original
two Leave data points (`2→6h`, `4→12h`) were NOT capped and DID reproduce
exactly on live re-test — but turned out to still not generalize to a
usable formula once a wider range was tested. Two clean, reproducible data
points are necessary but not sufficient evidence for a linear model.

| Preset | Measure (unit) | Min | Max | Default (real) | Write formula | Status (2026-09-11) |
|---|---|---|---|---|---|---|
| Leave | Hours | 0 | 12 | 6 | `duration` = target_hours / 12 (fraction of scene_max), clamps to `1` above — **identical to Party** | RESOLVED — confirmed live via mitmproxy capture + direct API confirmation, see below |
| Holiday | Days | 0 | 30 | 15 | `duration` = raw days directly, no scaling | Gateway enforces no ceiling (tested to 100d); app's 30d limit is client-side only |
| Party | Hours | 0 | 12 | 6 | `duration` = target_hours / 12 (fraction of scene_max), clamps to `1` above | Confirmed live, multiple isolated tests |
| Boost | Minutes | 0 | 120 | 60 | `duration` = target_minutes / 120 (fraction of scene_max), clamps to `1` above | Confirmed live, multiple isolated tests (real-time countdown) |

Min/Max/Default columns match the vendor-documented values supplied
2026-09-01. **The "Write formula" and "Status" columns superseded the
original 2026-09-01 "Factor"/"send value" columns on 2026-09-09** after
`api_methods.py`'s `set_scene()` gained a proper `target=` parameter and
each scene's real wire behavior was re-derived from first principles via
`get_scene_duration()` (now trustworthy — see below) rather than only via
physical-display observation:

- **Party and Boost:** the 2026-09-01 factors (`×12` for Party, `×120`
  for Boost) are exactly the reciprocal of "send the fraction of
  scene_max directly" (`0.5×12=6h` ⟺ `6h/12=0.5`) — these were correct
  descriptions of the same underlying mechanism all along, just phrased
  as a multiplication instead of a division. Independently re-confirmed
  2026-09-09 via `scripts/manual_probe_scene_duration_all_scenes.py` and
  `scripts/manual_probe_set_scene_regression.py`.
- **Holiday:** the 2026-09-01 factor (`×30`, from `0.5→15d`) is now known
  to be WRONG — `duration` is raw days sent directly, not a fraction of
  30. `0.5→15d` would only be true if the write field were a fraction of
  a 30-day scene_max, but a direct test sending `15` (not `0.5`) was
  echoed back as `~15` by `get_scene_duration()`, and `100` as `~100`,
  with no gateway-side clamping observed at all up to that point. This
  is a genuinely different write mechanism from Party/Boost's fraction
  model, not just a different factor. **`const.
  SCENE_ACTIVATION_DURATION["Holiday"]` in the HA integration layer still
  needs correcting to match this** — not yet done as of the API-layer fix
  in `0.0.21` (see CLAUDE.md's "Next planned work").
- **Leave — RESOLVED 2026-09-11.** The original 2026-09-01 measurement
  (`2→6h`, `4→12h`, clean `×3` line) was **re-tested live on 2026-09-09
  and reproduced EXACTLY**, both together in one script run and again
  individually with an explicit poll-until-confirmed-inactive check
  before each send (ruling out request-timing contamination from a prior
  test). But a wider sweep of send-values (`1, 3, 5, 6, 8`), tested with
  the same rigor, produced a table that fit NO model tried:
  ```
  sent:    1     2     3     4     5     6     8
  implied: 12h   6h    12h   12h   6h    12h   5h
  ```
  This was wrongly concluded to be a genuine, unsolved mystery in the
  fraction/gateway-firmware behavior itself. **The actual root cause,
  found 2026-09-11: every one of those sent values (`1` through `8`) is
  OUTSIDE the `[0,1]` fraction domain the gateway/app actually use for
  this scene.** The real Smile App's own duration slider for Leave never
  produces a raw wire value above `1` — sending a raw integer like `2`
  or `8` directly (as the 2026-09-09 sweep did) is not a valid "2×" or
  "8×" input, it is simply out-of-spec, and appears to hit an
  unspecified/inconsistent gateway firmware code path (hence the
  non-monotonic table above) rather than revealing anything about the
  real formula.

  **Confirmed via two independent pieces of live evidence, gathered in
  one session (2026-09-11):**
  1. A live **mitmproxy capture of the real Smile App** activating Leave
     six times (`scripts/mitm_scene_capture.py` — captures full
     `/api/scene/*` request AND response bodies, not just the known
     `duration` field, specifically to rule out the app sending some
     additional field this project didn't know about). The app almost
     always sent a *not-perfectly-round* fraction for `duration` — e.g.
     `0.06996047`, `0.222758`, `0.6771102` — never a clean `n/12` value
     except at the slider's default (`0.5`) and hard maximum (`1`)
     positions. `get_scene_duration()` read back immediately afterwards
     always returned the value **rounded to the nearest `1/12` step**
     (matching Leave's own `"step": 1` reported live in
     `/api/scene/status`'s `scenes` array): `round(sent × 12) / 12`
     reproduces the readback exactly for all three "noisy" sends (and
     trivially for the two exact ones):
     | Sent by app | Read back (`get_scene_duration()`) | `round(sent×12)/12` |
     |---|---|---|
     | `0.5` | `0.5` | `0.5` ✓ |
     | `0.5` (repeat) | `0.5` | `0.5` ✓ |
     | `0.06996047` | `0.08333333` (=1/12) | `0.08333333` ✓ |
     | `1` | `1` | `1.0` ✓ |
     | `0.222758` | `0.25` (=3/12) | `0.25` ✓ |
     | `0.6771102` | `0.6666667` (=8/12) | `0.6666667` ✓ |

     The "noise" is very likely the duration-picker UI widget reporting
     its exact (slightly-off-grid) drag/touch position rather than the
     display-rounded hour value, with the gateway then quantizing on
     receipt — not a clock- or session-time effect as originally
     speculated.
  2. A **direct confirmation via this project's own
     `ApiMethods.set_scene()`**, sending a clean in-domain fraction the
     app capture hadn't produced exactly (`5/12 ≈ 0.41666667`, i.e. "5
     hours"): `get_scene_duration()` returned exactly
     `0.4166666666666667` (`5.00h`, no rounding needed since it was
     already sent on-grid) — proving Party/Boost's fraction-of-scene_max
     formula works identically for Leave through our own production code
     path, once given valid input.

  **Conclusion: Leave uses the IDENTICAL write formula to Party** (both
  share `scene_max=12h`) — there was never a separate, unsolved Leave
  mechanism, only an artifact of testing outside the valid input domain.
  `api_methods.set_scene()`'s `target=` block for Leave has been removed;
  it is now handled by the same `FRACTION_DURATION_SCENES` code path as
  Party/Boost, with no Leave-specific logic anywhere.
  `const.SCENE_ACTIVATION_DURATION["Leave"]` was corrected from the old,
  never-understood `2` to `0.5` (the correct, now-understood fraction for
  the 6h default — see `const.py`'s change log).

**`get_scene_duration()` (`/api/scene/duration`) was not a usable
verification source while the `duration=0` bug was still present** (fixed
2026-09-01, see `scene_manager.py`'s change log) — at the time of the
original 2026-09-01 investigation it returned near-zero noise while
inactive, and — this was checked explicitly, immediately after
activation, across every scene — it did **not** echo back the just-
configured value either. **This limitation no longer applies as of
2026-09-09** — with a real (non-zero, correctly-triggered) activation,
`get_scene_duration()` has proven completely reliable for Boost, Party,
and Holiday (used directly to re-derive and confirm all three write
formulas above), and reliable on the READ side for Leave too (only
Leave's WRITE side remains unresolved).

**Decimal/fractional `duration` values are handled correctly by the API**
— confirmed via multiple genuinely-fractional sends (`0.5`, `0.75`, `1.5`)
that all produced exactly the values the linear-factor model predicted
for Party/Boost; no evidence of silent rounding or truncation for those
two scenes.

## 4e. Standby persists silently in the background under an active preset
(2026-09-01)

`scripts/manual_probe_roomstatus_compound.py` tested every combination of
Standby ON simultaneously with each of the four presets, set purely via
the Smile App (bypassing our own write path entirely, for a clean signal).
Result:

| Scenario | roomstatus |
|---|---|
| Standby alone | 12 |
| Leave alone / Standby+Leave | 10 (identical either way) |
| Boost alone / Standby+Boost | 6 (identical either way) |
| Party alone / Standby+Party | 3 (identical either way) |
| Holiday alone | 7 |
| **Standby+Holiday** | **12 — reverts to Standby's own code, unlike the other three** |

Two conclusions:

1. **`roomstatus` is confirmed to be a flat, single "currently winning
   state" code, not a bitfield.** This was a live hypothesis worth testing
   (raised by the user, given `roomstatus`'s known codes don't decompose
   into a clean single-bit-per-mode pattern) but the compound-state data
   rules it out cleanly: no combined/OR'd value is ever observed, and
   Standby+Leave/Boost/Party report *exactly* the same code as the preset
   alone.
2. **Standby's own `isActive` flag stays `True` in the background, for all
   four presets, even when `roomstatus` reports only the preset's code.**
   `roomstatus` alone cannot tell you whether Standby is *also* still
   active underneath a displayed preset.

**Consequence, confirmed via `scripts/manual_check_standby_reassertion.py`
using the REAL production `SceneManager.remove_member_from_scene()` call
(exactly what `climate.py`'s `async_set_preset_mode()` invokes when a
preset is switched away from or cleared):** if Standby was active in the
background while a preset was active, removing that preset causes
`roomstatus` to **immediately** (no staleness, no nudge needed — unlike
the unrelated "leaving Standby to nothing" staleness bug) fall back to `12`
(Standby), because nothing in the removal path ever touches Standby. This
is arguably *correct* given the gateway's real internal state (Standby
genuinely never got turned off) — but it means `climate.py`'s `hvac_mode`
property, which infers `OFF`/`AUTO` purely from whether `roomstatus ==
ROOM_STATUS_STANDBY`, can display a misleading `AUTO` for as long as a
preset masks a still-active Standby, then flip to `OFF` the moment that
preset is cleared, without the user ever touching `hvac_mode` themselves.
**Not yet fixed** — see CLAUDE.md's "Still untested / open" for the
proposed design directions (this is an architecture question, not a
one-line patch, since it likely requires the coordinator to also poll
`/api/scene/status` so `hvac_mode` can read Standby's real state directly
instead of inferring it from `roomstatus`).

## 4f. Holiday+Standby simultaneously active — confirmed gateway firmware
quirk, not a request-encoding bug (2026-09-01)

Per §4e's table, `Standby+Holiday` is the **only** compound state where
`roomstatus` fails to reflect the preset. This was first found via this
project's own write path (`manual_check_preset_nudge.py`, with a correctly
non-zero duration and `scene/status.isActive(Holiday)` confirmed `true`
throughout) and then **independently reproduced via the Smile App itself**
(`manual_probe_roomstatus_compound.py`, zero write calls from our side).
Reproducing the same anomaly through two completely different write paths
rules out a bug in this project's request construction — it is a genuine
Honeywell gateway firmware quirk specific to the Holiday+Standby
combination.

**Confirmed workaround:** explicitly deactivate Standby *before* activating
Holiday. Tested live (`manual_check_preset_nudge.py`) — with Standby
deactivated first, `roomstatus` reached Holiday's code (`7`) **immediately**
(0.0s), no staleness, no nudge needed. Not yet implemented in production
code (`scene_manager.py`/`climate.py`) — see CLAUDE.md.

## 4g. Writing `desiredTempDay`/`desiredTempDay2`/`desiredTempNight` — same
endpoint as the live setpoint, different `change_mode` (confirmed 2026-09-18)

Confirms the hypothesis raised in CLAUDE.md's "TOP PRIORITY" entry: no
separate endpoint is needed. `/api/room/settemperature` — the exact same
endpoint `ApiMethods.set_temperature()` already uses with `change_mode=0`
for the live setpoint — also accepts `change_mode=1`/`2`/`3` to write the
three fixed per-slot-type temperatures.

**Method:** live mitmproxy capture of the real Smile App
(`scripts/mitm_desired_temp_capture.py`, scoped to ALL `/api/` traffic
since the target endpoint was unknown up front — the app sets these values
in its weekly-schedule screen, which also drives the unrelated
`switchingtimes/set2` endpoint, so a narrower `/api/room/` scope could
plausibly have missed a genuinely different endpoint). The user set H
("Comfort Hi"), L ("Comfort Lo"), and N ("Night") for one room, several
times each, while the capture ran.

**Confirmed `change_mode` mapping**, cross-referenced against the
`/api/room/list` response taken immediately after each write (not just
trusting `success:true` — see this project's long-standing verification
principle, e.g. §4d/§4e below and the many `api_methods.py` incidents in
CLAUDE.md):

| `change_mode` | Field | 
|---|---|
| `0` | `desiredTemperature` (live setpoint — already known, unchanged by this finding) |
| `1` | `desiredTempNight` |
| `2` | `desiredTempDay` |
| `3` | `desiredTempDay2` |

**Confirmed rounding: the gateway floors the sent value to the nearest
lower 0.5 °C step** — `stored = floor(sent / 0.5) * 0.5`. Evidence (each
row is one write, immediately followed by a `room/list` read; the German-
locale app sends a comma decimal separator, which the gateway parses
correctly — not truncated at the comma):

| sent (`temperature=`) | `change_mode` | stored value read back | `floor(sent/0.5)*0.5` |
|---|---|---|---|
| `23,94957` | `2` (Day) | `23.5` | `23.5` ✓ |
| `15,32586` | `3` (Day2) | `15.0` | `15.0` ✓ |
| `12,71069` | `1` (Night) | `12.5` | `12.5` ✓ |
| `21,14241` | `2` (Day) | `21.0` | `21.0` ✓ |
| `15,20336` | `3` (Day2) | `15.0` | `15.0` ✓ |

All five held exactly. This rounding was never previously characterized
for this endpoint — the existing decimal-value confirmation for
`change_mode=0` (§5's "Decimal temperature values" item, 2026-08-30) only
ever tested `24.5`, a value already on the 0.5 grid, so it could not have
revealed flooring behavior either way. Whether `change_mode=0` floors the
same way on an off-grid input is still unconfirmed — not exercised by
this session's capture, since the live setpoint wasn't touched during it.

**Implemented in 0.3.0:** `ApiMethods.set_desired_temperature()`, the
`set_desired_temperature` HA Action and per-room `number` sliders
(`number.py`). Per-type ranges enforced client-side (from the Smile App UI,
user-provided 2026-09-18): H 15-25, L 13-21, N 12-14.5 degC. Still
untested: `roomid` scoping on multi-room installs (the capture and live
tests only covered the single room, `roomid=1`), and whether writes are
ignored under Standby.

## 5. Open Items (as of project handover)

- [ ] Verify the exact PBKDF2/SHA512 parameters for password hashing
- [ ] Confirm the AES decrypt IV on Honeywell (identical to standard HeatApp?)
- [x] **`setrooms` behavior before scene activation** — RESOLVED
      (2026-08-30), but not the way originally framed: it was never an
      ordering question, and it wasn't purely a wire-encoding problem
      either (though a real encoding bug was found and fixed along the
      way — array-valued parameters like `rooms` were rendered via
      Python's default `str()` on the raw list instead of the protocol's
      actual format, and empty arrays specifically needed the literal
      string `"undefined"`, not an empty string, matching the gateway's
      own JS `undefined`-coercion behavior). **The actual root cause:**
      `/api/scene/setrooms` with a genuinely empty room list appears to
      hang the gateway's firmware itself (10-second `ReadTimeout`,
      reproduced identically under two different encodings of the empty
      value, and again in a completely separate incident on 2026-09-09
      via a different call site) — this is a device-side limitation, not
      something fixable via request formatting. **Real fix:** avoid ever
      calling `/api/scene/setrooms` with an empty list — when removing
      the last/only room from a scene, `/api/scene/set(active=false)`
      alone is sufficient to deactivate it; there is no need to also
      clear room membership to zero. As of 2026-09-09, `ApiMethods.
      set_scene_rooms()` itself also raises `ValueError` for an empty
      list before sending anything, closing the gap that let the
      2026-09-09 incident happen via a call site other than
      `scene_manager.py`. See `api_request.py`'s and `scene_manager.py`'s
      change logs, `api_methods.py`'s change log, and CLAUDE.md, for the
      full multi-round story.
- [x] **Decimal temperature values (e.g. 20.5 °C)** — RESOLVED
      (2026-08-30): dot notation (`24.5`) is correctly interpreted by
      `/api/room/settemperature`, no comma conversion needed. Verified via
      `scripts/manual_check_decimal_temperature.py` with Standby confirmed
      inactive throughout (an earlier run showed a false-negative
      "MISMATCH" caused by Standby still being active during the test,
      not a notation problem - see the script's own change log).
- [ ] **New `roomstatus` code observed: `11`.** Seen with Standby
      deactivated and no other scene (Boost/Party/Leave/Holiday) active -
      likely the "plain schedule-following, nothing special active"
      baseline state. Not yet formally added to `const.py` since the
      existing `hvac_mode`/`preset_mode` logic already handles it
      correctly by omission (anything that isn't `ROOM_STATUS_STANDBY`
      falls through to `HVACMode.AUTO`, and anything that isn't one of the
      four named scenes falls through to `preset_mode == None`) - no code
      change needed unless a dedicated constant/label for this state
      becomes useful later.
- [ ] Clarify the purpose of `/api/xpertonly/start`, `/admin/sentry/*`
- [x] **Scene `duration` parameter (why presets failed to activate)** —
      RESOLVED (2026-09-01), root cause and per-scene write behavior now
      in §4d, corrected 2026-09-09 after further live testing. Fix (the
      `duration=0` root cause) shipped in `0.0.18`; the write-formula
      corrections and Leave's `target=` block shipped in `0.0.21`.
      Holiday's `const.SCENE_ACTIVATION_DURATION` entry is still
      outstanding, see CLAUDE.md.
- [x] **Whether `roomstatus` could be a bitfield (Standby + preset encoded
      independently)** — RESOLVED/REFUTED (2026-09-01), see §4e. It is a
      flat single-state code.
- [ ] **Standby silently reasserts itself when a preset is removed while
      Standby was active in the background** — confirmed (2026-09-01, §4e)
      via the real `SceneManager.remove_member_from_scene()` path. Not yet
      fixed — needs a design decision (see CLAUDE.md) on how `hvac_mode`
      should read Standby's true state.
- [ ] **Holiday+Standby simultaneously active never resolves `roomstatus`
      to Holiday's code** — confirmed as a genuine gateway firmware quirk
      (2026-09-01, §4f), workaround (deactivate Standby first) verified
      live but not yet implemented in production code.
- [x] **Leave's write-side `duration` formula** — RESOLVED (2026-09-11,
      §4d). Confirmed identical to Party/Boost's fraction-of-scene_max
      formula via a live mitmproxy capture of the real Smile App plus a
      direct confirmation through `ApiMethods.set_scene()`. The
      2026-09-09 "unsolved mystery" conclusion was an artifact of testing
      with raw values outside the gateway's actual `[0,1]` input domain
      for this scene. `target=` unblocked for Leave; `const.
      SCENE_ACTIVATION_DURATION["Leave"]` corrected from `2` to `0.5`.
- [x] **Writing `desiredTempDay`/`desiredTempDay2`/`desiredTempNight`** —
      RESOLVED (2026-09-18), see §4g. Same `/api/room/settemperature`
      endpoint as the live setpoint, `change_mode=2`/`3`/`1` respectively;
      gateway floors the sent value to the nearest 0.5 °C step. Implemented
      in 0.3.0 (`set_desired_temperature()` Action + sliders), see §4g.