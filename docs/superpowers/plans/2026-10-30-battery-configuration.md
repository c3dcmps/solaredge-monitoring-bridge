# Read-only battery configuration implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans
> to implement this plan task-by-task. Preserve native execution already chosen.

**Goal:** Expose four read-only battery configuration sensors from the portal every five minutes.

**Architecture:** Reuse the authenticated homeautomation GET transport and its fixed origin.
An independent coordinator reads mode/programs and reserve metadata separately; failure in
one endpoint must not hide the other endpoint's data or affect power/EV telemetry.
A pure bounded parser publishes typed allowlisted configuration, never raw device information.

**Tech Stack:** Existing Python/aiohttp/Home Assistant, no new runtime dependencies.

**Spec:** User approved four sensors in chat on 2026-10-30:
mode, reserve percentage, manual schedule count/details, tariff schedule count/details;
Weather Guard in reserve attributes; both programs remain visible; polling every five minutes.

## Global constraints

- Read-only GET; no battery writes, new credentials, Modbus or UniFi calls.
- Existing fast telemetry and EV controls retain their cadence and behavior.
- Use only confirmed paths: `/storage/{site_id}/batteryMode` and
  `/storage/{site_id}/getBatteries?triggerHF=false` on the existing devices base URL.
- Reserve is `userBackupReserveValue`, not `backupMinSOE` or instantaneous SoC.
- Do not infer backup activation, physical charging, or program activation from a reserve value.
- Preserve both configured programs; identify the selected mode separately from enabled flags.
- Explicit unknown/malformed fields are unavailable, never fake zero/false/empty schedules.
- Bound recursion/collections and validate numeric clocks, weekdays and months.
- Retain only normalized mode labels, clocks, calendars, booleans, percentages and timestamps.
- Four entities on the existing monitoring device; no new helpers or writable entities.

## Review focus

- Tariff program persists under MANUAL_TOU and must not appear selected.
- One endpoint unavailable must preserve independently successful settings.
- Absent enable flags must remain unknown, not silently become enabled or disabled.
- Unrecognized future enums must not expose arbitrary text or invent behavior.
- Missing battery, malformed envelopes and credential-like payloads must not leak or fake settings.

### Task 1: Typed configuration parser

**Files:** Create `battery_configuration.py`, `tests/test_battery_configuration.py`.

**Interface:** `parse_battery_configuration(mode_payload, reserve_payload, *, time_zone, now)`
returns a frozen `BatteryConfigurationSnapshot` with four sensor values and sanitized attributes.
Mode/reserve payloads may independently be `None` after an unavailable endpoint.

- [ ] Write failing behavior tests using confirmed MANUAL_TOU, manual 05:00–06:15/16:00–17:00,
  stored tariff 07:00–12:00/17:00–23:00 and reserve 15 versus minimum 0.
- [ ] Run pytest; confirm feature is missing.
- [ ] Implement dataclasses and parser with strict finite percentages, bounded enum labels,
  valid envelope, normalized months/weekdays, minute clocks, overnight markers and enabled flags.
- [ ] Verify empty versus missing programs; partial/malformed input; unknown enums; disabled slots;
  midnight boundaries; independent endpoint failure; no raw identifiers or tokens.

### Task 2: Portal read boundary

**Files:** Modify `portal.py`, `portal_client.py`; extend portal/client tests.

**Interfaces:** Portal `battery_configuration()` / `battery_reserve()` return raw GET payloads.
Adapter `async_get_battery_configuration()` returns the typed snapshot from Task 1.

- [ ] Write failing transport tests checking GET path/query, same origin, bearer, no redirects,
  failed envelope and auth recovery; snapshot tests preserve independently successful reads.
- [ ] Run relevant tests and confirm missing methods fail.
- [ ] Add two fixed read methods using `_authenticated_get(device_list=True)`.
- [ ] Adapter reads both independently and passes failed reads as None; no raw data stored.
- [ ] Run portal/parser/adapter tests and verify unchanged EV behavior.

### Task 3: Five-minute coordinator and four HA sensors

**Files:** Create `battery_configuration_sensor.py`, entity tests; wire `__init__.py`,
`coordinator.py`, `sensor.py`, strings and en translations.

**Interface:** `SolarEdgeBatteryConfigurationCoordinator` reads the adapter every 300 seconds.
`async_setup_battery_configuration_sensors()` creates four read-only sensors in portal mode.

- [ ] Write failing entity tests for four values, program attributes, reserve Weather Guard,
  endpoint-specific availability, failure/recovery, no stale attributes and bridge mode exclusion.
- [ ] Run tests and confirm missing behavior.
- [ ] Implement coordinator, four sensors, wiring and translations using existing device identity.
- [ ] No total-increasing/measurement statistics for configured settings or program counts.
- [ ] Verify first fetch failure does not fail integration setup; successful recovery populates sensors.

### Task 4: Documentation, verification and release

**Files:** README, CHANGELOG, manifest version 0.6.0 and this execution record.

- [ ] Document exact field semantics, five-minute GET polling, unknown backup activation,
  modes versus physical flow and the distinction between configured/inactive programs.
- [ ] Run tests, Ruff/format, complexity <=8, targeted ty, privacy scan and diff checks.
- [ ] Obtain independent whole-branch review in its own worktree and fix findings.
- [ ] Commit, push feature branch, open PR, merge, release v0.6.0 and install via HACS.
- [ ] Verify actual four entities, mode MANUAL_TOU, reserve 15, both counts 2, program times,
  cadence, existing EV/power availability and no integration errors.
- [ ] If live runtime cannot be inspected, report the exact limitation without claiming success.

## Execution record

- Approval: four sensors explicitly approved in chat; release/deployment workflow previously authorized.
- Ruling: native worktree tool has no repository parameter and targets this HA task checkout;
  use Git worktree fallback for the separate integration repository.
- Ruling: preserve the user-approved native execution and proceed without another approval round
  for this implementation plan; it implements the four-capteur interface just approved.
- Pre-flight: Task 2 produces typed snapshots consumed by Task 3; signatures agree.
- Base: clean origin/main 564e3cd; isolated branch feat/battery-configuration-readonly.
- Tasks 1–3 complete in c498934: 50 added behavior cases, suite 260 + 6 subtests passed.
- Parser TDD: 38 cases failed on missing module, then all passed; seven client cases failed
  on missing methods / malformed nested action, then passed; five entity cases failed
  on missing sensor module, then passed.
- Ruling: preserve bounded uppercase provider mode codes instead of guessing undocumented
  enum translations. Selection is known for confirmed MSC/MANUAL_TOU only; other codes
  retain the mode value and leave program selection unknown.
- Test boundary repair: defer the parser test helper import to avoid reloading portal error
  classes during pytest collection; update existing AST harness globals for new setup calls.
- Ruff, formatting, complexity <=8, targeted ty and privacy scan passed before review.
- Independent review requested against c498934 in a separate detached worktree.
