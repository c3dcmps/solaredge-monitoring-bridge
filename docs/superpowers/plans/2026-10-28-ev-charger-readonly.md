# EV charger read-only implementation plan

**Goal:** Discover SolarEdge EV chargers and expose status, activation mode and available power.

**Approved scope:** Read-only, using the configured portal credentials and polling interval.
No charging commands or live HA installation in this change.

**Architecture:** Keep the aggregate snapshot protocol unchanged. A separate EV coordinator
uses the same private portal session; EV errors cannot fail aggregate updates. Each charger
has a stable hashed site/reporter identity and three sensor entities. Raw identifiers,
serial numbers, names, credentials and provider payloads never become sensor attributes.

**Tech stack:** Existing Python, aiohttp and Home Assistant APIs; no runtime dependency added.

## Constraints and review focus

- Fixed HTTPS GET endpoint for device discovery; bounded responses and existing auth safeguards.
- Missing values remain unavailable. An empty successful list means no chargers.
- Malformed envelopes and authorization failures are errors, not empty successful lists.
- Duplicate/invalid reporter IDs cannot create ambiguous devices.
- Aggregate EV power may be assigned only when exactly one charger is present.
- Newly appearing chargers are added after startup; removed chargers become unavailable.
- EV discovery must keep polling after an initial failure or empty list.
- Both coordinators and their listeners must unload with the config entry.

## Task 1 — Portal discovery and trust boundary

- [x] Add failing tests in `tests/test_ev_charger.py` for fixed GET routing, response failures,
  hashed identities, field allowlisting, missing/malformed data and multiple chargers.
- [x] Add `ev_charger.py`: immutable `EVChargerSnapshot` and strict `parse_ev_chargers`.
- [x] Extend `portal.py` with `ev_chargers()` using the fixed homeautomation endpoint.
- [x] Extend `portal_client.py` with `async_get_ev_chargers()` and cached flow power reuse.
- [x] Verify the targeted tests and existing authentication tests.

## Task 2 — HA discovery and sensors

- [x] Add failing behavior tests for EV coordinator failures, entity availability,
  value updates, new/removed devices, and cleanup.
- [x] Add `ev_coordinator.py` and `ev_sensor.py` with independent polling and dynamic discovery.
- [x] Expose the portal client from `coordinator.py`; call EV sensor setup from `sensor.py`.
- [x] Add sensor names to `strings.json` and `translations/en.json`.
- [x] Update diagnostics with EV health/count only, and document entities/limits in README.

## Task 3 — Verification

- [x] Run the complete existing suite plus new behavioral tests; verify red/green cycles.
- [x] Run ruff, type checks for the new boundary, packaging and privacy validation.
- [x] Review the diff for network destinations, secret exposure and lifecycle errors.
- [x] Deliver the branch and patch without altering the running HA integration.

## Execution record

- Baseline: 53 tests and 6 subtests pass on Python 3.13.
- Worktree: `/private/tmp/solaredge-monitoring-bridge-ev-readonly`.
- Branch: `feat/ev-charger-readonly`, based on `8e9476b`.
- Execution: inline, continuing the user's approved read-only scope.

## Verification and review

- Final suite: 95 tests and 6 subtests pass.
- Ruff lint and new-module complexity checks pass.
- ty checks pass for ev_charger.py, portal.py and portal_client.py.
- Privacy scan and git diff whitespace checks pass.
- HA entity and coordinator tests execute production entry points with framework
  boundary doubles. Full HA runtime imports, scheduling and shutdown were not exercised.
- No real account credentials or live charging requests were used in tests.
- Independent review found two issues, both reproduced and fixed: optional power
  failures must preserve status/mode; missing/invalid timestamps must not imply fresh power.
- No remaining blocking findings after review of the fixes.
- The pre-existing offline manifest script still expects local_polling although the
  current integration manifest is cloud_polling; the existing manifest tests pass.
  That unrelated script discrepancy is outside this change.
- No deployment, remote push, merge or new runtime dependency.
