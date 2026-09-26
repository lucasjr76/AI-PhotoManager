#!/usr/bin/env bash
# Linux AppImage from the PyInstaller onedir build. Run on Ubuntu 22.04 (glibc 2.35) so it
# works on 22.04+ / Zorin 17. The window uses the system WebKitGTK (package
# gir1.2-webkit2-4.1); without it the app opens in the default browser.
set -euo pipefail
VERSION="${1:-0.1.0}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APPDIR="$ROOT/build/AppDir"
TOOL="$ROOT/build/appimagetool"

rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/lib"
cp -a "$ROOT/build/dist/AI-PhotoDocsManager" "$APPDIR/usr/lib/"
cp "$ROOT/packaging/icon.png" "$APPDIR/ai-photodocsmanager.png"
cat > "$APPDIR/ai-photodocsmanager.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=AI-PhotoDocsManager
Comment=Busca offline em fotos e documentos
Exec=AI-PhotoDocsManager
Icon=ai-photodocsmanager
Categories=Graphics;Photography;
Terminal=false
DESKTOP
cat > "$APPDIR/AppRun" <<'APPRUN'
#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"
exec "$HERE/usr/lib/AI-PhotoDocsManager/AI-PhotoDocsManager" "$@"
APPRUN
chmod +x "$APPDIR/AppRun"

if [ ! -x "$TOOL" ]; then
  curl -fsSL -o "$TOOL" \
    https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage
  chmod +x "$TOOL"
fi
mkdir -p "$ROOT/build/installer"
ARCH=x86_64 "$TOOL" --appimage-extract-and-run "$APPDIR" \
  "$ROOT/build/installer/AI-PhotoDocsManager-$VERSION-x86_64.AppImage"
