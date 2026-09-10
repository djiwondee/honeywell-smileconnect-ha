# CLAUDE.md — Project Context for Claude Code

This file is loaded automatically by Claude Code. It contains the full
reverse-engineering knowledge and architecture decisions for this project, so
no session has to start from zero.

## Project Goal

A HACS-compatible Home Assistant integration for the **Honeywell Smile
Connect** heating gateway (model **SCN-10**). This is an OEM/rebranded
variant of the HeatApp system by EbV GmbH, but it uses an **incompatible**
protocol (different cryptography, different request signing). For that
reason this integration is deliberately **not** derived from existing
`heatapp_local` / `py-heatapp-de` libraries, even though some code fragments
(class structure, variable names) were used as a starting point from that
project.

**Important:** If HeatApp naming fragments show up in the code (leftovers
from the original fork), they should be progressively replaced with this
project's own vocabulary (`honeywell_smileconnect`, `SmileConnect...`) to
avoid confusion with the incompatible standard HeatApp protocol.

## Disclaimer / Trademarks

- No affiliation with, and no authorization from, Honeywell or EbV GmbH.
- "Honeywell" and "Smile Connect" are used strictly to identify the
  supported hardware (nominative fair use), consistent with other HA
  community integrations (e.g. `tado`, `netatmo`).
- Never imply an official vendor relationship — not in code, docs, issues,
  or commit messages.

## Target System / Test Environment

