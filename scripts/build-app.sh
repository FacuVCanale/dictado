#!/bin/sh
# Construye e instala Dictado.app (launcher C + Info.plist, firma ad-hoc).
#
# Idempotente: si el launcher, el Info.plist y la ruta del python no cambiaron, NO toca el bundle.
# Re-firmar cambia el cdhash, y con firma ad-hoc TCC ata los permisos a ese cdhash: un rebuild real
# obliga a dar Micrófono y Accesibilidad de nuevo. El código Python vive en
# el repo (no en el bundle), así que cambiarlo nunca requiere rebuild.
#
# Uso: scripts/build-app.sh [--force]
#   DICTADO_APP_DIR    destino (default ~/Applications)
#   DICTADO_BUNDLE_ID  bundle id y label del servicio launchd (default io.github.dictado)
set -eu

REPO=$(cd "$(dirname "$0")/.." && pwd)
APP_DIR=${DICTADO_APP_DIR:-$HOME/Applications}
APP="$APP_DIR/Dictado.app"
PY="$REPO/.venv/bin/python"
BUNDLE_ID=${DICTADO_BUNDLE_ID:-io.github.dictado}
FORCE=0
[ "${1:-}" = "--force" ] && FORCE=1

VERSION=$(sed -n 's/^version = "\(.*\)"/\1/p' "$REPO/pyproject.toml" | head -1)

echo "[1/4] uv sync (venv del repo)"
(cd "$REPO" && uv sync --frozen --quiet)
[ -x "$PY" ] || { echo "ERROR: no existe $PY" >&2; exit 1; }

STAMP=$( { cat "$REPO/macos-app/launcher.c" "$REPO/macos-app/Info.plist.in"; echo "$PY $VERSION $BUNDLE_ID"; } | shasum -a 256 | cut -d' ' -f1)
if [ "$FORCE" = 0 ] && [ -f "$APP/Contents/Resources/build-stamp" ] \
   && [ "$(cat "$APP/Contents/Resources/build-stamp")" = "$STAMP" ] \
   && codesign --verify --strict "$APP" 2>/dev/null; then
    echo "[2/4] $APP sin cambios: no se recompila ni se re-firma (los permisos de TCC siguen valiendo)."
    exit 0
fi

echo "[2/4] compilando launcher"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
B="$TMP/Dictado.app/Contents"
mkdir -p "$B/MacOS" "$B/Resources"
clang -O2 -Wall -Werror -arch arm64 -mmacosx-version-min=13.0 \
    -DDICTADO_PYTHON="\"$PY\"" -o "$B/MacOS/Dictado" "$REPO/macos-app/launcher.c"
sed -e "s/@VERSION@/$VERSION/g" -e "s/@BUNDLE_ID@/$BUNDLE_ID/g" "$REPO/macos-app/Info.plist.in" > "$B/Info.plist"
plutil -lint -s "$B/Info.plist"
echo "$STAMP" > "$B/Resources/build-stamp"

echo "[3/4] firma ad-hoc"
codesign --force --sign - --identifier "$BUNDLE_ID" "$TMP/Dictado.app"
codesign --verify --strict "$TMP/Dictado.app"

echo "[4/4] instalando en $APP"
mkdir -p "$APP_DIR"
rm -rf "$APP"
mv "$TMP/Dictado.app" "$APP"
echo "Listo: $APP"
echo "AVISO: el bundle se re-firmó. Si ya tenía permisos, macOS los invalida: volvé a activar Accesibilidad (README)."
