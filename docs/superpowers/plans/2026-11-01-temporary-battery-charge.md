# Temporary battery charging implementation plan

> **For agentic workers:** Use superpowers:executing-plans task by task.

**Goal:** Schedule and cancel one temporary battery charging window through the existing portal.

**Architecture:** A pure planner validates complete fresh settings and builds an all-days/all-months
CHARGING window. A serialized manager persists intent before a single PUT, verifies by GET, and
removes the owned window after its end. HA services and a one-minute recovery timer wrap it.

**Tech Stack:** Python, existing aiohttp client, HA Store and service registry; no dependencies.

**Spec:** User-approved scope in the conversation: quarter-hour start/end, preserve programs,
automatic persisted cleanup, explicit cancellation, conflict/capacity/overlap refusals,
MANUAL_TOU only, no reserve changes. SolarEdge controls the physical end, with observed lag.

## Constraints and implementation choices

- Services `schedule_battery_charge` (start/end ISO datetimes) and `cancel_battery_charge`.
- One integration entry exists by design. Services resolve its runtime manager, portal mode only.
- Site timezone controls local minutes; require aware datetimes, same local date, positive future
  interval, quarter-hour boundaries, end within 24 hours; refuse midnight crossing and DST folds.
- New slot repeats all days and all months; must be removed to avoid recurrence.
- Preserve the four known writable configuration fields in full; never persist response metadata,
  site identifiers, authentication material or reserve endpoint data.
- Persist before PUT; never retry an addition automatically or reauthenticate/replay a PUT.
- Normalize only known calendar list order. Refuse automatic removal if other settings changed.
- Cleanup reads fresh state each time: original => clear; expected => PUT original and verify;
  conflict => visible persistent notification, keep record for manual resolution.
- Interrupted reads/writes retain intent; retry cleanup every minute, including after HA restart.
- Unload leaves persisted intent, cancels local timer; integration must run to remove recurrence.
- No real equipment commands in tests or runtime validation.

## Review focus

- Restart between persistence and PUT; ambiguous applied PUT must not duplicate a window.
- Concurrent schedule/cancel/timer calls must serialize across the entire read/write sequence.
- Remote changes, calendar permutations and disabled or malformed slots must not be overwritten.
- Offline cleanup must retain state and raise a notification, recovering without replaying add.
- Time boundaries, timezone mismatch, DST ambiguity and invalid stored state must fail safely.

## Tasks

- [x] Add tests for pure calendar planning, preservation, refusals and semantic comparison;
  run red; implement `battery_charge_schedule.py`; run green.
- [x] Test durable manager with fake external boundaries: add/cancel, ambiguous write, offline,
  conflict, crash recovery, persistence failure, serialization; implement `battery_charge.py`.
- [x] Add fixed-origin raw-config GET/PUT transport using current bearer, bounded envelope
  validation, no redirect or retry; test rejection, interrupted responses and privacy.
- [x] Wire HA Store, one-minute recovery, visible notifications, service schemas/translations,
  setup/unload and refresh; test framework boundaries and service targeting.
- [ ] Document usage and limits, run relevant and regression tests, Ruff, targeted ty, privacy;
  independent review before integration. Prepare PR for review; no physical command.

## Execution ledger

- Planner, manager, transport and service tests written red, then implemented green.
- HA Store can log write errors without raising; added independent Store readback before any
  remote write. Storage uses private, atomic writes; regression test red then green.
- Preserved existing test module identity instead of reloading portal exception classes, fixing
  test-order-dependent exception mismatches exposed by the new tests.
- Configuration equality distinguishes JSON booleans from numeric values; regression tested.
- Metadata targets version 0.7.0. No hardware commands, HA restart or deployment performed.

- Ruling: after an app conflict, cancellation can acknowledge a verified manual removal if
  no current manual window overlaps the owned interval on any calendar. Clear only local
  ownership, without any remote PUT; preserve all other app changes. Tested red then green.

- Independent review found three P2 issues, reproduced with failing tests: incomplete
  configuration acceptance, stopping cleanup on a failed platform unload, and clearing
  unresolved warnings at restart. Require every writable key, stop only after successful
  unload, and reconcile restored intent by fresh GET before clearing warnings.
- A queued recovery race was reproduced and fixed by evaluating deadlines under the same
  manager lock as scheduling/cancellation. Tests cover a new operation queued behind cancel.
