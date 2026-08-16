#!/usr/bin/env python3
from __future__ import annotations

import builtins
import getpass
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / ".env"


def _language() -> str:
    # Backward compatibility: installations created before language support
    # keep the original German manager until the user explicitly switches.
    if not ENV_FILE.exists():
        return "de"
    for raw in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("HAMSPOTTER_LANGUAGE="):
            value = line.split("=", 1)[1].strip().lower()
            return value if value in {"en", "de"} else "en"
    return "de"


def _set_language(value: str) -> None:
    lang = value.strip().lower()
    if lang not in {"en", "de"}:
        raise ValueError("Language must be 'en' or 'de'.")
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines() if ENV_FILE.exists() else []
    out: list[str] = []
    found = False
    for raw in lines:
        if raw.strip().startswith("HAMSPOTTER_LANGUAGE="):
            out.append(f"HAMSPOTTER_LANGUAGE={lang}")
            found = True
        else:
            out.append(raw)
    if not found:
        if out and out[-1].strip():
            out.append("")
        out.extend(["# HAM Spotter interface language", f"HAMSPOTTER_LANGUAGE={lang}"])
    ENV_FILE.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")


# The core manager remains the single implementation of backup/update/config logic.
# This frontend translates only user-facing text when English is selected.
REPLACEMENTS: tuple[tuple[str, str], ...] = (
    ("Docker ist nicht installiert.", "Docker is not installed."),
    ("Container neu gebaut/gestartet.", "Container rebuilt/restarted."),
    ("Warte auf Container-Health …", "Waiting for container health …"),
    ("Container ist healthy und Web/API bereit.", "Container is healthy and Web/API is ready."),
    ("Health-API meldet ok=false", "Health API reports ok=false"),
    ("Container wurde nicht rechtzeitig bereit", "Container did not become ready in time"),
    ("Healthcheck nach Update fehlgeschlagen.", "Healthcheck after update failed."),
    ("Logs · Strg+C beendet", "Logs · Ctrl+C to stop"),
    ("Rufzeichen", "Callsign"),
    ("Modus", "Mode"),
    ("API derzeit nicht erreichbar", "API is currently unavailable"),
    ("/health nicht erreichbar", "/health is unreachable"),
    ("Web/API erreichbar", "Web/API reachable"),
    ("Healthcheck meldet Fehler", "Healthcheck reports an error"),
    ("Ungültiges Rufzeichenformat.", "Invalid callsign format."),
    ("Ungültiger Maidenhead-Locator (2/4/6/8 Zeichen).", "Invalid Maidenhead locator (2/4/6/8 characters)."),
    ("HF + 6 m aktivieren", "Enable HF + 6 m"),
    ("4 m / 2 m / 70 cm / 23 cm aktivieren", "Enable 4 m / 2 m / 70 cm / 23 cm"),
    ("Mindestens eine Schicht muss aktiv sein; HF + 6 m bleibt aktiv.", "At least one layer must be enabled; HF + 6 m remains enabled."),
    ("Bänder aktualisiert.", "Bands updated."),
    ("Primärer Ausbreitungsmodus", "Primary propagation mode"),
    ("Primärmodus", "Primary mode"),
    ("Ungültige Auswahl.", "Invalid selection."),
    ("Telegram verwenden", "Use Telegram"),
    ("Telegram deaktiviert.", "Telegram disabled."),
    ("Bot-Token (leer = unverändert)", "Bot token (empty = unchanged)"),
    ("Telegram Chat-ID", "Telegram Chat ID"),
    ("Telegram-Konfiguration gespeichert (Token wird nicht angezeigt).", "Telegram configuration saved (token is not displayed)."),
    ("Web-Port", "Web port"),
    ("Ungültiger Port.", "Invalid port."),
    ("Lokaler RX-Radius in km", "Local RX radius in km"),
    ("Ungültiger Radius.", "Invalid radius."),
    ("Netzwerk/RX-Radius aktualisiert.", "Network/RX radius updated."),
    ("Erwartet: 0 <= WATCH < OPEN < STRONG <= 100", "Expected: 0 <= WATCH < OPEN < STRONG <= 100"),
    ("Score-Schwellen aktualisiert.", "Score thresholds updated."),
    ("Dashboard-Zeitzone (IANA)", "Dashboard timezone (IANA)"),
    ("Rohspot-Aufbewahrung in Stunden", "Raw spot retention in hours"),
    ("Ungültige Aufbewahrungszeit.", "Invalid retention time."),
    ("Weitere Einstellungen aktualisiert.", "Additional settings updated."),
    ("Konfiguration", "Configuration"),
    ("Aktuell:", "Current:"),
    ("Bänder / Schichten", "Bands / layers"),
    ("Score-Schwellen", "Score thresholds"),
    ("Zeitzone / Datenaufbewahrung", "Timezone / data retention"),
    ("Änderungen anwenden (Neustart)", "Apply changes (restart)"),
    ("Zurück", "Back"),
    ("Auswahl", "Selection"),
    ("SQLite-Backup quick_check fehlgeschlagen:", "SQLite backup quick_check failed:"),
    ("Backup wird erstellt …", "Creating backup …"),
    ("VOLLSTÄNDIG inkl. Rohspots", "FULL including raw spots"),
    ("KOMPAKT (empfohlen, ohne kurzlebige Rohspots)", "COMPACT (recommended, excluding short-lived raw spots)"),
    ("SQLite-Snapshot fertig:", "SQLite snapshot complete:"),
    ("kurzlebige Rohspots nicht archiviert (werden nach Restore neu gesammelt).", "short-lived raw spots not archived (they will be collected again after restore)."),
    ("Archiv wird komprimiert …", "Compressing archive …"),
    ("Backup fertig:", "Backup complete:"),
    ("Größe:", "Size:"),
    ("Dauer:", "Duration:"),
    ("Backup-Datei", "Backup file"),
    ("Backup nicht gefunden:", "Backup not found:"),
    ("Aktuelle Konfiguration/Daten wirklich überschreiben", "Really overwrite current configuration/data"),
    ("Restore abgeschlossen.", "Restore complete."),
    ("Lade ", "Downloading "),
    ("Unsicherer ZIP-Pfad erkannt.", "Unsafe ZIP path detected."),
    ("ZIP enthält kein erkennbares HAM-Spotter-Paket.", "ZIP does not contain a recognizable HAM Spotter package."),
    ("Pfad oder HTTPS-URL zum HAM-Spotter ZIP", "Path or HTTPS URL to the HAM Spotter ZIP"),
    ("Datei nicht gefunden:", "File not found:"),
    ("Update abgeschlossen. Installierte Version:", "Update complete. Installed version:"),
    ("Übergebe Update an HAM Spotter", "Handing update over to HAM Spotter"),
    ("Installierter Manager-Frontend fehlt:", "Installed manager frontend is missing:"),
    ("Manager-Handoff ist unerwartet zurückgekehrt.", "Manager handoff returned unexpectedly."),
    ("Deinstallation", "Uninstall"),
    ("Über HAM Spotter", "About HAM Spotter"),
    ("Urheber / Maintainer", "Author / Maintainer"),
    ("Lizenz", "License"),
    ("Freie Open-Source-Software für den Amateurfunk.", "Free and open-source amateur-radio software."),
    ("Projekt", "Project"),
    ("Installationsverzeichnis:", "Installation directory:"),
    ("HAM-Spotter Container stoppen und entfernen", "Stop and remove the HAM Spotter container"),
    ("Sicherungsdatei außerhalb des Projektordners:", "Backup file outside the project directory:"),
    ("Auch Programmordner und lokale Daten löschen", "Also delete the program directory and local data"),
    ("Programmordner entfernt.", "Program directory removed."),
    ("Container entfernt; Dateien bleiben erhalten.", "Container removed; files are preserved."),
    ("Neustart", "Restart"),
    ("Ende", "Exit"),
    ("Abgebrochen.", "Cancelled."),
    ("Enter zum Fortfahren …", "Press Enter to continue …"),
    ("inkl. kurzlebiger Rohspots", "including short-lived raw spots"),
    ("Fehler:", "Error:"),
    ("[J/n]", "[Y/n]"),
    ("[j/N]", "[y/N]"),
)


