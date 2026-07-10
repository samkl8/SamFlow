#!/bin/bash
# SamFlow installer for macOS (Apple Silicon).
#
# Sets up everything that can be automated: dependencies, the Whisper model, the
# Python environment, the warm-model background service, and the .app bundle that
# auto-starts at login. The three macOS permission grants and the Fn-key setting
# are GUI actions you do by hand afterwards — the script prints exactly what.
#
# Safe to re-run: every step checks whether it is already done.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOME_DIR="$HOME"
MODEL_URL="https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo-q5_0.bin"
MODEL="$DIR/models/ggml-large-v3-turbo-q5_0.bin"
APP="$HOME/Applications/SamFlow.app"
LAUNCHD="$HOME/Library/LaunchAgents/com.samflow.server.plist"

say()  { printf '\033[1m==>\033[0m %s\n' "$1"; }
warn() { printf '\033[33m!\033[0m %s\n' "$1"; }

# ---------------------------------------------------------------- dependencies
say "1/6  Dependencies"
command -v brew >/dev/null || { warn "Homebrew ontbreekt. Installeer via https://brew.sh en draai opnieuw."; exit 1; }
for pkg in whisper-cpp ffmpeg; do
  if ! brew list "$pkg" >/dev/null 2>&1; then
    say "    brew install $pkg"
    brew install "$pkg"
  else
    echo "    $pkg ✓"
  fi
done
if ! command -v uv >/dev/null; then
  say "    uv installeren (beheerde Python, blijft heel bij brew-upgrades)"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi
echo "    uv ✓"

# --------------------------------------------------------------------- model
say "2/6  Whisper-model (large-v3-turbo q5_0, ~547 MB)"
mkdir -p "$DIR/models"
if [ -f "$MODEL" ]; then
  echo "    al aanwezig ✓"
else
  curl -L --progress-bar -o "$MODEL.part" "$MODEL_URL" && mv "$MODEL.part" "$MODEL"
fi

# --------------------------------------------------------------------- python
say "3/6  Python-omgeving (uv, Python 3.12)"
cd "$DIR"
uv python install 3.12 >/dev/null 2>&1 || true
[ -d .venv ] || uv venv --python 3.12
uv pip install -q -r requirements.txt
echo "    venv + dependencies ✓"

# ---------------------------------------------------- warm-model launchd service
say "4/6  Warm-model service (whisper-server via launchd)"
WHISPER_SERVER="$(command -v whisper-server)"
LANGUAGE="$(grep -m1 '^LANGUAGE' samflow.py | sed -E 's/^LANGUAGE[^"]*"([a-z]+)".*/\1/')"; LANGUAGE="${LANGUAGE:-en}"
mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"
sed -e "s|@@WHISPER_SERVER@@|$WHISPER_SERVER|g" \
    -e "s|@@SAMFLOW_DIR@@|$DIR|g" \
    -e "s|@@HOME@@|$HOME_DIR|g" \
    -e "s|@@LANGUAGE@@|$LANGUAGE|g" \
    "$DIR/macos/com.samflow.server.plist" > "$LAUNCHD"
launchctl bootout "gui/$UID/com.samflow.server" 2>/dev/null || true
launchctl bootstrap "gui/$UID" "$LAUNCHD"
echo "    server geladen (taal: $LANGUAGE) ✓"

# --------------------------------------------------------------- app bundle
say "5/6  SamFlow.app (auto-start + stabiele permissierechten)"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS"
cp "$DIR/macos/Info.plist" "$APP/Contents/Info.plist"
sed "s|@@SAMFLOW_DIR@@|$DIR|g" "$DIR/macos/SamFlow.launcher" > "$APP/Contents/MacOS/SamFlow"
chmod +x "$APP/Contents/MacOS/SamFlow"
codesign --force --deep --sign - "$APP"
echo "    gebouwd + ad-hoc ondertekend ✓"

# ------------------------------------------------------------------ login item
if ! osascript -e 'tell application "System Events" to get the name of every login item' 2>/dev/null | grep -q SamFlow; then
  osascript -e "tell application \"System Events\" to make login item at end with properties {path:\"$APP\", hidden:true}" >/dev/null
  echo "    als login item toegevoegd ✓"
else
  echo "    login item bestaat al ✓"
fi

# ------------------------------------------------------------- manual steps
say "6/6  Handmatige stappen (GUI — kan het script niet voor je doen)"
cat <<EOF

  a) Rechten toekennen aan SamFlow (mic, invoercontrole, toegankelijkheid):
        open -n -a "$APP" --args --grant
     Klik 'Sta toe' op de mic-dialoog; zet SamFlow AAN in
     Systeeminstellingen > Privacy > Toegankelijkheid en Invoercontrole.

  b) Fn-toets vrijmaken:
        Systeeminstellingen > Toetsenbord > "Druk op de fn-toets om" > "Niets doen"

  c) Taal & jargon: standaard staat de taal op '$LANGUAGE'. Pas 'LANGUAGE' in
     samflow.py aan en vul VOCAB in cleanup.py met jouw eigen woorden.

  Daarna starten:
        open -a "$APP"
        "$DIR/.venv/bin/python" samflow.py --check   # alles groen?

EOF
say "Klaar. Houd Fn ingedrukt, praat, laat los."
