# HAM Spotter 1.13.8

Reverse Beacon Network node-directory compatibility release.

## Fixed

- The RBN node directory endpoint now returns `application/json` rows containing `call` and `grid`; HAM Spotter now parses that format directly.
- The existing HTML table parser remains as a fallback for compatibility.
- RBN node refresh can again populate/update the local node directory instead of reporting `RBN node page parsed, but no callsign/grid pairs were found`.

## Tests

- Added regression tests based on the current RBN JSON row shape.
- JSON wrapper/alias handling and the legacy HTML fallback are covered.

Existing `.env`, SQLite data and backups remain protected during update.
