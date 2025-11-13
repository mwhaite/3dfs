#!/bin/sh
set -e

# Set up the Python path for the system installation
export PYTHONPATH="/usr/lib/python3/dist-packages${PYTHONPATH:+:$PYTHONPATH}"

# Enable debug output if APPIMAGE_DEBUG is set
if [ -n "$APPIMAGE_DEBUG" ]; then
  set -x
  echo "[three-dfs] PYTHONPATH=$PYTHONPATH" >&2
fi

# Verify Python is available
if ! command -v /usr/bin/python3 >/dev/null 2>&1; then
  echo "[three-dfs] Error: Python 3 not found at /usr/bin/python3" >&2
  exit 1
fi

# Execute the application module
exec /usr/bin/python3 -m three_dfs "$@"
