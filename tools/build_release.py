#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import hashlib
import os
import shutil
import textwrap
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

RUNTIME_FILES = (
    ".env.example",
    "Dockerfile",
    "LICENSE",
    "README.md",
    "README.de.md",
    "VERSION",
    "docker-compose.yml",
    "hamspotter",
    "install.sh",
    "requirements.txt",
    "upgrade.sh",
)
RUNTIME_DIRS = ("app", "tools")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def ignore_runtime(path: Path) -> bool:
    parts = set(path.parts)
    return (
        "__pycache__" in parts
        or ".pytest_cache" in parts
        or path.suffix in {".pyc", ".pyo"}
        or path.name in {".DS_Store"}
    )


def copy_runtime(stage: Path, version: str) -> Path:
    package = stage / f"ham-spotter-v{version}"
    package.mkdir(parents=True)

    for name in RUNTIME_FILES:
        src = ROOT / name
        if not src.exists():
            raise FileNotFoundError(f"Required release file is missing: {name}")
        shutil.copy2(src, package / name)

    version_upgrade = ROOT / f"upgrade_v{version}.sh"
    if version_upgrade.exists():
        shutil.copy2(version_upgrade, package / version_upgrade.name)

    for dirname in RUNTIME_DIRS:
        src_dir = ROOT / dirname
        dst_dir = package / dirname
        for src in src_dir.rglob("*"):
            rel = src.relative_to(src_dir)
            if ignore_runtime(rel):
                continue
            dst = dst_dir / rel
            if src.is_dir():
                dst.mkdir(parents=True, exist_ok=True)
            elif src.is_file():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)

    # Build tooling is useful in the source repository, but not required by an
    # installed station. Keep the universal package focused on runtime files.
    (package / "tools" / "build_release.py").unlink(missing_ok=True)

    for executable in (
        package / "install.sh",
        package / "hamspotter",
        package / "upgrade.sh",
        package / f"upgrade_v{version}.sh",
        package / "tools" / "hamspotter_manager.py",
        package / "tools" / "hamspotter_manager_i18n.py",
    ):
        if executable.exists():
            executable.chmod(executable.stat().st_mode | 0o111)

    return package


def make_zip(package: Path, output: Path) -> Path:
    zip_path = output / "ham-spotter-universal.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for src in sorted(package.rglob("*")):
            arcname = src.relative_to(package.parent)
            if src.is_dir():
                continue
            info = zipfile.ZipInfo.from_file(src, arcname.as_posix())
            info.compress_type = zipfile.ZIP_DEFLATED
            info._compresslevel = 6
            with src.open("rb") as fh:
                zf.writestr(info, fh.read())
    return zip_path


