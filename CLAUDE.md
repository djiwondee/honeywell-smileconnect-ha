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

Rules, all implemented in `api/apiRequest.py`:
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

- **`roomstatus` code mapping is unverified for Honeywell.** The codes
  currently mapped in `const.py` (43=Party, 46=Boost, 127=Holiday,
  130=Leave, 132=Standby) were carried over from the generic HeatApp
  reference project and have NOT been confirmed against this gateway. A
  live room was observed with `roomstatus=12` and `status: "new"`, which
  matches none of the mapped codes and currently falls through to "no
  preset". **To fix:** activate each scene one at a time via the API (or
  the stock Honeywell UI) and record the resulting `roomstatus` value from
  the debug log, OR switch entirely to a ground-truth approach: query
  `/api/scene/getrooms` per scene and check room-ID membership instead of
  inferring state from `roomstatus`.
- **`actualTemperature` is not always present.** Observed missing entirely
  on a single-zone "Regler MK1" (relay-only) installation with
  `roomstatus=12`. `climate.py` now uses `.get()` defensively rather than
  assuming the key exists (was previously a `KeyError` crash on entity
  setup). Open question: is there a different endpoint that reports actual
  temperature for this kind of installation, or does this gateway variant
  genuinely not have a room sensor?
- **`minTemperature`/`maxTemperature` may not be meaningful on this
  installation** — observed both equal to `12`, identical to
  `desiredTemperature`, on a system that looks not-yet-fully-configured
  (`status: "new"`). `scheduleTempMin`/`scheduleTempMax` (observed `12`/`25`)
  might be the actually-relevant bounds instead. Needs checking against a
  fully-configured room, or against the stock Honeywell UI's displayed
  min/max.
- Behavior of `setrooms` **before** scene activation (does order matter?)
- Decimal temperature values (e.g. 20.5 °C) — specifically whether the
  request expects comma or dot notation (see `_prepareRequestBodyForHash` in
  the generic HeatApp code, which contains a commented-out attempt to
  replace dots with commas for `temperature`)

### Next planned work (agreed in project discussion, not yet started)

- **Outside temperature sensor.** `api/apiMethods.get_weather()` already
  exists and calls `/api/weather`, but there is no HA entity for it yet
  (no `sensor.py` or `weather.py` platform). Straightforward addition.
- **Verify `roomstatus` codes properly** (see above) — this both fixes
  preset sync accuracy and is a prerequisite for trusting `hvac_mode`.
- **Full temperature sync** — resolve the `actualTemperature` /
  min/max-temperature open questions above.
- **Options flow** so the polling interval can be changed after initial
  setup without recreating the config entry.
- **Expose switching times** (`get_switching_times`/`set_switching_times`
  already implemented in `api/apiMethods.py`) via a service or entity — not
  currently wired to anything in the HA integration layer.
- **Reconnect/error handling strategy** — currently the coordinator would
  presumably just re-login every refresh cycle on failure; this works but
  is inefficient and not a deliberate design. Worth revisiting once basic
  functionality is solid.

## Test Suite

`tests/` contains regression tests for the HA-independent `api/` layer
(crypto, login, request signing, response parsing). Run with:

```bash
pytest tests/ -v
```

- `tests/fixtures/` holds **real payloads captured from a live Honeywell
  gateway** (`192.168.1.132`) during development — `challenge_response.json`,
  `login_response.json`, `room_list_response.json`. These are genuine
  recorded API responses, not hand-written approximations, and are safe to
  keep in the repo (no real password or long-lived secret is contained in
  them; the captured devicetoken/challenge values are single-use and
  already expired).
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

**When adding a new endpoint or fixing a parsing bug:** capture the real
request/response via the browser-console technique or a live debug-log
session, add it as a new fixture under `tests/fixtures/`, and add a test
that exercises the actual parsing code against it — this is the pattern to
follow going forward, not just for the crypto layer.

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

**Preferred interaction pattern:** produce self-contained JS code blocks for
manual paste into the browser console, rather than automated tab control —
browser MCP connections have been unreliable in the past.

**Validation principle:** every new piece of API behavior is verified live
against the gateway before it is adopted into the integration.

## Integration Architecture

- `custom_components/honeywell_smileconnect/api/` — pure protocol layer
  (login, requests, signing), no HA dependencies. Deliberately kept as a
  standalone, testable module (potentially extractable into its own PyPI
  package later, similar to `py-heatapp-de`, but under a new name to avoid
  any compatibility confusion).
  - `crypto.py` — shared PBKDF2/SHA-512 primitives (`string_to_charcodes`,
    `pbkdf2_base64`), used by both `login.py` and `apiRequest.py`. Keep
    this the single source of truth for the crypto scheme — do not
    reimplement it inline elsewhere.
  - `login.py` — challenge/response login, password hashing, AES devicetoken
    decryption.
  - `apiRequest.py` — signs and executes authenticated requests.
  - `apiMethods.py` — high-level per-endpoint methods.
  - `scene_manager.py` — add/remove a room from a scene (handles the
    getrooms/setrooms/set sequencing).
  - `credentials.py` — session state, including `reqcount` with correct
    post-increment semantics (see reqcount section above).
- `coordinator.py` — `DataUpdateCoordinator`, polls room/scene status.
- `climate.py` — one `ClimateEntity` per room, preset mapping onto scenes
  (Boost/Holiday/Leave/Party/Standby). Shower/Towel are not wired up for now
  (no test hardware available), but the protocol constants for them are kept
  in place. Field access uses `.get()` defensively since not all fields
  (e.g. `actualTemperature`) are guaranteed present on every installation.
- `config_flow.py` — host/user/password/interval, validated via an actual
  login attempt against the gateway.

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
- When proposing a plan (per the Session Workflow rules above), also
  propose the appropriate version bump and, once beta status applies,
  the branch name to use.