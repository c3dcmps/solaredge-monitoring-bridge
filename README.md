<p align="center">
  <img src="assets/bridge-mark.svg" width="128" height="128" alt="Solar and battery bridge symbol">
</p>

# SolarEdge Monitoring Bridge

> **Pre-release / developer preview:** this repository currently contains the
> Home Assistant client and an open snapshot protocol. The required companion
> bridge service has **not** been released. This is not yet a general-purpose
> HACS installation.

SolarEdge Monitoring Bridge is a privacy-minimizing Home Assistant custom
integration for importing whole-site solar, grid, consumption, and battery
aggregates from a separately operated local bridge. It does not log in to a
SolarEdge account and it contains no reverse-engineered portal endpoints,
cookies, client identifiers, or credentials.

The design keeps provider access outside Home Assistant and exposes only a
small, versioned JSON contract on a local/private network. A companion may use
a documented provider API, local gateway, or another authorized source, but it
must normalize and sanitize its output before Home Assistant sees it.

## Why this exists

Home Assistant already has excellent SolarEdge integrations. This project is
for the narrower case where an operator wants:

- explicit import/export and battery charge/discharge direction entities;
- cumulative whole-site energy entities suitable for the Energy dashboard;
- a sanitized storage operating-plan label when an authorized source provides it;
- per-field completeness and observation-time provenance; and
- a strict local trust boundary between provider access and Home Assistant.

If Home Assistant's built-in SolarEdge integration or a local Modbus integration
meets your needs, prefer that simpler path.

## Current status

| Area | Status |
|---|---|
| Home Assistant config flow and sensors | Implemented |
| Snapshot schema v1 and privacy allowlist | Implemented |
| Response-size, redirect, endpoint, and freshness guards | Implemented |
| Reauthentication, reconfiguration, and polling options | Implemented |
| Redacted diagnostics | Implemented |
| HACS/hassfest validation workflows | Implemented |
| Public companion bridge | **Not released; installation blocker** |
| Stable release and HACS default listing | **Not ready** |

No release should be advertised as generally installable until a companion
bridge with documented setup, authentication, upgrades, and support is
available. An undocumented web portal is not an acceptable public dependency.

## Entities

The integration creates up to fourteen entities. Missing or explicitly
incomplete fields are unavailable rather than guessed.

- Production and consumption power
- Grid import and export power
- Battery charge and discharge power
- Battery state of energy
- Lifetime production and consumption energy
- Lifetime grid import and export energy
- Lifetime battery charged and discharged energy
- Storage operating-plan label, with bounded `active` and `block_count` attributes

Power is expressed in W, energy in kWh, and state of energy in percent. Energy
values must be lifetime or otherwise monotonically increasing counters; a
current-day total is not valid for these entities.

## Snapshot protocol

The integration performs an HTTP `GET` against a local/private endpoint and
sends one header:

```text
X-SolarEdge-Bridge-Secret: <shared secret>
```

The response must be at most 64 KiB and implement schema version 1. A minimal
synthetic response is:

```json
{
  "schema_version": 1,
  "connected": true,
  "observed_at": "2026-01-15T20:05:00Z",
  "provider": "documented_api",
  "site": {
    "production_power_w": 5400.0,
    "consumption_power_w": 3100.0,
    "grid_export_power_w": 1800.0,
    "battery_charge_power_w": 500.0,
    "battery_state_of_energy_pct": 72.4,
    "production_energy_kwh": 12345.678
  },
  "completeness": {
    "production_power_w": true,
    "consumption_power_w": true,
    "grid_export_power_w": true
  }
}
```

The complete normative contract is in
[docs/snapshot-protocol.md](docs/snapshot-protocol.md).

## Installation for protocol developers

These steps are intentionally for developers operating their own compatible
local bridge. There is no supported companion package yet.

1. Copy `custom_components/solaredge_one_bridge` into Home Assistant's
   `custom_components` directory.
2. Restart Home Assistant.
3. Add **SolarEdge Monitoring Bridge** from Settings > Devices & services.
4. Enter a local/private snapshot URL and the same high-entropy shared secret
   configured on the bridge.

The config flow refuses public fully-qualified hostnames, embedded URL
credentials, query strings, fragments, and redirects. Single-label hostnames
are allowed for add-on and container DNS.

## Security and privacy

- Only allowlisted aggregate fields are retained.
- Unknown fields are ignored, including identifiers and arbitrary metadata.
- The secret and endpoint are redacted from diagnostics.
- Raw values are omitted from downloadable diagnostics.
- HTTP redirects are rejected to prevent a local endpoint from forwarding the
  authenticated request elsewhere.
- Connected snapshots must be recent and timezone-aware.
- Public TLS termination does not make a public endpoint acceptable; this
  client is deliberately local-only.

The shared-secret header protects against accidental local access. It is not a
replacement for network isolation. Prefer loopback or a private container/add-on
network and use HTTPS when the request crosses a network you do not fully trust.

See [SECURITY.md](SECURITY.md) for vulnerability reporting.

## Development

```bash
python -m pip install pytest aiohttp ruff
ruff check .
pytest -q
```

GitHub Actions also runs HACS validation and Home Assistant hassfest. See
[CONTRIBUTING.md](CONTRIBUTING.md) before proposing protocol changes.

## Trademark and affiliation

This community project is not affiliated with, sponsored by, or endorsed by
SolarEdge Technologies. SolarEdge is a trademark of its respective owner. The
project's bridge artwork is original and does not reproduce the SolarEdge logo.

## License

Apache License 2.0. See [LICENSE](LICENSE).