def make_self_extractor(version: str, payload_zip: Path, output: Path) -> Path:
    payload_sha = sha256(payload_zip)
    payload_b64 = base64.b64encode(payload_zip.read_bytes()).decode("ascii")
    wrapped = "\n".join(textwrap.wrap(payload_b64, 76))

    script = f'''#!/usr/bin/env bash
set -euo pipefail

VERSION="{version}"
PAYLOAD_SHA256="{payload_sha}"
SELF="$(readlink -f "${{BASH_SOURCE[0]}}")"

banner() {{
  cat <<EOF

╔══════════════════════════════════════════════════════════╗
║                HAM Spotter V${{VERSION}}                      ║
║               Universal One-File Installer              ║
╚══════════════════════════════════════════════════════════╝
EOF
}}

need_python() {{
  if ! command -v python3 >/dev/null 2>&1; then
    echo "ERROR: Python 3 is required by this installer." >&2
    echo "Raspberry Pi OS/Debian: sudo apt-get update && sudo apt-get install -y python3" >&2
    exit 1
  fi
}}

extract_payload() {{
  local dest="$1"
  mkdir -p "$dest"
  python3 - "$SELF" "$dest" "$PAYLOAD_SHA256" <<'PY'
from pathlib import Path
from io import BytesIO
import base64, hashlib, sys, zipfile

self_path = Path(sys.argv[1])
dest = Path(sys.argv[2]).resolve()
expected = sys.argv[3].lower()
text = self_path.read_text(encoding="utf-8")
begin_marker = "__HAMSPOTTER_PAYLOAD_BEGIN__\\n"
end_marker = "\\n__HAMSPOTTER_PAYLOAD_END__"
try:
    start = text.index(begin_marker) + len(begin_marker)
    end = text.index(end_marker, start)
except ValueError:
    raise SystemExit("ERROR: Embedded HAM Spotter package was not found.")

payload = "".join(text[start:end].splitlines())
try:
    raw = base64.b64decode(payload, validate=True)
except Exception as exc:
    raise SystemExit(f"ERROR: Embedded package is damaged: {{exc}}")

actual = hashlib.sha256(raw).hexdigest()
if actual != expected:
    raise SystemExit(
        "ERROR: Embedded package checksum mismatch.\\n"
        f"Expected: {{expected}}\\nFound: {{actual}}"
    )

with zipfile.ZipFile(BytesIO(raw)) as zf:
    for member in zf.infolist():
        target = (dest / member.filename).resolve()
        try:
            target.relative_to(dest)
        except ValueError:
            raise SystemExit(f"ERROR: Unsafe path in package: {{member.filename}}")
    zf.extractall(dest)
PY
}}

verify_payload() {{
  local temp
  temp="$(mktemp -d)"
  trap 'rm -rf "$temp"' RETURN
  extract_payload "$temp"
  echo "✓ Embedded HAM Spotter V$VERSION package is complete and its SHA-256 checksum is valid."
  rm -rf "$temp"
  trap - RETURN
}}

banner
need_python

case "${{1:-}}" in
  --verify)
    verify_payload
    exit 0
    ;;
  --extract-only)
    DEST="${{2:-$PWD/ham-spotter-v$VERSION-extracted}}"
    DEST="$(python3 -c 'import os,sys; print(os.path.abspath(os.path.expanduser(sys.argv[1])))' "$DEST")"
    if [[ -e "$DEST" && -n "$(ls -A "$DEST" 2>/dev/null || true)" ]]; then
      echo "ERROR: Destination directory is not empty: $DEST" >&2
      exit 1
    fi
    echo "Extracting to: $DEST"
    extract_payload "$DEST"
    echo "✓ Done."
    exit 0
    ;;
  -h|--help)
    cat <<EOF
Usage:
  ./hamspotter-installer.sh
      Starts the interactive HAM Spotter installer.

  ./hamspotter-installer.sh --verify
      Verifies the embedded installation package.

  ./hamspotter-installer.sh --extract-only [DIRECTORY]
      Extracts the package without installing it.
EOF
    exit 0
    ;;
esac

TMPDIR_INSTALL="$(mktemp -d -t hamspotter-install-XXXXXX)"
cleanup() {{ rm -rf "$TMPDIR_INSTALL"; }}
trap cleanup EXIT INT TERM

echo "Verifying and extracting the installation package …"
extract_payload "$TMPDIR_INSTALL"
ROOT="$TMPDIR_INSTALL/ham-spotter-v$VERSION"
if [[ ! -f "$ROOT/install.sh" ]]; then
  echo "ERROR: install.sh is missing from the embedded package." >&2
  exit 1
fi

echo "✓ Installation package verified."
echo "Starting setup assistant …"
echo

set +e
bash "$ROOT/install.sh"
STATUS=$?
set -e

if [[ $STATUS -eq 0 ]]; then
  echo
  echo "✓ HAM Spotter installation completed."
else
  echo
  echo "Installation ended with exit code $STATUS." >&2
fi
exit "$STATUS"

: <<'__HAMSPOTTER_PAYLOAD_END__'
__HAMSPOTTER_PAYLOAD_BEGIN__
{wrapped}
__HAMSPOTTER_PAYLOAD_END__
'''

    path = output / "hamspotter-installer.sh"
    path.write_text(script, encoding="utf-8")
    path.chmod(0o755)
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="Build HAM Spotter release assets")
    parser.add_argument("--output", default="dist", help="Output directory")
    args = parser.parse_args()

    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    if not version:
        raise RuntimeError("VERSION is empty")

    output = (ROOT / args.output).resolve()
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)

    stage = output / ".stage"
    stage.mkdir()
    package = copy_runtime(stage, version)
    payload_zip = make_zip(package, output)
    installer = make_self_extractor(version, payload_zip, output)

    notes = ROOT / f"RELEASE_NOTES_{version}.md"
    if notes.exists():
        shutil.copy2(notes, output / notes.name)

    sums = output / "SHA256SUMS.txt"
    sums.write_text(
        f"{sha256(installer)}  {installer.name}\n"
        f"{sha256(payload_zip)}  {payload_zip.name}\n",
        encoding="utf-8",
    )

    shutil.rmtree(stage)
    print(f"Built HAM Spotter {version} release assets in {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
