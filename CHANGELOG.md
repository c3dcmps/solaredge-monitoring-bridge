# Changelog

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
