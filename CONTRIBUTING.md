# Contributing

This project is in a prerelease protocol-design phase. Changes should keep the
Home Assistant integration a thin, read-only client of the documented local
snapshot contract.

Before opening a change:

1. Do not add undocumented provider portal endpoints, private client
   identifiers, copied web-session behavior, or credentials.
2. Keep all provider data behind the snapshot allowlist.
3. Add neutral synthetic tests for every contract or validation change.
4. Run `ruff check .` and `pytest -q`.
5. Explain compatibility and privacy effects of schema changes.

Provider adapters are welcome only when they use an interface the provider
documents for third-party use and their authentication and rate-limit behavior
can be documented without secrets.

