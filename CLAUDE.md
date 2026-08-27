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

This is the root cause of why existing HeatApp libraries do NOT work:

| Parameter | Standard HeatApp | Honeywell Smile Connect |
|---|---|---|
| Password hashing | MD5 | PBKDF2/SHA512 + `stringToCharcodes` pre-processing |
| Parameter separator (signature string) | `&` | `\|` (pipe), with a trailing pipe |
| `udid` | random UUID | fixed `"web"` |
| `devicename` | `"homeassistant"` | `"Computer"` |

> ⚠️ **Open / needs verification:** The exact PBKDF2 parameters (iteration
> count, salt, output length, exact `stringToCharcodes` transformation) are
> not yet fully documented. Re-verify against the admin console (see the
> reverse-engineering method below) before finalizing the login
> cryptography. The code in `api/login.py` has a marked placeholder there
> (`# TODO VERIFY`).

Known from the generic HeatApp protocol (baseline, likely applicable to
Honeywell with the adjustments above):

- Request signature: sorted parameters are joined into
  `key=value|key=value|...` (pipe instead of `&`), the device token is
  appended, then the whole string is hashed.
- Login flow: `challenge` → `hashed token response` → `devicetoken_encrypted`
  is decrypted with AES-256-CBC, key = SHA-256(password), fixed IV
  (`D3GC5NQEFH13is04KD2tOg==` on standard HeatApp — **still to be verified
  whether Honeywell uses the same one**).

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

- Authentication & session management
- Retrieving the room list
- Retrieving scene status
- Setting temperature
- Scene activation: Boost (minutes), Party/Leave (hours), Holiday (days),
  Standby (no duration)
- Switching times via `/api/room/switchingtimes/set2`

### Still untested / open

- Behavior of `setrooms` **before** scene activation (does order matter?)
- Decimal temperature values (e.g. 20.5 °C) — specifically whether the
  request expects comma or dot notation (see `_prepareRequestBodyForHash` in
  the generic HeatApp code, which contains a commented-out attempt to
  replace dots with commas for `temperature`)

## Reverse-Engineering Method (for further, still-unknown endpoints)

1. Open the browser console at `http://<gateway-ip>/admin/dashboard/index`.
2. `CryptoJS` is already preloaded there and directly usable.
3. Key JS objects in the admin area:
   - `request.hashAuthenticationToken`
   - `request.makeRequestData`
   - `request.getRequestSignature`
   - `Crypt.aes256decrypt`
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
- `coordinator.py` — `DataUpdateCoordinator`, polls room/scene status.
- `climate.py` — one `ClimateEntity` per room, preset mapping onto scenes
  (Boost/Holiday/Leave/Party/Standby). Shower/Towel are not wired up for now
  (no test hardware available), but the protocol constants for them are kept
  in place.
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

## Conventions

- Domain: `honeywell_smileconnect`
- All new symbols (classes, constants) use `SmileConnect` or
  `honeywell_smileconnect` prefixes — no leftover `heatapp` naming in new
  code.
- Commit messages in English; docs/comments should also default to English
  for upstream compatibility (HA contributions).
