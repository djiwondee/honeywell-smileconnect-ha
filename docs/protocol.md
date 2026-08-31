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
   - Arrays: "[a,b,c]" (brackets, comma-separated, no spaces)
   - single-element array: just the value, no brackets
   - scalars: plain string representation
3. Build the data string: "key1=val1|key2=val2|...|"   (pipe, NOT &, with
   a trailing pipe)
4. Signature = MD5(data_string + devicetoken)
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

## 5. Open Items (as of project handover)

- [ ] Verify the exact PBKDF2/SHA512 parameters for password hashing
- [ ] Confirm the AES decrypt IV on Honeywell (identical to standard HeatApp?)
- [ ] Test `setrooms` behavior before scene activation (order dependency)
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
