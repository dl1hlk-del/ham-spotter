# HAM Spotter 1.13.4

Version-consistency maintenance release following the new About & Attribution functionality.

## Fixed

- Docker images now include the repository `VERSION` file.
- `/health`, dashboard/API version reporting and `hamspotter version` now use the same release version.
- Public installation smoke tests now explicitly verify the container version as well as the management version.

## Included from 1.13.3

- `hamspotter about`
- **About HAM Spotter / Über HAM Spotter** management-menu entry
- copyright and maintainer information
- GNU GPL v3.0 only attribution
- discreet dashboard footer with the GitHub project link
- English/German About information

## Recommended download

Download **`hamspotter-installer.sh`**, then:

```bash
chmod +x hamspotter-installer.sh
./hamspotter-installer.sh
```

## Existing installations

The V1.13.4 upgrade preserves `.env`, database and runtime data. Rebuilding the Docker image applies the version-reporting fix.
