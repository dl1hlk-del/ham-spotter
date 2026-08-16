# HAM Spotter 1.13.2

International installer and management release.

## Highlights

- English/German installer language selection
- English is the default for new installations
- selected language is stored in `HAMSPOTTER_LANGUAGE`
- bilingual `hamspotter` management interface
- switch later with `hamspotter language en` or `hamspotter language de`
- existing installations keep German after upgrade unless changed explicitly
- automated release packaging and public-install smoke testing
- same Docker-based universal installation and GPL-3.0-only license

## Recommended download

Download **`hamspotter-installer.sh`**, then:

```bash
chmod +x hamspotter-installer.sh
./hamspotter-installer.sh
```

At startup choose:

```text
Language / Sprache:
  1) English
  2) Deutsch
```

English is the default for new installations.

The installer verifies its embedded payload before installation.

## Alternative

`ham-spotter-universal.zip` contains the same universal package for manual extraction and installation.

## Existing installations

The V1.13.2 upgrade preserves the existing `.env` and runtime data. Installations created before language support keep German as their management language by default.

To switch afterward:

```bash
hamspotter language en
```

or:

```bash
hamspotter language de
```

## Scope

This release internationalizes the installer and `hamspotter` management interface. The web dashboard itself is not yet fully translated.
