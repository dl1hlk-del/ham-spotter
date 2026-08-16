# HAM Spotter 1.13.1

First GitHub-ready public release of the universal HAM Spotter installer and management tooling.

## Highlights

- GNU GPL v3.0 license
- Docker-based universal installation
- interactive callsign and Maidenhead QTH setup
- HF + 6 m and VHF/UHF band layers
- SSB / CW / DIGITAL propagation views
- PSK Reporter, RBN, DX Cluster, NOAA and CTY.DAT integration
- `hamspotter` management menu
- compact/full backup and restore
- DIGITAL performance optimizations
- generic station-independent QTH logic

## Recommended download

Download **`hamspotter-installer.sh`**, then:

```bash
chmod +x hamspotter-installer.sh
./hamspotter-installer.sh
```

The installer verifies its embedded payload before installation.

## Alternative

`ham-spotter-universal.zip` contains the same universal source package for manual extraction and installation.

## Upgrade note

Existing development installations should use the corresponding patch/update package rather than reinstalling from scratch. Back up first.
