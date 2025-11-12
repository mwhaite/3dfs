#!/bin/sh
set -e
export PYTHONPATH="/app/lib/python3/site-packages${PYTHONPATH:+:$PYTHONPATH}"
exec /app/bin/python3 -m three_dfs "$@"
