# HAM Spotter 1.13.11

Compact-backup reliability fix.

## Fixed

- Compact and full backups no longer include historical `data/upgrade-*` directories.
- Local `data/maintenance-backup-*` directories are also excluded from new archives.
- Normal runtime files/subdirectories and the consistent SQLite snapshot remain included.
- Compact backups continue to omit short-lived raw `spots` rows while preserving long-lived history and configuration.

This prevents backup-within-backup growth where multi-gigabyte old database snapshots were compressed into every new compact archive.

Existing `.env`, SQLite runtime data and existing backup files are not deleted or modified by the update.
