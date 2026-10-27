#!/usr/bin/env bash
# Install Promodor for the current user.
#   ./install.sh                 install
#   ./install.sh --autostart     install and start at login
#   ./install.sh --uninstall     remove the app, keep settings and tasks
#   ./install.sh --purge         remove the app and all settings, tasks and caches
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
DEST="$HOME/.local/share/promodor"
BIN="$HOME/.local/bin/promodor"
DESKTOP="$HOME/.local/share/applications/promodor.desktop"
ICON="$HOME/.local/share/icons/hicolor/scalable/apps/promodor.svg"
AUTOSTART="$HOME/.config/autostart/promodor.desktop"
CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}/promodor"

remove() {
  local path
  for path in "$@"; do
    if [[ -e "$path" || -L "$path" ]]; then
      rm -rf -- "$path"
      echo "  removed $path"
    fi
  done
}

if [[ "${1:-}" == "--uninstall" || "${1:-}" == "--purge" ]]; then
  # Quit a running widget so nothing writes its files back after removal.
  if pkill -f '^python3[^ ]* [^ ]*promodor\.py' 2>/dev/null; then
    echo "  stopped running Promodor"
  fi
  echo "Removing Promodor..."
  remove "$DEST" "$BIN" "$DESKTOP" "$ICON" "$AUTOSTART"
  if [[ "$1" == "--purge" ]]; then
    remove "$CONFIG" "$SRC/__pycache__"
  fi
  if [[ "$1" == "--purge" ]]; then
    echo "Promodor completely removed, including settings and tasks."
    echo "(The source folder $SRC was left in place; delete it yourself if you no longer need it.)"
  else
    echo "Promodor removed. Settings and tasks kept in $CONFIG (use --purge to delete them)."
  fi
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
