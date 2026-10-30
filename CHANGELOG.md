# Changelog

## 0.6.0 — 2026-10-30

- Add read-only battery mode, backup reserve, manual program and tariff program sensors.
- Read battery settings every five minutes independently of power and EV telemetry.
- Preserve stored inactive programs, explicit disabled windows and unknown activation flags.
- Expose normalized calendars, retrieval/settings timestamps and Weather Guard metadata.
- Keep endpoint failures isolated and do not infer backup activation or physical battery behavior.

## 0.1.4 — 2026-10-21

- Isolate SolarEdge portal login cookies in a dedicated session per config entry,
  using Home Assistant's automatic cleanup on unload, failed setup, and shutdown.
- Use a separate short-lived session for portal validation and release it on
  success, errors, or cancellation without closing Home Assistant's connector.

## 0.1.3 — 2026-10-18

- Resolve portal time zones asynchronously with Home Assistant's cached helper
  during configuration validation and entry setup, keeping time-zone database
  reads off the event loop.
- Reuse the resolved time zone for energy date windows and snapshot timestamps,
  preserving daylight-saving behavior and the stored configuration format.

## 0.1.2 — 2026-08-31

- Led the repository and setup flow with the main benefit: a monitoring account
  with permission to view the site is sufficient; site ownership and an API key
  are not required.
- Replaced implementation-oriented public copy with direct descriptions of the
  available telemetry and connection choices.

## 0.1.1 — 2026-08-31

- Added repository-level 256/512-pixel PNG exports of the original project mark.

## 0.1.0 — 2026-08-31

- Added experimental direct, read-only SolarEdge monitoring-portal access.
- Constrained authentication and data requests to a fixed exact-host and
  endpoint allowlist, with PKCE, bounded responses, redirect validation, and one
  authentication retry.
- Added recursive provider-payload sanitization and a second aggregate-only
  snapshot trust boundary.
- Retained the provider-neutral local bridge connection mode.
- Added thirteen aggregate metric sensors and one operating-plan sensor.
- Added strict field allowlisting, freshness validation, reauthentication,
  reconfiguration, polling options, and redacted diagnostics.
- Added neutral synthetic tests, original local brand assets, and
  HACS/hassfest/gitleaks validation workflows.

The direct portal is unofficial and may stop working if SolarEdge changes its
private web interface.
