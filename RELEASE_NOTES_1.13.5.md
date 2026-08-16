# HAM Spotter 1.13.5

Updater reliability maintenance release.

## Fixed
- Fixes `Permission denied` when a version-specific `upgrade_v*.sh` helper loses its executable bit during ZIP extraction.
- The manager marks all `upgrade*.sh` helpers executable before migrations.
- Upgrade wrappers are invoked through `bash` as an additional compatibility safeguard.
- Upgrading from older HAM Spotter managers no longer requires a manual `chmod`.

## Regression coverage
A new automated test reproduces the real failure mode with non-executable upgrade scripts and verifies that the update completes.

## Existing installations
`.env`, SQLite data and backups remain protected during the update.
