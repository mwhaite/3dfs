#!/usr/bin/env bash
set -e

# Resolve project root
PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." >/dev/null 2>&1 && pwd)"
APPIMAGETOOL="${PROJECT_ROOT}/appimagetool"
APPIMAGETOOL_URL="https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage"

# Ensure appimagetool exists
if [ ! -f "${APPIMAGETOOL}" ]; then
    echo "Downloading appimagetool..."
    wget -O "${APPIMAGETOOL}" "${APPIMAGETOOL_URL}"
    chmod +x "${APPIMAGETOOL}"
fi

# Ensure it's executable
if [ ! -x "${APPIMAGETOOL}" ]; then
    chmod +x "${APPIMAGETOOL}"
fi

# Determine python interpreter
PYTHON="python3"
if [ -f "${PROJECT_ROOT}/.venv/bin/python" ]; then
    PYTHON="${PROJECT_ROOT}/.venv/bin/python"
fi

echo "Using Python: ${PYTHON}"
echo "Building AppImage..."

# Run the build script
cd "${PROJECT_ROOT}"
"${PYTHON}" scripts/build_appimage.py

echo "Done."
