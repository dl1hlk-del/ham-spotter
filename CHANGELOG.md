# Changelog

All notable user-facing changes to HAM Spotter are documented here.

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
