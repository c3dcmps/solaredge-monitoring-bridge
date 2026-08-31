# Changelog

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

