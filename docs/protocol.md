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
duration (2026-09-01)

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
documented unit — each scene applies its own multiplicative factor, and
three of the four scenes additionally enforce a hard cap. This was only
found by testing multiple distinct, deliberately small send-values per
scene and reading the *actually configured* duration back from the Smile
App/physical regler display (the only trustworthy source — see the
warning below about `get_scene_duration()`).

**A methodological trap worth recording:** the first two Holiday and Party
data points (`3→30d`, `1.5→30d` for Holiday; `3→12h`, `1.5→12h` for Party)
each showed the *same* output for *different* inputs — which looks like
strong evidence for a specific factor (e.g. `×10` was the first, wrong,
conclusion for Holiday) but is actually the signature of **both inputs
having already saturated a cap**, revealing nothing about the real factor
below it. Only a *third*, deliberately smaller test value per scene (which
landed below the cap) revealed the true factor. Lesson: two data points
that agree do not by themselves prove linearity — check whether they might
both be capped before trusting a factor derived from them.

| Preset | Measure (unit) | Min | Max | Default (real) | Factor (`real = sent × factor`, capped at Max) | Send value for the real Default |
|---|---|---|---|---|---|---|
| Leave | Hours | 0 | 12 | 6 | ×3 | **2** |
| Holiday | Days | 0 | 30 | 15 | ×30 | **0.5** |
| Party | Hours | 0 | 12 | 6 | ×12 | **0.5** |
| Boost | Minutes | 0 | 120 | 60 | ×120 | **0.5** |

Min/Max/Default columns match the vendor-documented values supplied
2026-09-01; the Factor and "send value" columns are this project's own
live-verified findings (`scripts/manual_check_preset_nudge.py`), each
**confirmed via at least two independent data points**, at least one of
them below the scene's cap:

- **Leave** — `2→6h`, `4→12h` (=Max, boundary case). Clean ×3 line, no
  saturation ambiguity since `2` was clearly unsaturated.
- **Holiday** — `0.5→15d` (below cap, exact); `1.5→30d` and `3→30d` both
  saturate at the 30-day cap. The originally-recorded `×10` factor was
  wrong (see methodological trap above) — corrected to `×30`.
- **Party** — `0.5→6h` and `0.75→9h` (both below the 12h cap, both exact);
  `1.5→12h` and `3→12h` both saturate. Corrected from an initial (also
  cap-confused) `×4`/`×8` guess to the confirmed `×12`.
- **Boost** — `0.5→60min` (below cap, exact); `1→120min`, `5→120min`,
  `20→120min` all saturate at the 120-minute cap. One further data point
  (`10→108min`) never fit any tested model (linear, capped-linear, affine)
  and is treated as a one-off measurement anomaly, not a real signal — see
  `manual_check_preset_nudge.py`'s own change log for the full elimination
  process across five separate Boost test runs.

**`get_scene_duration()` (`/api/scene/duration`) is not a usable
verification source**, for any scene, at any point: it returns near-zero
noise while inactive, and — this was checked explicitly, immediately after
activation, across every scene — it does **not** echo back the just-
configured value either (e.g. Holiday consistently showed ~0.013-0.017
days regardless of whether `0.5`, `1.5`, or `3` was sent; Boost
consistently showed exactly `0`). The only reliable way to confirm what
duration actually got configured is reading the Smile App or the physical
regler display.

**Decimal/fractional `duration` values are handled correctly by the API**
— confirmed via multiple genuinely-fractional sends (`0.5`, `0.75`, `1.5`)
that all produced exactly the values the linear-factor model predicted;
no evidence of silent rounding or truncation.

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
      value) — this is a device-side limitation, not something fixable
      via request formatting. **Real fix:** avoid ever calling
      `/api/scene/setrooms` with an empty list — when removing the
      last/only room from a scene, `/api/scene/set(active=false)` alone
      is sufficient to deactivate it; there is no need to also clear room
      membership to zero. See `api_request.py`'s and `scene_manager.py`'s
      change logs, and CLAUDE.md, for the full multi-round story.
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
      RESOLVED (2026-09-01), root cause and per-scene factor table now in
      §4d. Fix not yet implemented in `scene_manager.py`/`climate.py`.
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
