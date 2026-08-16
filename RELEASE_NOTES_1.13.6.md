# HAM Spotter 1.13.6

Startup-health reliability maintenance release.

## Fixed

- `hamspotter update` now waits for the Docker container to report `healthy` after rebuilding/restarting.
- The manager verifies `/health` only after Docker readiness, avoiding transient `Connection reset by peer` output during normal startup.
- Update completion is reported only after the post-start healthcheck succeeds.
- Manual restart and restore paths benefit from the same readiness wait.

## Regression coverage

Automated tests verify that the manager waits through Docker `starting` states, checks the API only after `healthy`, and performs readiness waiting after `docker compose up -d --build`.

## Existing installations

`.env`, SQLite data and backups remain protected during the update.
