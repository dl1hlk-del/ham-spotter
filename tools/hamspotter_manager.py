#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
import urllib.error
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / ".env"
DATA_DIR = ROOT / "data"
BACKUP_DIR = ROOT / "backups"
VERSION_FILE = ROOT / "VERSION"

BANDS_HF = "6m,10m,12m,15m,17m,20m,40m,60m,80m"
BANDS_VHF = "4m,2m,70cm,23cm"


def version() -> str:
    try:
        return VERSION_FILE.read_text(encoding="utf-8").strip()
    except Exception:
        return "unknown"


def heading(title: str) -> None:
    print("\n" + "═" * 58)
    print(f" HAM Spotter {version()} · {title}")
    print("═" * 58)


def load_env() -> dict[str, str]:
    out: dict[str, str] = {}
    if not ENV_FILE.exists():
        return out
    for raw in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def set_env(updates: dict[str, str]) -> None:
    ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not ENV_FILE.exists():
        example = ROOT / ".env.example"
        if example.exists():
            shutil.copy2(example, ENV_FILE)
        else:
            ENV_FILE.touch()
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines()
    pending = {str(k): str(v) for k, v in updates.items()}
    new_lines: list[str] = []
    for raw in lines:
        stripped = raw.strip()
        if stripped and not stripped.startswith("#") and "=" in raw:
            key = raw.split("=", 1)[0].strip()
            if key in pending:
                new_lines.append(f"{key}={pending.pop(key)}")
                continue
        new_lines.append(raw)
    if pending:
        if new_lines and new_lines[-1].strip():
            new_lines.append("")
        new_lines.append("# HAM Spotter Manager")
        for key, value in pending.items():
            new_lines.append(f"{key}={value}")
    ENV_FILE.write_text("\n".join(new_lines).rstrip() + "\n", encoding="utf-8")
    try:
        os.chmod(ENV_FILE, 0o600)
    except OSError:
        pass


