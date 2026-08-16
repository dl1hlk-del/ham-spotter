#!/usr/bin/env bash
set -euo pipefail

SOURCE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_TARGET="${HOME}/ham-spotter"
TARGET=""
IN_PLACE=0
HAM_LANGUAGE="en"
INSTALLER_VERSION="$(cat "$SOURCE_ROOT/VERSION" 2>/dev/null || printf 'unknown')"

if [[ "${1:-}" == "--in-place" ]]; then
  IN_PLACE=1
  TARGET="$SOURCE_ROOT"
fi

bi() {
  local en="$1" de="$2"
  if [[ "$HAM_LANGUAGE" == "de" ]]; then
    printf '%s' "$de"
  else
    printf '%s' "$en"
  fi
}

banner() {
  printf '\n╔══════════════════════════════════════════════════════════╗\n'
  printf '║                HAM Spotter %-10s                 ║\n' "V${INSTALLER_VERSION}"
  printf '║          Universal Installer & Management               ║\n'
  printf '╚══════════════════════════════════════════════════════════╝\n'
}

select_language() {
  cat <<'EOF'

Language / Sprache:
  1) English
  2) Deutsch
EOF
  local choice
  read -r -p "Selection / Auswahl [1]: " choice
  case "${choice:-1}" in
    2|de|DE|deutsch|Deutsch) HAM_LANGUAGE="de" ;;
    *) HAM_LANGUAGE="en" ;;
  esac
  echo
  if [[ "$HAM_LANGUAGE" == "de" ]]; then
    echo "✓ Sprache: Deutsch"
  else
    echo "✓ Language: English"
  fi
}

ask() {
  local prompt="$1" default="${2:-}" value
  if [[ -n "$default" ]]; then
    read -r -p "$prompt [$default]: " value
    printf '%s' "${value:-$default}"
  else
    read -r -p "$prompt: " value
    printf '%s' "$value"
  fi
}

ask_yesno() {
  local prompt="$1" default="${2:-yes}" answer suffix
  if [[ "$HAM_LANGUAGE" == "de" ]]; then
    if [[ "$default" == "yes" ]]; then suffix='[J/n]'; else suffix='[j/N]'; fi
  else
    if [[ "$default" == "yes" ]]; then suffix='[Y/n]'; else suffix='[y/N]'; fi
  fi
  read -r -p "$prompt $suffix: " answer
  answer="${answer,,}"
  if [[ -z "$answer" ]]; then [[ "$default" == "yes" ]]; return; fi
  [[ "$answer" =~ ^(j|ja|y|yes|1)$ ]]
}

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "$(bi "ERROR: '$1' is missing." "FEHLER: '$1' fehlt.")" >&2
    exit 1
  }
}

validate_locator() {
  python3 - "$1" <<'PY'
import re,sys
v=sys.argv[1].strip().upper()
ok=len(v) in (2,4,6,8) and bool(re.fullmatch(r'[A-R]{2}(?:[0-9]{2}(?:[A-X]{2}(?:[0-9]{2})?)?)?',v))
sys.exit(0 if ok else 1)
PY
}

locator_center() {
  python3 - "$1" <<'PY'
import sys
loc=sys.argv[1].strip().upper()
lon=-180.0+(ord(loc[0])-65)*20.0
lat=-90.0+(ord(loc[1])-65)*10.0
lon_size,lat_size=20.0,10.0
if len(loc)>=4:
    lon+=int(loc[2])*2.0; lat+=int(loc[3]); lon_size,lat_size=2.0,1.0
if len(loc)>=6:
    lon+=(ord(loc[4])-65)*(2/24); lat+=(ord(loc[5])-65)*(1/24); lon_size,lat_size=2/24,1/24
if len(loc)>=8:
    lon+=int(loc[6])*(2/240); lat+=int(loc[7])*(1/240); lon_size,lat_size=2/240,1/240
print(f'{lat+lat_size/2:.5f}, {lon+lon_size/2:.5f}')
PY
}

