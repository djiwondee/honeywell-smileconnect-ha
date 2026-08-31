# Honeywell Smile Connect — Home Assistant Integration

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![Version](https://img.shields.io/badge/version-0.0.16-yellow.svg)](https://github.com/djiwondee/honeywell-smileconnect-ha/releases)
[![Status](https://img.shields.io/badge/status-pre--alpha-orange.svg)](CLAUDE.md#versioning--branching-strategy)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Validate](https://github.com/djiwondee/honeywell-smileconnect-ha/actions/workflows/validate.yml/badge.svg)](https://github.com/djiwondee/honeywell-smileconnect-ha/actions/workflows/validate.yml)
[![Lint](https://github.com/djiwondee/honeywell-smileconnect-ha/actions/workflows/lint.yml/badge.svg)](https://github.com/djiwondee/honeywell-smileconnect-ha/actions/workflows/lint.yml)

A HACS-compatible Home Assistant custom integration for the **Honeywell Smile
Connect** heating gateway (model **SCN-10**).

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

Early-stage / actively developed. Core functionality (login, room list,
temperature control, scene activation) has been verified against a real
SCN-10 gateway. See [`CLAUDE.md`](CLAUDE.md) for the current state, open
questions, and architecture notes.

## Features

- Local polling, no cloud dependency
- Per-room climate entities (temperature read/set)
- Scene-based presets: Boost, Party, Leave, Holiday, Standby

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

See [`docs/protocol.md`](docs/protocol.md) for the reverse-engineered API
protocol documentation.

## License

MIT — see [`LICENSE`](LICENSE). Note that this differs from the licensing
(GPL/AGPL) of the upstream `ruby-heatapp` / `py-heatapp-de` projects this
work conceptually builds upon; this codebase is an independent
implementation based on original reverse-engineering (see
[`CLAUDE.md`](CLAUDE.md) and [`docs/protocol.md`](docs/protocol.md)), not a
fork or derivative of their code.
