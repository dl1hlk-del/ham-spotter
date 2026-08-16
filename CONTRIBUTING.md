# Contributing

Thanks for helping improve HAM Spotter.

## Before opening an issue

For bugs, please include:

- HAM Spotter version (`hamspotter version`)
- Raspberry Pi / Linux model and OS
- Docker version (`docker --version`)
- Docker Compose version (`docker compose version`)
- the affected band and mode
- relevant log excerpts

**Remove secrets before posting.** Never include `.env`, Telegram bot tokens, chat IDs, private backups or other credentials.

## Pull requests

1. Keep each pull request focused on one change.
2. Add or update tests where practical.
3. Run:

```bash
pip install -r requirements.txt -r requirements-dev.txt
PYTHONPATH=. pytest -q
bash -n install.sh hamspotter upgrade.sh upgrade_v1.13.1.sh
```

4. Explain the radio/propagation rationale when changing scoring or classification logic.
5. Avoid introducing station-specific callsigns, locators, IP addresses or regional assumptions into production defaults.


## Contribution license

By submitting a contribution to HAM Spotter, you agree that it may be distributed under the project license, **GNU GPL v3.0 only (GPL-3.0-only)**.
