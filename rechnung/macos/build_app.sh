#!/bin/bash
# Baut "CS Rechnung.app" und ein ZIP daraus nach rechnung/dist/.
# Läuft auf macOS und Linux (keine Xcode-Abhängigkeit).
set -euo pipefail
HIER="$(cd "$(dirname "$0")" && pwd)"
SRC="$(cd "$HIER/.." && pwd)"
DIST="$SRC/dist"
VERSION="${1:-1.0}"
APP="$DIST/CS Rechnung.app"

rm -rf "$APP" "$DIST/CS-Rechnung-mac.zip"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources/app/vorlagen" "$APP/Contents/Resources/app/ui"
sed "s/__VERSION__/$VERSION/g" "$HIER/Info.plist" > "$APP/Contents/Info.plist"
cp "$HIER/CS-Rechnung" "$APP/Contents/MacOS/CS-Rechnung"
chmod 755 "$APP/Contents/MacOS/CS-Rechnung"
cp "$HIER/AppIcon.icns" "$APP/Contents/Resources/AppIcon.icns"
cp "$SRC/rechnung_aus_ab.py" "$SRC/quelle_ariba.py" "$SRC/app.py" "$APP/Contents/Resources/app/"
cp "$SRC/ui/index.html" "$APP/Contents/Resources/app/ui/"
cp "$SRC/vorlagen/"*.dotx "$APP/Contents/Resources/app/vorlagen/"
printf 'pdfplumber>=0.10\n' > "$APP/Contents/Resources/app/requirements-app.txt"

if command -v ditto >/dev/null; then
  (cd "$DIST" && ditto -c -k --keepParent "CS Rechnung.app" CS-Rechnung-mac.zip)
else
  (cd "$DIST" && zip -qr -X CS-Rechnung-mac.zip "CS Rechnung.app")
fi
echo "$DIST/CS-Rechnung-mac.zip"