def _translate(value: str) -> str:
    if _language() != "en":
        return value
    out = value
    for source, target in REPLACEMENTS:
        out = out.replace(source, target)
    return out


_ORIGINAL_PRINT = builtins.print
_ORIGINAL_INPUT = builtins.input
_ORIGINAL_GETPASS = getpass.getpass


def _print(*args, **kwargs):
    converted = tuple(_translate(arg) if isinstance(arg, str) else arg for arg in args)
    return _ORIGINAL_PRINT(*converted, **kwargs)


def _input(prompt: str = "") -> str:
    return _ORIGINAL_INPUT(_translate(prompt))


def _getpass(prompt: str = "Password: ", stream=None) -> str:
    return _ORIGINAL_GETPASS(_translate(prompt), stream=stream)


def _language_command() -> int:
    current = _language()
    if len(sys.argv) == 2:
        _ORIGINAL_PRINT(f"Current HAM Spotter language: {current}")
        _ORIGINAL_PRINT("Use: hamspotter language en|de")
        return 0
    try:
        _set_language(sys.argv[2])
    except ValueError as exc:
        _ORIGINAL_PRINT(f"Error: {exc}", file=sys.stderr)
        return 2
    selected = _language()
    if selected == "de":
        _ORIGINAL_PRINT("✓ HAM Spotter Sprache: Deutsch")
    else:
        _ORIGINAL_PRINT("✓ HAM Spotter language: English")
    return 0


def main() -> int:
    if len(sys.argv) >= 2 and sys.argv[1] == "language":
        return _language_command()

    if _language() == "en":
        builtins.print = _print
        builtins.input = _input
        getpass.getpass = _getpass

    import hamspotter_manager as core
    return core.main()


if __name__ == "__main__":
    raise SystemExit(main())
