# HAM Spotter 1.13.9

RBN node-directory synchronization reliability release.

## Fixed

- A successful RBN node refresh now replaces the local `rbn_nodes` snapshot atomically instead of only inserting/updating rows.
- Nodes that disappeared from the current upstream RBN directory are therefore removed locally on the next successful refresh.
- Empty or invalid refresh results are rejected before replacement, preserving the last known-good node list during upstream failures.

## Tests

- Added regression coverage proving that a previously valid but stale node is removed by the next snapshot.
- Added regression coverage proving that an empty snapshot cannot wipe existing RBN node data.

Existing `.env`, SQLite spot/history data and backups remain protected during update.
