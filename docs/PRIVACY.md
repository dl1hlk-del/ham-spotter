# Privacy and external connections

HAM Spotter is designed as a local, self-hosted application. Its persistent station configuration and history are stored locally on the host.

Runtime connections can include:

- **PSK Reporter:** receives public amateur-radio reception reports.
- **Reverse Beacon Network:** receives beacon/skimmer data and node metadata.
- **DX Cluster / DXSpider:** the configured callsign can be used as the cluster login.
- **NOAA SWPC:** downloads public space-weather data.
- **ADIF / CTY.DAT:** downloads public reference catalogues.
- **Telegram (optional):** the bot token/chat ID stored in `.env` are used to send alerts and process commands.

Do not publish `.env`, local backups or database files unless you have intentionally reviewed and sanitized them.