- Gateway reachable at `192.168.1.132` (developer's example IP)
- Admin console: `http://<gateway-ip>/admin/dashboard/index`
- Exactly **one room** ("Alle"/"All", room ID 1) controls the whole house in
  the test installation — the integration itself must stay generic for n
  rooms, though.
- **No hot water control** in the test installation (no Shower/Towel scenes
  in use) — still model them in code, since the protocol supports them.
- The vendor's update server appears defunct — don't build any dependency on it.

## Core Finding: Protocol Differences, HeatApp vs. Honeywell Smile Connect

This is the root cause of why existing HeatApp libraries do NOT work. All
details below are **confirmed** — extracted directly from the gateway's own
JS via its admin console (see "Reverse-Engineering Method" further down) and
verified end-to-end with a real login + authenticated API call
(`/api/room/list` returning real room data). This is no longer a guess.

| Parameter | Standard HeatApp | Honeywell Smile Connect |
|---|---|---|
| Password hashing | MD5 | PBKDF2/SHA-512, 1 iteration, 64-byte output, Base64-encoded — see below |
| Request signature | MD5(pipe-string + devicetoken) | **Same PBKDF2 scheme as password hashing**, applied to (pipe-string, devicetoken) instead of (password, challenge_token) |
| Parameter separator (signature string) | `&` | `\|` (pipe), with a trailing pipe |
| `udid` | random UUID | fixed `"web"` |
| `devicename` | `"homeassistant"` | `"Computer"` |
| `reqcount` semantics | (not applicable / differs) | current stored value is used to sign, THEN incremented for the next call — see below |
| AES devicetoken decrypt padding | (n/a) | standard PKCS7 — do NOT use the `\x10`-strip hack seen in the generic HeatApp reference code, it only works by coincidence |

### The shared PBKDF2 scheme (`Crypt.pbkdf2`)

Both password hashing and request signing reduce to the exact same
underlying primitive, extracted verbatim from the gateway's admin console:

```js
// request.stringToCharcodes
function(a) {
    var b = "";
    if (a.length > 0)
        for (var c = 0; c < a.length; c++) {
            for (var d = "" + a.charCodeAt(c); d.length < 3;)
                d = "0" + d;
            b += d
        }
    return b
}
// e.g. "AB" -> charCode('A')=65 -> "065", charCode('B')=66 -> "066" -> "065066"
// Returns a STRING of concatenated 3-digit-zero-padded char codes, not an array.

// Crypt.pbkdf2
function(a, b, c) {
    c || (c = "base64");
    var d = CryptoJS.PBKDF2(a, b, {
        hasher: CryptoJS.algo.SHA512,
        keySize: 16,   // CryptoJS counts in 32-bit WORDS -> 16*4 = 64 bytes
        iterations: 1  // yes, really just 1 - this is NOT a hardened KDF here
    });
    return d.toString(CryptoJS.enc.Base64)
}
```

i.e. `pbkdf2_base64(a, b) = Base64(PBKDF2-HMAC-SHA512(password=a, salt=b,
iterations=1, dkLen=64 bytes))`. Implemented once, shared, in
`api/crypto.py` as `string_to_charcodes()` and `pbkdf2_base64()`.

**Login password hashing** (`request.hashAuthenticationToken`):
```js
function(a, b) {
    return a = request.stringToCharcodes(a),   // a = password
           b = request.stringToCharcodes(b),   // b = challenge_token
           Crypt.pbkdf2(a, "" + b)              // the "" + is a no-op, b is already a string
}
```
→ `hashed = pbkdf2_base64(charcodes(password), charcodes(challenge_token))`

**Request signature** (`request.encodeRequestSignature`, called from
`request.getRequestSignature`):
```js
// getRequestSignature(devicetoken, params_object):
//   builds "key=value|key=value|...|" from sorted params_object keys
//   (see pipe-string rules below), then:
function encodeRequestSignature(a, b) {   // a = pipe data string, b = devicetoken
    return b = request.stringToCharcodes(b),
           a = request.stringToCharcodes(a),
           Crypt.pbkdf2(a, b)
}
```
→ `request_signature = pbkdf2_base64(charcodes(pipe_data_string), charcodes(devicetoken_plaintext))`

This was the original bug: the generic HeatApp reference code (and this
project's first draft) used plain `MD5(pipe_string + devicetoken)` for the
signature. Honeywell uses the **same PBKDF2 scheme as the login hash**, just
with different inputs. Both are implemented via the shared
`api/crypto.pbkdf2_base64()` helper.

### Pipe-string construction (`request.getRequestSignature`)

```js
function(devicetoken, params) {
    for (var c = "", keys = Object.keys(params).sort(), i = 0; i < keys.length; i++) {
        var key = keys[i], val = params[key];
        if (Object.prototype.toString.call(val) === "[object Array]")
            c += val.length < 2 ? key + "=" + val[0] : key + "=[" + val.join(",") + "]";
        else {
            if (val === undefined || val == "undefined") { delete params[key]; continue }
            c += key + "=" + val
        }
        c += "|"
    }
    return request.encodeRequestSignature(c, devicetoken)
}
```

Rules, all implemented in `api/api_request.py`:
- Parameters sorted alphabetically by key.
- `None`/`undefined` values are dropped **entirely** — both from the
  signature string and from the actual request body (never send a
  `key=None` pair).
- A single-element array/list renders as the bare value (`key=value`, not
  `key=[value]`); 2+ elements render as `key=[v1,v2,...]`.
- Joined with `|`, trailing pipe included.
- Signature is computed over `udid` + `userid` + `reqcount` + the
  endpoint-specific params combined (all added to the same sorted object
  before signing) — `request_signature` itself is added to the body
  afterwards, not included in its own input.

### `reqcount` semantics (`request.makeRequestData`)

```js
function(path, params, onError) {
    params = params || {};
    params.udid = params.udid || "web";
    var exempt = ["/api/user/token/challenge", "/api/user/token/response",
                  "/api/ping", "/api/version", "/api/xpertonly/start",
                  "/admin/sentry/sentry", "/admin/sentry/status"];
    var needsAuth = exempt.indexOf(path) == -1 && path.indexOf("/initial") == -1;
    if (needsAuth) {
        var stored = store.getJSON("devicetoken");
        if (!stored) return onError && onError(i18n.translate("invalid_signature")), null;
        if (stored.unconfigured) return params;
        params[request.counter] = isNaN(stored[request.counter]) ? 0 : stored[request.counter];
        // request.counter === "reqcount" (confirmed)
        params.userid = stored.userid;
        if ("udid" in stored) params.udid = stored.udid;
        stored[request.counter] = parseInt(params[request.counter], 10) + 1;
        store.set("devicetoken", stored);
        params.request_signature = request.getRequestSignature(stored.devicetoken, params);
    }
    return params
}
```

**Critical detail:** the CURRENT stored counter value is used to sign THIS
request; only afterwards is it incremented and persisted for the NEXT
request. The first authenticated call after login uses `reqcount=0`.

Getting this backwards (incrementing before use) was an actual bug hit
during development — it caused every authenticated call to fail with
`"Your session is finished, please log in again."` even though login itself
had succeeded. Fixed in `api/credentials.py`:
`Credentials.next_reqcount()` returns the current value, then increments —
post-increment semantics, not pre-increment.

### AES devicetoken decryption (`Crypt.aes256decrypt`) — confirmed correct

```js
function(a, b) {   // a = password, b = devicetoken_encrypted (base64)
    a = CryptoJS.SHA256(a);
    var c = CryptoJS.AES.decrypt(b, a, {
        iv: CryptoJS.enc.Base64.parse("D3GC5NQEFH13is04KD2tOg==")
    });
    return c.toString(CryptoJS.enc.Utf8)
}
```
Key = SHA-256(password), fixed IV, AES-256-CBC — this matches what was
already implemented and needed no changes. The one correction made:
`CryptoJS`'s `.toString(Utf8)` implicitly strips standard PKCS7 padding;
pycryptodome does not do this automatically. `api/login.py` now uses
`Crypto.Util.Padding.unpad()` explicitly, rather than the fragile
`.strip("\x10")` hack seen in the generic HeatApp reference code (which only
happens to produce correct output when the padding is exactly 16 bytes).

## Known API Endpoints (verified against the Honeywell gateway)

```
POST /api/user/token/challenge      Body: udid=web
POST /api/user/token/response
POST /api/user/login
POST /api/user/list
POST /api/user/datetime
POST /api/weather
POST /api/room/list
POST /api/room/settemperature
POST /api/room/switchingtimes/get2
POST /api/room/switchingtimes/set2
POST /api/scene/status
POST /api/scene/duration
POST /api/scene/set
POST /api/scene/getrooms
POST /api/scene/setrooms
POST /api/portal/access/data
POST /api/systemstate
POST /initial/system/state
GET  /api/ping
GET  /api/version
GET  /api/xpertonly/start          (purpose still unclear)
GET  /admin/sentry/sentry          (purpose still unclear)
GET  /admin/sentry/status          (purpose still unclear)
GET  /assets/images/room/default.png
GET  /admin/login/index            (returns HTML of the config menu)
```

### Already successfully tested (live against the gateway)

- Full authentication flow: challenge → password hash (PBKDF2/SHA-512) →
  login → AES devicetoken decrypt. Confirmed working end-to-end.
- Authenticated, signed requests (PBKDF2-based signature, correct `reqcount`
  ordering). Confirmed via `/api/room/list` returning real room data.
- Session management across multiple polling cycles (coordinator refresh
  every `interval` seconds working without re-login failures).
- Setting temperature (integer values confirmed; decimals still untested —
  see below).
- The climate entity in HA: mode (Heat/Off), preset dropdown
  (None/Boost/Holiday/Leave/Party/Standby) all render and are settable via
  the UI without errors.

### Still untested / open

- ~~**`roomstatus` code mapping is unverified for Honeywell.**~~ **RESOLVED
  (2026-08-27).** Live-verified via
  `scripts/manual_probe_roomstatus_via_app.py` — modes set through the
  Smile App itself (not our own scene_manager.py write path), while our
  code only read `roomstatus` + `/api/scene/status` passively. Final
  confirmed mapping: `3=Party`, `6=Boost`, `7=Holiday`, `10=Leave`,
  `12=Standby`. **Leave vs. Holiday took three attempts to settle** — two
  uncontrolled runs (scenes not reset between tests, so Standby stayed
  stacked underneath) gave opposite swapped results; a third, deliberately
  controlled run (explicit clean-Standby-only reset before testing EACH of
  Leave/Holiday, eliminating the stacking confound) reproduced the first
  run's values, giving 2-out-of-3 agreement with a plausible explanation
  for the outlier — see `docs/protocol.md` §4 for the full blow-by-blow if
  this ever needs re-litigating.
  Also discovered along the way: scenes can be simultaneously active on
  this gateway (Boost/Party layer on top of an active Standby baseline
  without turning it off; Leave/Holiday appear to replace it instead) —
  `roomstatus` itself already resolves this to one priority-appropriate
  value, so no change to the single-preset model was needed.
  Also confirmed: the real `/api/scene/status` scene *names* (Party,
  Boost, Holiday, Shower, Leave, Standby, Towel) matched the generic
  HeatApp reference project exactly and needed no correction — only the
  *numeric roomstatus codes* were wrong, not the scene name strings. One
  naming caveat: the Smile App's own UI labels "Leave" as "Economy" —
  cosmetic vendor-app display label only; the API/internal name stays
  `"Leave"`.
- **`actualTemperature` is not always present.** Observed missing entirely
  on a single-zone "Regler MK1" (relay-only) installation with
  `roomstatus=12`. `climate.py` now uses `.get()` defensively rather than
  assuming the key exists (was previously a `KeyError` crash on entity
  setup). Open question: is there a different endpoint that reports actual
  temperature for this kind of installation, or does this gateway variant
  genuinely not have a room sensor?
- ~~**`minTemperature`/`maxTemperature` may not be meaningful on this
  installation**~~ **CONFIRMED (2026-08-30).** The user verified against
  the real Smile App: the actual selectable range is `12`-`25`, matching
  `scheduleTempMin`/`scheduleTempMax` exactly — `minTemperature`/
  `maxTemperature` (observed `12`/`12`, identical to `desiredTemperature`)
  are indeed not meaningful bounds on this installation. Code consuming
  temperature bounds should prefer `scheduleTempMin`/`scheduleTempMax`
  over `minTemperature`/`maxTemperature` — see
  `scripts/manual_check_decimal_temperature.py` for the first place this
  was applied (with a `12`/`25` fallback if those fields are ever
  missing). ~~`climate.py`'s own `min_temp`/`max_temp` properties still use
  `minTemperature`/`maxTemperature`~~ **FIXED (2026-08-30)** — now use
  `scheduleTempMin`/`scheduleTempMax` with the same fallback chain.
- ~~Behavior of `setrooms` **before** scene activation (does order
  matter?)~~ **SUPERSEDED (2026-08-30) by a much more significant finding:**
  it wasn't an ordering question at all — `api_request.py`'s HTTP body
  construction had a real bug where array-valued parameters (like
  `scene/setrooms`'s `rooms` field) were sent using Python's default
  `str()` on the raw list, producing `"[1]"` for a single room (should be
  the bare `"1"`) and `"[6, 7, 8, 9]"` with spaces for multiple rooms
  (should be `"[6,7,8,9]"`, no spaces) — diverging from both the signature
  string AND the protocol's documented wire format. Since this project's
  test installation has exactly one room, **every single scene
  add/remove call** went through the single-element-list bug path,
  plausibly explaining several previously-observed anomalies attributed
  to other causes (Standby-stacking, timing) at the time — including
  production HA reports of inconsistent preset/hvac_mode switching
  (sometimes working, sometimes not) and possibly the very first
  `manual_check_roomstatus.py` run showing `roomstatus` stuck at
  `12`/Standby regardless of which scene was toggled. Fixed by extracting
  a single `_render_value()` helper used for BOTH the signature string and
  the actual body now, so they cannot diverge again by construction. See
  `api_request.py`'s own change log and `tests/test_api_request.py`'s new
  `TestRequestBodyMatchesSignature` class (which inspects the actual body
  sent, not just the signature computation — a test that only covered
  `_build_pipe_signature_string()` in isolation would NOT have caught
  this, since that function itself was always correct).
- **Follow-up to the above, found immediately after deploying the first
  fix (2026-08-30): a second, more subtle bug in the same area.** The
  first fix's `_render_value()` rendered an EMPTY array as an empty
  string (`""`). Re-deriving the exact JS one more time
  (`g.length<2 ? f+"="+g[0] : ...`) revealed that for an empty array,
  `g[0]` is JavaScript's `undefined`, and JS string-concatenation coerces
  `undefined` into the literal text `"undefined"` — NOT an empty string.
  This wrong assumption was present from the very first version of
  `api_request.py` and had never been exercised against a live gateway
  until the user tried deactivating `Standby` on this single-room
  installation (which calls `set_scene_rooms("Standby", [])` — the empty-
  list case) and got a 10-second `ReadTimeout` from the gateway — its
  firmware appears to hang on a genuinely empty `rooms=` value rather
  than returning a clean error. Fixed: empty arrays now render as the
  literal string `"undefined"`, matching the real JS behavior exactly.
  **Lesson: when re-deriving protocol behavior from extracted JS, trace
  through JavaScript's own type-coercion rules literally (e.g. what does
  `x[0]` evaluate to on an empty array, and what does string-concatenating
  that actually produce) rather than substituting the "obviously sensible"
  Python equivalent (empty string) — the two are not always the same, and
  this project has now hit that gap twice in the same function.**
- **Third round on the same issue (2026-08-30): the `"undefined"` fix
  above did NOT resolve the timeout.** The exact same 10-second
  `ReadTimeout` on `/api/scene/setrooms` recurred, with the corrected
  wire encoding in place — proving conclusively that this was never a
  wire-format/encoding problem at all. **The gateway's firmware appears
  unable to handle `/api/scene/setrooms` with a genuinely empty room list
  under any encoding.** The `_render_value()`/`"undefined"` fix is still
  correct and kept (it fixes the signature/body consistency issue, a
  real bug in its own right), but it does not address this deeper
  limitation. **Real fix: avoid calling `/api/scene/setrooms` with an
  empty list at all.** `scene_manager.remove_member_from_scene()` now
  skips that call entirely when removing the last room from a scene —
  the same failing production log showed `/api/scene/set(active=False)`
  completing successfully just before the `setrooms` call that hung, so
  deactivating the scene alone is apparently sufficient; there is no need
  to also clear room membership to zero. See `scene_manager.py`'s own
  change log and the new `tests/test_scene_manager.py` (this project's
  first tests for that module at all — a real gap, given how much this
  function has been at the center of production bugs). **Lesson: don't
  assume a fix is complete just because it's principled and well-derived
  — verify against the real gateway before declaring victory, especially
  for anything involving edge cases (empty collections, boundary values)
  that a generic reference implementation may never have exercised either.**
  **Reconfirmed live 2026-09-09** during unrelated regression testing of
  `set_scene()`'s new room-assignment guard (see below): a diagnostic
  script called `api_methods.set_scene_rooms("Boost", [])` directly (a
  different call site than `scene_manager.py`, which already avoided this)
  and hit the identical hang. Gateway recovered on its own without a power
  cycle. This means the empty-list hang is not fully contained just by
  `scene_manager.py` avoiding it — any future direct caller of
  `ApiMethods.set_scene_rooms()` could reintroduce it. **Fixed at the
  source this time:** `set_scene_rooms()` itself now raises `ValueError`
  for an empty `room_ids` list before building any request, so the
  restriction no longer depends on every call site remembering to avoid
  it.
- **Fourth round on the same issue (2026-08-30): a fourth, DIFFERENT bug
  in the same function, found immediately after the empty-list-skip fix
  eliminated the timeout.** No more timeout, but `/api/scene/set(active=
  False)` always returned `success:true` while the scene never actually
  deactivated — confirmed via live log: a `scene/status` poll immediately
  after showed `Standby` still `isActive:true`, repeatedly, across
  multiple user attempts. Root cause: `active` is a Python `bool`, and
  `_render_value()` fell through to plain `str()` for it — producing
  `"True"`/`"False"` (Python-capitalized). The gateway's own JS coerces
  booleans to lowercase `"true"`/`"false"` in string concatenation
  (`"active=" + false` → `"active=false"` in JS) — same class of bug as
  the empty-array case two rounds ago (Python's "obviously equivalent"
  stringification differing from JavaScript's actual coercion rules), just
  affecting a different value type. Fixed by adding an explicit `bool`
  branch to `_render_value()`, checked before the list/tuple and generic
  `str()` branches (`bool` is a subclass of `int` in Python — must never
  render as `"1"`/`"0"` either). Verified this was the ONLY boolean
  write-parameter in the entire codebase (`params.active` in
  `api_methods.set_scene()`), so the fix's blast radius is fully
  understood. **This makes it four real, previously-undiscovered
  protocol bugs found in one single function (`api_request.py`'s request
  body construction) within about two hours of live production testing —
  all stemming from the same root pattern: assuming Python's default
  stringification of a value matches what the gateway's own JavaScript
  would produce, when it silently doesn't for arrays and booleans
  specifically. Numbers and plain strings were never a problem. If a
  FIFTH such type ever needs sending (e.g. `None`/null, though that's
  already handled separately by being filtered out entirely), check its
  JS string-coercion behavior explicitly before assuming str() is
  correct.**
- ~~**Decimal temperature values (e.g. 20.5 °C)**~~ **RESOLVED
  (2026-08-30).** Dot notation (`24.5`) is correctly interpreted by
  `/api/room/settemperature` — no comma conversion needed, unlike the
  generic HeatApp reference code's commented-out dot→comma attempt in
  `_prepareRequestBodyForHash`, which turned out not to apply here.
  Verified via `scripts/manual_check_decimal_temperature.py`, but only
  after fixing a real bug the user spotted in that script's first version:
  it deactivated Standby, then blindly `sleep(3)`'d and proceeded WITHOUT
  verifying the deactivation actually took effect or re-fetching the
  room — producing a false "MISMATCH" on the first run (Standby was very
  likely still active when the temperature was set, which unrelated
  behavior — see `docs/protocol.md` §4c — silently rejects temperature
  changes). Fixed with an active poll-and-verify loop; a clean re-run with
  Standby confirmed inactive throughout showed a correct match. **Lesson
  for future manual scripts that toggle a mode and then test something
  depending on it: always verify the mode change took effect (poll +
  re-fetch) rather than sleeping a fixed duration and hoping — a sibling
  lesson to the reqcount/signature debugging earlier in this project.**
- **New `roomstatus` code observed: `11`.** Seen with Standby deactivated
  and no other scene active — likely the "plain schedule-following,
  nothing else active" baseline. Not yet given a dedicated `const.py`
  constant since existing `hvac_mode`/`preset_mode` logic already handles
  it correctly by omission (see `docs/protocol.md` §5) — only add one if a
  concrete future need for a dedicated label arises.
- ~~**Preset activation appearing to "revert" in the HA UI (Leave/Holiday/
  Boost/Party).**~~ **RESOLVED (2026-09-01, shipped in 0.0.18).** Root
  cause: `scene_manager.py`'s `add_member_to_scene()` re-sent whatever
  `get_scene_duration()` currently reported for an *inactive* scene —
  which is always `0` (or, for Holiday, a meaningless near-zero fractional
  leftover) — and `set_scene(active=True, duration=0)` is silently
  rejected by the gateway (`success:true`, but `scene/status.isActive`
  never flips). Beyond that, the numeric value you DO send is not the
  real-world duration at all — each of the four preset scenes applies its
  own multiplicative factor, and three of the four additionally cap at a
  hard maximum. Full investigation, the "two data points that agreed were
  actually both capped" trap that produced a wrong `×10` guess for Holiday
  along the way, and the final confirmed factor table (with vendor-
  supplied Min/Max/Default columns) are in `docs/protocol.md` §4d — do not
  re-derive this from scratch. **Fix:** `const.SCENE_ACTIVATION_DURATION`
  holds the confirmed send-values (Leave `2`→6h, Holiday `0.5`→15d, Party
  `0.5`→6h, Boost `0.5`→60min); `add_member_to_scene()` uses that lookup
  instead of `get_scene_duration()`. Locked in by
  `tests/test_scene_manager.py::TestAddMemberToSceneUsesCorrectedDuration`
  and live-verified end-to-end in HA by the user.
  **⚠️ RE-OPENED (2026-09-09), PARTIALLY RECONCILED — see below for the
  final state.** A completely independent live investigation into
  `/api/scene/set`'s `duration` parameter (see the new dedicated entry
  directly below) initially appeared to contradict two of the four
  values in this table:
  - Boost `0.5`→60min and Party `0.5`→6h DO match the newly-confirmed
    fraction formula (`target/scene_max`: `60/120=0.5`, `6/12=0.5`) — no
    issue here, and independently re-confirmed live.
  - **Leave `2`→6h — RE-TESTED LIVE (2026-09-09) AND CONFIRMED CORRECT.**
    A wider sweep of send-values (`1,3,5,6,8`) around this, however,
    revealed that Leave's write-side duration formula is NOT a simple
    factor at all — the wider sweep produced non-monotonic results that
    fit no tested model (see the dedicated entry below and
    `api_methods.py`'s 2026-09-09 change log for the full data and
    methodology, including ruling out a request-timing/race-condition
    explanation via a fully isolated re-test). `set_scene()`'s `target=`
    parameter is now deliberately disabled for Leave
    (`NotImplementedError`) rather than exposing a formula that only
    holds for the two originally-tested values. **Practical consequence:
    `SCENE_ACTIVATION_DURATION["Leave"] = 2` remains correct as-is and
    should NOT be changed** — but `add_member_to_scene()` must not be
    generalized to compute other Leave durations dynamically (e.g. via a
    future "let the user pick a custom Leave duration" feature) until the
    real formula is understood. Shipped as a fix in `0.0.21`.
  - **Holiday `0.5`→15d does NOT match, and this part of the table is
    genuinely WRONG — still needs correcting in `const.py`.** Holiday is
    confirmed (2026-09-09, see below) to send RAW DAYS, not a fraction —
    sending `15` is echoed back as `~15`, and `100` is echoed back as
    `~100` (gateway does not clamp Holiday at all, unlike Boost/Party).
    If `duration=0.5` is what `SCENE_ACTIVATION_DURATION["Holiday"]`
    still sends in production, that sets **0.5 days (12 hours)**, not 15
    days — this is very likely a real, currently-live bug, distinct from
    the Leave situation (which turned out to be correct, just narrower
    than assumed). **This specifically still needs to be checked against
    `const.py` and fixed** — not yet done as of `0.0.21`.
  **Status as of `0.0.21`:** Leave's specific contradiction is resolved
  (the old value was right, the newly-built generalization was wrong, and
  is now blocked rather than shipped). Holiday's contradiction is NOT yet
  resolved — treat that as the remaining top-priority item for the next
  session that touches `scene_manager.py`/`const.py`.
- **`api/api_methods.py`'s `set_scene()` `duration` parameter semantics,
  confirmed live 2026-09-09** (independent of, and predating discovery of,
  the `SCENE_ACTIVATION_DURATION` conflict directly above). Full live
  falsification testing (`scripts/manual_probe_scene_duration.py`,
  `scripts/manual_probe_scene_duration_all_scenes.py`,
  `scripts/manual_probe_set_scene_regression.py`,
  `scripts/manual_probe_leave_duration_contradiction.py`,
  `scripts/manual_probe_leave_formula_ceiling.py`,
  `scripts/manual_probe_leave_single_value.py`) established:
  - **Boost, Party:** `duration` is a **fraction of the scene's own
    `scene_max`** from `/api/scene/status` (Boost=120min, Party=12h —
    both confirmed live via `scene/status`). A value >1 is **silently
    clamped to `1` (=scene_max) by the gateway**, no error returned
    (`success:true` regardless). Confirmed for Boost via a real countdown
    (`/api/scene/duration` polled while a genuine app-triggered Boost ran
    down, landing exactly on the formula's predicted value — 28min
    remaining matched `duration=0.2333...` × 120 exactly); confirmed for
    Party via the identical clamp-test pattern (`0.5` echoed back
    unchanged, `6`/raw-hours clamped to `1`).
  - **Leave — read-side confirmed, write-side UNRESOLVED.** `Leave`'s
    READ interpretation via `get_scene_duration()` matches the same
    fraction-of-`scene_max=12h` model as Party (`0.5` raw → `6h`, `1.0`
    raw → `12h`), so `SCENE_MAX["Leave"] = 12` remains correct for
    reading. But the WRITE side does **not** follow "send the fraction
    directly" like Party/Boost — extensive live testing found that
    sending raw values `1,2,3,4,5,6,8` via `duration=` produced this
    non-monotonic table (all confirmed via a fully isolated
    poll-until-inactive methodology, ruling out request-timing
    contamination):
    ```
    sent:    1     2     3     4     5     6     8
    implied: 12h   6h    12h   12h   6h    12h   5h
    ```
    No tested model (a fixed `×3` factor — which matched the ORIGINAL
    two-point 2026-09-01 measurement exactly, `2→6h`/`4→12h`, and even
    re-confirmed exactly in an isolated re-test — fraction-of-scene_max
    like Party/Boost, or simple modular arithmetic) fits the full
    7-point set. The results are suspiciously clean fractions (`0.5`,
    `1.0`, `5/12`) rather than noise, suggesting a real but
    not-yet-understood mechanism (possibly time- or session-state-
    dependent, in a similar spirit to Holiday's small unexplained read
    offset below) rather than a simple multiplicative factor. **Decision
    (shipped in `0.0.21`): `set_scene()`'s `target=` parameter is
    deliberately disabled for Leave** (`NotImplementedError`, with an
    explanatory message pointing here) rather than shipping a formula
    that has now been live-falsified. `duration=` (the raw wire value)
    remains fully available for Leave, unaffected — only `2` is
    currently known to reliably produce `~6h`; no other value has been
    validated as trustworthy for real-world use.
  - **Holiday:** `duration` is **RAW DAYS**, not a fraction. Sending `15`
    was echoed back by `/api/scene/duration` as `~15.0014` (small offset
    likely rounding/an internal absolute-end-datetime calculation, not
    significant) — critically, **NOT clamped to `1`** like Boost/Party,
    proving it uses a different wire format entirely. The original,
    3-month-old pre-rewrite `apiMethods.py`'s claim ("Holiday: Tage
    direkt") was correct for Holiday specifically, even though the same
    file's claim for Boost ("Minuten direkt") was wrong — this is why
    trusting either claim without live-testing would have been a mistake
    either way.
  - **The gateway itself enforces NO ceiling for Holiday** — tested live
    up to `100` days with no clamping observed
    (`scripts/manual_probe_holiday_max.py`). The Smile App's own UI limit
    (1-30 days, confirmed directly by the user) is purely an app-side
    restriction, not a protocol one. Same story for Boost/Party's
    app-visible ranges (Boost 30-120min raster of 30, Party 1-12h, all
    confirmed by the user, 0 not selectable in the app for any of the
    four scenes) — the gateway's own clamp-at-`scene_max` behavior for
    those two happens to coincide with enforcing SOMETHING, but not
    necessarily the exact app-visible bounds, so these are enforced
    client-side too, not assumed to be covered by the gateway's clamp.
    Leave's own app-UI range (1-12h) is documented in `SCENE_APP_LIMITS`
    too, but is currently unreachable in practice since `target=` is
    blocked entirely for Leave.
  - **`api_methods.py` now bakes all of this in directly:** `SCENE_MAX`,
    `FRACTION_DURATION_SCENES`, `RAW_DAYS_DURATION_SCENES`,
    `NO_DURATION_SCENES`, `SCENE_APP_LIMITS`, and
    `LEAVE_TARGET_UNSUPPORTED_MSG` module-level constants; `set_scene()`
    gained a `target` parameter (real-world unit — minutes/hours/days as
    appropriate) that validates against `SCENE_APP_LIMITS` (raises
    `ValueError` outside the app's own min/max/raster), then converts
    correctly per scene — except Leave, where it raises
    `NotImplementedError` instead (see above) — while the pre-existing
    `duration` parameter (raw wire value) still works unchanged for any
    existing caller and deliberately bypasses the `SCENE_APP_LIMITS`
    check — an intentional power-user escape hatch, not an oversight.
  - **Room-assignment guard added to `set_scene()`:** confirmed live that
    activating a scene with no rooms assigned returns `success:true` but
    `isActive` silently stays `false` (matches a check present in the
    original 3-month-old `apiMethods.py`'s `activateScene()` that this
    project's rewrite had dropped without noticing). `set_scene()` now
    raises `ValueError` in that situation unless `room_ids` is passed to
    assign rooms first — regression-tested locally in
    `scripts/test_scene_guards_local.py` (mocked, no gateway contact) so
    it can be re-verified after future refactors without repeating the
    live incident below.
  - **Live incident during regression testing of the room-assignment
    guard (2026-09-09):** a diagnostic script called
    `set_scene_rooms("Boost", [])` to intentionally trigger the new
    guard's "no rooms" condition — this is the exact empty-list call
    already known (see the dedicated entry above) to hang the gateway,
    which the diagnostic script had not cross-checked against. Gateway
    hung with a `ReadTimeout`, then recovered on its own within the
    session without a power cycle; Boost's room assignment (`[1]`) was
    confirmed intact afterward via a read-only follow-up check. Root-
    caused and fixed at `set_scene_rooms()` itself (see the empty-list
    entry above) rather than only in the test script, and the guard test
    was rewritten as a fully local, gateway-free test
    (`scripts/test_scene_guards_local.py`) so it can never repeat this.
  All of the above is captured directly in `api_methods.py`'s own change
  log — see that file, not just this summary, for the exact reasoning.
- ~~**Whether `roomstatus` might be a bitfield** (Standby + active preset
  encoded as independent bits, rather than one flat state code) — raised
  as a live hypothesis by the user given the known codes don't decompose
  into a clean single-bit-per-mode pattern.~~ **REFUTED (2026-09-01)** via
  `scripts/manual_probe_roomstatus_compound.py`, which tested every
  Standby+preset combination set purely through the Smile App (bypassing
  our own write path for a clean signal): no combined/OR'd value was ever
  observed; `roomstatus` is confirmed to be a flat, single "currently
  winning state" code. Full compound-state table in `docs/protocol.md`
  §4e.
- ~~**Standby persists silently in the background under an active preset,
  and reasserts itself the moment the preset is removed** — and,
  separately, **Holiday+Standby simultaneously active never resolves
  `roomstatus` to Holiday's code (`7`)**, a genuine gateway firmware
  quirk confirmed via two independent write paths (this project's API
  calls AND the Smile App), not a request-encoding bug.~~ **RESOLVED
  TOGETHER (2026-09-01, shipped in 0.0.18)**, with a different — and
  better — fix than either issue's own first-drafted workaround. An
  initial plan tried to fix Holiday specifically by forcibly deactivating
  Standby whenever Holiday was selected; **the user correctly rejected
  this during planning**, pointing out that Leave/Boost/Holiday/Party must
  all stay genuinely selectable independent of Standby, and a fix must
  work uniformly for all four, not special-case one of them by mutating
  Standby's state as a side effect. **Actual fix:** `coordinator.py` now
  polls `/api/scene/status` + `/api/scene/getrooms` for Standby and all
  four presets every cycle (new `coordinator.data["scene_active_rooms"]`
  field — ground-truth per-scene room membership). `climate.py`'s
  `hvac_mode` and `preset_mode` (via `_update_active_preset()`) now read
  this directly instead of inferring state from `roomstatus` at all —
  `roomstatus` is no longer used anywhere in `climate.py`. This fixes the
  masking problem uniformly for all four presets (Holiday included) and
  never touches Standby's actual state, so it's immune to the Holiday
  firmware quirk entirely rather than working around it. **Hard
  constraint honored:** `async_set_hvac_mode()`,
  `async_set_preset_mode()`'s activation logic, and
  `_nudge_temperature_after_leaving_standby()` were left byte-for-byte
  unchanged — only the read side changed. User live-verified the already-
  working Standby toggle showed zero regression, and all four presets
  (including Holiday) now display correctly while Standby is active in
  the background, without `hvac_mode` ever flipping as a side effect.
  Full story in `docs/protocol.md` §4e/§4f.
- ~~**No way to clear a selected preset back to "none" from the HA UI**
  without picking a different preset instead.~~ **RESOLVED (2026-09-01,
  shipped in 0.0.18).** Found live, immediately after the fix above:
  `preset_modes` deliberately excluded `PRESET_NONE` (see the 2026-08-27
  (e) entry above) on the assumption that Python `None` alone was
  sufficient for display — true for *reading* the state, but missed that
  HA's preset dropdown only offers entries actually present in
  `preset_modes`, so there was nothing to click to explicitly clear a
  preset. **Fix:** `PRESET_NONE` ("none") added to `_attr_preset_modes`;
  `preset_mode` property now returns `PRESET_NONE` instead of Python
  `None` when nothing is active (internal `self._active_preset` tracking
  unchanged); `async_set_preset_mode()` treats `preset_mode ==
  PRESET_NONE` as "remove whatever preset is active, don't activate
  anything" instead of trying to activate a nonexistent `"none"` gateway
  scene. Also directly confirms the second half of the fix above: if a
  preset is deactivated via the Smile App (not HA), `preset_mode`
  correctly falls back to `"none"` within one poll cycle. User live-
  verified both directions.
- **`set_switching_times()` was never live-verified and had a real
  wire-format bug.** RESOLVED (2026-09-09). Live mitmproxy capture of the
  real Smile App's own `set2` request (obtained by routing the phone's
  WLAN traffic through a mitmproxy container while manually editing a
  schedule) revealed two issues with the previous, never-tested
  implementation: (1) **the wire field name is `"from"`, not `"from_"`.**
  The previous code set `params.from_ = from_times` (since `from` is a
  reserved Python word, it couldn't be used as an attribute name via dot
  notation), and that attribute name went straight onto the wire
  unchanged — the gateway never received a `from` field at all, and every
  call failed with `success:false, "The input format is invalid: 1"`.
  (2) **Hours must be sent WITHOUT a leading zero** (e.g. `"4:30"`, not
  `"04:30"`), even though `get2` returns them zero-padded — confirmed via
  the real Smile App request, which is built by a `.NET`/RestSharp client
  (`User-Agent: RestSharp/106.6.9.0`), not the browser-JS path this
  project's other signing logic was reverse-engineered from.
  Separately, also discovered: **the gateway enforces an undeclared
  contiguous-slot rule per day** — slots must be filled from slot 1
  upward with no gaps. Violating this is NOT rejected with an error;
  instead the gateway silently shifts the sent value to the first free
  slot in that day AND drops the sent `type`, replacing it with the
  day's existing first slot's type — a silent data-corrupting write, the
  same failure class as several `api_request.py` bugs above (`success:
  true` with no/wrong actual effect). **Fix:** `setattr(params, "from",
  ...)` instead of `params.from_ = ...` (setattr accepts any string key;
  `ApiRequest.request()` reads params via `vars()`, which picks up
  setattr-assigned keys identically to normal attributes — no change
  needed in `api_request.py` itself); leading-zero stripping via a new
  `_strip_leading_zero_hour()` helper; a new
  `_validate_switching_times()` that enforces the contiguous-slot rule
  client-side, raising `ValueError` before any request is built, since
  the gateway itself won't. **Signature changed:**
  `set_switching_times()` now takes a single `switchingtimes` list in the
  exact shape `get_switching_times()` already returns (a flat, day-major
  list of `{"from","to","type"}` dicts or `None` per slot) instead of
  three separate `from_times`/`to_times`/`types` CSV strings — this
  endpoint had no existing callers (not yet wired to any HA entity), so
  this was a clean break, not a compatibility concern. `slots_per_day` is
  derived from the list length (`len(switchingtimes) // 7`) rather than
  hardcoded to `3`, since the protocol never declares this count
  explicitly and it may differ on other gateway hardware. Live-verified
  end to end via `scripts/manual_probe_switchingtimes_echo_v3.py` and
  `scripts/manual_probe_switchingtimes_slotops_v2.py` (both call the real
  `ApiMethods.set_switching_times()` directly, not a hand-built request):
  echo round-trip identical before/after, plus explicit create/edit/
  delete of individual slots, all confirmed via `get2` afterward, not
  just `set2`'s `success:true`.

### Next planned work (agreed in project discussion, not yet started)

- **Top priority, before anything else touching scene activation:**
  `const.SCENE_ACTIVATION_DURATION["Holiday"]` (currently `0.5`, intended
  to mean 15 days) is very likely WRONG given Holiday's confirmed
  raw-days write formula — `0.5` would actually set 0.5 days (12 hours),
  not 15 days. This still needs to be checked and fixed in `const.py`/
  `scene_manager.py`. (Leave's equivalent contradiction — see "Still
  untested / open" above — is resolved: the old value was correct, only
  the newly-attempted generalization was wrong, and that generalization
  is now blocked rather than shipped.)
- All three preset/roomstatus/Standby-masking bugs above, plus the
  PRESET_NONE gap found during their live verification, are now shipped
  in 0.0.18 — no outstanding implementation work from that investigation
  specifically (see the re-opened item above for a NEW, separate concern
  found afterward, partially resolved as of 0.0.21).
  The pre-existing items below (never blocked on this work, e.g. the
  `actualTemperature`/temperature-sync question, reconnect/error-handling
  strategy) remain the next candidates, alongside the switching-times
  items below.

- ~~**Outside temperature sensor.**~~ **DONE (2026-08-27).** Implemented as
  `sensor.py` with three entities (outside temperature, min, max) reading
  from `coordinator.data["weather"]`. Real response captured manually via
  `scripts/manual_check_weather.py` first and verified as
  `tests/fixtures/weather_response.json` before writing any entity code —
  see "Test Suite" section below. Decision made along the way: `forlocation`
  in the real response contains an actual postal code/city (the account's
  configured location) and was deliberately kept as-is in the fixture
  rather than anonymized, on the basis that it is expected to match the
  real HA setup's own location anyway.
- ~~**Verify `roomstatus` codes properly**~~ **DONE (2026-08-27)** —
  see "Still untested / open" above for the full mapping and the
  Leave/Holiday disambiguation story.
- **Full temperature sync** — resolve the `actualTemperature` /
  min/max-temperature open questions above.
- ~~**Options flow**~~ **DONE (2026-08-27).** Implemented as
  `OptionsFlowHandler` in `config_flow.py` — host/credentials/both poll
  intervals (`CONF_INTERVAL`, `CONF_PING_INTERVAL`) can now be changed
  without recreating the entry. Bundled together with the ping/connectivity
  feature below since both needed `config_flow.py` changes anyway.
- ~~**Ping-based connectivity binary sensor**~~ **DONE (2026-08-27).**
  Implemented as a fully independent `SmileConnectPingCoordinator` +
  `binary_sensor.py` (connectivity) + a diagnostic response-time entity in
  `sensor.py` — see "Integration Architecture" above for the full design.
  This also drove the `device.py` extraction that fixed the device-
  structure bug, and the `config_flow.py` rework that added the
  `/api/ping`-derived `unique_id` and the options flow together.
- ~~**Expose switching times**~~ **`get_switching_times`/
  `set_switching_times` in `api/api_methods.py` are now live-verified and
  working** (see "Still untested / open" entry above, 2026-09-09) — the
  wire-format bug that made `set_switching_times()` unusable is fixed,
  and both are regression-tested against the real `ApiMethods` code path.
  Still not wired to anything in the HA integration layer (service or
  entity) — this is now the concrete next step, not a blocked-on-
  verification item anymore. See the new HA Action bullet directly below.
- **New: Home Assistant Action (service) to let automations set hvac
  mode, preset, thermostat temperature, and switching times.** Not yet
  designed. Should expose, as callable HA services (not just entity
  UI interactions), at minimum: setting `hvac_mode` (Standby on/off),
  setting `preset_mode` (Boost/Party/Leave/Holiday/none), setting target
  temperature, and writing switching-time schedules (leveraging the now-
  working `set_switching_times()`). Needs a design proposal (per Session
  Workflow rule 3 below) before implementation — in particular how
  switching-times input should be structured for a service call (likely
  not the raw 21-slot list directly, since that's an internal wire
  format, not a natural service-call shape) and whether per-room
  targeting uses `entity_id` (mapping to the existing climate entity) or
  a separate `room_id` parameter. **This is the first piece of work
  planned for the `0.1.x` line** — see "Versioning & Branching Strategy"
  below: the last `0.0.x` release is `0.0.21`, and this feature starts
  the initial `0.1.x` beta release, which means it must be developed on a
  dedicated feature branch and merged via pull request, not committed
  directly to `main`.
- **Possible future gateway-attached entities from `/api/weather`'s
  remaining fields** (`iconUrl`, `forlocation`) — deliberately NOT
  implemented now. Per project discussion: the outside
  temperature/min/max sensors were confirmed to belong on the **regler**
  device (the physical sensor hardware is regler-side; the gateway only
  relays the reading via `/api/weather`) and were deliberately kept there
  rather than moved to match, specifically to avoid ambiguity if a future
  installation ever has multiple reglers (the gateway has no way to tell
  us which regler physically owns a given weather reading, so binding
  weather sensors to "the first reported room" would be a coin-flip on
  such a setup — single-regler installations like the current one don't
  expose this problem, but it would silently misattribute data on a
  multi-regler one). `iconUrl` (a weather icon/condition) and
  `forlocation` (the configured location name) are different in kind,
  though: they are genuinely internet-weather-service data the *gateway*
  itself fetches (not a regler-side physical measurement), so if/when
  these are ever turned into entities, they belong on the **gateway**
  device, not the regler — noted here so a future session doesn't have to
  re-derive this reasoning.
- **Reconnect/error handling strategy** — currently the coordinator would
  presumably just re-login every refresh cycle on failure; this works but
  is inefficient and not a deliberate design. Worth revisiting once basic
  functionality is solid.
- **Runtime verification of this round's changes** — see the "⚠️ Needs
  runtime verification" callout under "Integration Architecture": the
  `EntityCategory` import location, the `OptionsFlowHandler` base-class
  behavior, and `suggested_area` actually triggering HA's area-suggestion
  UI have none of them been confirmed against a live HA instance yet,
  since this round of changes was implemented without live HA available in
  the session.
- **Live-verify `ping_response.json`** via `scripts/manual_check_ping.py`
  — the current fixture is transcribed from pre-existing user
  documentation, not captured live in a chat session (unlike every other
  fixture in this project).

## Test Suite

**Test infrastructure debugging story (2026-08-27) — read this before
touching test import setup again:**

A `ModuleNotFoundError` / raw `KeyError: 'honeywell_smileconnect'` (deep in
`importlib` internals) appeared for several test files after
`pytest-homeassistant-custom-component` became active. Several fixes were
applied across multiple attempts:

1. `tests/__init__.py` added (empty), making `tests/` a proper package, so
   `from .conftest import load_fixture` (relative import) is used instead
   of the previously fragile bare `from conftest import load_fixture`.
2. `pytest.ini` added with `pythonpath = .` (repo root) plus a new empty
   `custom_components/__init__.py`, so tests import via
   `custom_components.honeywell_smileconnect.xxx` — the exact same dotted
   path Home Assistant itself uses at runtime — instead of a test-only
   shortcut that flattened the `custom_components.` prefix away.
3. A `conftest.py`-level `sys.path.insert(...)` that had been kept
   "as a redundant safety net" alongside the `pytest.ini` mechanism was
   removed entirely (running two path-injection mechanisms for the same
   directory at once is worth avoiding regardless, even though it turned
   out NOT to be the deciding fix here - see below).
4. **The failure persisted through all of the above**, identically,
   including when reproduced with plain `python3 -c "..."` outside pytest
   entirely - which at the time seemed to rule out pytest/plugin
   interaction as the cause. Ultimately what resolved it was a **full,
   clean reset of the local working copy** (`rm -rf custom_components
   tests scripts docs` + re-extracting a complete, freshly-verified
   project ZIP) after many rounds of incremental copy/paste patches had
   plausibly caused local file drift (a stale or partially-overwritten
   `api/api_methods.py` or similar, without either side noticing).

**Honest conclusion: the exact root cause was never conclusively isolated.**
It may have been local file drift/corruption from many incremental patch
rounds (the leading theory, given a full reset fixed it and the plain-
python reproduction had already ruled out pytest itself), the import-path
mismatch fixed in step 2 (possible but unconfirmed - was never re-tested
in isolation against the old drifted files), or some combination. Both
fixes are kept because they are good practice independent of which one
mattered: importing via the real `custom_components.honeywell_smileconnect`
path (matching HA's own runtime resolution) is more correct than a
test-only shortcut regardless, and avoiding duplicate path-injection
mechanisms is safer regardless.

**Practical lesson for future sessions:** after many rounds of shipping
incremental patch bundles for the same files across a long chat session,
treat "the code I'm sending should already match what's in the repo" as an
assumption worth periodically re-verifying, not a given - a full,
clean-checkout re-sync (as eventually done here) is a legitimate and
sometimes necessary troubleshooting step, not just a last resort. If an
import error resists several targeted fixes and reproduces even outside
pytest, suspect local file drift before continuing to iterate on pytest
configuration.

If tests fail again with `ModuleNotFoundError` or a raw `KeyError` for
`honeywell_smileconnect`/`conftest`, try in this order: (1) clear caches
(`find . -name __pycache__ -exec rm -rf {} +` and `rm -rf .pytest_cache`),
(2) reproduce with plain `python3 -c "..."` outside pytest to isolate
whether it's pytest-specific, (3) if the plain-python reproduction also
fails, suspect local file drift and consider a clean re-sync before
further config changes.

`tests/` contains regression tests for the HA-independent `api/` layer
(crypto, login, request signing, response parsing). Run with:

```bash
pytest tests/ -v
```

- `tests/fixtures/` holds **real payloads captured from a live Honeywell
  gateway** (`192.168.1.132`) during development — `challenge_response.json`,
  `login_response.json`, `room_list_response.json`, `weather_response.json`.
  These are genuine recorded API responses, not hand-written
  approximations, and are safe to keep in the repo (no real password or
  long-lived secret is contained in them; the captured devicetoken/challenge
  values are single-use and already expired). `weather_response.json`
  deliberately keeps the real `forlocation` value (a real postal
  code/city) rather than anonymizing it, since it is expected to match
  whatever location the real HA setup itself is configured with anyway.
- `test_crypto.py` — locks in the PBKDF2/SHA-512/Base64 scheme against an
  independent hashlib-based reference computation, so a future refactor
  can't silently reintroduce the original MD5-based signature bug.
- `test_login.py` — parses the real challenge/login fixtures; verifies AES
  decrypt/PKCS7 handling via a self-constructed round trip (a real password
  is never available to, or stored in, this repo, so this can't test
  against the real fixture's actual encrypted value directly).
- `test_api_request.py` — locks in the pipe-string signature construction
  rules (sorting, array rendering, `None`-filtering) and the `reqcount`
  post-increment ordering that caused the original "session is finished"
  bug.
- `test_api_methods.py` — parses the real `room_list_response.json`
  fixture; specifically asserts `actualTemperature` is genuinely absent
  (not just `None`) on this hardware, guarding against reintroducing the
  `KeyError` crash that was hit in `climate.py` before it switched to
  `.get()`.
- `test_weather.py` — parses the real `weather_response.json` fixture;
  confirms `temperature`/`min`/`max` are floats on real hardware (relevant
  to the still-open question about decimal handling on
  `/api/room/settemperature`).
- `test_ping.py` — verifies the unauthenticated GET request is built
  correctly (no signature, no body, no auth headers) and confirms the
  response shape via `ping_response.json`. **Note this fixture's provenance
  differs from the others:** it is transcribed from the user's own
  pre-existing PDF documentation of their gateway, not live-captured in a
  chat session — see the fixture's own `_comment` and `scripts/
  manual_check_ping.py` for closing that gap with a fresh live capture.
- `test_device.py` — locks in the hub/sub-device identifier scheme
  (`gateway_device_info()` / `regler_device_info()`), specifically that
  `regler_device_info()`'s `via_device` actually matches
  `gateway_device_info()`'s own identifier — this is precisely the kind of
  mismatch that caused the original "two unrelated devices" bug, so it's
  asserted explicitly rather than just implicitly.
- **`scripts/test_scene_guards_local.py` (2026-09-09, extended to v3)** —
  NOT under `tests/`, deliberately: it lives alongside the other manual/
  diagnostic scripts in `scripts/` (so it's exempt from the `lint.yml`
  scope, same as its siblings) but unlike them, it needs **no gateway, no
  credentials, and no network at all** — it constructs `ApiMethods` with
  mocked credentials/`_request` and asserts purely at the Python level.
  Covers: `set_scene_rooms()` rejecting an empty list before any request
  is built; `set_scene()`'s room-assignment guard raising when
  `get_scene_rooms()` reports no rooms (and NOT raising when it does);
  all of `SCENE_APP_LIMITS`' min/max/raster cases across the timed
  scenes, both via the low-level `_validate_target_against_app_limits()`
  function directly and end-to-end through `set_scene(..., target=...)`;
  a case confirming `duration=` (the raw wire value) deliberately
  bypasses that validation; and, new as of `0.0.21`, a case confirming
  `set_scene("Leave", ..., target=...)` raises `NotImplementedError`, and
  a companion case confirming `duration=` still works normally for Leave
  (only the `target=` convenience path is blocked). Written specifically
  so the room-assignment guard can be regression-tested without
  repeating the live empty-list-hang incident documented above — run it
  with `python3 scripts/test_scene_guards_local.py`, no `.env`/
  credentials needed, safe to run anytime including in CI if that's ever
  set up for `scripts/`.

**When adding a new endpoint or fixing a parsing bug:** capture the real
request/response via the browser-console technique or a live debug-log
session, add it as a new fixture under `tests/fixtures/`, and add a test
that exercises the actual parsing code against it — this is the pattern to
follow going forward, not just for the crypto layer.

**Not covered by automated tests (HA-dependent, no test harness set up
yet):** `climate.py`, `sensor.py`, `binary_sensor.py`, `coordinator.py`,
`ping_coordinator.py`, `config_flow.py` (including `OptionsFlowHandler`),
`__init__.py`. These all import Home Assistant directly and would need
`pytest-homeassistant-custom-component` (already listed in
`requirements_test.txt` but not yet wired up with fixtures/conftest
support for it) to test properly. Until that harness exists, changes to
these files must be verified manually in the dev container — this is why
the "⚠️ Needs runtime verification" callout exists under "Integration
Architecture" above for the device-structure changes made in this round.

**Manual capture tool:** `scripts/manual_check_weather.py` is a one-off,
interactive diagnostic script (prompts for host/username/password via
`getpass`, never stores credentials) that logs in and pretty-prints a raw
endpoint response. It was used to capture `weather_response.json` before
`sensor.py` was written. This is the reusable pattern for any future
endpoint that needs a real fixture before entity code is written for it —
copy/adapt this script rather than guessing at a response shape from a
generic reference project. The `scripts/manual_probe_switchingtimes*.py`
family of scripts (2026-09-09) follows the same pattern for the
`get2`/`set2` investigation, and the `scripts/manual_probe_scene_duration*
.py` / `scripts/manual_probe_holiday_max.py` / `scripts/
manual_probe_set_scene_regression.py` / `scripts/
check_and_restore_boost_rooms.py` / `scripts/
manual_probe_leave_duration_contradiction.py` / `scripts/
manual_probe_leave_formula_ceiling.py` / `scripts/
manual_probe_leave_single_value.py` family (also 2026-09-09) follows it
for the `scene/set`/`scene/duration` investigation — see "Still untested /
open" above. Lessons from that round worth calling out explicitly for
future manual scripts: (1) **check `api_request.py`'s/`api_methods.py`'s
own documented gateway quirks (e.g. the empty-list hang) before writing a
test that deliberately exercises an edge case** — a diagnostic script
caused a live incident by not doing this (see the empty-list entry
above); (2) **when exploring an undocumented numeric range experimentally
(e.g. "how high can Holiday's duration go"), ask whether a known real-
world limit already exists (the app's own UI) before probing far beyond
it** — an earlier round of this same investigation tested up to 100 days
before the user pointed out the app's own limit is 30, which was
unnecessary reach for a question the user could have answered directly;
(3) **when testing multiple values of the same parameter in one script
run, isolate each measurement (poll-until-confirmed-inactive before each
send, not just a fixed sleep) or run them as fully separate script
invocations** — the Leave duration investigation initially produced
non-monotonic, unexplainable results from rapid back-to-back tests, and
even after isolating each test as its own script invocation with an
explicit inactive-confirmation pre-flight, the SAME non-monotonic pattern
reproduced - which turned out to be the more important lesson: don't
assume a surprising result is a testing-methodology artifact just because
one plausible artifact (timing) comes to mind, and don't stop
investigating once you've ruled out the first suspect if the data still
doesn't fit any model.

## Reverse-Engineering Method (for further, still-unknown endpoints)

1. Open the browser console at `http://<gateway-ip>/admin/dashboard/index`.
2. `CryptoJS` is already preloaded there and directly usable.
3. Key JS objects in the admin area (all successfully extracted via
   `.toString()` in the console — this is the technique that unlocked the
   whole protocol):
   - `request.hashAuthenticationToken` — login password hashing
   - `request.stringToCharcodes` — shared char-code pre-processing step
   - `request.encodeRequestSignature` — request signature (PBKDF2, not MD5)
   - `request.getRequestSignature` — builds the pipe-string, calls the above
   - `request.makeRequestData` — reqcount handling, session state assembly
   - `request.counter` — literal string, confirmed `"reqcount"`
   - `Crypt.pbkdf2` — the shared PBKDF2/SHA-512/Base64 primitive
   - `Crypt.aes256decrypt` — devicetoken decryption (confirmed correct)
   - `store.getJSON` / `store.set`
   - `admin.request`
4. **Most effective technique:** temporarily overwrite `admin.request` or
   `request.requestFor` on `window` (monkey-patching) to intercept requests
   and see the exact parameter formats before they go out.
5. `store` holds session state: `devicetoken`, `userid`, `udid`, `reqcount`,
   `ereqcount`.
6. **For endpoints exercised by the Smile mobile app rather than the
   browser admin console** (e.g. `switchingtimes/set2` — the schedule
   editor only exists in the phone app, not the admin dashboard, and this
   round's `scene/set`/`scene/duration` investigation similarly relied on
   the phone app rather than the admin console), the browser-console
   technique above doesn't apply. Instead: route the phone's WLAN traffic
   through a plain HTTP proxy (mitmproxy, run in its own container,
   listening on `0.0.0.0` so the phone can reach it over LAN) and capture
   the real request while manually performing the action in the app.
   Since the gateway itself is plain HTTP (not HTTPS), this needs no TLS
   root certificate on the phone for the gateway traffic itself — the
   request body is visible in plaintext. This is how the
   `switchingtimes/set2` wire format (see "Still untested / open" above)
   was finally confirmed, after several rounds of educated-guess `set2`
   calls were rejected by the gateway, and how the `scene/set`
   `duration`/app-vs-gateway-limits investigation (also above) was
   confirmed.

**Preferred interaction pattern:** produce self-contained JS code blocks for
manual paste into the browser console, rather than automated tab control —
browser MCP connections have been unreliable in the past.

**Validation principle:** every new piece of API behavior is verified live
against the gateway before it is adopted into the integration.

## Integration Architecture

### Physical model (see project discussion, confirmed against Honeywell's
own "Smile Connect System" documentation the user provided)

- **Smile Connect Gateway** — the single physical hub. Communicates with
  the heat generator, talks to the SDC Regler(s) over the "Smile Bus".
  Represents itself in HA as ONE top-level device, carrying everything
  that is not tied to a specific room: weather sensors, ping-based
  connectivity/response-time diagnostics.
- **SDC Regler** — one physical controller per room/zone, connected to the
  gateway via the Smile Bus (NOT a separate piece of hardware you'd buy
  independently — it's the in-room thermostat/regulator). Each one
  reported by `/api/room/list` becomes its own HA device, linked to the
  gateway device via `via_device` (hub/sub-device hierarchy, not two
  unrelated top-level devices — this was a real bug, see "Known Fixes"
  below).
- **Smile App** — Honeywell's own mobile UI. No HA equivalent; Home
  Assistant itself fills this role for this integration's purposes.
- **WLAN/LAN Router** — bauseitig (customer-provided), pure network
  transport. No HA equivalent.

### Known Fixes (device structure)

Two devices appeared where a clean hub/sub-device hierarchy was intended,
because `climate.py` and `sensor.py` each built their own ad-hoc
`device_info` dict independently, using different, uncoordinated
identifiers. Fixed by extracting **`device.py`** as the single source of
truth for both device shapes (`gateway_device_info()` /
`regler_device_info()`) - every platform must use these builders, never
construct a `device_info` dict inline.

### Module layout

- `custom_components/honeywell_smileconnect/api/` — pure protocol layer
  (login, requests, signing), no HA dependencies. Deliberately kept as a
  standalone, testable module (potentially extractable into its own PyPI
  package later, similar to `py-heatapp-de`, but under a new name to avoid
  any compatibility confusion).
  > **Historical note (2026-08-30):** `api_methods.py`/`api_request.py`
  > were originally named `apiMethods.py`/`apiRequest.py` (camelCase,
  > left over from the very first bootstrap). Renamed to snake_case for
  > PEP 8 / `ruff` `N999` compliance, once `lint.yml` CI was added and
  > flagged it. The `ApiMethods`/`ApiRequest` **class names** did NOT
  > change, only the file names and their import paths — if you ever see
  > a reference to `apiMethods.py`/`apiRequest.py` (e.g. in an old commit,
  > an old chat, or muscle memory), it means `api_methods.py`/
  > `api_request.py` now.
  - `crypto.py` — shared PBKDF2/SHA-512 primitives (`string_to_charcodes`,
    `pbkdf2_base64`), used by both `login.py` and `api_request.py`. Keep
    this the single source of truth for the crypto scheme — do not
    reimplement it inline elsewhere.
  - `login.py` — challenge/response login, password hashing, AES devicetoken
    decryption.
  - `api_request.py` — signs and executes authenticated requests.
  - `api_methods.py` — high-level per-endpoint methods. As of 2026-09-09,
    also owns `SCENE_MAX`, `FRACTION_DURATION_SCENES`,
    `RAW_DAYS_DURATION_SCENES`, `NO_DURATION_SCENES`, `SCENE_APP_LIMITS`,
    and `LEAVE_TARGET_UNSUPPORTED_MSG` — see "Still untested / open" above
    for what each encodes and why. `set_scene_rooms()` and `set_scene()`
    both carry live-confirmed guard clauses (empty room list; no rooms
    assigned; Leave's `target=` block) — see the same section.
  - `scene_manager.py` — add/remove a room from a scene (handles the
    getrooms/setrooms/set sequencing). **Holiday's
    `SCENE_ACTIVATION_DURATION` entry needs fixing** per the re-opened
    item above (still outstanding as of `0.0.21`) — Leave's entry is
    confirmed correct as-is and should NOT be touched.
  - `credentials.py` — session state, including `reqcount` with correct
    post-increment semantics (see reqcount section above).
  - `ping.py` — **deliberately separate** from everything above: a plain,
    unauthenticated `GET /api/ping`, no Login/Credentials/signing
    involved at all. The entire point of this endpoint is to work when
    authentication is broken - it must never gain a dependency on
    authenticated session state.
- `coordinator.py` — `SmileConnectCoordinator` (`DataUpdateCoordinator`),
  polls room list, weather, AND per-scene room membership in a single
  cycle (one shared, already-logged-in session). `coordinator.data` is
  `{"rooms": [...], "weather": {...}, "scene_active_rooms": {scene_name:
  {room_id, ...}, ...}}` — the third field (added 0.0.18) is the ground-
  truth read by `climate.py`'s `hvac_mode`/`preset_mode` instead of
  `roomstatus` (see `climate.py`'s own bullet below and `docs/
  protocol.md` §4e/§4f).
- `ping_coordinator.py` — `SmileConnectPingCoordinator`, a **second,
  fully independent** `DataUpdateCoordinator` that only polls `/api/ping`.
  Deliberately does not share any state, session, or failure mode with
  `SmileConnectCoordinator` — a broken login must never make the
  connectivity sensor look wrong, and vice versa. Has its own configurable
  poll interval (`CONF_PING_INTERVAL`, default 15s — see const.py; the
  gateway's own internet-facing heartbeat is documented at ~90s, but this
  local, unauthenticated, lightweight call is a different use case and
  intentionally more responsive by default).
- `device.py` — shared `device_info` builders (`gateway_device_info()`,
  `regler_device_info()`). Single source of truth for the hub/sub-device
  hierarchy described above — see "Known Fixes".
- `climate.py` — one `ClimateEntity` per room/regler. **`hvac_mode`
  (AUTO/OFF) and `preset_mode` (Boost/Party/Leave/Holiday/none) are
  deliberately independent of each other** — see `docs/protocol.md` §4c
  for the full "what Standby actually means" explanation from the user.
  `hvac_mode` is driven exclusively by the `Standby` scene (`OFF` =
  schedule ignored/heating off; `AUTO` = following the per-room schedule
  to its programmed setpoint — there is no `HEAT` mode, since there's no
  "hold a fixed setpoint, ignore the schedule" concept here, and frost
  protection is always enforced by the regler itself, uncontrollable via
  the gateway). **As of 0.0.18, neither `hvac_mode` nor `preset_mode`
  reads `roomstatus` at all** — both read `coordinator.data[
  "scene_active_rooms"]` (ground-truth per-scene room membership from
  `/api/scene/status`+`/api/scene/getrooms`, fetched by `coordinator.py`
  every poll cycle) instead, since `roomstatus` cannot be trusted for
  compound states (a still-active Standby can be masked by a preset; see
  `docs/protocol.md` §4e/§4f for the full story, including a genuine
  Holiday+Standby gateway firmware quirk this sidesteps entirely rather
  than working around). `preset_mode` includes `PRESET_NONE` ("none") in
  `preset_modes` (added 0.0.18, after live use showed the earlier "no
  explicit none entry" design left no way to clear a preset from the HA
  UI without picking a different one) — `Standby` itself remains
  intentionally NOT a preset, still exclusively an `hvac_mode` toggle.
  Shower/Towel are not wired up at all (no test hardware available), but
  the protocol constants for them are kept in place. Field access uses
  `.get()` defensively since not all fields (e.g. `actualTemperature`) are
  guaranteed present on every installation. Uses `has_entity_name = True`
  + a `translation_key` so the entity's display name combines its
  device's name with a translated "Thermostat" label (see `const.py`'s
  own comment on this choice).
- `sensor.py` — two entity groups, both attached to the **gateway**
  device via `device.gateway_device_info()`:
  - Weather: outside temperature/min/max, sourced from
    `coordinator.data["weather"]` (fed by the main, authenticated
    coordinator). One parameterized `SmileConnectWeatherSensor` class
    covers all three.
  - Diagnostics: `SmileConnectPingResponseTimeSensor`
    (`entity_category = DIAGNOSTIC`), fed by `SmileConnectPingCoordinator`
    instead — reports the gateway's own `"performance"` field from
    `/api/ping`.
- `binary_sensor.py` — `SmileConnectConnectivitySensor`
  (`device_class = CONNECTIVITY`, `entity_category = DIAGNOSTIC`), also on
  the gateway device, fed by `SmileConnectPingCoordinator`. `uniqueid`,
  `configured`, `remoteAddress` from the raw ping response are exposed as
  `extra_state_attributes` rather than separate entities (deliberate
  granularity decision from project discussion: 2 entities +
  attributes, not N entities for every ping field).
- `config_flow.py` — host/user/password + two poll intervals
  (`CONF_INTERVAL`, `CONF_PING_INTERVAL`), validated via an actual login
  attempt against the gateway. Also opportunistically calls `/api/ping`
  during setup to capture the gateway's own `"uniqueid"` and registers it
  as this entry's **native HA `unique_id`** via
  `async_set_unique_id()` + `_abort_if_unique_id_configured()` (falls back
  to a host-based id if ping fails during setup) — this also makes the
  pre-existing `"already_configured"` abort string, which used to be dead
  code, actually functional. Also implements `OptionsFlowHandler` so
  host/credentials/both intervals can be changed after initial setup
  without recreating the entry (and therefore without losing the
  `unique_id`-based device identity).
- `__init__.py` — creates and owns BOTH coordinators, wraps them plus the
  entry's `unique_id` in a small `SmileConnectData` dataclass stored in
  `hass.data[DOMAIN][entry_id]`. Every platform reads from that dataclass,
  not from a bare coordinator reference.

### ⚠️ Needs runtime verification (not yet confirmed against a real HA
install, since this was implemented without live HA available)

- `EntityCategory` is imported from `homeassistant.const` in `sensor.py`
  and `binary_sensor.py`. This is believed correct for current HA versions
  but was not confirmed by actually running the integration - if you hit
  an `ImportError` here, check whether your HA version instead expects
  `from homeassistant.helpers.entity import EntityCategory` and fix at
  that single point (both files import from the same place).
- The `OptionsFlowHandler` deliberately does NOT define `__init__` /
  assign `self.config_entry` manually, relying on the base `OptionsFlow`
  class providing `self.config_entry` automatically (current recommended
  pattern, older manual-assignment pattern is deprecated). Confirm this
  works as expected on first use of the options flow in the dev container.
- `suggested_area` in `device.regler_device_info()` has not yet been
  confirmed to actually trigger HA's area-suggestion UI on first device
  creation — verify by deleting and re-adding the integration and checking
  whether the regler device gets an area suggestion matching the room name.

## Development Workflow

- Dev container with Home Assistant Core in debug mode, `custom_components`
  live-mounted (see `.devcontainer/devcontainer.json`).
- Claude Code runs in the container terminal and automatically has access to
  this file plus the full codebase — no more manually copying context out of
  the original chat/project.
- For live tests against the real gateway: do NOT commit credentials to any
  file — provide them via `.env` (see `.env.example`) or environment
  variables inside the dev container.
- CI (GitHub Actions) validates on every push via `hassfest` and
  `hacs/action` that the manifest/repo stays HACS-compliant.
  `script.hassfest` cannot be run locally in this dev container — it only
  exists inside a full `home-assistant/core` checkout, not in the
  `homeassistant` PyPI package installed here. Rely on the GitHub Action
  (`.github/workflows/validate.yml`) after pushing instead of trying to
  invoke it locally.
- A separate `.github/workflows/lint.yml` runs `ruff check` against
  `custom_components/` only on every push/PR — deliberately scoped to just
  the actually-shipped integration code, not `tests/` or `scripts/` (those
  are dev-only helpers never loaded by Home Assistant or checked by
  `hassfest`/HACS, and the manual diagnostic scripts in particular use a
  deliberately loose style — broad `except Exception`, interactive
  prompts — that isn't worth linting for a HACS integration). This CAN and
  SHOULD be run locally before pushing — `pip install ruff && ruff check
  custom_components/` — since `ruff` has no dependency on a full Home
  Assistant checkout.
- **`BLE001` ("do not catch blind exception") is genuinely active** in
  ruff's default rule set — a broad `except Exception` is only flagged
  when it is the SOLE handler in its `try` (no more specific `except`
  before it) AND does not re-raise. Broad excepts that re-raise a more
  specific exception, or that follow other specific `except` clauses in
  the same `try`, are correctly left unflagged. `config_flow.py`'s
  `validate_input()` ping-fallback is the one deliberate exception to
  this in the codebase — a standalone broad catch, by design, so setup
  never blocks just because the diagnostic `/api/ping` call had a
  hiccup — and carries a justified `# noqa: BLE001` for exactly that
  reason. Don't remove it, and don't add new bare `# noqa: BLE001`
  comments elsewhere without first checking whether `ruff` actually
  flags that specific line (most won't need one).

## Conventions

- Domain: `honeywell_smileconnect`
- All new symbols (classes, constants) use `SmileConnect` or
  `honeywell_smileconnect` prefixes — no leftover `heatapp` naming in new
  code.
- Commit messages in English; docs/comments should also default to English
  for upstream compatibility (HA contributions).

## Code Standards (mandatory for all code in this repo)

- **Follow official Home Assistant custom integration standards and HACS
  standards** at all times (entity naming, config flow patterns, unique IDs,
  device registry usage, `manifest.json` requirements, `hacs.json`
  requirements, etc.). When in doubt, check the current Home Assistant
  developer docs and HACS publishing requirements rather than guessing.
- **All identifiers in code are in English** — variable names, function
  names, class names, constants, file names. No German (or any other
  non-English language) in code identifiers, regardless of what language
  the surrounding chat/discussion happens to be in.
- **Code must be adequately commented in English.** Non-obvious logic,
  protocol quirks, and anything a future reader (human or Claude) would
  need to understand without re-deriving it from scratch must have an
  English comment explaining it. This project's cryptography section above
  is the model to follow: explain the "why", not just the "what".
- **Every changed module must carry a change-log comment at the top of the
  file** documenting what changed and why, so changes remain traceable over
  time without needing to dig through git blame. Add a new entry rather
  than replacing prior ones. A simple format is sufficient, e.g.:
  ```python
  # Change log:
  # - 2026-08-27: Fixed request signature to use PBKDF2/SHA-512 instead of
  #   MD5 (confirmed against gateway JS). See CLAUDE.md for details.
  # - 2026-08-20: Initial implementation (untested crypto assumptions).
  ```
  This applies to any file being modified, not just newly created ones —
  when editing an existing file that doesn't yet have a change-log block,
  add one and backfill at least the current change.
  **Exception:** JSON files (`manifest.json`, `hacs.json`,
  `translations/*.json`) have no comment syntax, so this rule cannot apply
  to them literally. For those, the version bump (see "Versioning &
  Branching Strategy" below) plus the commit message serve as the
  traceability mechanism instead.

## Localization (GUI-facing strings)

- **Never hardcode end-user-facing text.** Any label, error message, form
  field name, or other string that appears in the Home Assistant UI must go
  through Home Assistant's standard localization mechanism (the
  `strings.json` / `translations/<lang>.json` pattern used by
  `config_flow.py`, entity names, etc.) so it is translatable — never
  hardcoded English (or German) strings directly in Python logic that
  reaches the UI.
- **Minimum supported languages: English, German, Spanish, French.** Every
  user-facing string added or changed must have translations added for at
  least `en`, `de`, `es`, and `fr` under
  `custom_components/honeywell_smileconnect/translations/`. Additional
  languages are welcome but these four are the floor, not the ceiling.
- Code identifiers themselves (see Code Standards above) stay in English
  regardless of this — localization applies only to strings actually
  rendered to the end user, not to internal naming.
- **Current status (as of 2026-08-27):** `en`, `de`, `es`, `fr` are all
  present under `custom_components/honeywell_smileconnect/translations/`,
  covering both the config flow strings and the `sensor.py` entity names
  (`entity.sensor.*`). `strings.json` at the component root mirrors the
  English translation as the source-of-truth file per current HA
  convention — keep both in sync when English strings change (the
  `translations/en.json` copy exists for compatibility with tooling that
  still expects it there).

## Session Workflow (applies to every new chat/session on this project)

These rules govern how any assistant (Claude in chat, or Claude Code)
should operate at the start of, and during, a work session on this repo —
because each session typically results in changes to the main codebase and
must not proceed carelessly.

1. **At the start of every new chat/session in this project, read the
   underlying GitHub repository first**, not just this file from memory.
   `CLAUDE.md` reflects the state as of its last edit, but the actual repo
   may have moved on since (other commits, manual edits, a previous
   session's uncommitted work). Check the current state of the relevant
   files before assuming anything about them.
2. **Explicitly check for updated files in the GitHub repository at the
   start of each new chat/session** — don't rely solely on what's described
   in this document or in prior chat history. Verify against the actual
   current file contents.
3. **Before making any code change for a new feature, propose a plan
   first** and get it confirmed before touching code.
4. **Before making any code change for a bugfix, propose a plan first** and
   get it confirmed before touching code.
5. **For every change (feature or bugfix), propose one or more solution
   options and explicitly ask which option to implement** before writing
   code — do not silently pick one approach and implement it. This applies
   even when only one option seems reasonable; state it as a proposal and
   wait for confirmation rather than assuming approval.
6. **After completing a feature, bugfix, or release in a given chat/session,
   always**:
   - Remind the person to check that the `hassfest` GitHub Action passes
     after pushing — **not** to run `python3 -m script.hassfest` locally.
     `script.hassfest` only exists inside a full `home-assistant/core` git
     checkout, not in the `homeassistant` PyPI package this project's dev
     container installs, so it is not available locally without cloning
     all of `home-assistant/core` separately (impractical for routine use).
     The repo's `.github/workflows/validate.yml` already runs the
     equivalent `home-assistant/actions/hassfest` action on every push —
     check the "Actions" tab on GitHub after pushing instead. This was
     confirmed the hard way during development (`ModuleNotFoundError: No
     module named 'script'` when attempted inside the dev container).
   - Propose an English-language commit message summarizing the change.

## Versioning & Branching Strategy

- The integration's version follows `x.y.z` (see `manifest.json`
  `"version"` field).
- **From version `x.1.y` onward, the codebase is considered to be in beta
  status.** Once beta status is reached, direct development on `main` is no
  longer permitted. All further feature work and bugfixes must happen on a
  dedicated feature or bugfix branch and be merged via pull request rather
  than committed straight to `main`.
- Before beta status (i.e. `x.0.y`), direct commits to `main` are
  acceptable for rapid early-stage iteration, as has been the practice so
  far in this project.
- **Current version: `0.0.21`** (bumped 2026-09-09, patch-only — still
  `0.0.x`, so this is a normal direct-to-`main` release per the rule
  above, not an exception to it). Fixes a real defect introduced in
  `0.0.20`: `set_scene()`'s new `target=` parameter assumed Leave shares
  Party's fraction-of-scene_max formula (same `scene_max=12h` in
  `scene/status`) — live re-testing this session disproved that
  assumption. A wider sweep of raw `duration=` values (`1,3,5,6,8`)
  produced non-monotonic results (`1→12h, 2→6h, 3→12h, 4→12h, 5→6h,
  6→12h, 8→5h`) that fit no tested model, confirmed via a fully isolated
  methodology (poll-until-confirmed-inactive before each send, ruling out
  a request-timing/race-condition explanation). Contents of `0.0.21`:
  - `set_scene()` now raises `NotImplementedError` for Leave's `target=`
    parameter instead of silently using an unverified formula, with a
    clear explanatory message (`LEAVE_TARGET_UNSUPPORTED_MSG`).
    `duration=` (the raw wire value) is unaffected — Leave still works
    fine with a known-good raw value like `2` (confirmed live to produce
    ~6h).
  - `scripts/test_scene_guards_local.py` gained two new cases: the
    `NotImplementedError` for Leave's `target=`, and a confirmation that
    `duration=` still works normally for Leave.
  - `CLAUDE.md` and `docs/protocol.md` §4d updated to reflect the
    corrected, honest state — Leave's original `2→6h` factor-of-3
    measurement from `0.0.18`'s investigation turned out to be accurate
    for that specific value, but does NOT generalize into a usable
    formula; Holiday's `SCENE_ACTIVATION_DURATION` entry is still
    outstanding and NOT fixed by this release (see "Next planned work").
  - Party/Boost/Holiday's `0.0.20` behavior is unaffected by this
    release — their formulas were independently re-confirmed via
    multiple isolated live tests each, not implicated by the Leave
    finding.
  - No new user-facing HA feature — same category as `0.0.19`/`0.0.20`.
- **The next round of work — the HA Action/service for setting mode,
  preset, temperature, and switching times (see "Next planned work"
  above) — starts the initial `0.1.x` release.** Per the beta-status rule
  above, this means: do NOT commit it directly to `main`. Create a
  dedicated feature branch first (e.g. `feature/ha-actions`), do the work
  there, and merge via pull request once ready. This also means the
  README status badge (see below) must be updated from pre-alpha to beta
  as part of that work, not before.
- When proposing a plan (per the Session Workflow rules above), also
  propose the appropriate version bump and, once beta status applies,
  the branch name to use.
- **README badge maintenance:** `README.md`'s badge row includes a static
  `version-x.y.z` badge (not auto-updating) and a `status-pre--alpha`/
  `status-beta` badge reflecting the tier above. Whenever `manifest.json`'s
  `version` is bumped, update the version badge to match in the same
  commit; whenever the project actually transitions from pre-alpha to
  beta status, update the status badge's text/color/link accordingly
  (e.g. to `status-beta-yellow.svg` or similar) rather than leaving it
  saying "pre-alpha" past that point.