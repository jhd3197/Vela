#!/usr/bin/env bash
# Unified Vela development launcher.
#
# Root wrappers:
#   ./dev.sh                 Linux/macOS/WSL/Git Bash entry point
#   .\dev.ps1                Windows entry point (native, no WSL needed)
#
# Usage:
#   ./dev.sh                 Start backend + frontend (Vite, with HMR)
#   ./dev.sh backend         Start the Vela server only (serves web/dist)
#   ./dev.sh frontend        Start Vite only, proxying to the backend port
#   ./dev.sh build           Build the dashboard into web/dist and stop
#   ./dev.sh check           Run the hub checks (npm --prefix web run check)
#   ./dev.sh setup           First-run setup: venv, dependencies, dashboard build
#
# Options:
#   --backend-port,  -BackendPort   Pin/prefer a backend port (default 7700)
#   --frontend-port, -FrontendPort  Pin/prefer a frontend port (default 5173)
#   --no-auto-port,  -NoAutoPort    Fail when a preferred port is busy

set -euo pipefail

SCRIPTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPTS_DIR/.." && pwd)"
WEB_DIR="$PROJECT_ROOT/web"
VENV_DIR="${VELA_VENV:-$PROJECT_ROOT/.venv}"
DEV_DATA_DIR="${VELA_DATA_DIR:-$PROJECT_ROOT/.local/dev-data}"

MODE="start"
MODE_SET=0
BACKEND_PORT="${VELA_BACKEND_PORT:-7700}"
FRONTEND_PORT="${VELA_FRONTEND_PORT:-5173}"
AUTO_PORT=1
KILL_PORTS="${VELA_KILL_PORTS:-1}"

CYAN='\033[0;36m'
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
DIM='\033[2m'
NC='\033[0m'

usage() {
    cat <<'EOF'
Vela development launcher

Usage:
  ./dev.sh [mode] [options]
  .\dev.ps1 [mode] [options]

Modes:
  start       Start backend + frontend (default)
  backend     Start the Vela server only (serves the built dashboard)
  frontend    Start Vite only, proxying API calls to the backend port.
              Handy for CSS/UI-only work against an already running server.
  build       Build the dashboard into web/dist and stop. For CSS-only
              changes when you would rather not run Vite at all.
  check       Run the hub checks (lint, format, tests, build)
  setup       First-run setup: venv, Python deps, npm ci, dashboard build

Options:
  --backend-port PORT,  -BackendPort PORT     Pin/prefer a backend port
  --frontend-port PORT, -FrontendPort PORT    Pin/prefer a frontend port
  --no-auto-port,       -NoAutoPort           Fail if a preferred port is busy
  -h, --help                                  Show this help

Environment:
  VELA_BACKEND_PORT      Optional pinned/preferred backend port (default 7700)
  VELA_FRONTEND_PORT     Optional pinned/preferred frontend port (default 5173)
  VELA_DATA_DIR          Dev server data directory (default .local/dev-data,
                         disposable and separate from an installed Vela)
  VELA_VENV              Python virtualenv directory (default .venv)
  VELA_KILL_PORTS=0      Disable startup cleanup of the dev ports

Defaults:
  Backend http://localhost:7700, frontend http://localhost:5173. If either
  port is busy, the launcher stops the existing listener, or moves to the
  next free nearby port when it cannot.
EOF
}

header() {
    echo
    echo -e "${CYAN}=== $1 ===${NC}"
    echo
}

die() {
    echo -e "${RED}Error:${NC} $*" >&2
    exit 1
}

is_windows() {
    case "$(uname -s 2>/dev/null || true)" in
        MINGW*|MSYS*|CYGWIN*) return 0 ;;
    esac
    return 1
}

python_command_works() {
    local candidate="$1"
    [ -n "$candidate" ] || return 1
    "$candidate" -c 'import sys' >/dev/null 2>&1
}