ensure_docker() {
  if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    return 0
  fi
  echo
  echo "$(bi "Docker with the Compose plugin was not found." "Docker mit Compose-Plugin wurde nicht gefunden.")"
  if ! ask_yesno "$(bi "Install Docker now" "Docker jetzt installieren")" yes; then
    echo "$(bi "Installation cancelled. Please install Docker and 'docker compose'." "Installation abgebrochen. Bitte Docker + 'docker compose' installieren.")"
    exit 1
  fi
  if ! command -v apt-get >/dev/null 2>&1; then
    echo "$(bi "Automatic Docker installation is supported only on Debian/Raspberry Pi OS." "Automatische Docker-Installation wird nur auf Debian/Raspberry Pi OS unterstützt.")" >&2
    exit 1
  fi
  need_cmd sudo
  sudo apt-get update
  if ! sudo apt-get install -y docker.io docker-compose-plugin; then
    sudo apt-get install -y docker.io docker-compose-v2
  fi
  sudo systemctl enable --now docker || true
  sudo usermod -aG docker "$USER" || true
  if ! sudo docker compose version >/dev/null 2>&1; then
    echo "$(bi "ERROR: Docker Compose is still unavailable after installation." "FEHLER: Docker Compose ist nach der Installation nicht verfügbar.")" >&2
    exit 1
  fi
  echo "$(bi "Docker was installed. Until the next login the installer will use sudo when required." "Docker wurde installiert. Bis zur nächsten Anmeldung verwendet der Installer bei Bedarf sudo.")"
}

docker_compose() {
  if docker info >/dev/null 2>&1; then
    docker compose "$@"
  else
    sudo docker compose "$@"
  fi
}

install_global_command() {
  local target="$1/hamspotter"
  if [[ -w /usr/local/bin ]]; then
    ln -sfn "$target" /usr/local/bin/hamspotter
    echo "$(bi "✓ Command installed: /usr/local/bin/hamspotter" "✓ Befehl installiert: /usr/local/bin/hamspotter")"
  elif command -v sudo >/dev/null 2>&1; then
    sudo ln -sfn "$target" /usr/local/bin/hamspotter
    echo "$(bi "✓ Command installed: /usr/local/bin/hamspotter" "✓ Befehl installiert: /usr/local/bin/hamspotter")"
  else
    mkdir -p "$HOME/.local/bin"
    ln -sfn "$target" "$HOME/.local/bin/hamspotter"
    echo "$(bi "✓ Command installed: $HOME/.local/bin/hamspotter" "✓ Befehl installiert: $HOME/.local/bin/hamspotter")"
    echo "$(bi "  If required, add $HOME/.local/bin to PATH." "  Falls nötig, ergänze $HOME/.local/bin in PATH.")"
  fi
}

banner
select_language
need_cmd python3

if [[ "$IN_PLACE" -eq 0 ]]; then
  TARGET="$(ask "$(bi 'Installation directory' 'Installationsverzeichnis')" "$DEFAULT_TARGET")"
  TARGET="$(python3 -c 'import os,sys; print(os.path.abspath(os.path.expanduser(sys.argv[1])))' "$TARGET")"
  if [[ "$TARGET" != "$SOURCE_ROOT" ]]; then
    if [[ -e "$TARGET/.env" ]]; then
      echo "$(bi "A .env file already exists in the target. Use the update function for an existing installation." "Im Ziel existiert bereits eine .env. Für eine bestehende Installation bitte das Update verwenden.")" >&2
      exit 1
    fi
    mkdir -p "$TARGET"
    # Copy the release while keeping generated runtime data out of the transfer.
    (cd "$SOURCE_ROOT" && tar --exclude='./.env' --exclude='./data/*' --exclude='./backups/*' --exclude='./.pytest_cache' --exclude='*/__pycache__' -cf - .) | (cd "$TARGET" && tar -xf -)
    chmod +x "$TARGET/install.sh" "$TARGET/hamspotter" "$TARGET/tools/hamspotter_manager.py"
    exec "$TARGET/install.sh" --in-place --language "$HAM_LANGUAGE"
  fi
fi

# Preserve the selected language when install.sh re-executes in the target folder.
if [[ "${1:-}" == "--in-place" && "${2:-}" == "--language" && -n "${3:-}" ]]; then
  HAM_LANGUAGE="$3"
fi

cd "$TARGET"
ensure_docker

