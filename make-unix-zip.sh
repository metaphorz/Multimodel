#!/bin/zsh
# Build a clean, macOS/Linux-ready zip of the Pro Team Multimodel viewer.
#
# Includes ONLY what the app needs at runtime (run ./start, then open the printed URL):
#   start, stop, index.html (redirect), web/ (app + vendored Leaflet),
#   outputs/web/ (the JSON data), README-Unix.txt
# Excludes build-time bulk (venv/, pipeline/, data/, docs/, xlsx, etc.).
#
# Usage:  ./make-unix-zip.sh
# Output: outputs/FormS6-viewer-unix.zip

set -e
cd "$(dirname "$0")"

STAGE="$(mktemp -d)/FormS6-viewer"
OUT="outputs/FormS6-viewer-unix.zip"

mkdir -p "$STAGE"
cp index.html "$STAGE"/
cp -R web "$STAGE"/web
mkdir -p "$STAGE"/outputs
cp -R outputs/web "$STAGE"/outputs/web

# Standalone start/stop. The repo's own ./start uses ./venv/bin/python and writes its
# pidfile under tests/auto/ — neither ships in this zip, so the bundle gets its own
# scripts that rely only on the system python3.
cat > "$STAGE"/start <<'TXT'
#!/usr/bin/env bash
# Serve the Pro Team Multimodel viewer on http://localhost:8012/web/
# (fetching the JSON data requires http://, not file://.)
cd "$(dirname "$0")"
PORT=8012
if ! command -v python3 > /dev/null 2>&1; then
  echo "python3 not found — install Python 3 and re-run ./start"; exit 1
fi
echo "Serving Pro Team Multimodel at http://localhost:${PORT}/web/index.html"
echo "Stop with ./stop"
python3 -m http.server ${PORT} > server.log 2>&1 &
echo $! > .server.pid
sleep 1
echo "PID $(cat .server.pid)"
if command -v open > /dev/null 2>&1; then
  open "http://localhost:${PORT}/web/index.html"
elif command -v xdg-open > /dev/null 2>&1; then
  xdg-open "http://localhost:${PORT}/web/index.html"
else
  echo "Open http://localhost:${PORT}/web/index.html in your browser."
fi
TXT

cat > "$STAGE"/stop <<'TXT'
#!/usr/bin/env bash
# Stop the Pro Team Multimodel viewer server.
cd "$(dirname "$0")"
if [[ -f .server.pid ]]; then
  PID=$(cat .server.pid)
  if kill "$PID" 2>/dev/null; then echo "Stopped server PID $PID"; else echo "No live process $PID"; fi
  rm -f .server.pid
else
  echo "No .server.pid found"
fi
TXT
chmod +x "$STAGE"/start "$STAGE"/stop

cat > "$STAGE"/README-Unix.txt <<'TXT'
Pro Team Multimodel — Running on macOS / Linux
==============================================

1. Open a terminal in this folder.
2. Run:   ./start
3. Open the URL it prints (http://localhost:8012/web/index.html).
4. When finished:   ./stop

Requires only Python 3 (preinstalled on macOS and most Linux distributions).
No packages to install — the viewer is static HTML/CSS/JavaScript and
Leaflet is vendored in web/vendor/.
TXT

# strip macOS cruft
find "$STAGE" -name '.DS_Store' -delete

rm -f "$OUT"
( cd "$(dirname "$STAGE")" && zip -rq "$OLDPWD/$OUT" "FormS6-viewer" -x '*/.DS_Store' )
rm -rf "$(dirname "$STAGE")"

echo "Wrote $OUT"
echo "  size: $(du -h "$OUT" | cut -f1)"
echo "  Unzip, then run ./start"
