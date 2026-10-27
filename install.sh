#!/usr/bin/env bash
# Install Promodor for the current user.  Usage: ./install.sh [--autostart] | --uninstall
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
DEST="$HOME/.local/share/promodor"
BIN="$HOME/.local/bin/promodor"
DESKTOP="$HOME/.local/share/applications/promodor.desktop"
ICON="$HOME/.local/share/icons/hicolor/scalable/apps/promodor.svg"
AUTOSTART="$HOME/.config/autostart/promodor.desktop"

if [[ "${1:-}" == "--uninstall" ]]; then
  rm -rf "$DEST" "$BIN" "$DESKTOP" "$ICON" "$AUTOSTART"
  echo "Promodor removed (settings kept in ~/.config/promodor)."
  exit 0
fi

python3 -c 'import gi; gi.require_version("Gtk","3.0"); from gi.repository import Gtk' 2>/dev/null || {
  echo "Missing GTK3 Python bindings. Run: sudo apt install python3-gi gir1.2-gtk-3.0"; exit 1; }

mkdir -p "$DEST" "$(dirname "$BIN")" "$(dirname "$DESKTOP")" "$(dirname "$ICON")"
install -m 755 "$SRC/promodor.py" "$DEST/promodor.py"
install -m 644 "$SRC/promodor.svg" "$ICON"
printf '#!/bin/sh\nexec python3 "%s/promodor.py" "$@"\n' "$DEST" > "$BIN"
chmod 755 "$BIN"

cat > "$DESKTOP" <<DESK
[Desktop Entry]
Type=Application
Name=Promodor
Comment=Floating Pomodoro clock with optional to-do list
Exec=$BIN
Icon=promodor
Terminal=false
Categories=Utility;
StartupWMClass=promodor
DESK

if [[ "${1:-}" == "--autostart" ]]; then
  mkdir -p "$(dirname "$AUTOSTART")"
  cp "$DESKTOP" "$AUTOSTART"
  echo "X-GNOME-Autostart-enabled=true" >> "$AUTOSTART"
  echo "Autostart enabled."
fi
echo "Installed. Launch 'Promodor' from the app grid, or run: $BIN"
