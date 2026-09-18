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
  - ~~**Leave `2`→6h — RE-TESTED LIVE (2026-09-09) AND CONFIRMED
    CORRECT.**~~ **SUPERSEDED (2026-09-11) — see the dedicated resolution
    entry below.** A wider sweep of send-values (`1,3,5,6,8`) around this
    had revealed non-monotonic results that fit no tested model at the
    time, leading `0.0.21` to block `set_scene()`'s `target=` for Leave
    entirely rather than expose an incomplete formula. That sweep's real
    problem, found 2026-09-11, was that every one of those sent values is
    outside the gateway's actual `[0,1]` input domain for this scene —
    Leave uses the exact same fraction-of-scene_max formula as Party, no
    separate mechanism. `target=` is unblocked for Leave and
    `SCENE_ACTIVATION_DURATION["Leave"]` was corrected from `2` (an
    out-of-domain value that happened to work, but was never understood)
    to `0.5` (the correct, now-understood in-domain fraction for 6h).
  - ~~**Holiday `0.5`→15d does NOT match, and this part of the table is
    genuinely WRONG — still needs correcting in `const.py`.**~~ **FIXED
    (2026-09-10, shipped in `0.1.0`)** — see the dedicated "Top priority"
    entry elsewhere in this file. Holiday is confirmed (2026-09-09, see
    below) to send RAW DAYS, not a fraction — sending `15` is echoed back
    as `~15`, and `100` is echoed back as `~100` (gateway does not clamp
    Holiday at all, unlike Boost/Party). `SCENE_ACTIVATION_DURATION[
    "Holiday"]` was `0.5` (0.5 days = 12h, not the intended 15 days) and
    is now `15`.
  **Status as of 2026-09-11:** both contradictions in this table are now
  resolved — Holiday's value was fixed in `0.1.0` (2026-09-10), Leave's
  in `0.1.0` (2026-09-11, this session) after finding its formula is
  identical to Party's once tested with valid in-domain input (see the
  dedicated resolution entry below).
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
  - ~~**Leave — read-side confirmed, write-side UNRESOLVED.**~~
    **WRITE-SIDE RESOLVED (2026-09-11).** `Leave`'s READ interpretation
    via `get_scene_duration()` matches the same fraction-of-
    `scene_max=12h` model as Party (`0.5` raw → `6h`, `1.0` raw → `12h`),
    so `SCENE_MAX["Leave"] = 12` remains correct for reading. The WRITE
    side originally appeared NOT to follow "send the fraction directly"
    like Party/Boost — extensive live testing on 2026-09-09 found that
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
    like Party/Boost, or simple modular arithmetic) fit the full 7-point
    set at the time. **Root cause found 2026-09-11: every one of those
    sent values (`1` through `8`) is OUTSIDE the `[0,1]` fraction domain
    the gateway/app actually use for Leave** — the real Smile App's own
    duration slider never sends a raw value above `1`; the 2026-09-09
    sweep was testing genuinely out-of-spec input, which hits an
    unspecified/inconsistent gateway firmware code path (hence the
    non-monotonic table), not evidence of a separate Leave-specific
    formula. Confirmed via (1) a live mitmproxy capture of the real
    Smile App activating Leave six times
    (`scripts/mitm_scene_capture.py`), showing the app sends "noisy"
    in-domain fractions (e.g. `0.06996047`) that the gateway rounds to
    the nearest `1/12` step on receipt (`round(sent×12)/12` reproduces
    every readback exactly — a UI-slider-precision artifact, not a
    clock/session-time effect), and (2) a direct confirmation via this
    project's own `ApiMethods.set_scene(duration=0.41666667)` (5/12,
    "5 hours") returning exactly `5.00h` back. **`set_scene()`'s
    `target=` parameter is no longer blocked for Leave** — it now uses
    the identical `FRACTION_DURATION_SCENES` code path as Party/Boost,
    with the `NotImplementedError` special-case removed entirely. Full
    writeup: `docs/protocol.md` §4d, `api_methods.py`'s and `const.py`'s
    change logs.
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
    too, and is now a normal, reachable path since `target=` is no
    longer blocked for Leave (see resolution above).
  - **`api_methods.py` now bakes all of this in directly:** `SCENE_MAX`,
    `FRACTION_DURATION_SCENES`, `RAW_DAYS_DURATION_SCENES`,
    `NO_DURATION_SCENES`, and `SCENE_APP_LIMITS` module-level constants;
    `set_scene()` gained a `target` parameter (real-world unit —
    minutes/hours/days as appropriate) that validates against
    `SCENE_APP_LIMITS` (raises `ValueError` outside the app's own
    min/max/raster), then converts correctly per scene (Leave included,
    as of 2026-09-11 — see resolution above) — while the pre-existing
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

- ~~**TOP PRIORITY (decided 2026-09-17, explicitly ordered before the
  switching-times phase 2 bullet below): investigate how to write
  `desiredTempDay`/`desiredTempDay2`/`desiredTempNight`**~~ **RESOLVED
  (2026-09-18, branch `feature/desired-temperatures`).** Live mitmproxy
  capture of the real Smile App (`scripts/mitm_desired_temp_capture.py` —
  new, generalized from `mitm_scene_capture.py` to scope ALL `/api/`
  traffic rather than one endpoint family, since the target endpoint was
  genuinely unknown up front) while the user set H/L/N for one room
  several times, cross-referenced against `/api/room/list` reads taken
  immediately after each write — not just trusting `success:true`.
  **Confirmed: no separate endpoint needed.** `/api/room/settemperature` —
  the exact same endpoint `set_temperature()` already uses with
  `change_mode=0` for the live setpoint — also accepts `change_mode=1`
  (→ `desiredTempNight`), `change_mode=2` (→ `desiredTempDay`), and
  `change_mode=3` (→ `desiredTempDay2`). **Also discovered along the way:
  the gateway floors the sent value to the nearest lower 0.5 °C step**
  (`stored = floor(sent / 0.5) * 0.5`) for all three modes — confirmed
  across 5 separate writes, each verified via a fresh `room/list` read
  matching the formula exactly. This rounding behavior was never
  previously characterized for this endpoint (the existing `change_mode=0`
  decimal-value confirmation from 2026-08-30 only ever tested `24.5`, a
  value already on the 0.5 grid, so it couldn't have revealed flooring
  either way — whether `change_mode=0` floors identically on an off-grid
  input remains unconfirmed). Full evidence table and methodology in
  `docs/protocol.md` §4g — do not re-derive this from scratch.
  **Not yet implemented in `api_methods.py` or exposed to HA** — this
  session only confirmed the protocol; see the new bullet directly below
  for the implementation, which still needs its own plan/options
  discussion per the Session Workflow rules before any code is written.
  Rationale for prioritizing this investigation before the native-helper/
  auto-sync phase further below stands as originally stated: the schedule
  Actions shipped in `0.2.0` are already live-verified and usable
  end-to-end for H/L selection, but the underlying temperatures those
  types actually apply couldn't be managed from HA at all until this gap
  closes.
