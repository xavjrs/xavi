#!/bin/sh
# XAVI desktop dashboard (Streamlit) for macOS / Linux:  sh start_app.sh   (or ./start_app.sh)
cd "$(dirname "$0")" || exit 1
for py in python3.12 python3.11 python3.13 python3.10 python3 python; do
  if command -v "$py" >/dev/null 2>&1 && "$py" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
    exec "$py" run.py app "$@"
  fi
done
echo "Python 3.10 or newer is needed. Install it from https://www.python.org/downloads/"
exit 1
