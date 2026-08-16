# HAM Spotter

**Real-time amateur-radio propagation dashboard for HF, VHF and UHF.**

HAM Spotter combines live amateur-radio observations and space-weather context to help answer a practical question: **which band and direction are worth trying right now?**

It is designed for Raspberry Pi / Debian-class Linux systems and runs in Docker.

> **Current release:** 1.13.1  
> **Status:** hobby / community software — propagation classifications are indicators, not guarantees.

[Deutsch](README.de.md)

## What it does

- HF + 6 m overview: **6 / 10 / 12 / 15 / 17 / 20 / 40 / 60 / 80 m**
- VHF/UHF layer: **4 m / 2 m / 70 cm / 23 cm**
- Separate propagation views for **SSB, CW and DIGITAL**
- PSK Reporter based digital activity
- Reverse Beacon Network CW/FT8 context
- DX Cluster based human SSB spots
- CTY.DAT / DXCC enrichment
- live DX highlights and best-DX views
- directional sector analysis and propagation radar
- opening history and activity trends
- VHF indicators for Tropo, Sporadic-E, Meteor Scatter and Aurora potential
- NOAA space-weather context
- optional Telegram alerts and commands
- built-in management command: `hamspotter`
- compact and full backup / restore

## Recommended installation

Open the repository's **Releases** page and download:

```text
hamspotter-installer.sh
```

Then on the Raspberry Pi / Linux host:

```bash
chmod +x hamspotter-installer.sh
./hamspotter-installer.sh
```

The installer asks for the station-specific settings instead of requiring manual file editing:

- callsign
- Maidenhead QTH locator
- enabled band layers
- primary SSB / CW / DIGITAL view
- web port
- local PSK/RBN radius
- timezone
- optional Telegram bot token and chat ID

It then creates the local `.env`, builds the Docker image, starts the service, checks health and installs the `hamspotter` management command.

## Management

After installation:

```bash
hamspotter
```

Menu:

```text
1  Status
2  Configuration
3  Update
4  Backup
5  Restore
6  Logs
7  Restart
8  Healthcheck
9  Uninstall
0  Exit
```

Direct commands are also available:

```bash
hamspotter status
hamspotter configure
hamspotter backup
hamspotter backup --full
hamspotter restore /path/to/backup.tar.gz
hamspotter logs
hamspotter restart
hamspotter healthcheck
hamspotter version
```

## Docker architecture

HAM Spotter runs as a Docker Compose service. Runtime data is stored outside the container:

```text
ham-spotter/
├── .env              # local station configuration — never commit this
├── data/             # SQLite database and persistent runtime data
├── backups/          # local backups
├── app/              # application source
└── docker-compose.yml
```

The default dashboard port is `8095` and can be changed during installation or later with `hamspotter configure`.

## Data sources

HAM Spotter uses or can use these external services/catalogues at runtime:

- PSK Reporter
- Reverse Beacon Network
- DX Cluster / DXSpider
- NOAA Space Weather Prediction Center
- ADIF/DXCC resources
- CTY.DAT country/prefix data

These services are operated independently from this project. Availability and data quality can vary.

## QTH and privacy

Precise local calculations use the configured Maidenhead locator. DX Cluster connections use the configured callsign as login where required.

**Never publish your `.env`, backups, database, Telegram token or chat ID.** The repository `.gitignore` excludes the usual runtime secrets and data paths, but always review changes before committing.

See [SECURITY.md](SECURITY.md) and [docs/PRIVACY.md](docs/PRIVACY.md).

## Propagation interpretation

The dashboard scores are **decision support**, not a physical proof of a propagation mechanism. For example, VHF Tropo / Es / Meteor Scatter / Aurora labels are inferred from observation patterns and supporting context. The radio operator remains the final judge.

## Development

Python 3.13 is used by the Docker image.

For a local test environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
PYTHONPATH=. pytest -q
```

Shell syntax checks:

```bash
bash -n install.sh hamspotter upgrade.sh upgrade_v1.13.1.sh
```

GitHub Actions runs these checks automatically for pushes and pull requests.

## Contributing

Bug reports and focused improvements are welcome. Please read [CONTRIBUTING.md](CONTRIBUTING.md) before opening a pull request.

## ☕ Support HAM Spotter

HAM Spotter is free and open-source hobby software. If the project is useful to you and you would like to support continued development, an optional tip is appreciated.

[**Leave a tip via PayPal**](https://paypal.me/RJockwer)

Support is completely optional and does not affect access to the software or any of its features.

## License

HAM Spotter is licensed under the **GNU General Public License v3.0 only (GPL-3.0-only)**. See [LICENSE](LICENSE).

You may use, study, modify and redistribute the software under the terms of GPL-3.0. If you distribute modified versions, the corresponding source code must remain available under the GPL terms.

## Disclaimer

HAM Spotter is amateur-radio hobby software. It is provided without any guarantee of radio propagation, service availability, data completeness or suitability for safety-critical use.
