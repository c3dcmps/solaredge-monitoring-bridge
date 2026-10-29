# Read-only EV schedules

**Goal:** Expose configured charging windows and the next provider-reported window
in Home Assistant, refreshing schedules every five minutes independently of EV
status/power and manual controls.

**User requirement:** Retain configured but disabled schedules; they must never
be considered the next enabled window. Do not add schedule editing in this release.

## Confirmed transport and schema

The existing fixed device-list GET contains `deviceTriggers`: boolean `enable`,
minute-of-day `startTime`/`endTime`, enum `scheduledDays`, `smartSaver`, and a private
trigger ID. `scheduleTitle=NEXT_SCHEDULED_CHARGE` and `scheduleInfo.startDate/endDate`
carry Unix milliseconds. Public examples retain disabled windows and omit the next
window in several inactive modes. No additional credentials or endpoint are needed.
Source: briadelour/SolarEdgeEVCharger public example_raw_json.zip, inspected locally;
https://github.com/orgs/home-assistant/discussions/302 .

## Decisions

- Two entities on the existing EV device: configured schedule count (attributes
  with enabled flag, weekdays, start/end) and next-start timestamp (end attribute).
- Keep all valid configured slots, including disabled ones. Expose enabled_count.
- Use only a provider-reported future interval matching an enabled fixed schedule
  in the site's timezone. No speculative next-date calculation in this release.
- Missing or invalid schedule configuration is unavailable, not an empty list.
  Explicitly empty and all-disabled lists are valid; next-start is unknown with
  a reason. Reject unsupported smart-saver semantics rather than misinterpreting them.
- Use an independent 300-second coordinator and the existing OAuth GET transport.
  Schedule failures cannot disable the existing status/power/buttons or site sensors.
- Strip trigger/reporter IDs and arbitrary strings. Store only bounded typed fields.

## Tasks

- [x] Tests first: disabled/empty/malformed configurations, overnight windows,
  timestamps in milliseconds, timezone/DST matching, stale/mismatched next windows,
  identity scoping, privacy, coordinator failure/recovery and HA discovery/attributes.
- [x] Pure parser in ev_schedule.py; device identity routing in ev_charger.py;
  forwarding method in portal_client.py; independent schedule coordinator/sensors;
  initialize it before platform forwarding and register schedule entities.
- [x] Document the read-only semantics and add names; version 0.5.0, no dependencies.
- [x] Verify tests, Ruff, targeted ty, privacy and whitespace; independent review.
- [ ] PR/release/install and verify actual configured/disabled slots and next window.

## Baseline

Main 417a888: 170 tests and 6 subtests pass. Isolated branch
feat/ev-schedules-readonly. No charger command or schedule write is part of this work.

## Validation

- 210 tests and 6 subtests pass; Ruff, complexity, targeted ty, privacy and
  whitespace checks pass. Independent review approved 5cc4686.
- The parser also accepts all eight public reference responses and preserves
  disabled windows without inventing a next date.
- Live staging in HA: configured/disabled counts and the provider next interval
  match the parsed enabled window in the site timezone. The interval correctly
  spans midnight. Both new sensors share the existing EV device, the integration
  is loaded and original 60-second polling settings are retained. No integration
  runtime errors or charger/schedule commands were issued during validation.