- ~~**Implement write support for `desiredTempDay`/`desiredTempDay2`/
  `desiredTempNight` in `api_methods.py` and expose it to HA**~~ **DONE
  (2026-09-18, shipped in `0.3.0`)** — see the `0.3.0` entry in
  "Versioning & Branching Strategy" below for the design decisions taken
  (dedicated method, new Action with explicit `type`, per-room sliders).
- **Switching-times phase 2: native `schedule.*` helper UI + automatic
  Gateway↔HA sync** (deferred during planning for `0.2.0`, 2026-09-17 —
  see that version's changelog entry above for what phase 1 shipped
  instead). Phase 1 covers the raw read/write Action layer only; this
  phase would add:
  - A native HA "Schedule" helper per room as the visual weekly-plan
    input surface, with the H/L type tagged via each block's `data` field
    (a real HA core feature since 2024.10 — `CONF_DATA` in
    `homeassistant.components.schedule`) — **confirmed during planning
    that the stock visual block editor does NOT expose this field**; the
    user would need to use the helper's "Edit in YAML" advanced view to
    set it. Options-flow work needed: a room ↔ `schedule.*` entity
    mapping step (`EntitySelector(domain="schedule")`), which also
    requires fixing a **real latent bug** found in `config_flow.py`
    during planning: `OptionsFlowHandler.async_step_init()` currently
    calls `async_create_entry(title="", data=user_input)` — this
    OVERWRITES the entire options dict with only the connection-settings
    fields, so adding a second options category (the schedule mapping)
    without also fixing this would silently wipe it out the next time
    someone just changes the poll interval. Must become
    `data={**dict(self.config_entry.options), **user_input}` (merge, not
    replace) in both the existing step and any new one.
  - Automatic HA→Gateway sync on every coordinator poll cycle, with
    conflict detection: only auto-push when the HA-side schedule changed
    since our own last push; if the gateway itself differs from what we
    last pushed (i.e. the user edited via the Smile App), do NOT
    overwrite it — surface a "diverged" diagnostic sensor instead. This
    guards against the failure mode where a naive unconditional push
    would clobber a fresh App-made change with a stale HA copy.
  - **Confirmed during planning: true automatic Gateway→HA sync (writing
    a detected App-side change back into the native helper) is NOT
    achievable at all**, regardless of implementation language (Python,
    HA script, or automation) — `homeassistant/components/schedule/
    __init__.py` exposes no public service or `hass.data` entry for its
    `ScheduleStorageCollection`; the only mutation path is a private,
    frontend-only WebSocket route. The realistic fallback is a generated
    YAML snippet (from the gateway's current schedule) for the user to
    manually paste into the helper's YAML view — this is a deliberate
    design decision given HA's actual capabilities, not a shortcut taken
    for lack of time; do not re-attempt a `.storage/schedule` direct-write
    hack (unsupported, can break across HA versions, races the running
    component's in-memory state).
  - `desiredTempDay`/`desiredTempDay2`/`desiredTempNight` write support
    (see the "TOP PRIORITY" bullet directly above - unrelated to whether
    this phase happens, but relevant if a per-block temperature is ever
    wanted beyond the H/L type selection phase 1 already supports).
  - `"N"` (Night) type support, if `"N"` is ever needed and if actual
    Honeywell Room Connect SRC-10 hardware becomes available to verify
    against — `switching_times.py` deliberately rejects `"N"` today (see
    `VALID_TYPES`) rather than guessing at unverified behavior.
- **No automatic re-login on session failure — coordinator gets
  permanently stuck "unavailable" until HA restart/integration reload**
  (found 2026-09-11, while investigating a user question about
  `reqcount` overflow behavior - see `api/credentials.py`'s
  `next_reqcount()`). Root cause, confirmed by reading the code (not
  just assumed, correcting a previously-wrong note in this file - see
  the struck-through "Reconnect/error handling strategy" entry above):
  `SmileConnectCoordinator._async_update_data()` only calls
  `async_login()` when `self.api is None`, which is only ever true
  once - the very first update after HA (re)starts. Any later session
  invalidation (gateway reboot, prolonged network loss, or anything else
  that makes the gateway reject the current devicetoken/session) causes
  every subsequent poll to fail with `UpdateFailed` forever, since
  nothing ever resets `self.api` back to `None` to trigger a fresh
  login. `reqcount` itself is a plain Python `int` (`Credentials.
  reqcount`, reset to `0` only on a fresh login) - no client-side
  overflow is possible, and at this project's default 30s poll interval
  (~4 authenticated calls/cycle, ~4 million/year of continuous uptime)
  even a 32-bit counter on the gateway's own side would take on the
  order of centuries to overflow if it exists at all - genuinely
  untested/unknown whether the gateway enforces any limit, but not
  considered the practical risk here. **The permanent-stuck-unavailable
  failure mode is the real, already-confirmed problem, independent of
  whether `reqcount` overflow is ever actually involved.** Proposed fix
  (not yet designed in detail): on catching the exception in
  `_async_update_data()`'s `try`/`except`, reset `self.api = None`
  (forcing a re-login on the next cycle) before raising `UpdateFailed`,
  or add an explicit retry-with-relogin path. Needs a plan session
  before implementing (project workflow rule) - in particular, decide
  whether to blindly re-login on EVERY failure (simple, but would also
  re-login on unrelated transient errors, e.g. a single dropped request)
  or only after distinguishing session-loss from other failure types
  (more correct, more code, and no confirmed way yet to tell them apart
  from the gateway's own error responses - would need live testing,
  e.g. forcing a gateway reboot mid-session and inspecting exactly what
  error comes back).
- **HACS-appropriate `README.md` rewrite, documenting the integration and
  its features properly** (agreed 2026-09-11, explicitly deferred to a
  separate session/PR - not part of the `set_hvac_mode_and_temperature`
  work it was raised alongside). Should cover, at minimum: the two custom
  Actions (`set_preset_mode_with_duration`,
  `set_hvac_mode_and_temperature`) and when to use each instead of the
  standard `climate.*` services; the known `climate.set_temperature`
  combined-call limitation (see the 2026-09-11 addendum #3 entry below);
  the hub/sub-device model (gateway + per-room SDC Regler); supported
  presets/scenes; the disclaimer/trademark language already established
  in this file's own header. Check current HACS README requirements
  before writing it (badges, structure) rather than assuming the
  existing README's shape is still sufficient.
- ~~**Top priority, before anything else touching scene activation:**
  `const.SCENE_ACTIVATION_DURATION["Holiday"]` (currently `0.5`, intended
  to mean 15 days) is very likely WRONG given Holiday's confirmed
  raw-days write formula — `0.5` would actually set 0.5 days (12 hours),
  not 15 days.~~ **FIXED (2026-09-10, shipped in `0.1.0`).** Changed to
  `15` (raw days) in `const.py`, alongside the `set_preset_mode_with_duration`
  HA Action work below, which touches exactly this code path. See
  `const.py`'s own change log. (Leave's equivalent contradiction — see
  "Still untested / open" above — is now ALSO fully resolved, 2026-09-11:
  Leave's write-side formula turned out to be identical to Party/Boost's,
  and `target=` is unblocked for it in `0.1.0`.)
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
  ~~Still not wired to anything in the HA integration layer~~ **WIRED
  (2026-09-17, `0.2.0`, `feature/switching-times`)** — see the
  "Versioning & Branching Strategy" `0.2.0` entry below for the full
  writeup: three new entity Actions (`get_schedule_room`/
  `set_schedule_room`/`set_schedule_room_weekday`) on `climate.py`, backed
  by the new `switching_times.py` conversion module.
- **Home Assistant Action (service) to let automations set hvac mode,
  preset, thermostat temperature, and switching times.** **PARTIALLY
  SHIPPED (2026-09-10, `0.1.0`, on `feature/ha-actions` — starts the
  `0.1.x` beta line, see "Versioning & Branching Strategy" below).**
  Design review during planning established that `hvac_mode`, `preset_mode`,
  and target temperature are already fully controllable today via the
  standard `ClimateEntity` overrides in `climate.py` — the generic
  `climate.set_hvac_mode`/`climate.set_preset_mode`/`climate.set_temperature`
  HA services already work per-entity, no custom Action needed for those
  three verbs. The one genuinely new capability the API layer supported
  but nothing exposed was a **per-activation custom preset duration**
  (`ApiMethods.set_scene()`'s `target=`/`duration=`, previously only
  reachable with the hardcoded `SCENE_ACTIVATION_DURATION` default via
  `SceneManager.add_member_to_scene()`). Shipped as the new
  **`honeywell_smileconnect.set_preset_mode_with_duration`** entity Action
  (`climate.py`, registered via `entity_platform.async_register_entity_service()`;
  schema/labels in `services.yaml` + `strings.json`/`translations/*.json`),
  backed by `SceneManager.add_member_to_scene()`'s new optional
  `target=`/`duration=` parameters (`api/scene_manager.py`) — see both
  files' change logs. **Still open / deferred, NOT part of this shipped
  piece:** a dedicated `hvac_mode`/temperature Action (deliberately
  skipped — would just duplicate the standard `climate.*` services with no
  new capability) and a switching-times Action (needs its own design pass
  for how the raw slot list should be shaped for a service call — see the
  bullet above this one; `get_switching_times`/`set_switching_times` in
  `api/api_methods.py` remain unwired to anything HA-facing).
- ~~**Possible future gateway-attached entities from `/api/weather`'s
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
  multi-regler one).~~ **The "kept on the gateway to avoid ambiguity"
  part is SUPERSEDED (2026-09-15, shipped in `0.1.1`)** — see the
  dedicated entry directly below for the actual fix. The `iconUrl`/
  `forlocation` observation is unaffected and still stands: they are
  genuinely internet-weather-service data the *gateway* itself fetches
  (not a regler-side physical measurement), so if/when these are ever
  turned into entities, they belong on the **gateway** device, not the
  regler — still not implemented, no change needed here.
- **Outside temperature/min/max sensors moved from the gateway device to
  the sole Regler's device, on single-room installations only (2026-09-15,
  shipped in `0.1.1`, branch `feature/regler-weather-sensors`).** The user
  confirmed the real hardware physically contradicts the original
  gateway-attachment decision above: the outside-temperature sensor is
  wired directly to the Regler ("Smile Controller"), which uses it locally
  for its own weather-compensated control logic (e.g. lowering the
  setpoint when it's warmer outside) — the gateway only relays the
  already-measured value via `/api/weather`. Fixed in `sensor.py`:
  `SmileConnectWeatherSensor` now takes optional `room_id`/`room_name`,
  and `async_setup_entry` passes the sole room's id/name only when
  exactly one room is reported, so `device_info` resolves to
  `device.regler_device_info(...)` in that case.
  **Multi-room (SRC-10 present) case deliberately left unresolved, NOT
  fixed by this change:** discussed with the user — the optional SRC-10
  add-on module (single-room control for up to 16 additional rooms) adds
  its own room controllers *on top of* the SCN-10's always-present base
  Regler, rather than replacing it. This makes it plausible (but **NOT
  confirmed — no SRC-10 hardware available to test**) that the base
  Regler stays the first room reported by `/api/room/list` even with an
  SRC-10 installed. Rather than guess, `sensor.py` leaves the 3 weather
  sensors on the gateway device whenever 2+ rooms are reported — exactly
  the pre-`0.1.1` behavior, left unchanged rather than risking a wrong
  guess for an untested configuration. `unique_id` for the 3 sensors was
  deliberately left unchanged (no room segment) — only 3 instances are
  ever created regardless of room count, so there's no collision risk,
  and the entity registry's `unique_id` → device link updates
  transparently in HA with no migration step needed; existing users only
  see the entities' friendly name change (via `has_entity_name`, from
  "Smile Connect Gateway <X>" to "<Room name> <X>") and move to the
  Regler device. **Revisit the multi-room case once real SRC-10 hardware
  is available to test against**, rather than guessing — see
  `sensor.py`'s own change log for the exact reasoning to avoid
  re-deriving it from scratch.
