# EV charger controls implementation plan

**Goal:** Add Charge now and Stop charging buttons to each discovered portal EV device.

**Approved scope:** The user's requested next version extends the deployed v0.2.1
monitoring integration with the two manual SolarEdge commands previously tested in
the browser. Continue the established PR, merge, version and HACS deployment workflow.

**Architecture:** Initialize the shared EV coordinator before forwarding sensor/button
platforms. Each command resolves its opaque charger key against a fresh site device
list; raw reporter IDs exist only transiently while building the fixed PUT URL. Use
the existing private session and Bearer OAuth header, adding CSRF when supplied. Check both HTTP and application
validation. Serialize commands and request a state refresh without optimistic values.

**Version:** 0.3.0. Existing interval and sensor identities remain unchanged.

## Constraints

- Start body: `{"mode":"MANUAL","level":100,"duration":null}`.
- Stop body: `{"mode":"MANUAL","level":0,"duration":null}`.
- PUT only to the fixed monitoring host and activationState route.
- Require Bearer OAuth authorization; add CSRF when issued and reject redirects or failed validation.
- Never retry a PUT automatically after a timeout, HTTP failure or ambiguous response.
- Raw identifiers, tokens, provider payloads and error messages must not be logged.
- Do not infer physical charging or persistent inhibition from an accepted command.
- Stop cancels the manual demand; the server-reported mode remains authoritative.
- No timer, charging automation, schedule modification, UniFi or KEBA operation.
- No new dependency. The local snapshot bridge remains read-only.

## Task 1 — Hardened transport

- [x] Add failing tests for resolving known/unknown/duplicate/site-scoped charger IDs.
- [x] Add failing tests for fixed PUT route and exact start/stop JSON bodies, CSRF,
  redirects, HTTP/application rejection, malformed results and no write retries.
- [x] Add a pure reporter-ID resolver and the specific portal command method.
- [x] Add the snapshot adapter forwarding method and verify the transport tests.

## Task 2 — Shared lifecycle and buttons

- [x] Move EV initialization into config-entry setup before both platform setups.
- [x] Add failing tests for command serialization, unavailable devices, error reporting,
  refresh requests, dynamic button discovery, correct device identity and local mode.
- [x] Add button platform and coordinator command method, preserving sensor identities.
- [x] Add translations, change read-only documentation where applicable and set 0.3.0.

## Task 3 — Verification and delivery

- [x] Run all tests, ruff, targeted ty checks, privacy and whitespace checks.
- [x] Obtain independent review in a separate worktree and address findings.
- [ ] Publish PR, merge and release without rewriting any published version.
- [ ] Install the pinned version through HACS, compare files and restart HA.
- [ ] Verify both buttons, original sensors, 60-second interval and own logs.
- [x] Verify a brief start/stop using prior test authorization and restore AUTO;
  if actuation is unavailable, report the exact limitation without claiming success.

## Evidence and execution

- Base: main commit `43ba203`, deployed v0.2.1.
- Baseline tests: 95 tests and 6 subtests.
- Inline execution in `/private/tmp/solaredge-monitoring-bridge-ev-controls`.

- Verified before publication: 130 tests and 6 subtests; ruff, targeted ty, privacy
  scanning and whitespace checks pass.
- Independent review found an interrupted PUT body could bypass sanitized error
  handling and refresh. Four regression cases reproduced it; the fix wraps only
  transport errors without retrying the write. Final review has no blocking finding.
- Live validation will use the reviewed feature branch before the final release.

- Live session diagnostics showed Bearer OAuth authorization with no CSRF or legacy
  cookie. The initial client-side CSRF requirement blocked a valid OAuth session;
  removing that assumption allowed both native commands to pass server validation.
- Runtime test: Charge now accepted, KEBA charging state became on (70 W observed
  during the short handshake); Stop charging accepted, KEBA returned off and 0 W.
- The provider activationMode remained AUTO during the short test; it must not be
  interpreted as a reliable manual-override flag. Provider status propagation can lag.
- Safe diagnostics expose boolean session flags only. Action errors expose only
  fixed reasons and validated numeric HTTP status, never raw provider messages.

- Final auth/error/diagnostic review: no blocking findings; 133 tests and 6 subtests pass.
