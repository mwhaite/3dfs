#!/usr/bin/env bash
# 3dfs: local development environment bootstrap

set -Eeuo pipefail

PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
VENV_DIR="${PROJECT_ROOT}/.venv"
PYTHON=""
MODE="hatch"   # hatch | pip
RECREATE=false
AUTO_ACTIVATE="auto"  # auto | yes | no
RUN_APP="yes"   # yes | no
RUN_CMD=""

usage() {
  cat <<'USAGE'
Usage: ./setup.sh [options]

Options:
  -p, --python PATH    Python interpreter to use (e.g. /usr/bin/python3.11)
  -m, --mode MODE      Setup mode: 'hatch' (default) or 'pip'
  -r, --recreate       Recreate the virtual environment (.venv)
  -h, --help           Show this help and exit
  --activate           After setup, drop into an activated shell
  --no-activate        Do not activate the virtualenv (default in non‑TTY)
  --no-run             Do not run the application after setup
  --run CMD            Run a custom command after setup (default: three-dfs)

What this does
  - Creates .venv using Python 3.11+
  - Installs Hatch (default mode) and initializes the Hatch env
    or installs dev deps directly via pip (pip mode)

After setup
  - By default the application is launched.
  - To skip launching, pass --no-run. To run a different command, use --run.
  - If you ran:  source setup.sh       → your shell stays activated after the app exits
  - If you ran:  ./setup.sh --activate → opens an activated shell (runs app first)
  - Otherwise:   source .venv/bin/activate
  Then you can run:
    hatch run lint && hatch run test   # hatch mode
    or
    ruff check src tests && pytest -q  # pip mode
USAGE
}

log() { printf "\033[1;34m[setup]\033[0m %s\n" "$*"; }
warn() { printf "\033[1;33m[warn ]\033[0m %s\n" "$*"; }
err() { printf "\033[1;31m[error]\033[0m %s\n" "$*"; exit 1; }

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      -p|--python) PYTHON="$2"; shift 2;;
      -m|--mode) MODE="$2"; shift 2;;
      -r|--recreate) RECREATE=true; shift;;
      --activate) AUTO_ACTIVATE="yes"; shift;;
      --no-activate) AUTO_ACTIVATE="no"; shift;;
      --no-run) RUN_APP="no"; shift;;
      --run) RUN_CMD="$2"; RUN_APP="yes"; shift 2;;
      -h|--help) usage; exit 0;;
      *) err "Unknown option: $1";;
    esac
  done
}

pick_python() {
  if [[ -n "${PYTHON}" ]]; then
    command -v "${PYTHON}" >/dev/null 2>&1 || err "Python not found: ${PYTHON}"
    return
  fi
  for cand in python3.11 python3 python; do
    if command -v "$cand" >/dev/null 2>&1; then
      PYTHON="$cand"; break
    fi
  done
  [[ -n "${PYTHON}" ]] || err "No Python interpreter found. Install Python 3.11+."
}