CALLSIGN="$(ask "$(bi 'Callsign' 'Rufzeichen')")"
CALLSIGN="${CALLSIGN^^}"
while [[ ! "$CALLSIGN" =~ ^[A-Z0-9/]{2,20}$ ]]; do
  echo "$(bi "Invalid callsign format." "Ungültiges Rufzeichenformat.")"
  CALLSIGN="$(ask "$(bi 'Callsign' 'Rufzeichen')")"; CALLSIGN="${CALLSIGN^^}"
done

QTH="$(ask "$(bi 'QTH locator (Maidenhead)' 'QTH-Locator (Maidenhead)')")"; QTH="${QTH^^}"
until validate_locator "$QTH"; do
  echo "$(bi "Invalid locator. Allowed: 2/4/6/8 characters, e.g. FN31PR." "Ungültiger Locator. Erlaubt: 2/4/6/8 Zeichen, z.B. FN31PR.")"
  QTH="$(ask "$(bi 'QTH locator (Maidenhead)' 'QTH-Locator (Maidenhead)')")"; QTH="${QTH^^}"
done
CENTER="$(locator_center "$QTH")"
echo "$(bi 'Locator center' 'Locator-Zentrum'): $CENTER"
if ! ask_yesno "$(bi "Use callsign $CALLSIGN and QTH $QTH" "Rufzeichen $CALLSIGN und QTH $QTH übernehmen")" yes; then
  echo "$(bi 'Installation cancelled.' 'Installation abgebrochen.')"
  exit 1
fi

HF=yes; VHF=yes
ask_yesno "$(bi 'Enable HF + 6 m' 'HF + 6 m aktivieren')" yes || HF=no
ask_yesno "$(bi 'Enable 4 m / 2 m / 70 cm / 23 cm' '4 m / 2 m / 70 cm / 23 cm aktivieren')" yes || VHF=no
if [[ "$HF" == no && "$VHF" == no ]]; then
  echo "$(bi 'At least one band layer must be enabled. HF + 6 m will be enabled.' 'Mindestens eine Band-Schicht muss aktiv sein. HF + 6 m wird aktiviert.')"
  HF=yes
fi

if [[ "$HAM_LANGUAGE" == "de" ]]; then
  cat <<'EOF'

Primärer Ausbreitungsmodus:
  1) SSB
  2) CW
  3) DIGITAL
EOF
else
  cat <<'EOF'

Primary propagation mode:
  1) SSB
  2) CW
  3) DIGITAL
EOF
fi
MODE_CHOICE="$(ask "$(bi 'Selection' 'Auswahl')" '1')"
case "${MODE_CHOICE,,}" in
  1|ssb) MODE=ssb ;;
  2|cw) MODE=cw ;;
  3|digital) MODE=digital ;;
  *) MODE=ssb ;;
esac

PORT="$(ask "$(bi 'Web port' 'Web-Port')" '8095')"
while [[ ! "$PORT" =~ ^[0-9]+$ ]] || (( PORT < 1 || PORT > 65535 )); do
  echo "$(bi 'Invalid port.' 'Ungültiger Port.')"
  PORT="$(ask "$(bi 'Web port' 'Web-Port')" '8095')"
done

RADIUS="$(ask "$(bi 'Local PSK/RBN radius in km' 'Lokaler PSK/RBN-Radius in km')" '325')"
TZ_DEFAULT="Europe/Berlin"
if [[ -r /etc/timezone ]]; then TZ_DEFAULT="$(tr -d '\n' </etc/timezone)"; fi
TIMEZONE="$(ask "$(bi 'Dashboard timezone (IANA)' 'Dashboard-Zeitzone (IANA)')" "$TZ_DEFAULT")"

TG_TOKEN=""; TG_CHAT=""; TG_ENABLED=false
if ask_yesno "$(bi 'Configure Telegram' 'Telegram einrichten')" no; then
  TG_ENABLED=true
  read -r -s -p "Telegram Bot Token: " TG_TOKEN; echo
  TG_CHAT="$(ask 'Telegram Chat ID')"
fi

cp .env.example .env
chmod 600 .env

BANDS=""
DEFAULT_LAYER=hf
if [[ "$HF" == yes ]]; then BANDS="6m,10m,12m,15m,17m,20m,40m,60m,80m"; fi
if [[ "$VHF" == yes ]]; then
  [[ -n "$BANDS" ]] && BANDS+=","
  BANDS+="4m,2m,70cm,23cm"
  [[ "$HF" == no ]] && DEFAULT_LAYER=vhf