def _docker_prefix() -> list[str]:
    if shutil.which("docker") is None:
        raise RuntimeError("Docker ist nicht installiert.")
    probe = subprocess.run(["docker", "info"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if probe.returncode == 0:
        return ["docker"]
    if shutil.which("sudo"):
        probe = subprocess.run(["sudo", "docker", "info"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if probe.returncode == 0:
            return ["sudo", "docker"]
    return ["docker"]


def compose(args: list[str], check: bool = True) -> subprocess.CompletedProcess:
    cmd = _docker_prefix() + ["compose", *args]
    return subprocess.run(cmd, cwd=ROOT, check=check)


def _container_health_status() -> str:
    cmd = _docker_prefix() + [
        "inspect",
        "--format",
        "{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}",
        "ham-spotter",
    ]
    probe = subprocess.run(
        cmd,
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        check=False,
    )
    if probe.returncode != 0:
        return "missing"
    return probe.stdout.strip().lower() or "unknown"


def _wait_for_container_ready(timeout: float = 120.0, poll: float = 2.0) -> None:
    print("• Warte auf Container-Health …", flush=True)
    deadline = time.monotonic() + timeout
    last_state = ""
    last_api_error = ""

    while time.monotonic() < deadline:
        state = _container_health_status()
        if state != last_state:
            print(f"  Docker: {state}", flush=True)
            last_state = state

        if state == "healthy":
            try:
                data = _api("/health", timeout=5)
                if bool(data.get("ok")):
                    print("✓ Container ist healthy und Web/API bereit.", flush=True)
                    return
                last_api_error = "Health-API meldet ok=false"
            except Exception as exc:
                last_api_error = str(exc)

        time.sleep(poll)

    detail = f"Docker={last_state or 'unknown'}"
    if last_api_error:
        detail += f", API={last_api_error}"
    raise RuntimeError(f"Container wurde nicht rechtzeitig bereit ({detail}).")


def restart() -> None:
    heading("Neustart")
    compose(["up", "-d", "--build"])
    print("✓ Container neu gebaut/gestartet.")
    _wait_for_container_ready()


def _port() -> int:
    env = load_env()
    try:
        return int(env.get("HAMSPOTTER_PORT", "8095"))
    except ValueError:
        return 8095


def _api(path: str, timeout: int = 5) -> dict:
    url = f"http://127.0.0.1:{_port()}{path}"
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def status() -> None:
    heading("Status")
    compose(["ps"], check=False)
    try:
        data = _api("/api/status")
        print(f"\nVersion:   {data.get('version', '?')}")
        print(f"Rufzeichen: {data.get('callsign', '?')}")
        print(f"QTH:        {data.get('qth', '?')}")
        print(f"Modus:      {data.get('primary_mode', '?')}")
        print(f"Port:       {_port()}")
        spots = data.get("spots_last_hour") or {}
        if spots:
            print("Spots/1h:   " + ", ".join(f"{k}={v}" for k, v in spots.items()))
    except Exception as exc:
        print(f"\n⚠ API derzeit nicht erreichbar: {exc}")


def healthcheck() -> bool:
    heading("Healthcheck")
    try:
        data = _api("/health", timeout=8)
    except Exception as exc:
        print(f"✗ /health nicht erreichbar: {exc}")
        return False
    ok = bool(data.get("ok"))
    print("✓ Web/API erreichbar" if ok else "✗ Healthcheck meldet Fehler")
    for src in data.get("sources") or []:
        name = src.get("source", "?")
        state = src.get("status", "?")
        mark = "✓" if state == "LIVE" else ("!" if state == "DEGRADED" else "✗")
        print(f"{mark} {name:18} {state}")
    return ok


def logs() -> None:
    heading("Logs · Strg+C beendet")
    try:
        compose(["logs", "-f", "--tail=120", "ham-spotter"], check=False)
    except KeyboardInterrupt:
        print()


def _ask(prompt: str, default: str | None = None, secret: bool = False) -> str:
    label = prompt + (f" [{default}]" if default not in (None, "") else "") + ": "
    if secret:
        import getpass
        value = getpass.getpass(label)
    else:
        value = input(label)
    value = value.strip()
    return value if value else (default or "")


def _ask_bool(prompt: str, default: bool = True) -> bool:
    suffix = "[J/n]" if default else "[j/N]"
    raw = input(f"{prompt} {suffix}: ").strip().lower()
    if not raw:
        return default
    return raw in {"j", "ja", "y", "yes", "1"}


def _valid_locator(value: str) -> bool:
    value = value.strip().upper()
    if len(value) not in {2, 4, 6, 8}:
        return False
    return bool(re.fullmatch(r"[A-R]{2}(?:[0-9]{2}(?:[A-X]{2}(?:[0-9]{2})?)?)?", value))


def _configure_station(env: dict[str, str]) -> None:
    call = _ask("Rufzeichen", env.get("CALLSIGN", "")).upper()
    while not re.fullmatch(r"[A-Z0-9/]{2,20}", call):
        print("Ungültiges Rufzeichenformat.")
        call = _ask("Rufzeichen", env.get("CALLSIGN", "")).upper()
    locator = _ask("QTH-Locator (Maidenhead)", env.get("QTH_LOCATOR", "")).upper()
    while not _valid_locator(locator):
        print("Ungültiger Maidenhead-Locator (2/4/6/8 Zeichen).")
        locator = _ask("QTH-Locator", env.get("QTH_LOCATOR", "")).upper()
    set_env({"CALLSIGN": call, "QTH_LOCATOR": locator, "DXCLUSTER_LOGIN": call})
    print(f"✓ Station: {call} · {locator}")


def _configure_bands(env: dict[str, str]) -> None:
    existing = set(x.strip().lower() for x in env.get("BANDS", f"{BANDS_HF},{BANDS_VHF}").split(",") if x.strip())
    hf = _ask_bool("HF + 6 m aktivieren", any(b in existing for b in BANDS_HF.split(",")))
    vhf = _ask_bool("4 m / 2 m / 70 cm / 23 cm aktivieren", any(b in existing for b in BANDS_VHF.split(",")))
    bands: list[str] = []
    if hf:
        bands.extend(BANDS_HF.split(","))
    if vhf:
        bands.extend(BANDS_VHF.split(","))
    if not bands:
        print("Mindestens eine Schicht muss aktiv sein; HF + 6 m bleibt aktiv.")
        bands = BANDS_HF.split(",")
    default_layer = "hf" if hf else "vhf"
    set_env({
        "BANDS": ",".join(bands),
        "HF_LAYER_BANDS": BANDS_HF,
        "VHF_LAYER_BANDS": BANDS_VHF,
        "DASHBOARD_DEFAULT_LAYER": default_layer,
    })
    print("✓ Bänder aktualisiert.")


def _configure_mode(env: dict[str, str]) -> None:
    current = env.get("PRIMARY_PROP_MODE", "ssb").lower()
    print("1) SSB   2) CW   3) DIGITAL")
    default = {"ssb": "1", "cw": "2", "digital": "3"}.get(current, "1")
    choice = _ask("Primärer Ausbreitungsmodus", default)
    mode = {"1": "ssb", "2": "cw", "3": "digital", "ssb": "ssb", "cw": "cw", "digital": "digital"}.get(choice.lower())
    if not mode:
        print("Ungültige Auswahl.")
        return
    set_env({"PRIMARY_PROP_MODE": mode})
    print(f"✓ Primärmodus: {mode.upper()}")


def _configure_telegram(env: dict[str, str]) -> None:
    enabled = _ask_bool("Telegram verwenden", bool(env.get("TELEGRAM_BOT_TOKEN")))
    if not enabled:
        set_env({"TELEGRAM_BOT_TOKEN": "", "TELEGRAM_CHAT_ID": "", "TELEGRAM_ALERTS": "false", "TELEGRAM_COMMANDS": "false"})
        print("✓ Telegram deaktiviert.")
        return
    token = _ask("Bot-Token (leer = unverändert)", "", secret=True) or env.get("TELEGRAM_BOT_TOKEN", "")
    chat = _ask("Telegram Chat-ID", env.get("TELEGRAM_CHAT_ID", ""))
    set_env({"TELEGRAM_BOT_TOKEN": token, "TELEGRAM_CHAT_ID": chat, "TELEGRAM_ALERTS": "true", "TELEGRAM_COMMANDS": "true"})
    print("✓ Telegram-Konfiguration gespeichert (Token wird nicht angezeigt).")


def _configure_network(env: dict[str, str]) -> None:
    port = _ask("Web-Port", env.get("HAMSPOTTER_PORT", "8095"))
    if not port.isdigit() or not (1 <= int(port) <= 65535):
        print("Ungültiger Port.")
        return
    radius = _ask("Lokaler RX-Radius in km", env.get("LOCAL_RX_RADIUS_KM", "325"))
    try:
        float(radius)
    except ValueError:
        print("Ungültiger Radius.")
        return
    set_env({"HAMSPOTTER_PORT": port, "LOCAL_RX_RADIUS_KM": radius})
    print("✓ Netzwerk/RX-Radius aktualisiert.")


def _configure_thresholds(env: dict[str, str]) -> None:
    watch = _ask("WATCH Score", env.get("WATCH_SCORE", "40"))
    opened = _ask("OPEN Score", env.get("OPEN_SCORE", "65"))
    strong = _ask("STRONG Score", env.get("STRONG_SCORE", "85"))
    try:
        w, o, s = int(watch), int(opened), int(strong)
        if not (0 <= w < o < s <= 100):
            raise ValueError
    except ValueError:
        print("Erwartet: 0 <= WATCH < OPEN < STRONG <= 100")
        return
    set_env({"WATCH_SCORE": str(w), "OPEN_SCORE": str(o), "STRONG_SCORE": str(s)})
    print("✓ Score-Schwellen aktualisiert.")


def _configure_misc(env: dict[str, str]) -> None:
    tz = _ask("Dashboard-Zeitzone (IANA)", env.get("DASHBOARD_TIMEZONE", "Europe/Berlin"))
    retention = _ask("Rohspot-Aufbewahrung in Stunden", env.get("RETENTION_HOURS", "72"))
    try:
        if int(retention) < 1:
            raise ValueError
    except ValueError:
        print("Ungültige Aufbewahrungszeit.")
        return
    set_env({"DASHBOARD_TIMEZONE": tz, "RETENTION_HOURS": retention})
    print("✓ Weitere Einstellungen aktualisiert.")


def configure() -> None:
    while True:
        env = load_env()
        heading("Konfiguration")
        print(f"Aktuell: {env.get('CALLSIGN','?')} · {env.get('QTH_LOCATOR','?')} · Port {env.get('HAMSPOTTER_PORT','8095')} · {env.get('PRIMARY_PROP_MODE','ssb').upper()}")
        print("\n1  Rufzeichen / QTH")
        print("2  Bänder / Schichten")
        print("3  SSB / CW / DIGITAL")
        print("4  Telegram")
        print("5  Web-Port / RX-Radius")
        print("6  Score-Schwellen")
        print("7  Zeitzone / Datenaufbewahrung")
        print("8  Änderungen anwenden (Neustart)")
        print("0  Zurück")
        choice = input("\nAuswahl: ").strip()
        if choice == "0":
            return
        if choice == "1": _configure_station(env)
        elif choice == "2": _configure_bands(env)
        elif choice == "3": _configure_mode(env)
        elif choice == "4": _configure_telegram(env)
        elif choice == "5": _configure_network(env)
        elif choice == "6": _configure_thresholds(env)
        elif choice == "7": _configure_misc(env)
        elif choice == "8": restart()
        else: print("Ungültige Auswahl.")


def _human_bytes(value: int) -> str:
    size = float(max(0, value))
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024.0 or unit == "TiB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024.0
    return f"{size:.1f} TiB"


def _sqlite_is_db(path: Path) -> bool:
    try:
        with path.open("rb") as fh:
            return fh.read(16) == b"SQLite format 3\x00"
    except OSError:
        return False


def _copy_sqlite_compact(src_path: Path, dst_path: Path) -> tuple[int, int]:
    """Create a consistent SQLite snapshot without ephemeral raw spot rows.

    The `spots` table schema/indexes are preserved, but its rows are intentionally
    omitted because raw spots are short-lived and repopulate automatically. This
    keeps normal backups fast and small while preserving opening history, Rare-DX,
    activity samples, band state, alerts, health/cache state, etc.
    """
    uri = f"file:{src_path.resolve()}?mode=ro"
    src = sqlite3.connect(uri, uri=True, timeout=30)
    src.row_factory = sqlite3.Row
    dst = sqlite3.connect(dst_path, timeout=30)
    copied_rows = 0
    skipped_spots = 0
    try:
        src.execute("PRAGMA busy_timeout=30000")
        dst.execute("PRAGMA journal_mode=DELETE")
        dst.execute("PRAGMA synchronous=OFF")
        dst.execute("PRAGMA foreign_keys=OFF")

        # Stable WAL snapshot for the duration of the copy. Readers do not block
        # writers in WAL mode.
        src.execute("BEGIN")
        table_rows = src.execute(
            "SELECT name, sql FROM sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%' AND sql IS NOT NULL "
            "ORDER BY name"
        ).fetchall()

        for row in table_rows:
            name = str(row["name"])
            sql = str(row["sql"])
            dst.execute(sql)
            if name == "spots":
                try:
                    skipped_spots = int(src.execute("SELECT COUNT(*) FROM spots").fetchone()[0])
                except Exception:
                    skipped_spots = 0
                continue
            cols = [str(r[1]) for r in src.execute(f'PRAGMA table_info("{name}")').fetchall()]
            if not cols:
                continue
            quoted = ",".join('"' + c.replace('"', '""') + '"' for c in cols)
            placeholders = ",".join("?" for _ in cols)
            cur = src.execute(f'SELECT {quoted} FROM "{name}"')
            while True:
                rows = cur.fetchmany(2000)
                if not rows:
                    break
                dst.executemany(
                    f'INSERT INTO "{name}" ({quoted}) VALUES ({placeholders})',
                    [tuple(r) for r in rows],
                )
                copied_rows += len(rows)
            print(f"  ✓ SQLite: {name}", flush=True)

        # Recreate indexes/triggers after inserting the data. Indexes belonging to
        # `spots` are kept too, so a restored installation is immediately ready.
        objects = src.execute(
            "SELECT type, name, sql FROM sqlite_master "
            "WHERE type IN ('index','trigger') AND sql IS NOT NULL "
            "ORDER BY CASE type WHEN 'index' THEN 0 ELSE 1 END, name"
        ).fetchall()
        for obj in objects:
            try:
                dst.execute(str(obj["sql"]))
            except sqlite3.OperationalError as exc:
                # A duplicate implicit object is harmless; surface anything else.
                if "already exists" not in str(exc).lower():
                    raise

        # Preserve AUTOINCREMENT counters for long-lived tables when available.
        seq_exists = src.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='sqlite_sequence'"
        ).fetchone()
        if seq_exists:
            for name, seq in src.execute("SELECT name, seq FROM sqlite_sequence WHERE name <> 'spots'").fetchall():
                try:
                    dst.execute("INSERT OR REPLACE INTO sqlite_sequence(name,seq) VALUES(?,?)", (name, seq))
                except sqlite3.OperationalError:
                    pass

        user_version = int(src.execute("PRAGMA user_version").fetchone()[0])
        dst.execute(f"PRAGMA user_version={user_version}")
        dst.commit()
        check = dst.execute("PRAGMA quick_check").fetchone()
        if not check or str(check[0]).lower() != "ok":
            raise RuntimeError(f"SQLite-Backup quick_check fehlgeschlagen: {check}")
    finally:
        try:
            src.rollback()
        except Exception:
            pass
        src.close()
        dst.close()
    return copied_rows, skipped_spots


def _copy_sqlite_full(src_path: Path, dst_path: Path) -> None:
    """Consistent full SQLite snapshot using SQLite's online backup API."""
    uri = f"file:{src_path.resolve()}?mode=ro"
    src = sqlite3.connect(uri, uri=True, timeout=30)
    dst = sqlite3.connect(dst_path, timeout=30)
    try:
        src.execute("PRAGMA busy_timeout=30000")
        src.backup(dst, pages=4096, sleep=0.02)
        dst.commit()
        check = dst.execute("PRAGMA quick_check").fetchone()
        if not check or str(check[0]).lower() != "ok":
            raise RuntimeError(f"SQLite-Backup quick_check fehlgeschlagen: {check}")
    finally:
        src.close()
        dst.close()


def backup(destination: str | None = None, full: bool = False) -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    call = load_env().get("CALLSIGN", "station").replace("/", "-")
    kind = "full" if full else "compact"
    path = Path(destination).expanduser().resolve() if destination else BACKUP_DIR / f"hamspotter-{call}-{stamp}-{kind}.tar.gz"
    path.parent.mkdir(parents=True, exist_ok=True)

    print("\nBackup wird erstellt …", flush=True)
    print("  Typ: " + ("VOLLSTÄNDIG inkl. Rohspots" if full else "KOMPAKT (empfohlen, ohne kurzlebige Rohspots)"), flush=True)
    started = time.monotonic()

    # Never archive a live SQLite database file byte-for-byte. A consistent
    # snapshot is created first and only that snapshot is compressed.
    temp_parent = BACKUP_DIR if os.access(BACKUP_DIR, os.W_OK) else Path(tempfile.gettempdir())
    try:
        with tempfile.TemporaryDirectory(prefix=".backup-work-", dir=temp_parent) as td:
            stage = Path(td)
            stage_data = stage / "data"
            stage_data.mkdir(parents=True, exist_ok=True)

            if ENV_FILE.exists():
                shutil.copy2(ENV_FILE, stage / ".env")
                print("  ✓ Konfiguration", flush=True)
            if VERSION_FILE.exists():
                shutil.copy2(VERSION_FILE, stage / "VERSION")

            skipped_spots_total = 0
            if DATA_DIR.exists():
                entries = sorted(DATA_DIR.iterdir(), key=lambda p: p.name.lower())
                for item in entries:
                    # WAL/SHM files belong to SQLite snapshots and must not be
                    # copied independently.
                    if item.name.endswith(("-wal", "-shm")):
                        continue
                    target = stage_data / item.name
                    if item.is_file() and _sqlite_is_db(item):
                        size = item.stat().st_size
                        print(f"  • SQLite-Snapshot: {item.name} ({_human_bytes(size)})", flush=True)
                        if full:
                            _copy_sqlite_full(item, target)
                        else:
                            _, skipped = _copy_sqlite_compact(item, target)
                            skipped_spots_total += skipped
                        print(f"  ✓ SQLite-Snapshot fertig: {_human_bytes(target.stat().st_size)}", flush=True)
                    elif item.is_dir():
                        shutil.copytree(item, target, symlinks=True)
                    else:
                        shutil.copy2(item, target)

            if skipped_spots_total:
                print(f"  ℹ {skipped_spots_total:,} kurzlebige Rohspots nicht archiviert (werden nach Restore neu gesammelt).".replace(",", "."), flush=True)

            print("  • Archiv wird komprimiert …", flush=True)
            # compresslevel=1 is intentionally chosen for Raspberry Pi: backups
            # are much faster while remaining compressed.
            with tarfile.open(path, "w:gz", compresslevel=1) as tf:
                if (stage / ".env").exists():
                    tf.add(stage / ".env", arcname=".env")
                if stage_data.exists():
                    tf.add(stage_data, arcname="data")
                if (stage / "VERSION").exists():
                    tf.add(stage / "VERSION", arcname="VERSION")
    except Exception:
        try:
            path.unlink(missing_ok=True)
        except Exception:
            pass
        raise

    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    elapsed = time.monotonic() - started
    size = path.stat().st_size if path.exists() else 0
    print(f"✓ Backup fertig: {path}", flush=True)
    print(f"  Größe: {_human_bytes(size)} · Dauer: {elapsed:.1f} s", flush=True)
    return path


def restore(source: str | None = None) -> None:
    heading("Restore")
    src = source or _ask("Backup-Datei")
    path = Path(src).expanduser().resolve()
    if not path.is_file():
        raise RuntimeError(f"Backup nicht gefunden: {path}")
    if not _ask_bool("Aktuelle Konfiguration/Daten wirklich überschreiben", False):
        print("Abgebrochen.")
        return
    backup()
    compose(["down"], check=False)
    with tarfile.open(path, "r:gz") as tf:
        members = [m for m in tf.getmembers() if m.name == ".env" or m.name.startswith("data/") or m.name == "data"]
        tf.extractall(ROOT, members=members)
    restart()
    print("✓ Restore abgeschlossen.")


def _download(url: str, dest: Path) -> None:
    print(f"Lade {url}")
    req = urllib.request.Request(url, headers={"User-Agent": f"HAM-Spotter-Manager/{version()}"})
    with urllib.request.urlopen(req, timeout=60) as response, dest.open("wb") as out:
        shutil.copyfileobj(response, out)


def _safe_zip_extract(zf: zipfile.ZipFile, dest: Path) -> None:
    root = dest.resolve()
    for member in zf.infolist():
        target = (dest / member.filename).resolve()
        if root not in target.parents and target != root:
            raise RuntimeError("Unsicherer ZIP-Pfad erkannt.")
    zf.extractall(dest)


def _find_payload_root(temp: Path) -> Path:
    if (temp / "app").is_dir():
        return temp
    candidates = [p for p in temp.iterdir() if p.is_dir() and (p / "app").is_dir()]
    if len(candidates) == 1:
        return candidates[0]
    raise RuntimeError("ZIP enthält kein erkennbares HAM-Spotter-Paket.")


def update(source: str | None = None) -> None:
    heading("Update")
    src = source or _ask("Pfad oder HTTPS-URL zum HAM-Spotter ZIP")
    if not src:
        print("Abgebrochen.")
        return
    backup()
    with tempfile.TemporaryDirectory(prefix="hamspotter-update-") as td:
        td_path = Path(td)
        zip_path = td_path / "update.zip"
        if re.match(r"^https?://", src, re.I):
            _download(src, zip_path)
        else:
            in_path = Path(src).expanduser().resolve()
            if not in_path.is_file():
                raise RuntimeError(f"Datei nicht gefunden: {in_path}")
            shutil.copy2(in_path, zip_path)
        extract = td_path / "extract"
        extract.mkdir()
        with zipfile.ZipFile(zip_path) as zf:
            _safe_zip_extract(zf, extract)
        payload = _find_payload_root(extract)
        protected = {".env", "data", "backups"}
        for item in payload.iterdir():
            if item.name in protected:
                continue
            target = ROOT / item.name
            if item.is_dir():
                if item.name in {"app", "tools", "tests"}:
                    target.mkdir(parents=True, exist_ok=True)
                    shutil.copytree(item, target, dirs_exist_ok=True)
                else:
                    shutil.copytree(item, target, dirs_exist_ok=True)
            else:
                shutil.copy2(item, target)
        for script in (ROOT / "hamspotter", ROOT / "install.sh"):
            if script.exists():
                script.chmod(script.stat().st_mode | 0o111)
        # ZIP extraction does not reliably preserve executable bits. Make both
        # the generic wrapper and version-specific helpers executable before
        # running migrations.
        for script in ROOT.glob("upgrade*.sh"):
            if script.is_file():
                script.chmod(script.stat().st_mode | 0o111)
        # A patch may ship one explicit upgrade helper for env/schema migrations.
        upgrade = payload / "upgrade.sh"
        if not upgrade.is_file():
            candidates = sorted(payload.glob("upgrade_v*.sh"))
            upgrade = candidates[0] if len(candidates) == 1 else Path()
        if upgrade and upgrade.is_file():
            installed_upgrade = ROOT / upgrade.name
            installed_upgrade.chmod(installed_upgrade.stat().st_mode | 0o111)
            subprocess.run(["bash", str(installed_upgrade)], cwd=ROOT, check=True)
    restart()
    if not healthcheck():
        raise RuntimeError("Healthcheck nach Update fehlgeschlagen.")
    print(f"✓ Update abgeschlossen. Installierte Version: {version()}")


def uninstall() -> None:
    heading("Deinstallation")
    print(f"Installationsverzeichnis: {ROOT}")
    if not _ask_bool("HAM-Spotter Container stoppen und entfernen", False):
        print("Abgebrochen.")
        return
    final_backup = backup(str(ROOT.parent / f"hamspotter-before-uninstall-{time.strftime('%Y%m%d-%H%M%S')}.tar.gz"))
    compose(["down", "--remove-orphans"], check=False)
    print(f"Sicherungsdatei außerhalb des Projektordners: {final_backup}")
    if _ask_bool("Auch Programmordner und lokale Daten löschen", False):
        symlink = Path("/usr/local/bin/hamspotter")
        try:
            if symlink.is_symlink() and symlink.resolve() == (ROOT / "hamspotter").resolve():
                if os.access(symlink.parent, os.W_OK):
                    symlink.unlink()
                elif shutil.which("sudo"):
                    subprocess.run(["sudo", "rm", "-f", str(symlink)], check=False)
        except Exception:
            pass
        shutil.rmtree(ROOT)
        print("✓ Programmordner entfernt.")
    else:
        print("✓ Container entfernt; Dateien bleiben erhalten.")


def about() -> None:
    heading("Über HAM Spotter")
    print("HAM Spotter")
    print(f"Version: {version()}")
    print("Urheber / Maintainer: DL1HLK")
    print("Copyright © 2026 DL1HLK")
    print("Lizenz: GNU General Public License v3.0 only (GPL-3.0-only)")
    print("Freie Open-Source-Software für den Amateurfunk.")
    print("Projekt: https://github.com/dl1hlk-del/ham-spotter")


def menu() -> None:
    while True:
        heading("Management")
        print("1  Status")
        print("2  Konfiguration")
        print("3  Update")
        print("4  Backup")
        print("5  Restore")
        print("6  Logs")
        print("7  Neustart")
        print("8  Healthcheck")
        print("9  Über HAM Spotter")
        print("10 Deinstallation")
        print("0  Ende")
        choice = input("\nAuswahl: ").strip()
        try:
            if choice == "0": return
            if choice == "1": status()
            elif choice == "2": configure()
            elif choice == "3": update()
            elif choice == "4": backup()
            elif choice == "5": restore()
            elif choice == "6": logs()
            elif choice == "7": restart()
            elif choice == "8": healthcheck()
            elif choice == "9": about()
            elif choice == "10": uninstall(); return
            else: print("Ungültige Auswahl.")
        except KeyboardInterrupt:
            print("\nAbgebrochen.")
        except Exception as exc:
            print(f"✗ Fehler: {exc}")
        input("\nEnter zum Fortfahren …")


def main() -> int:
    parser = argparse.ArgumentParser(prog="hamspotter", description="HAM Spotter Management")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("menu")
    sub.add_parser("status")
    sub.add_parser("configure")
    up = sub.add_parser("update"); up.add_argument("source", nargs="?")
    bk = sub.add_parser("backup"); bk.add_argument("destination", nargs="?"); bk.add_argument("--full", action="store_true", help="inkl. kurzlebiger Rohspots")
    rs = sub.add_parser("restore"); rs.add_argument("source", nargs="?")
    sub.add_parser("logs")
    sub.add_parser("restart")
    sub.add_parser("healthcheck")
    sub.add_parser("about")
    sub.add_parser("uninstall")
    sub.add_parser("version")
    args = parser.parse_args()
    cmd = args.cmd or "menu"
    try:
        if cmd == "menu": menu()
        elif cmd == "status": status()
        elif cmd == "configure": configure()
        elif cmd == "update": update(args.source)
        elif cmd == "backup": backup(args.destination, full=args.full)
        elif cmd == "restore": restore(args.source)
        elif cmd == "logs": logs()
        elif cmd == "restart": restart()
        elif cmd == "healthcheck": return 0 if healthcheck() else 1
        elif cmd == "about": about()
        elif cmd == "uninstall": uninstall()
        elif cmd == "version": print(version())
        return 0
    except KeyboardInterrupt:
        print("\nAbgebrochen.")
        return 130
    except Exception as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
