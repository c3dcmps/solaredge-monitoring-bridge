# Local snapshot protocol version 1

This document defines the only provider-facing boundary consumed by the Home
Assistant integration. It deliberately does not define how a companion obtains
provider data.

## Transport

- Method: `GET`
- Scheme: HTTP or HTTPS
- Location: loopback, private/link-local IP, or a single-label local hostname
- Authentication header: `X-SolarEdge-Bridge-Secret`
- Redirects: forbidden
- Successful status: `200`
- Authentication failure: `401` or `403`
- Maximum response body: 65,536 bytes
- Body encoding: UTF-8 JSON object

The bridge should use constant-time comparison for the shared secret and return
no provider details in error bodies. Generate at least 32 random bytes for the
secret and never place it in a URL.

## Envelope

| Field | Type | Required | Meaning |
|---|---|---:|---|
| `schema_version` | integer | yes | Must be exactly `1` |
| `connected` | boolean | yes | Whether the companion currently has an authoritative provider observation |
| `observed_at` | ISO 8601 string or null | yes | Time of the observation; timezone required when connected |
| `provider` | string or null | no | Sanitized source class, maximum 64 characters |
| `site` | object | yes | Allowlisted whole-site metrics |
| `completeness` | object | no | Boolean completeness by allowlisted key |

When `connected` is true, `observed_at` is mandatory. Home Assistant rejects a
connected snapshot older than its configured freshness limit or more than five
minutes in the future. When `connected` is false, all entities are unavailable.

Unknown envelope, site, and completeness fields are ignored by design. A
companion must nevertheless avoid emitting account data, location, serial
numbers, cookies, tokens, raw policies, or other identifiers.

## Site fields

All numeric values must be finite, non-negative JSON numbers. Booleans and
numeric strings are not numbers in this protocol.

| Key | Unit | Semantics |
|---|---:|---|
| `production_power_w` | W | Current whole-site production magnitude |
| `consumption_power_w` | W | Current whole-site consumption magnitude |
| `grid_import_power_w` | W | Current import magnitude; zero when not importing |
| `grid_export_power_w` | W | Current export magnitude; zero when not exporting |
| `battery_charge_power_w` | W | Current charge magnitude; zero when not charging |
| `battery_discharge_power_w` | W | Current discharge magnitude; zero when not discharging |
| `battery_state_of_energy_pct` | % | State of energy from 0 through 100 |
| `production_energy_kwh` | kWh | Lifetime monotonically increasing production |
| `consumption_energy_kwh` | kWh | Lifetime monotonically increasing consumption |
| `grid_import_energy_kwh` | kWh | Lifetime monotonically increasing import |
| `grid_export_energy_kwh` | kWh | Lifetime monotonically increasing export |
| `battery_charge_energy_kwh` | kWh | Lifetime monotonically increasing charged energy |
| `battery_discharge_energy_kwh` | kWh | Lifetime monotonically increasing discharged energy |
| `storage_operating_plan` | string | Optional concise current plan label, maximum 64 characters |
| `storage_operating_plan_active` | boolean | Optional provider-reported active flag |
| `storage_operating_plan_block_count` | integer | Optional count from 0 through 1000 |

Directional power keys are magnitudes. A companion must split any signed source
value into mutually exclusive direction keys and document its sign convention.
Do not calculate lifetime energy counters by repeatedly integrating samples in
an ephemeral process unless durable monotonic state and reset behavior are
explicitly implemented.

## Completeness

`completeness` may contain a boolean for each allowed site key. `false` makes
that entity unavailable even if a numeric value is present. Omitted keys mean
that the bridge made no separate completeness assertion. This mechanism should
represent source coverage, not confidence guesses.

## Compatibility

Changes that alter field meaning or validation require a new integer schema
version. New optional fields may be added to the protocol, but version 1 clients
will ignore them until explicitly allowlisted.

## Explicit non-goals

This protocol does not expose or standardize:

- account, site, installation, equipment, or location identifiers;
- credentials, tokens, cookies, headers, or provider request payloads;
- command/control operations;
- battery reserve or curtailment setpoints;
- actor-attributed dispatch or configuration-change history; or
- raw storage-plan schedules or policy documents.

