#!/usr/bin/env bash
#
# serve.sh — Arranca/para gunicorn (uvicorn workers) para payments-service.
#
# Uso:
#   sudo ./serve.sh start     # lanza gunicorn como daemon
#   sudo ./serve.sh stop      # detiene el proceso
#   sudo ./serve.sh restart   # stop + start
#   ./serve.sh debug          # primer plano con logs en terminal (Ctrl+C para salir)
#   ./serve.sh status         # muestra estado y pid
#   ./serve.sh logs           # tail -f del log de errores
#
#   sudo ./serve.sh open-port    # solo abrir el puerto en ufw (para pruebas)
#   sudo ./serve.sh close-port   # solo cerrar el puerto en ufw
#   ./serve.sh firewall          # muestra estado de ufw
#
# Variables de entorno (opcionales):
#   BIND     dirección:puerto — por defecto 127.0.0.1:8010
#   WORKERS  número de workers — por defecto 2
#
# Nota: por defecto el bind es 127.0.0.1 porque nginx hace el proxy de
# api.pagos.fierro.dev hacia este puerto — NO hace falta abrir 8010 en el firewall.
# 'open-port' queda disponible solo para pruebas directas sin nginx.
#

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_BIN="$PROJECT_DIR/.venv/bin"
GUNICORN="$VENV_BIN/gunicorn"
PID_FILE="$PROJECT_DIR/gunicorn.pid"
LOG_DIR="$PROJECT_DIR/logs"
ACCESS_LOG="$LOG_DIR/access.log"
ERROR_LOG="$LOG_DIR/error.log"

BIND="${BIND:-127.0.0.1:8010}"
WORKERS="${WORKERS:-2}"
APP="main:app"                              # módulo FastAPI
WORKER_CLASS="uvicorn.workers.UvicornWorker"
PORT="${BIND##*:}"

mkdir -p "$LOG_DIR"

is_running() {
    [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null
}

start() {
    if is_running; then
        echo "Ya está corriendo (pid $(cat "$PID_FILE"))."
        exit 0
    fi
    if [[ ! -x "$GUNICORN" ]]; then
        echo "No se encuentra $GUNICORN — ¿ejecutaste install.sh?" >&2
        exit 1
    fi

    cd "$PROJECT_DIR"
    "$GUNICORN" \
        --worker-class "$WORKER_CLASS" \
        --bind        "$BIND" \
        --workers     "$WORKERS" \
        --pid         "$PID_FILE" \
        --access-logfile "$ACCESS_LOG" \
        --error-logfile  "$ERROR_LOG" \
        --daemon \
        "$APP"

    sleep 1
    if is_running; then
        echo "gunicorn arrancado (pid $(cat "$PID_FILE")) en $BIND"
    else
        echo "Falló el arranque. Revisa $ERROR_LOG" >&2
        exit 1
    fi
}

stop() {
    if ! is_running; then
        echo "No está corriendo."
        rm -f "$PID_FILE"
        exit 0
    fi
    PID=$(cat "$PID_FILE")
    kill "$PID"
    for _ in $(seq 1 10); do
        kill -0 "$PID" 2>/dev/null || break
        sleep 0.5
    done
    if kill -0 "$PID" 2>/dev/null; then
        echo "No respondió a SIGTERM, enviando SIGKILL..."
        kill -9 "$PID"
    fi
    rm -f "$PID_FILE"
    echo "gunicorn detenido."
}

status() {
    if is_running; then
        PID=$(cat "$PID_FILE")
        echo "gunicorn corriendo (pid $PID, bind $BIND)"
        ps -fp "$PID" || true
    else
        echo "gunicorn detenido."
    fi
}

logs() {
    tail -f "$ERROR_LOG" "$ACCESS_LOG"
}

debug() {
    if is_running; then
        echo "Hay una instancia corriendo (pid $(cat "$PID_FILE")). Detenla primero con 'stop'."
        exit 1
    fi
    if [[ ! -x "$GUNICORN" ]]; then
        echo "No se encuentra $GUNICORN — ¿ejecutaste install.sh?" >&2
        exit 1
    fi

    echo "Arrancando en modo DEBUG (primer plano)..."
    echo "Presiona Ctrl+C para detener."
    echo "------------------------------------------------------------"

    cd "$PROJECT_DIR"
    "$GUNICORN" \
        --worker-class "$WORKER_CLASS" \
        --bind        "$BIND" \
        --workers     1 \
        --access-logfile - \
        --error-logfile  - \
        "$APP"
}

require_root() {
    if [[ $EUID -ne 0 ]]; then
        echo "Este subcomando necesita sudo (modifica reglas de firewall)." >&2
        exit 1
    fi
}

ensure_ufw() {
    if ! command -v ufw >/dev/null 2>&1; then
        echo "ufw no está instalado. Instalando..."
        apt update && apt install -y ufw
    fi
}

open_port() {
    require_root
    ensure_ufw
    if ! ufw status | grep -q "Status: active"; then
        echo "ufw está inactivo — habilitando con SSH (22/tcp) permitido."
        ufw allow 22/tcp
        ufw --force enable
    fi
    ufw allow "${PORT}/tcp"
    echo "Puerto ${PORT}/tcp abierto en ufw."
    ufw status verbose
}

close_port() {
    require_root
    ensure_ufw
    ufw delete allow "${PORT}/tcp" || true
    echo "Puerto ${PORT}/tcp cerrado en ufw."
    ufw status verbose
}

firewall() {
    ensure_ufw
    ufw status verbose
}

case "${1:-}" in
    start)        start ;;
    stop)         stop ;;
    restart)      stop; start ;;
    debug)        debug ;;
    status)       status ;;
    logs)         logs ;;
    open-port)    open_port ;;
    close-port)   close_port ;;
    firewall)     firewall ;;
    *)
        echo "Uso: $0 {start|stop|restart|debug|status|logs|open-port|close-port|firewall}" >&2
        exit 1
        ;;
esac