check_version() {
  local v
  v="$(${PYTHON} -c 'import sys; print("%d.%d"%sys.version_info[:2])')"
  local major=${v%%.*}
  local minor=${v#*.}
  if (( major < 3 || (major == 3 && minor < 11) )); then
    warn "Detected Python ${v}. Python 3.11+ is recommended."
  fi
}

create_venv() {
  if [[ -d "${VENV_DIR}" && "${RECREATE}" == true ]]; then
    log "Removing existing venv at ${VENV_DIR}"
    rm -rf -- "${VENV_DIR}"
  fi
  if [[ ! -d "${VENV_DIR}" ]]; then
    log "Creating virtual environment at ${VENV_DIR} using ${PYTHON}"
    "${PYTHON}" -m venv "${VENV_DIR}"
  else
    log "Using existing virtual environment at ${VENV_DIR}"
  fi
  # shellcheck source=/dev/null
  source "${VENV_DIR}/bin/activate"
  python -m pip install --upgrade pip wheel >/dev/null
}

setup_hatch() {
  log "Installing Hatch and initializing the project environment"
  python -m pip install --upgrade hatch >/dev/null
  hatch --version || err "Hatch installation failed"
  # Create Hatch-managed env (contains project + dev deps specified for hatch)
  hatch env create

  # Also install into the local .venv so 'python -m three_dfs' and 'pytest' work
  # directly after 'source .venv/bin/activate', matching the Quick Start docs.
  log "Installing project and dev tools into .venv for direct usage"
  python -m pip install -e . >/dev/null
  python -m pip install -U pytest pytest-cov ruff black >/dev/null
  log "Hatch env ready (use 'hatch run ...'). Local .venv also primed for direct python/pytest."
}

setup_pip() {
  log "Installing project in editable mode with dev tools"
  python -m pip install -e . >/dev/null
  python -m pip install -U pytest pytest-cov ruff black >/dev/null
  log "Pip environment ready. Use: 'ruff check src tests' and 'pytest'"
}

main() {
  cd "${PROJECT_ROOT}"
  parse_args "$@"
  pick_python
  check_version
  create_venv
  case "${MODE}" in
    hatch) setup_hatch ;;
    pip) setup_pip ;;
    *) err "Unknown mode: ${MODE} (use 'hatch' or 'pip')" ;;
  esac

  # If the script was sourced, the current shell is already activated
  # from create_venv(). If it was executed, optionally open a new
  # interactive shell with the venv activated.

  # Detect if sourced (bash/zsh)
  local SOURCED=0
  if [ -n "${BASH_SOURCE:-}" ] && [ "${BASH_SOURCE[0]}" != "$0" ]; then
    SOURCED=1
  elif [ -n "${ZSH_EVAL_CONTEXT:-}" ] && [[ "${ZSH_EVAL_CONTEXT}" == *":file"* ]]; then
    SOURCED=1
  fi

  # Decide what to run by default
  local DEFAULT_RUN
  if [[ -n "${RUN_CMD}" ]]; then
    DEFAULT_RUN="${RUN_CMD}"
  else
    DEFAULT_RUN="${VENV_DIR}/bin/three-dfs"
  fi

  run_after_setup() {
    if [[ "${RUN_APP}" == "yes" ]]; then
      if [[ -x "${VENV_DIR}/bin/three-dfs" && -z "${RUN_CMD}" ]]; then
        log "Launching application: three-dfs"
        "${VENV_DIR}/bin/three-dfs"
      else
        log "Running: ${DEFAULT_RUN}"
        bash -lc "${DEFAULT_RUN}"
      fi
    fi
  }

  if (( SOURCED )); then
    log "Environment ready. Virtualenv is active in this shell."
    run_after_setup
    return 0
  fi

  # Not sourced; decide whether to open an activated subshell
  if [[ "${AUTO_ACTIVATE}" == "no" ]]; then
    run_after_setup
    log "Done. Activate with: source .venv/bin/activate"
    exit 0
  fi

  if [[ ! -t 1 && "${AUTO_ACTIVATE}" != "yes" ]]; then
    # Non-interactive context, avoid opening a shell
    run_after_setup
    log "Done. Activate with: source .venv/bin/activate"
    exit 0
  fi

  # Open an interactive subshell with the venv activated
  local SHELL_CMD
  SHELL_CMD="${SHELL:-/bin/bash}"
  # Build the interactive command: run the app (if enabled) then keep shell open
  local SHELL_SCRIPT
  if [[ "${RUN_APP}" == "yes" ]]; then
    if [[ -x "${VENV_DIR}/bin/three-dfs" && -z "${RUN_CMD}" ]]; then
      SHELL_SCRIPT="source '${VENV_DIR}/bin/activate'; ${VENV_DIR}/bin/three-dfs; echo '[setup] .venv activated. Type exit to leave.'; exec '${SHELL_CMD}' -i"
    else
      SHELL_SCRIPT="source '${VENV_DIR}/bin/activate'; ${DEFAULT_RUN}; echo '[setup] .venv activated. Type exit to leave.'; exec '${SHELL_CMD}' -i"
    fi
  else
    SHELL_SCRIPT="source '${VENV_DIR}/bin/activate'; echo '[setup] .venv activated. Type exit to leave.'; exec '${SHELL_CMD}' -i"
  fi
  log "Spawning an interactive shell with .venv activated (exit to return)"
  exec "${SHELL_CMD}" -i -c "${SHELL_SCRIPT}"
}

main "$@"