- ~~**Reconnect/error handling strategy** — currently the coordinator would
  presumably just re-login every refresh cycle on failure~~ **CORRECTED
  (2026-09-11): this assumption was wrong.** Verified by reading the
  actual code (raised by the user while asking about `reqcount`
  overflow, see below): `SmileConnectCoordinator._async_update_data()`
  only calls `async_login()` when `self.api is None` — i.e. exactly
  once, the first time. If the session becomes invalid for ANY reason
  afterward (gateway reboot, network hiccup during a request, a
  hypothetical `reqcount` issue - see the new bullet in "Next planned
  work" below), every subsequent poll just fails with `UpdateFailed` and
  entities go "unavailable" **permanently** — there is no automatic
  re-login, ever, until Home Assistant itself restarts or the user
  manually reloads the integration. Moved to "Next planned work" below
  as a concrete, scoped item now that the actual gap is understood
  precisely (not just "inefficient", but a real permanent-failure mode).
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
  bypasses that validation; and, as of `0.1.0` (2026-09-11), a case
  confirming `set_scene("Leave", ..., target=...)` now sends a request
  normally (Leave's `target=` block was removed once its formula was
  confirmed identical to Party/Boost's — see the resolution entries
  above), plus a companion case confirming `duration=` still works for
  Leave too. Written specifically
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
   confirmed, and how Leave's write-side `duration` formula was finally
   resolved (2026-09-11, see "Still untested / open" above) after
   extensive single-point-probe guessing against our own API had failed.
   **As of 2026-09-11, this technique is captured as a reusable repo
   script instead of being done purely ad-hoc each time:**
   `scripts/mitm_scene_capture.py` — an mitmdump addon that logs full
   request+response bodies for `/api/scene/*` traffic (not just the
   fields already known to matter) to a local JSON-Lines file. Run via
   `mitmdump -s scripts/mitm_scene_capture.py`; see the script's own
   docstring for the full setup/protocol. Worth extending to other path
   prefixes (or generalizing to log everything) if a future investigation
   needs a different endpoint family.

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
  - `api_methods.py` — high-level per-endpoint methods. Also owns
    `SCENE_MAX`, `FRACTION_DURATION_SCENES`, `RAW_DAYS_DURATION_SCENES`,
    `NO_DURATION_SCENES`, and `SCENE_APP_LIMITS` — see "Still untested /
    open" above for what each encodes and why. `set_scene_rooms()` and
    `set_scene()` both carry live-confirmed guard clauses (empty room
    list; no rooms assigned) — see the same section. As of 2026-09-11,
    Leave is handled identically to Party/Boost (no more special-case
    block) — its formula was confirmed identical, see the resolution
    entries above.
  - `scene_manager.py` — add/remove a room from a scene (handles the
    getrooms/setrooms/set sequencing). `const.SCENE_ACTIVATION_DURATION`
    is now correct for all five tracked scenes (Holiday fixed in `0.1.0`
    on 2026-09-10, Leave fixed in `0.1.0` on 2026-09-11 — see the
    resolution entries above for both).
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
- `sensor.py` — three entity groups:
  - Weather: outside temperature/min/max, sourced from
    `coordinator.data["weather"]` (fed by the main, authenticated
    coordinator). One parameterized `SmileConnectWeatherSensor` class
    covers all three. Attached to the **gateway** device via
    `device.gateway_device_info()`.
  - Diagnostics: `SmileConnectPingResponseTimeSensor`
    (`entity_category = DIAGNOSTIC`), fed by `SmileConnectPingCoordinator`
    instead — reports the gateway's own `"performance"` field from
    `/api/ping`. Also on the **gateway** device.
  - Preset duration remaining (added 2026-09-11):
    `SmileConnectPresetDurationSensor`, **four per room** (Boost/Party/
    Leave/Holiday, `TIMED_PRESET_SCENE_NAMES`), sourced from
    `coordinator.data["scene_active_rooms"]`/`["scene_duration_native"]`.
    Attached to the **regler** device (via `device.regler_device_info()`,
    the same device as that room's climate entity), NOT the gateway —
    framed as a companion to the room's preset control. Deliberately
    independent per (room, scene) pair rather than one dynamic sensor,
    since the gateway allows genuinely compound preset states that
    `climate.py`'s single-value `preset_mode` cannot represent — see the
    `0.1.0` addendum #4 entry (Versioning section below) for the full
    rationale and why `climate.py` itself needed no change for this.
- `number.py` — `SmileConnectDesiredTemperatureNumber` (added 2026-09-18,
  `0.3.0`): three sliders per room (Comfort Hi/Lo/Night =
  `desiredTempDay`/`desiredTempDay2`/`desiredTempNight`,
  `EntityCategory.CONFIG`, `NumberMode.SLIDER`, 0.5 °C step) on the
  **regler** device. min/max come from `api_methods.DESIRED_TEMP_APP_LIMITS`;
  rooms are looked up by id (not index); writes call
  `ApiMethods.set_desired_temperature()` and then
  `coordinator.async_refresh()` (NOT `async_request_refresh()`: the
  Debouncer's 10 s cooldown could skip the refresh after a second quick
  drag and leave a stale slider). No optimistic state - a write the
  gateway ignores makes the slider snap back after the refresh.
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
- **`0.0.21`** (bumped 2026-09-09, patch-only — still
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
- **`0.1.0` (2026-09-10/11, developed on branch `feature/ha-actions`,
  merged to `main` via PR — superseding `0.0.21` as the current version).**
  Starts the `0.1.x` beta line — the first HA Action for this integration.
  Contents:
  - New entity Action `honeywell_smileconnect.set_preset_mode_with_duration`
    (`climate.py`/`services.yaml`/`strings.json`/`translations/*.json`) —
    see the "Next planned work" entry above for the full design rationale
    (why this one Action, not three).
  - `SceneManager.add_member_to_scene()` gained optional `target=`/
    `duration=` parameters (`api/scene_manager.py`), defaulting to the
    prior hardcoded behavior when omitted.
  - Fixed `const.SCENE_ACTIVATION_DURATION["Holiday"]` (`0.5` → `15`) —
    see the "Top priority" item above, resolved as part of this same
    change since it touches the identical code path.
  - **2026-09-11 addendum (same branch, folded into this same `0.1.0`
    line rather than a separate version):** resolved Leave's write-side
    `duration` formula, previously blocked entirely (`NotImplementedError`
    on `target=`) since `0.0.21`. Investigated via a mitmproxy capture of
    the real Smile App (`scripts/mitm_scene_capture.py`, new) plus a
    direct confirmation through `ApiMethods.set_scene()` — see the
    dedicated resolution entries above and `docs/protocol.md` §4d. Leave
    turned out to use the identical fraction-of-scene_max formula as
    Party/Boost; the earlier "unsolved mystery" was an artifact of
    testing with raw values outside the gateway's actual `[0,1]` input
    domain for this scene. Removed the `NotImplementedError`/
    `LEAVE_TARGET_UNSUPPORTED_MSG` special-case in `api_methods.py`;
    fixed `const.SCENE_ACTIVATION_DURATION["Leave"]` (`2` → `0.5`);
    updated `scripts/test_scene_guards_local.py` accordingly.
  - **2026-09-11 addendum #2 (same branch):** simplified the
    `set_preset_mode_with_duration` Action now that `target` ("Duration")
    covers Leave too — removed the `duration` field ("Raw duration"),
    which existed only as a workaround for Leave's now-resolved block
    (`climate.py`'s `SET_PRESET_MODE_WITH_DURATION_SCHEMA`,
    `services.yaml`, `strings.json`/`translations/*.json`). `target`'s
    field description now spells out all four presets' units/ranges
    directly (sourced from `SCENE_APP_LIMITS` in `api_methods.py`:
    Boost minutes 30–120 step 30, Party/Leave hours 1–12, Holiday days
    1–30) instead of the old prose that also wrongly excluded Leave. The
    underlying Python API (`ApiMethods.set_scene(duration=...)`,
    `SceneManager.add_member_to_scene(duration=...)`) is unaffected -
    only the HA-facing Action surface changed.
  - **2026-09-11 addendum #3 (same branch): new
    `set_hvac_mode_and_temperature` Action, plus a newly-found limitation
    of the standard `climate.set_temperature` service on this entity.**
    Live-tested this session (real HA instance, real gateway): calling
    `climate.set_hvac_mode` alone works reliably, and calling
    `climate.set_temperature` with only `temperature` works reliably
    *while already in `auto`* — but `climate.set_temperature` called with
    **both** `temperature` and `hvac_mode` together (HA core's combined-
    call form) applies **neither** value, with no exception and no log
    warning at all. The exact mechanism inside HA core's own handling for
    this combined call was not pinned down further (out of scope - this
    is HA core's service layer, not the gateway protocol this project
    reverse-engineers). **Fix:** new entity Action
    `honeywell_smileconnect.set_hvac_mode_and_temperature`
    (`climate.py`/`services.yaml`/`strings.json`/`translations/*.json`)
    that sequences the two writes itself, reusing the existing Standby
    scene-membership logic. `async_set_hvac_mode()` is now a thin wrapper
    around a new shared `_async_apply_hvac_mode()` helper (same pattern
    as `_async_apply_preset()` from 2026-09-10). When a target
    temperature is given alongside `hvac_mode: auto`, the helper writes
    it directly instead of going through
    `_nudge_temperature_after_leaving_standby()`'s jump-to-max-then-
    drift-back workaround - a genuine target value already satisfies
    that workaround's own "must be a real change" requirement (see that
    method's docstring), so the extra round-trip is redundant here, and
    was flagged as a plausible contributor to the standard service's
    combined-call failure (two genuine writes in quick succession
    instead of one) - not confirmed as the definitive root cause, just
    the most likely explanation given the evidence. **Deliberately no
    extra validation was added** (explicit project decision): `hvac_mode:
    off` with a `temperature` given is accepted by the schema and the
    temperature is simply ignored downstream, matching how the gateway
    already silently ignores a temperature write while Standby is active
    (see the "Standby persists silently" entry elsewhere in this file).
    **This limitation of the standard `climate.set_temperature` service
    itself is now the recommended thing to document once the README
    rewrite happens** (see the new README bullet in "Next planned work")
    - for now it's captured here and in the new Action's own
    `strings.json` description, which explicitly points users at the new
    Action for combined calls.
  - **2026-09-11 addendum #4 (same branch): new per-room "preset duration
    remaining" sensors.** User's idea, raised as a natural companion to
    `set_preset_mode_with_duration`: nothing previously surfaced
    `ApiMethods.get_scene_duration()` (the confirmed-reliable read side
    of the exact duration API this session's Leave investigation already
    reverse-engineered the write side of) anywhere in HA. New
    `ApiMethods.get_scene_duration_native()` (`api_methods.py`) applies
    the existing raw→real-world conversion recipe
    (`get_scene_duration()`'s own docstring) so callers get a real-world
    number directly. `coordinator.py`'s `_get_scene_active_rooms()`
    (renamed `_get_scene_active_rooms_and_durations()`) now also returns
    a `scene_duration_native` dict, reusing the same `get_scene_status()`
    call and only fetching duration for scenes that are actually active
    (same cost-conscious pattern as the existing room-membership fetch).
    New `sensor.py` entity `SmileConnectPresetDurationSensor` - **four
    independent sensors per room** (Boost/Party/Leave/Holiday), not one
    dynamic "whichever preset is active" sensor. Deliberate design
    decision, raised by the user: the Smile App lets scenes be combined
    arbitrarily (e.g. Boost AND Party simultaneously active on the same
    room), which `climate.py`'s single-value `preset_mode` structurally
    cannot represent (`_update_active_preset()` picks one winner via a
    fixed priority order when more than one scene is active - see its
    own docstring). Four independent sensors sidestep this entirely -
    each reads `coordinator.data` directly with zero dependency on
    `climate.py`'s preset-resolution logic, so **`climate.py` itself did
    NOT need to change** for this feature (confirmed directly with the
    user during planning). New `TIMED_PRESET_SCENE_NAMES` constant in
    `const.py` (`BOOST, PARTY, LEAVE, HOLIDAY` - mirrors
    `TRACKED_SCENE_NAMES` but excludes `STANDBY`, which has no duration
    concept). Each sensor uses its OWN preset's native unit (fixed per
    entity instance - Boost: minutes, Party/Leave: hours, Holiday: days)
    rather than a normalized common unit, and reads `None` ("unknown")
    when the room isn't currently a member of that scene. New test
    `tests/test_api_methods.py::TestGetSceneDurationNative` covers both
    conversion branches; the sensor entities themselves are HA-dependent
    and verified manually (no automated harness yet, per "Test Suite"
    below).
  - `manifest.json` version bump + README badges (version `0.1.0`, status
    `pre-alpha` → `beta`).
  - New tests in `tests/test_scene_manager.py` covering the `target=`/
    `duration=` passthrough; `climate.py`'s Action registration itself is
    HA-dependent and verified manually (no automated harness for it yet,
    per "Test Suite" below).
  - Per the beta-status rule above: developed on `feature/ha-actions`, to
    be merged via pull request — not committed directly to `main`.
- **`0.1.1` (2026-09-15, developed on branch
  `feature/regler-weather-sensors`, per the beta-status rule above — not
  committed directly to `main`).** Corrects device attribution for the 3
  existing outside temperature/min/max sensors — see the dedicated entry
  under "Still untested / open" above for the full reasoning (SRC-10
  module discussion, why the multi-room case is deliberately left
  unresolved). No new user-facing capability, only a device/entity-naming
  correction for existing sensors on single-room installs. Contents:
  `sensor.py`'s `SmileConnectWeatherSensor` gained optional `room_id`/
  `room_name`, wired up only when `async_setup_entry` sees exactly one
  room; `manifest.json` + `README.md` version badge bumped to `0.1.1`;
  `README.md`'s entity table and "Known limitations" updated to describe
  the new per-install-size device attachment.
- **`0.2.0`** (2026-09-17, developed on branch
  `feature/switching-times`, per the beta-status rule above — not
  committed directly to `main`). First phase of exposing room switching-
  time schedules (Schaltzeiten) to Home Assistant — see
  `docs/switching-times-api.md` for the underlying protocol reference
  (already live-verified against the gateway, 2026-09-09, but previously
  completely unwired) and the "Still untested / open" entries below for
  the product decisions made while designing this. Deliberately scoped to
  just the read/write Action layer for this release — a native
  `schedule.*` helper UI and automatic gateway↔HA sync were designed in
  an earlier planning pass but explicitly deferred to a later phase (see
  "Next planned work" below) once it became clear during planning that
  (a) a nested per-block `data` field isn't settable from HA's stock
  visual schedule-helper editor (only via its "Edit in YAML" advanced
  view), and (b) the `schedule` core component has no public write API at
  all for creating/updating a helper's config programmatically (confirmed
  by reading `homeassistant/components/schedule/__init__.py` — its
  `ScheduleStorageCollection` is a local variable in `async_setup()`,
  never exposed via `hass.data` or a service; the only mutation path is a
  private, frontend-only WebSocket route) — so automatic Gateway→HA sync
  into a native helper is not achievable without an unsupported storage
  hack, which this project deliberately does not build (see "Reverse-
  Engineering Method"/validation principle above: don't ship what can't
  be verified/kept working across HA versions).
  Contents:
  - New, HA-independent module `switching_times.py` (no `homeassistant.*`
    import) — converts between the gateway's flat, day-major
    `switchingtimes` wire format and a `{"monday": [...], ...,
    "sunday": [...]}` per-weekday dict shape. Encodes two explicit
    product decisions made during planning:
    1. **No implicit type default.** A slot with `from`/`to` but no
       `type` is a hard validation error, never silently treated as
       "Comfort Lo" or as the implicit "Night" state. This corrects an
       earlier draft of the plan that HAD considered defaulting a
       missing type to `"L"` on the assumption that "no type" and
       "Night" were the same thing — the user explicitly corrected this:
       `"N"` (Night) is its own independent, real switching type, not a
       fallback value for "type omitted". `"N"` itself is deliberately
       rejected as a settable value (with a dedicated error message)
       since it requires the Honeywell Room Connect SRC-10 hardware
       extension to be meaningful, which is not available on this
       project's test installation — shipping support for it without any
       way to verify it live would repeat mistakes this project has
       already paid for elsewhere (see the many `api_request.py`/
       `api_methods.py` silent-corruption incidents above).
    2. `MAX_SLOTS_PER_DAY = 3` is kept as this hardware's empirically
       confirmed ceiling (`docs/switching-times-api.md`) and used as a
       default/validation bound, but `replace_weekday_slots()` (the
       single-weekday write path) always derives the REAL ceiling from
       the current live schedule's own length instead of trusting the
       constant — a single-weekday write must not silently change the
       slot-count shape of every other day.
  - Three new HA Actions on `climate.py`, registered the same way as the
    existing `set_preset_mode_with_duration`/`set_hvac_mode_and_temperature`
    (`entity_platform.async_register_entity_service()`):
    - `get_schedule_room` — no parameters, `supports_response=ONLY`,
      returns the per-weekday dict. Deliberately the same shape
      `set_schedule_room` expects, for a read→edit→write-back workflow.
    - `set_schedule_room` — one `schedule` field (a nested object/YAML
      value covering the whole week, up to 7×3×3 = 63 leaf values).
      Deliberately NOT broken into per-day/per-slot fields — that would
      mean ~21 field groups in the HA UI, a poor form experience for
      something users will mostly compose in an automation/script anyway.
      HA's Developer Tools → Actions already has a built-in YAML editor
      for any action call, so nothing extra was built for this - the
      user just switches modes there.
    - `set_schedule_room_weekday` — `weekday` + 9 FLAT fields
      (`slot_1_from`/`_to`/`_type`, `slot_2_*`, `slot_3_*`), each trio
      grouped via `vol.Inclusive(..., "slot_N")` so voluptuous itself
      enforces "all three or none" per slot, before the call ever
      reaches our own code. These are flat fields, not a nested
      `slot_1: {from, to, type}` object, because reading ha-core's own
      `services.yaml` "collapsed: true / fields:" pattern (used e.g. by
      `kitchen_sink`/`habitica` for grouped/"Advanced" UI sections)
      confirmed such nested groups are COSMETIC ONLY — the actual
      service-call data stays flat regardless, confirmed against those
      components' own voluptuous schemas. Exactly 3 slot groups exist (no
      `slot_4_*`) — this is how "max 3 slots per weekday" is enforced
      STRUCTURALLY for this Action, on top of `switching_times.py`'s own
      runtime check for `set_schedule_room`'s free-form input.
      Internally does read-modify-write via the new
      `replace_weekday_slots()` helper (the gateway has no partial-update
      endpoint - see `docs/switching-times-api.md`, point 4).
  - Both `set_*` Actions use `supports_response=OPTIONAL` and return the
    freshly re-read schedule after writing, rather than trusting a bare
    `success:true` — matches this project's long-standing "verify via a
    fresh read" principle (see the many `api_request.py`/`api_methods.py`
    incidents above where `success:true` alone had already been shown to
    lie).
  - New `tests/test_switching_times.py` (21 tests, no HA/gateway
    dependency) locks in the no-default-type rule, the `"N"`-rejection
    message, the max-3-slots validation, overlap/ordering checks, and
    that `replace_weekday_slots()` never touches any day other than the
    one given.
  - Localization: `get_schedule_room`/`set_schedule_room`/
    `set_schedule_room_weekday` added to `strings.json` +
    `translations/{en,de,es,fr}.json`, plus a new top-level `"selector"`
    section (`weekday` — reusing HA's own `[%key:common::time::monday%]`-
    style common keys rather than re-translating weekday names; and
    `slot_type` for the H/L dropdown labels "Comfort Hi"/"Comfort Lo").
  - `manifest.json` + `README.md` version badge bumped to `0.2.0` (minor
    bump, not a patch — new user-facing capability, matching how
    `0.0.21→0.1.0` was handled when the first Action shipped).
  - **2026-09-17 addendum (same branch, live-verification fixes, folded
    into this same `0.2.0` rather than a separate version):** four real
    bugs found testing against a real HA instance and a real gateway (not
    caught by unit tests, since none of the first three is exercisable
    without the actual HA frontend/schema-validation machinery, and the
    fourth needed the real gateway's own wire-level response):
    1. **`set_schedule_room_weekday` errored on a call where only
       `slot_1` was filled in via the GUI.** Root cause: the HA
       frontend's form for an untouched, optional field inside a
       collapsed section (`slot_2`/`slot_3`) submits an empty string
       `""` rather than omitting the key. `vol.Inclusive`'s "all or
       none" grouping trivially passed (all three keys ARE present,
       just empty), and `cv.time("")`/`vol.In(...)("")` then failed with
       a confusing schema error for a slot the user never touched. Fixed
       with a new `_drop_empty_slot_fields()` preprocessing step
       (`climate.py`): `vol.All(_drop_empty_slot_fields,
       cv.make_entity_service_schema(...))`, confirmed supported by
       reading `homeassistant/helpers/service.py`'s/`config_validation.py`'s
       own `is_entity_service_schema()` (explicitly walks into a
       `vol.All`-wrapped entity-service schema, not just a bare dict).
    2. **`INVALID_ARGUMENT_TYPE` "Translation error" shown in the HA UI
       for `get_schedule_room` (and, latently, `set_schedule_room`'s
       `schedule` field).** Root cause: HA's frontend renders service
       descriptions through `intl-messageformat` (ICU MessageFormat),
       which treats bare `{...}` in a string as argument-placeholder
       syntax, not literal text. The English (and de/es/fr) description
       strings for these two Actions illustrated the slot shape with
       literal JSON-like snippets - `each entry {from, to, type}` and
       `e.g. {"monday": [{"from": ..., "to": ..., "type": "H"}], ...}` -
       which ICU tried to parse as a formatted argument (`to` is not a
       valid ICU argument-type keyword, hence `INVALID_ARGUMENT_TYPE`).
       **Lesson for any future service/field description text in this
       project: never put a raw `{`/`}` JSON example directly in a
       `strings.json`/`translations/*.json` string** - describe the
       shape in prose instead (as the fixed versions of both descriptions
       now do). Fixed in all 5 files (`strings.json` +
       `translations/{en,de,es,fr}.json`); confirmed via a script that
       walks every string value in all 5 files checking for stray `{`/`}`
       characters, not just the two originally-reported ones.
    3. **The `weekday` selector's dropdown literally showed
       `[%key:common::time::monday%]` etc. instead of translated day
       names** (screenshot evidence from the user's live HA instance).
       Root cause: `[%key:...%]` is a build-time reference-substitution
       syntax that HA core's OWN release pipeline (`script.translations`/
       `hassfest`) expands into literal text before a core integration's
       translations ever ship - by the time a real HA release runs, core
       components' `strings.json`/`translations/*.json` no longer contain
       raw `[%key:...%]` markers. **A HACS custom integration never goes
       through that build step - the runtime frontend does NOT resolve
       `[%key:...%]` on the fly for custom-component translations.**
       This was a wrong assumption made while designing this feature
       (intending to reuse HA's own common weekday translations to avoid
       re-translating "Monday"/"Tuesday"/etc. in 4 languages) - looked
       plausible from reading core's OWN `strings.json` examples (e.g.
       `habitica`'s `"repeat"` selector uses exactly this pattern) without
       noticing those are core-only, already-expanded artifacts, not a
       runtime feature available to any integration. **Lesson: `[%key:
       ...%]` must never be used in a custom (HACS) integration's own
       strings.json/translations - always write the literal translated
       text out in each of the 4 language files instead.** Fixed by
       replacing all 7×5 weekday option strings with literal translated
       day names (Monday..Sunday / Montag..Sonntag / Lunes..Domingo /
       Lundi..Dimanche) in `strings.json` and all four
       `translations/*.json` files. Re-confirmed via the same
       stray-character scan approach used for bullet 2 above, generalized
       to also grep for any remaining `[%key:` marker anywhere under
       `custom_components/` - none found.
    4. **`set_schedule_room` failed against the real gateway with
       `success:false, "The input format is invalid: 14"` and wrote
       nothing** (confirmed via the user's HA debug log and a follow-up
       `get2` read showing the room's schedule unchanged). Root cause,
       and full protocol details, now in `docs/switching-times-api.md`'s
       wire-format point 5: the gateway rejects a `switchingtimes` array
       whose length doesn't match the room's OWN currently-configured
       slots-per-day, even though the length is still a valid multiple of
       7 - "a multiple of 7" (the only rule previously documented) is
       necessary but not sufficient. The user's test schedule needed only
       2 slots on its busiest day (Monday), so `weekday_dict_to_
       switching_times()` produced a 14-element array (2×7) - but this
       room's gateway-side shape is fixed at 21 elements (3×7), and the
       write was flatly rejected rather than silently corrupted like the
       point-3 contiguous-slot issue. `set_schedule_room_weekday` never
       hit this because it already reads the current schedule first
       (read-modify-write) and inherits its exact length - the bug was
       specific to `set_schedule_room`, which built an array from
       scratch. Fixed: `weekday_dict_to_switching_times()` gained a
       `slots_per_day` parameter that, when given, FIXES the output width
       instead of deriving it from the busiest day in the new content;
       `climate.py`'s `async_set_schedule_room()` now reads the room's
       current `switchingtimes` first (purely to learn its length) before
       building the write payload, mirroring what
       `set_schedule_room_weekday` already did correctly. Two new
       regression tests in `tests/test_switching_times.py`. Service
       description text updated in all 5 translation files to mention the
       capacity check.
  - **Full live verification, all four fixes above, confirmed working by
    the user (2026-09-17) against the real gateway and a real HA
    instance:** `get_schedule_room`/`set_schedule_room`/
    `set_schedule_room_weekday` all confirmed working end-to-end,
    including a deliberate test sending 4 slots on one day via
    `set_schedule_room` (exceeds this room's 3-slot capacity) correctly
    rejected with a clear validation error rather than a silent failure
    or a raw traceback. No further switching-times bugs outstanding as of
    this date - the read/write Action layer (phase 1) is considered done
    and stable. Per the user's own explicit prioritization, the next
    session's focus is the `desiredTempDay`/`desiredTempDay2`/
    `desiredTempNight` write investigation (see the "TOP PRIORITY" entry
    under "Next planned work" above), NOT phase 2's native helper UI.
- **Current version: `0.3.0`** (2026-09-18, developed on branch
  `feature/desired-temperatures`, per the beta-status rule above — merged
  via pull request, not committed directly to `main`). Adds write support
  for the three fixed per-room schedule temperatures (`desiredTempDay` =
  H "Comfort Hi", `desiredTempDay2` = L "Comfort Lo", `desiredTempNight` =
  N), the "TOP PRIORITY" item that followed `0.2.0`. Contents:
  - **Protocol (see `docs/protocol.md` §4g):** found via a live mitmproxy
    capture of the real Smile App (`scripts/mitm_desired_temp_capture.py`,
    scoped to ALL `/api/` traffic since the endpoint was unknown up
    front). No new endpoint: `/api/room/settemperature` with `change_mode`
    1 (Night) / 2 (Day) / 3 (Day2). The gateway floors the sent value to
    a 0.5 °C step (`floor(sent/0.5)*0.5`), verified across 5 writes via
    fresh `room/list` reads. The German-locale app sends a comma decimal
    separator, which the gateway parses correctly.
  - `api_methods.py`: new `set_desired_temperature(temperature, room_id,
    target)` (existing `set_temperature()` untouched, still
    `change_mode=0`), `DESIRED_TEMP_TARGETS` (letter → change_mode, field,
    response_key), `DESIRED_TEMP_APP_LIMITS` (H 15-25, L 13-21, N 12-14.5,
    user-provided from the Smile App UI; enforced client-side, whether the
    gateway enforces them is unknown), `round_to_gateway_step()` (half-up,
    deliberately NOT Python's banker's `round()`), and
    `validate_desired_temperature()` (range checked on the ROUNDED value).
  - New entity Action `honeywell_smileconnect.set_desired_temperature`
    (`climate.py`, `SupportsResponse.OPTIONAL`) with an explicit required
    `type` (`comfort_hi`/`comfort_lo`/`night` - its own enum, NOT `switching_times.VALID_TYPES`,
    which excludes `N` for schedule SLOT types only) + `temperature`.
    Response comes from a direct `api.get_specific_room()` re-read rather
    than `coordinator.async_request_refresh()` (Debouncer could skip the
    poll on a second write in its cooldown and report stale data):
    `{type, requested, sent, stored, verified, desired_temperatures}`.
    Mismatch → `verified: false` + warning, not an exception. The network
    call is deliberately not wrapped in `except ValueError` (JSONDecodeError
    is a ValueError). **Live-found:** `desired_temperatures` originally used
    `H`/`L`/`N` as keys and HA's YAML view rendered `"N"` quoted (YAML 1.1
    reads a bare N as a boolean) - keys are now `comfort_hi`/`comfort_lo`/
    `night`. Lesson: don't use bare single letters (N/Y) as dict keys in
    service responses. **Second live/CI finding (hassfest, PR #4):**
    select-option values in `services.yaml` double as translation keys and
    must match `[a-z0-9-_]+`, so the Action's `type` input is likewise
    `comfort_hi`/`comfort_lo`/`night` (climate.py maps to the API layer's
    H/L/N via `_DESIRED_TEMP_KEY_TO_TARGET`), not H/L/N. (Same rule that
    already bit the weekday selector in 0.2.0 - the schedule slot `type`
    selector uses `value:`/`label:` pairs and is unaffected.)
  - New `number.py` platform (`Platform.NUMBER`) - three config sliders per
    room, see the module layout entry above. Entity names in en/de/es/fr
    under `entity.number.*`.
  - `services.yaml`/`strings.json`/`translations/{en,de,es,fr}.json`: new
    action + `selector.desired_temperature_type` + number entity names;
    verified no stray `{`/`}` or `[%key:` markers.
  - Tests: `tests/test_api_methods.py` (rounding, change_mode mapping,
    per-type boundaries, rounding-before-range-check, `set_temperature()`
    unchanged guard, limits on the step grid). Entities/Action handler
    have no automated harness (project gap) - verified live on a real HA
    instance + gateway by the user, incl. sliders and the Action response.
  - Still unknown: whether writes are ignored under Standby, multi-room
    (`roomid` scoping only tested on the single-room install), and whether
    `change_mode=0` also floors off-grid values.
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