fi

# Write/replace settings without exposing the Telegram token on screen.
python3 - "$CALLSIGN" "$QTH" "$BANDS" "$MODE" "$PORT" "$RADIUS" "$TIMEZONE" "$DEFAULT_LAYER" "$TG_ENABLED" "$TG_TOKEN" "$TG_CHAT" "$HAM_LANGUAGE" <<'PY'
from pathlib import Path
import sys
p=Path('.env')
keys={
 'CALLSIGN':sys.argv[1], 'QTH_LOCATOR':sys.argv[2], 'BANDS':sys.argv[3],
 'PRIMARY_PROP_MODE':sys.argv[4], 'HAMSPOTTER_PORT':sys.argv[5],
 'LOCAL_RX_RADIUS_KM':sys.argv[6], 'DASHBOARD_TIMEZONE':sys.argv[7],
 'DASHBOARD_DEFAULT_LAYER':sys.argv[8], 'DXCLUSTER_LOGIN':sys.argv[1],
 'TELEGRAM_ALERTS':'true' if sys.argv[9]=='true' else 'false',
 'TELEGRAM_COMMANDS':'true' if sys.argv[9]=='true' else 'false',
 'TELEGRAM_BOT_TOKEN':sys.argv[10], 'TELEGRAM_CHAT_ID':sys.argv[11],
 'HAMSPOTTER_LANGUAGE':sys.argv[12],
}
lines=p.read_text(encoding='utf-8').splitlines(); left=dict(keys); out=[]
for line in lines:
    if line.strip() and not line.lstrip().startswith('#') and '=' in line:
        k=line.split('=',1)[0].strip()
        if k in left:
            out.append(f'{k}={left.pop(k)}'); continue
    out.append(line)
if left:
    out += ['', '# Universal Installer'] + [f'{k}={v}' for k,v in left.items()]
p.write_text('\n'.join(out).rstrip()+'\n',encoding='utf-8')
PY
chmod 600 .env
mkdir -p data backups
chmod 700 backups || true

install_global_command "$TARGET"

echo
echo "$(bi 'Building and starting HAM Spotter …' 'Baue und starte HAM Spotter …')"
docker_compose up -d --build

echo "$(bi 'Waiting for healthcheck …' 'Warte auf Healthcheck …')"
OK=0
for _ in $(seq 1 30); do
  if python3 - "$PORT" >/dev/null 2>&1 <<'PYHEALTH'
import sys, urllib.request
urllib.request.urlopen(f"http://127.0.0.1:{sys.argv[1]}/health", timeout=2).read()
PYHEALTH
  then OK=1; break; fi
  sleep 2
done

IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
HOST="$(hostname -f 2>/dev/null || hostname)"
echo
if [[ "$OK" -eq 1 ]]; then
  echo "$(bi '✓ HAM Spotter installed successfully.' '✓ HAM Spotter erfolgreich installiert.')"
else
  echo "$(bi '⚠ The container was started, but the healthcheck is not responding yet.' '⚠ Container wurde gestartet, der Healthcheck antwortet aber noch nicht.')"
  echo "$(bi '  Check with: hamspotter logs' '  Prüfen mit: hamspotter logs')"
fi

if [[ "$HAM_LANGUAGE" == "de" ]]; then
  cat <<EOF

Rufzeichen: $CALLSIGN
QTH:       $QTH
Modus:     ${MODE^^}
Port:      $PORT

Dashboard: http://${IP:-$HOST}:$PORT/
Health:    http://${IP:-$HOST}:$PORT/health

Management ab jetzt einfach mit:

    hamspotter

Direkte Befehle:
    hamspotter status
    hamspotter configure
    hamspotter backup
    hamspotter healthcheck
EOF
else
  cat <<EOF

Callsign: $CALLSIGN
QTH:      $QTH
Mode:     ${MODE^^}
Port:     $PORT

Dashboard: http://${IP:-$HOST}:$PORT/
Health:    http://${IP:-$HOST}:$PORT/health

Open the management menu with:

    hamspotter

Direct commands:
    hamspotter status
    hamspotter configure
    hamspotter backup
    hamspotter healthcheck
EOF
fi
