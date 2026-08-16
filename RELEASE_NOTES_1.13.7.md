# HAM Spotter 1.13.7

Self-update process handoff reliability release.

## Fixed
- After files and migrations are installed, the updater replaces the old in-memory manager process with the freshly installed manager frontend.
- Restart, Docker-health waiting and `/health` verification therefore use the new code in the same update transaction.
- This closes the bootstrap gap observed during the 1.13.5 → 1.13.6 update.

Existing `.env`, SQLite data and backups remain protected.
