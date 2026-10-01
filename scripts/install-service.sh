#!/bin/sh
# Instala (o reemplaza) el LaunchAgent que lanza Dictado.app. El plist se genera desde
# launchd/dictado.plist.in con las rutas de $HOME; el label es el bundle id de la app instalada
# (DICTADO_BUNDLE_ID al construirla). Si había otro LaunchAgent lanzando el mismo ejecutable
# (p. ej. con un bundle id anterior), se descarga y se borra para no correr dos copias.
# Requiere haber corrido scripts/build-app.sh y dado Accesibilidad a Dictado.app (README).
set -eu
REPO=$(cd "$(dirname "$0")/.." && pwd)
APP="${DICTADO_APP_DIR:-$HOME/Applications}/Dictado.app"
APP_EXE="$APP/Contents/MacOS/Dictado"
LOG="$HOME/Library/Logs/dictado.log"
AGENTS="$HOME/Library/LaunchAgents"
[ -x "$APP_EXE" ] || { echo "ERROR: falta $APP_EXE (corré scripts/build-app.sh)" >&2; exit 1; }
LABEL=$(plutil -extract CFBundleIdentifier raw "$APP/Contents/Info.plist")
DEST="$AGENTS/$LABEL.plist"
DOMAIN="gui/$(id -u)"

mkdir -p "$AGENTS" "$(dirname "$LOG")"
for old in "$AGENTS"/*.plist; do
    [ -f "$old" ] && grep -qF "<string>$APP_EXE</string>" "$old" || continue
    old_label=$(plutil -extract Label raw "$old" 2>/dev/null || basename "$old" .plist)
    launchctl bootout "$DOMAIN/$old_label" 2>/dev/null || true
    [ "$old" = "$DEST" ] || { rm -f "$old"; echo "Reemplazado el LaunchAgent anterior $old_label"; }
done
launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true

esc() { printf '%s' "$1" | sed -e 's/[&|\\]/\\&/g'; }
sed -e "s|@LABEL@|$(esc "$LABEL")|g" -e "s|@APP_EXE@|$(esc "$APP_EXE")|g" -e "s|@LOG@|$(esc "$LOG")|g" \
    "$REPO/launchd/dictado.plist.in" > "$DEST"
plutil -lint -s "$DEST"
launchctl bootstrap "$DOMAIN" "$DEST"
sleep 3
launchctl print "$DOMAIN/$LABEL" | grep -E "state =|program =|pid =" | head -3
echo "Servicio: $LABEL   Log: tail -f $LOG"
