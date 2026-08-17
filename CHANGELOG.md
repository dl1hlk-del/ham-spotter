# Changelog

All notable user-facing changes to HAM Spotter are documented here.

## 1.13.9 — 2026-08-17

### Fixed
- Successful RBN node refreshes now replace the local node table atomically so removed upstream nodes do not remain as stale local entries.
- Empty or invalid refresh results are rejected before any database replacement, preserving the last known-good node directory.

### Tests
- Added regression coverage proving that stale valid nodes are removed on refresh and that an empty snapshot cannot wipe existing node data.

## 1.13.8 — 2026-08-17

### Fixed
- RBN node-directory refresh now parses the current JSON response (`call` + `grid`) from Reverse Beacon Network.
- The legacy HTML-table parser remains available as a fallback.

### Tests
- Added regression coverage for the current RBN JSON format, wrapper aliases and HTML fallback.

## 1.13.7 — 2026-08-17

### Fixed
- Self-updates now hand control to the freshly installed manager process before restart and health verification.
- Manager/restart changes shipped by an update are active during that same update transaction rather than only on the next command invocation.

### Tests
- Added regression coverage for post-update ordering and `execv` handoff to the newly installed manager frontend.

## 1.13.6 — 2026-08-17

### Fixed
- Restart/update now waits for the Docker container health state to become `healthy` before continuing.
- `/health` is verified after Docker readiness, avoiding transient `Connection reset by peer` messages during normal startup.
- An update is only reported as complete after the post-start healthcheck succeeds.

### Tests
- Added regression coverage for Docker `starting` → `healthy` readiness and restart ordering.

## 1.13.5 — 2026-08-17

### Fixed
- Update ZIP extraction can no longer break version-specific upgrade helpers by dropping executable permissions.
- The manager marks all `upgrade*.sh` files executable before migrations and invokes the selected wrapper through `bash`.
- The release wrapper invokes its version-specific helper through `bash`, allowing older managers to upgrade safely.

### Tests
- Added a regression test reproducing a non-executable upgrade helper and verifying that the update completes successfully.

## 1.13.4 — 2026-08-17

### Fixed
- Docker images now include the repository `VERSION` file.
- Dashboard/API `/health` and the `hamspotter` management command now report the same release version.
- Public release smoke tests verify both management and container version metadata.

## 1.13.3 — 2026-08-17

### Added
- `hamspotter about` with version, maintainer, copyright, GPL license and project URL.
- About / Über HAM Spotter entry in the interactive management menu.
- Discreet dashboard footer with version, copyright, GPL attribution and GitHub project link.

### Changed
- Management menu keeps English/German translation support for the new About information.

## 1.13.2 — 2026-08-16

### Added
- English/German language selection in the universal installer; English is the default for new installations.
- English management interface for `hamspotter`.
- `hamspotter language en|de` to switch the management language later.
- Reproducible release builder and automated GitHub release publishing.

### Changed
- Fresh installations persist the selected interface language in `HAMSPOTTER_LANGUAGE`.
- Existing installations keep German when upgraded unless the language is explicitly changed.
- Installation smoke tests are language-aware and version-independent.
- Application version handling is synchronized with the repository `VERSION` file during the release process.

## 1.13.1 — 2026-08-16

### Added
- GNU GPL v3.0 project license for the public GitHub release.
- Universal interactive installer for callsign, QTH locator, band layers, primary mode, Telegram, port, local RX radius and timezone.
- Unified `hamspotter` management command for status, configuration, updates, backups, restore, logs, restart, healthcheck and uninstall.
- Self-extracting one-file installer for distribution.
- Compact and full backup modes.

### Changed
- Compact backup now shows progress, duration and final archive size.
- Compact backup keeps long-lived history/configuration while omitting short-lived raw spot rows.
- SQLite backups are created consistently instead of blindly copying a live database file.
- Generic QTH logic and CTY.DAT-based regional SSB weighting replace station-specific assumptions.

### Fixed
- DIGITAL dashboard performance on busy PSK Reporter streams.
- SSB/CW cards no longer expose raw `NONE 0%` direction placeholders when locator geometry is unavailable.