find_python() {
    local name path
    for name in python3 python; do
        path="$(command -v "$name" 2>/dev/null || true)"
        [ -n "$path" ] || continue
        # WSL can inherit Windows PATH entries; those shims cannot run as
        # Linux interpreters.
        case "$path" in
            /mnt/*) continue ;;
        esac
        if python_command_works "$path"; then
            echo "$path"
            return 0
        fi
    done
    return 1
}

PYTHON_BIN="$(find_python || true)"

require_python() {
    if [ -z "$PYTHON_BIN" ]; then
        die "python3 or python is required for local development."
    fi
}

# The interpreter inside the repo venv, whichever layout the platform created.
venv_python() {
    local candidate
    for candidate in "$VENV_DIR/Scripts/python.exe" "$VENV_DIR/bin/python" "$VENV_DIR/bin/python3"; do
        if python_command_works "$candidate"; then
            echo "$candidate"
            return 0
        fi
    done
    return 1
}

ensure_venv() {
    local py
    if py="$(venv_python)"; then
        VELA_PY="$py"
        return 0
    fi

    require_python
    echo -e "${YELLOW}Virtualenv missing or not runnable; creating $VENV_DIR${NC}"
    if ! "$PYTHON_BIN" -m venv "$VENV_DIR"; then
        die "Could not create the venv. On Debian/Ubuntu install venv support with: sudo apt install python3-venv"
    fi
    if ! py="$(venv_python)"; then
        die "The venv was created but its Python is not runnable."
    fi
    VELA_PY="$py"
}

requirements_hash() {
    "$VELA_PY" - "$PROJECT_ROOT/requirements.txt" <<'PY' 2>/dev/null
import hashlib, sys
try:
    with open(sys.argv[1], 'rb') as fh:
        print(hashlib.sha256(fh.read()).hexdigest())
except OSError:
    pass
PY
}

# Install when the venv cannot import the core packages (missing/broken) or
# when requirements.txt changed since the last successful install -- the hash
# catches the case the probe cannot see.
ensure_backend_packages() {
    local marker="$VENV_DIR/.requirements-sha256"
    local want have

    if "$VELA_PY" - <<'PY' >/dev/null 2>&1
import fastapi
import uvicorn
PY
    then
        want="$(requirements_hash)"
        have=""
        [ -f "$marker" ] && have="$(cat "$marker" 2>/dev/null)"
        if [ -z "$want" ] || [ "$want" = "$have" ]; then
            return 0
        fi
        echo -e "${YELLOW}requirements.txt changed since the last install; syncing...${NC}"
    fi

    echo -e "${YELLOW}Installing Python dependencies into $VENV_DIR...${NC}"
    "$VELA_PY" -m pip install --upgrade pip
    "$VELA_PY" -m pip install -r "$PROJECT_ROOT/requirements.txt"

    want="$(requirements_hash)"
    [ -n "$want" ] && printf '%s\n' "$want" > "$marker"
}

ensure_web_packages() {
    if [ -d "$WEB_DIR/node_modules" ]; then
        return 0
    fi
    echo -e "${YELLOW}web/node_modules is missing; running npm ci...${NC}"
    npm --prefix "$WEB_DIR" ci
}

validate_port() {
    local name="$1"
    local port="$2"
    if ! [[ "$port" =~ ^[0-9]+$ ]] || [ "$port" -lt 1 ] || [ "$port" -gt 65535 ]; then
        die "$name port must be between 1 and 65535. Got $port."
    fi
}

parse_args() {
    while [ "$#" -gt 0 ]; do
        case "$1" in
            start|backend|frontend|build|check|setup)
                if [ "$MODE_SET" -eq 1 ]; then
                    die "Only one mode can be provided."
                fi
                MODE="$1"
                MODE_SET=1
                shift
                ;;
            --backend-port|-BackendPort)
                [ "$#" -ge 2 ] || die "$1 requires a port."
                BACKEND_PORT="$2"
                shift 2
                ;;
            --backend-port=*|-BackendPort=*)
                BACKEND_PORT="${1#*=}"
                shift
                ;;
            --frontend-port|-FrontendPort)
                [ "$#" -ge 2 ] || die "$1 requires a port."
                FRONTEND_PORT="$2"
                shift 2
                ;;
            --frontend-port=*|-FrontendPort=*)
                FRONTEND_PORT="${1#*=}"
                shift
                ;;
            --no-auto-port|-NoAutoPort)
                AUTO_PORT=0
                shift
                ;;
            -h|--help)
                usage
                exit 0
                ;;
            *)
                die "Unknown argument: $1"
                ;;
        esac
    done

    validate_port "Backend" "$BACKEND_PORT"
    validate_port "Frontend" "$FRONTEND_PORT"
}

port_available() {
    local port="$1"
    require_python
    "$PYTHON_BIN" - "$port" <<'PY'
import socket
import sys

port = int(sys.argv[1])
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
try:
    sock.bind(("127.0.0.1", port))
except OSError:
    sys.exit(1)
finally:
    sock.close()
PY
}

listener_pids_for_port() {
    local port="$1"

    if command -v ss >/dev/null 2>&1; then
        ss -ltnp 2>/dev/null |
            awk -v suffix=":$port" '$4 ~ suffix "$" { print }' |
            grep -oE 'pid=[0-9]+' |
            cut -d= -f2 |
            sort -u
    elif command -v lsof >/dev/null 2>&1; then
        lsof -nP -iTCP:"$port" -sTCP:LISTEN -t 2>/dev/null | sort -u
    elif is_windows && command -v netstat >/dev/null 2>&1; then
        # Git Bash fallback: netstat -ano lists the owning PID as the last column.
        netstat -ano -p tcp 2>/dev/null |
            awk -v suffix=":$port" '$2 ~ suffix "$" && $4 == "LISTENING" { print $5 }' |
            sort -u
    fi
}

stop_listeners_on_port() {
    local port="$1"
    local label="$2"

    [ "$KILL_PORTS" = "1" ] || return 0

    local pids=()
    local pid
    while IFS= read -r pid; do
        [ -n "$pid" ] || continue
        [ "$pid" != "$$" ] || continue
        pids+=("$pid")
    done < <(listener_pids_for_port "$port")

    [ "${#pids[@]}" -gt 0 ] || return 0

    echo -e "${YELLOW}Stopping existing $label listener on port $port: ${pids[*]}${NC}"
    if is_windows && ! command -v ss >/dev/null 2>&1 && ! command -v lsof >/dev/null 2>&1; then
        for pid in "${pids[@]}"; do
            taskkill //PID "$pid" //F >/dev/null 2>&1 || true
        done
    else
        kill "${pids[@]}" 2>/dev/null || true
        sleep 1
        local still_running=()
        for pid in "${pids[@]}"; do
            if kill -0 "$pid" 2>/dev/null; then
                still_running+=("$pid")
            fi
        done
        if [ "${#still_running[@]}" -gt 0 ]; then
            kill -9 "${still_running[@]}" 2>/dev/null || true
        fi
    fi
}

RESOLVED_PORT=""
RESOLVED_CHANGED=0

resolve_dev_port() {
    local name="$1"
    local preferred="$2"
    shift 2
    local reserved_ports=("$@")

    local reserved
    for reserved in "${reserved_ports[@]}"; do
        if [ "$preferred" = "$reserved" ]; then
            die "$name port $preferred is already reserved by another service in this launch."
        fi
    done

    if port_available "$preferred"; then
        RESOLVED_PORT="$preferred"
        RESOLVED_CHANGED=0
        return
    fi

    if [ "$AUTO_PORT" -eq 0 ]; then
        die "$name port $preferred is already in use. Stop the other process or omit --no-auto-port."
    fi

    local upper_bound=$((preferred + 200))
    if [ "$upper_bound" -gt 65535 ]; then
        upper_bound=65535
    fi

    local candidate
    for ((candidate = preferred + 1; candidate <= upper_bound; candidate++)); do
        if port_available "$candidate"; then
            RESOLVED_PORT="$candidate"
            RESOLVED_CHANGED=1
            return
        fi
    done

    die "Could not find a free $name port near $preferred."
}

BACKEND_ACTUAL_PORT=""
BACKEND_PORT_CHANGED=0
FRONTEND_ACTUAL_PORT=""
FRONTEND_PORT_CHANGED=0
BACKEND_URL=""
FRONTEND_URL=""

resolve_ports() {
    local backend_may_already_be_running="${1:-0}"

    if [ "$backend_may_already_be_running" -eq 1 ]; then
        BACKEND_ACTUAL_PORT="$BACKEND_PORT"
        BACKEND_PORT_CHANGED=0
    else
        resolve_dev_port "Backend" "$BACKEND_PORT"
        BACKEND_ACTUAL_PORT="$RESOLVED_PORT"
        BACKEND_PORT_CHANGED="$RESOLVED_CHANGED"
    fi

    resolve_dev_port "Frontend" "$FRONTEND_PORT" "$BACKEND_ACTUAL_PORT"
    FRONTEND_ACTUAL_PORT="$RESOLVED_PORT"
    FRONTEND_PORT_CHANGED="$RESOLVED_CHANGED"

    BACKEND_URL="http://localhost:$BACKEND_ACTUAL_PORT"
    FRONTEND_URL="http://localhost:$FRONTEND_ACTUAL_PORT"
}

print_dev_summary() {
    echo
    echo -e "${CYAN}Vela Dev Server${NC}"
    echo "  Dashboard (Vite, HMR): $FRONTEND_URL"
    echo "  Backend:               $BACKEND_URL"
    echo "  Health:                $BACKEND_URL/api/health"
    echo "  Data dir:              $DEV_DATA_DIR"
    if [ "$BACKEND_PORT_CHANGED" -eq 1 ]; then
        echo -e "  ${YELLOW}Note:${NC} backend port $BACKEND_PORT was busy; using $BACKEND_ACTUAL_PORT."
    fi
    if [ "$FRONTEND_PORT_CHANGED" -eq 1 ]; then
        echo -e "  ${YELLOW}Note:${NC} frontend port $FRONTEND_PORT was busy; using $FRONTEND_ACTUAL_PORT."
    fi
    echo
}

build_dashboard() {
    ensure_web_packages
    echo -e "${YELLOW}Building the dashboard into web/dist...${NC}"
    npm --prefix "$WEB_DIR" run build
}

start_backend_process() {
    ensure_venv
    ensure_backend_packages
    mkdir -p "$DEV_DATA_DIR"
    cd "$PROJECT_ROOT"
    VELA_DATA_DIR="$DEV_DATA_DIR" "$VELA_PY" -m vela --no-open-browser --host 127.0.0.1 --port "$BACKEND_ACTUAL_PORT"
}

start_frontend_process() {
    ensure_web_packages
    cd "$WEB_DIR"
    VELA_BACKEND_URL="$BACKEND_URL" npm run dev -- --host 127.0.0.1 --port "$FRONTEND_ACTUAL_PORT" --strictPort
}

start_backend() {
    stop_listeners_on_port "$BACKEND_PORT" "backend"
    resolve_dev_port "Backend" "$BACKEND_PORT"
    BACKEND_ACTUAL_PORT="$RESOLVED_PORT"
    BACKEND_PORT_CHANGED="$RESOLVED_CHANGED"
    BACKEND_URL="http://localhost:$BACKEND_ACTUAL_PORT"
    header "Starting Vela backend ($BACKEND_URL)"
    echo "  Data dir: $DEV_DATA_DIR"
    if [ ! -d "$WEB_DIR/dist" ]; then
        echo -e "  ${YELLOW}Note:${NC} web/dist is missing; run ./dev.sh build or start the frontend for the dashboard."
    fi
    if [ "$BACKEND_PORT_CHANGED" -eq 1 ]; then
        echo -e "  ${YELLOW}Note:${NC} backend port $BACKEND_PORT was busy; using $BACKEND_ACTUAL_PORT."
    fi
    echo
    start_backend_process
}

start_frontend() {
    stop_listeners_on_port "$FRONTEND_PORT" "frontend"
    resolve_ports 1
    header "Starting Vite ($FRONTEND_URL)"
    echo "  API target: $BACKEND_URL"
    echo "  (expects a Vela backend there; ./dev.sh backend starts one)"
    if [ "$FRONTEND_PORT_CHANGED" -eq 1 ]; then
        echo -e "  ${YELLOW}Note:${NC} frontend port $FRONTEND_PORT was busy; using $FRONTEND_ACTUAL_PORT."
    fi
    echo
    start_frontend_process
}

cleanup_pids() {
    local pid
    for pid in "$@"; do
        if [ -n "${pid:-}" ]; then
            kill "$pid" 2>/dev/null || true
        fi
    done
    for pid in "$@"; do
        if [ -n "${pid:-}" ]; then
            wait "$pid" 2>/dev/null || true
        fi
    done
}

wait_for_first_exit() {
    if help wait 2>/dev/null | grep -q -- '-n'; then
        set +e
        wait -n "$@"
        local status=$?
        set -e
        return "$status"
    fi

    local pid running
    while true; do
        running="$(jobs -pr)"
        for pid in "$@"; do
            if ! printf '%s\n' "$running" | grep -qx "$pid"; then
                set +e
                wait "$pid" 2>/dev/null
                local status=$?
                set -e
                return "$status"
            fi
        done
        sleep 1
    done
}

start_both() {
    stop_listeners_on_port "$BACKEND_PORT" "backend"
    stop_listeners_on_port "$FRONTEND_PORT" "frontend"
    resolve_ports 0
    print_dev_summary

    start_backend_process &
    local backend_pid=$!

    sleep 2

    start_frontend_process &
    local frontend_pid=$!

    trap 'echo; echo -e "${YELLOW}Stopping...${NC}"; cleanup_pids "$backend_pid" "$frontend_pid"; echo "Stopped."; exit 0' INT TERM

    echo -e "${DIM}Press Ctrl+C to stop...${NC}"
    set +e
    wait_for_first_exit "$backend_pid" "$frontend_pid"
    local status=$?
    set -e

    echo
    echo -e "${YELLOW}One dev process stopped; shutting down the rest.${NC}"
    cleanup_pids "$backend_pid" "$frontend_pid"
    return "$status"
}

run_build() {
    header "Building the dashboard"
    build_dashboard
    echo
    echo -e "${GREEN}Done.${NC} web/dist is fresh; restart the backend (or reload the page) to see it."
}

run_check() {
    header "Vela checks"
    ensure_web_packages
    VELA_TEST_PYTHON="${VELA_TEST_PYTHON:-$(venv_python || true)}" npm --prefix "$WEB_DIR" run check
}

run_setup() {
    header "Vela first-run setup"
    ensure_venv
    ensure_backend_packages
    ensure_web_packages
    mkdir -p "$DEV_DATA_DIR"
    build_dashboard
    echo
    echo -e "${GREEN}Setup complete.${NC} Start the dev environment with ./dev.sh"
}

parse_args "$@"

case "$MODE" in
    backend) start_backend ;;
    frontend) start_frontend ;;
    build) run_build ;;
    check) run_check ;;
    setup) run_setup ;;
    start) start_both ;;
    *) die "Unknown mode: $MODE" ;;
esac
