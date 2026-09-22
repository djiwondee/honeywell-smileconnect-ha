# Honeywell Smile Connect — Home Assistant Integration

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![Version](https://img.shields.io/badge/version-0.4.0-yellow.svg)](https://github.com/djiwondee/honeywell-smileconnect-ha/releases)
[![Status](https://img.shields.io/badge/status-beta-yellow.svg)](CLAUDE.md#versioning--branching-strategy)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Validate](https://github.com/djiwondee/honeywell-smileconnect-ha/actions/workflows/validate.yml/badge.svg)](https://github.com/djiwondee/honeywell-smileconnect-ha/actions/workflows/validate.yml)
[![Lint](https://github.com/djiwondee/honeywell-smileconnect-ha/actions/workflows/lint.yml/badge.svg)](https://github.com/djiwondee/honeywell-smileconnect-ha/actions/workflows/lint.yml)

A HACS-compatible Home Assistant custom integration for the **Honeywell Smile
Connect** heating gateway (model **SCN-10**) — local polling, no cloud
dependency, reverse-engineered from the gateway's own protocol.

## ⚠️ Disclaimer

This is an independent, community-developed, reverse-engineered integration.
It is **not affiliated with, endorsed by, or supported by Honeywell** or by
**EbV Elektronikbau- und Vertriebs-GmbH** (maker of the underlying "heatapp!"
platform of which Smile Connect is an OEM-rebranded variant). "Honeywell" and
"Smile Connect" are used here solely to identify the hardware this
integration targets.

This integration is **not compatible** with standard HeatApp gateways or the
existing `heatapp_local` / `py-heatapp-de` projects — Honeywell's variant
uses a different authentication and request-signing protocol. See
[`docs/protocol.md`](docs/protocol.md) for the technical details.

Use at your own risk. Interacting with your heating system's API can affect
real heating behaviour in your home.

## Status

Beta. Login, room/climate control, scene (preset) activation, and all six
custom Actions below have all been live-verified against a real SCN-10
gateway and a real Home Assistant instance. See [`CLAUDE.md`](CLAUDE.md) for
the full architecture/history and [`docs/protocol.md`](docs/protocol.md) /
[`docs/switching-times-api.md`](docs/switching-times-api.md) for the
reverse-engineered wire protocol.

## Features

- Local polling only — no cloud account, no internet dependency
- One climate entity per room/SDC Regler, with temperature control and
  schedule on/off (`auto`/`off`)
- The gateway's five scenes as presets: **Boost**, **Party**, **Leave**,
  **Holiday** (temporary overrides), plus **Standby** (schedule on/off)
- Per-room sensors showing how long a preset has left to run
- Per-room sliders for the three fixed schedule temperatures (Comfort Hi,
  Comfort Lo, Night)
- Outside-temperature sensors (from the room's Regler, on single-room
  installations — see [Entities provided](#entities-provided))
- A lightweight, independent connectivity/response-time diagnostic, so you
  can tell "gateway unreachable" apart from "login broken"
- Six custom Actions for automations that need more control than the
  standard `climate.*` services offer, including full read/write access to
  a room's weekly switching-time schedule — see [Actions](#actions) below

## Entities provided

**Climate** (one per room):

| Entity | Notes |
|---|---|
| `climate.<room>` | `hvac_mode`: `auto` (follow the room's schedule) or `off` (Standby). `preset_mode`: `none`/`Boost`/`Party`/`Leave`/`Holiday`. Target temperature read/write. |

**Sensor**:

| Entity | Scope | Category | Notes |
|---|---|---|---|
| Outside temperature / min / max | Regler (single-room installs); gateway otherwise | primary | Value comes from the gateway's `/api/weather` relay, but the physical sensor is wired to the Regler for its own weather-compensated control — see [Known limitations](#known-limitations) for the multi-room caveat |
| Gateway response time | gateway | diagnostic | From the unauthenticated `/api/ping` endpoint |
| Boost / Party / Leave / Holiday remaining | **per room** | primary | Time left on that preset, in its own natural unit (minutes/hours/hours/days) — reads "unknown" when that specific preset isn't active for the room. See [Known limitations](#known-limitations) for why there are four independent sensors instead of one. |
| Schedule | **per room** | diagnostic | Number of switching-time slots in the week; the whole weekly plan sits in its attributes. This is what the [schedule card](#schedule-card) reads — you normally look at the card, not at this entity. |

**Number** (config entities, one set per room, on the Regler device):

| Entity | Range | Notes |
|---|---|---|
| Comfort Hi temperature | 15–25 °C | The temperature a schedule slot of type `H` applies |
| Comfort Lo temperature | 13–21 °C | The temperature a schedule slot of type `L` applies |
| Night temperature | 12–14.5 °C | The temperature the room uses outside any schedule slot |

Sliders move in 0.5 °C steps (the gateway itself rounds down to that grid).
Ranges are the ones the Smile App offers. Changes made in the Smile App show
up here on the next poll.

**Binary sensor**:

| Entity | Scope | Category | Notes |
|---|---|---|---|
| Connectivity | gateway | diagnostic | Reachability via `/api/ping`, independent of login state |

Devices: one **gateway** device (connectivity/diagnostics, plus weather on
multi-room installs — see [Known limitations](#known-limitations)), plus
one **SDC Regler** sub-device per room (climate entity + that room's four
preset sensors and three temperature sliders, plus weather on single-room
installs), linked to the
gateway via `via_device`.

## Installation

### Via HACS (custom repository, until/if accepted into the default store)

1. HACS → Integrations → ⋮ → Custom repositories
2. Add this repository URL, category "Integration"
3. Install "Honeywell Smile Connect"
4. Restart Home Assistant
5. Settings → Devices & Services → Add Integration → "Honeywell Smile Connect"

### Manual

Copy `custom_components/honeywell_smileconnect` into your Home Assistant
`custom_components` directory and restart.

## Configuration

Set up via the UI (Settings → Devices & Services → Add Integration):

| Field | Default | Notes |
|---|---|---|
| Gateway IP address | — | e.g. `192.168.1.132` |
| Username / Password | — | Your Smile App login |
| Polling interval | 30s | Room/climate/scene poll cycle |
| Ping interval | 15s | Independent connectivity check — deliberately more responsive, and kept separate so a broken login never makes the connectivity sensor look wrong |
| Schedule polling interval | 300s | How often the weekly switching times are re-read. Deliberately slow — schedules change rarely, and each cycle costs one request per room. A schedule changed from Home Assistant does not wait for it. |

All of the above, including credentials, can be changed later via the
integration's **Configure** (Options) button without losing entity/device
history.

## Actions

Standard `hvac_mode`, `preset_mode`, and temperature control already work
through Home Assistant's generic `climate.set_hvac_mode` /
`climate.set_preset_mode` / `climate.set_temperature` services — the two
Actions below are additive, for cases those don't cover.

### `honeywell_smileconnect.set_preset_mode_with_duration`

Activate a preset with a custom duration instead of the fixed vendor
default, or clear the current preset.

```yaml
action: honeywell_smileconnect.set_preset_mode_with_duration
target:
  entity_id: climate.living_room
data:
  preset_mode: Boost
  target: 45
```

`target` is a real-world value in the preset's own unit and range:

| Preset | Unit | Range |
|---|---|---|
| Boost | minutes | 30–120, step 30 |
| Party | hours | 1–12 |
| Leave | hours | 1–12 |
| Holiday | days | 1–30 |

Omit `target` to use the vendor default; set `preset_mode: none` (no
`target`) to clear whatever preset is currently active.

### `honeywell_smileconnect.set_hvac_mode_and_temperature`

Reliably set `hvac_mode` and a target temperature together in one call —
see [Known limitations](#known-limitations) for why this exists instead of
just using `climate.set_temperature` with both fields.

```yaml
action: honeywell_smileconnect.set_hvac_mode_and_temperature
target:
  entity_id: climate.living_room
data:
  hvac_mode: auto
  temperature: 20
```

`temperature` is optional; it's ignored when `hvac_mode` is `off` (see
below for why).

### `honeywell_smileconnect.set_desired_temperature`

Set one of a room's three fixed schedule temperatures — the same values the
sliders above control, but usable from automations and it returns what the
gateway actually stored.

```yaml
action: honeywell_smileconnect.set_desired_temperature
target:
  entity_id: climate.living_room
data:
  type: comfort_hi
  temperature: 21.5
```

`type` is `comfort_hi` (15–25 °C), `comfort_lo` (13–21 °C) or `night`
(12–14.5 °C). The value is rounded to the nearest 0.5 °C, and a value
outside the range for its type is rejected before anything is sent. The
response (enable "Return response" in Developer Tools) is read back from the
gateway after writing:

```yaml
type: comfort_hi
requested: 21.3
sent: 21.5
stored: 21.5
verified: true
desired_temperatures:
  comfort_hi: 21.5
  comfort_lo: 18.5
  night: 13
```

This is not the same as `climate.set_temperature`, which changes the room's
current target temperature.

### `honeywell_smileconnect.get_schedule_room`

Read a room's full weekly switching-time schedule from the gateway.

```yaml
action: honeywell_smileconnect.get_schedule_room
target:
  entity_id: climate.living_room
```

Returns an object with one list per weekday (`monday`..`sunday`); each
entry has `from`, `to`, and `type` (`H` = Comfort Hi, `L` = Comfort Lo). A
time not covered by any slot follows the room's implicit "Night" behaviour.
The response has the same shape `set_schedule_room` expects below, so it
can be read, tweaked, and written straight back.

### `honeywell_smileconnect.set_schedule_room`

Write a room's full weekly switching-time schedule. This always replaces
the **entire week** — the gateway has no partial-update endpoint, so read
the current schedule via `get_schedule_room` first if you only want to
change part of it.

```yaml
action: honeywell_smileconnect.set_schedule_room
target:
  entity_id: climate.living_room
data:
  schedule:
    monday:
      - from: "04:30"
        to: "08:30"
        type: H
      - from: "14:30"
        to: "16:30"
        type: L
    tuesday:
      - from: "04:30"
        to: "07:30"
        type: H
    # ... wednesday..sunday, or omit a day entirely for "no active slots"
```

The `schedule` field is a nested object (up to 7 days × 3 slots × 3 fields
— too large for a sane form UI), so it's entered via YAML: switch to
**Edit in YAML** in Developer Tools → Actions to type it directly. Every
defined slot must include `from`, `to`, **and** `type` — there is no
default type. The number of slots per weekday must not exceed the room's
current schedule capacity (usually 3); this is checked automatically
against a fresh read before writing, and a schedule that doesn't fit is
rejected with a clear error rather than silently failing.

### `honeywell_smileconnect.set_schedule_room_weekday`

Replace one weekday's switching-time slots, leaving every other day
unchanged — a lighter-weight alternative to `set_schedule_room` for the
common case of editing a single day.

```yaml
action: honeywell_smileconnect.set_schedule_room_weekday
target:
  entity_id: climate.living_room
data:
  weekday: monday
  slot_1_from: "04:00:00"
  slot_1_to: "08:00:00"
  slot_1_type: L
```

Up to 3 slots (`slot_1`/`slot_2`/`slot_3`), each with its own `_from`/`_to`/
`_type` fields. For a slot you want to set, give all three of its fields
together — the HA UI shows this as three checkboxes per slot that must all
be checked at once; leaving all three of a slot's fields empty clears that
slot.

## Schedule card

The integration ships a Lovelace card that edits a room's weekly switching
times the same way Home Assistant's built-in Schedule helper does: drag on
an empty area to create a block, drag a block to move it (including to
another day), drag its edges to resize, click it to edit or delete it.

It is registered automatically — **no manual entry under Settings →
Dashboards → Resources** — so after installing or updating the integration
the card is simply available. Add it via the dashboard's card picker
("Honeywell Smile Connect Schedule"), or in YAML:

```yaml
type: custom:smileconnect-schedule-card
entity: sensor.living_room_schedule
```

| Option | Default | Notes |
|---|---|---|
| `entity` | — | The room's schedule sensor. Its `climate.<room>` entity is accepted too and resolved automatically. |
| `title` | Room name | Card heading |
| `step_minutes` | `15` | Snap grid. One of 5, 10, 15, 20, 30, 60 |
| `night_gaps` | `false` | Paint the time not covered by any block in the night colour instead of leaving it neutral |
| `hour_height` | `26` | Pixel height of one hour row. The default makes a whole day fit without scrolling; raise it for a more detailed grid |
| `colors` | theme colours | Per-type overrides, e.g. `{H: "#db4437", L: "#43a047", N: "#039be5"}` |

Colours follow your theme by default: **red** for Comfort Hi (`H`), **green**
for Comfort Lo (`L`), **blue** for Night (`N`).

Things the card deliberately will not do:

- **It never creates a Night (`N`) block.** The gateway may in principle
  report one, and the card renders it blue and read-only if it ever does,
  but this integration will not write a type it has never been able to
  verify against real hardware. On an SCN-10 without the Room Connect
  SRC-10 extension this never comes up — the time outside any block simply
  uses the night temperature. If a schedule does contain such a block, the
  card switches to read-only and says so, rather than risk dropping it.
- **It will not add a fourth block to a day.** The gateway's array width is
  fixed per room (three slots per day on this hardware) and a write of the
  wrong width is rejected outright.
- **No block crosses midnight.** A block may start at `00:00` and end at
  `24:00` (the Smile App's own editor offers exactly that range), but the
  gateway requires `from` < `to`, so nothing wraps around midnight. The
  edit dialog shows a `24:00` end as `00:00`, because an HTML time field
  cannot hold `24:00`; an end of `00:00` is unambiguous since a slot
  cannot be zero-length.

The card asks the gateway for a fresh schedule whenever it is shown (and
when you return to the tab), so what is on screen is current regardless of
the background polling interval. Home Assistant debounces those requests,
so several cards or tabs collapse into one gateway read.

There is no visual (GUI) editor for the card's own options yet; the card
picker falls back to YAML.

### How the card gets loaded

You do not have to register anything. On setup — and on every Home
Assistant start — the integration adds itself to **Settings → Dashboards →
⋮ → Resources**, as a `module` entry pointing at a URL that contains a hash
of the card file, for example:

```
/honeywell_smileconnect/card/smileconnect-schedule-card.3ef4beca.js
```

**Leave that entry alone.** It is managed: when the card file changes, the
integration rewrites the existing entry to the new URL rather than adding a
second one. Editing or deleting it by hand only causes confusion — it is
restored on the next restart.

After installing or updating, **reload the browser page once**. An
already-open dashboard will not pick up a new card.

Two caveats worth knowing:

- **YAML-mode dashboards.** There the resource collection is read-only, so
  the integration logs a warning and falls back to Home Assistant's
  `extra_module_url` mechanism. That works, but without the ordering
  guarantee, so the card may need a second page load to appear. To get the
  guarantee, add the resource yourself — this URL is stable and unhashed:

  ```yaml
  lovelace:
    resources:
      - url: /honeywell_smileconnect/frontend/smileconnect-schedule-card.js
        type: module
  ```

- **Removing the integration leaves the resource entry behind.** It is not
  cleaned up automatically yet, so delete it manually under Resources after
  uninstalling; otherwise Home Assistant keeps trying to load a URL that no
  longer exists.

## Known limitations

- **`climate.set_temperature` does not reliably apply `temperature` and
  `hvac_mode` together in one call on this integration** — no error, it
  just silently applies neither. Use `set_hvac_mode_and_temperature` above,
  or two separate `climate.set_hvac_mode` / `climate.set_temperature`
  calls, both of which work reliably on their own.
- **Setting a temperature while `hvac_mode` is `off` (Standby active) is
  silently ignored by the gateway itself** — not a bug in this
  integration, the gateway's own firmware rejects it. Switch to
  `hvac_mode: auto` first.
- **Shower and Towel scenes are not exposed as entities.** The protocol
  layer supports them, but there's no test hardware with hot water control
  to verify against.
- **A switching-time slot's `type` (Comfort Hi/Lo) selects between two
  fixed, per-room temperatures already configured on the gateway/in the
  Smile App — it does not let you set an arbitrary temperature per slot.**
  Those underlying temperatures can be changed with the Comfort Hi / Comfort
  Lo / Night sliders or the `set_desired_temperature` Action above.
- **The "Night" switching-time type (`N`) is not supported in schedule
  slots.** It requires the Honeywell Room Connect SRC-10 hardware
  extension, which isn't available to verify against; only `H` (Comfort Hi)
  and `L` (Comfort Lo) are accepted for a slot's `type`. Setting the Night
  *temperature* (slider / `set_desired_temperature` with `type: night`) is
  supported.
- **No native visual weekly-schedule editor yet** — the three schedule
  Actions above are the read/write foundation; a proper UI (e.g. a native
  HA "Schedule" helper per room) and automatic gateway↔HA sync are a
  planned follow-up, deliberately scoped out of this release. See
  [`CLAUDE.md`](CLAUDE.md#next-planned-work-agreed-in-project-discussion-not-yet-started)
  for why true two-way auto-sync isn't achievable with HA's native helper
  at all.
- **No automatic reconnect if the gateway session is lost** (e.g. a
  gateway reboot) — entities go "unavailable" until Home Assistant
  restarts or the integration is reloaded. Tracked as a planned fix in
  [`CLAUDE.md`](CLAUDE.md#next-planned-work-agreed-in-project-discussion-not-yet-started).
- **Outside temperature/min/max sensors stay on the gateway device on
  multi-room installations** (an SRC-10 add-on module present), instead of
  moving to the correct Regler as they do on single-room installs. The
  gateway's `/api/weather` endpoint has no way to say which physical
  Regler a reading came from once more than one exists, and there is no
  SRC-10 hardware available to verify the right behavior against — see
  [`CLAUDE.md`](CLAUDE.md) for the full reasoning.

## Development

This repo ships a VS Code dev container with Home Assistant Core pre-installed
for local development:

1. Open the repo in VS Code, "Reopen in Container" when prompted.
2. Run `scripts/develop` to start Home Assistant with this integration loaded.
3. Edit code under `custom_components/honeywell_smileconnect/` — restart
   Home Assistant to pick up changes.

Claude Code can be used directly in the dev container's terminal; it will
automatically read [`CLAUDE.md`](CLAUDE.md) for full project context
(protocol details, open questions, conventions), so no manual copy-pasting
of prior research is needed.

See [`docs/protocol.md`](docs/protocol.md) and
[`docs/switching-times-api.md`](docs/switching-times-api.md) for the
reverse-engineered API protocol documentation.

## License

MIT — see [`LICENSE`](LICENSE). Note that this differs from the licensing
(GPL/AGPL) of the upstream `ruby-heatapp` / `py-heatapp-de` projects this
work conceptually builds upon; this codebase is an independent
implementation based on original reverse-engineering (see
[`CLAUDE.md`](CLAUDE.md) and [`docs/protocol.md`](docs/protocol.md)), not a
fork or derivative of their code.
