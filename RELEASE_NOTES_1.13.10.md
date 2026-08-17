# HAM Spotter 1.13.10

Reliability-hardening release for RBN node synchronization, CI validation and raw spot retention.

## Reliability

- RBN node refreshes now preserve the last known-good node directory when a new upstream snapshot is implausibly small.
- Once at least 50 nodes are known locally, a new valid snapshot containing less than 50% of the previous node count is rejected instead of replacing the local table.
- A rejected partial snapshot is reported as `DEGRADED`; normal plausible snapshots continue to replace the directory atomically.
- Existing protection against empty or invalid RBN snapshots remains in place.

## Data retention

- Fresh installations now default to 24 hours of raw spot retention instead of 72 hours.
- Existing installations keep their current `.env` value during update; no retention setting is rewritten automatically.
- Compact long-lived history such as opening events and Rare-DX learning remains separate from raw spot retention.

## CI / Tests

- GitHub Actions now runs the complete pytest suite rather than a manually maintained subset.
- The release workflow uses the same full-suite validation before publishing assets.
- Legacy tests were refreshed where they still depended on historical version strings, a fixed future timestamp or station-specific defaults.
- Validation for this release passes with 80 tests, Python syntax checks, shell syntax checks and release packaging verification.

Existing `.env`, SQLite spot/history data and backups remain protected during update.
