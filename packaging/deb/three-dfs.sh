#!/bin/sh
set -e
export PYTHONPATH="/usr/lib/python3/dist-packages${PYTHONPATH:+:$PYTHONPATH}"
exec /usr/bin/python3 -m three_dfs "$@"
