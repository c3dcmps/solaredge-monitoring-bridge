# Daily energy implementation plan

**Goal:** Expose six current-local-day energy sensors alongside existing lifetime counters.

**Approved scope:** Production, consumption, grid import/export, battery charge/discharge;
direct SolarEdge daily totals, refreshed every five minutes, invalidated at local midnight.
Existing counters and charger controls remain intact. No dependencies or new settings.

**Architecture:** Extend the sanitized snapshot with six allowlisted daily values and their
calendar date. The portal adapter fetches the existing current-day endpoint using a
five-minute, date-scoped cache. Dedicated daily sensor availability rejects yesterday's
values. Missing data and daily-only request failures never become zero or break live flow.
Use the configured site timezone, including daylight-saving changes. Midnight is processed
on the next normal integration poll (60 seconds on the installed instance).

**Tech stack:** Existing Python/aiohttp integration, pytest, Ruff, ty, HACS.

## Review focus

- Cache must expire at 300 seconds and immediately when the local calendar date changes.
- A request spanning midnight must not label yesterday's results as today's.
- Partial, negative, nonfinite and absent values must remain unavailable.
- Daily API failure must preserve lifetime/power values, without exposing provider secrets.
- Existing lifetime entities keep their IDs and six-hour cache; bridge mode gets no daily entities.

## Tasks

- [x] Add failing tests for snapshot mapping, date scoping, caching, midnight/DST,
  network failure/recovery, sensor availability and portal-only setup.
- [x] Extend model.py, portal_client.py and sensor.py with date-tagged daily metrics;
  use existing portal.current_day_energy_summary(local_date=...) and field mapping.
- [x] Add English names, README behavior and version 0.4.0, preserving cumulative sensors.
- [x] Run pytest, Ruff, targeted ty, privacy scan and whitespace checks.
- [x] Review the complete change independently, fix substantive findings with regressions.
- [ ] Open and merge a PR, publish v0.4.0, install through HACS and restart HA.
- [ ] Verify six daily states, units/date, existing counters, buttons and integration logs.

## Execution record

- Baseline: 133 tests and 6 subtests pass on main 722c00f.
- Execution is inline; the user approved the described six-sensor change.
- Independent review identified a missed-reset statistics case after an outage.
  Ruling: daily sensors use `TOTAL` plus explicit UTC `last_reset` at local midnight;
  lifetime sensors retain their existing metadata. Three regressions cover regular,
  spring and autumn days with a higher first total on the new day (observed red).

- Final verification: 163 tests and 6 subtests pass, Ruff and targeted ty pass,
  privacy scan and whitespace checks pass. Independent review approved 4e9466b.
- Live staging: all six daily sensors expose numeric kWh values, the current
  local calendar date and the correct UTC last_reset. Existing lifetime/power
  entities and EV buttons remain available; polling options remain at 60 seconds.
  No integration-specific runtime errors were found after restart.
- The app comparison exposed a semantic difference: total grid import includes
  flows not shown in the app's grid-to-home consumption breakdown. A separate
  Modbus meter history check confirmed the portal's total import. The two daily
  battery labels are explicit about solar-to-battery and battery-to-home because
  they use production/consumption distribution fields, not whole-battery totals.
  Lifetime battery counters keep their prior behavior.
- Scope addition approved after app comparison: add a seventh daily sensor for
  grid energy supplied directly to the home, from consumption distribution.
  Keep it separate from total grid import, which also includes battery charging
  and is the relevant measure for purchased energy. Reuse the same daily request.
