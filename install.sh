#!/usr/bin/env bash
#
# install.sh — Instalación automatizada de payments-service en servidor Linux.
#
# Detecta la versión de Python y, si es < 3.11, la instala desde el PPA deadsnakes
# (Ubuntu 20.04/22.04) o la compila desde fuente. Luego crea un venv, instala
# dependencias y deja todo listo para correr con gunicorn + uvicorn workers.
#
# Uso:
#   cd /var/www/sfs/payments-service
#   sudo bash install.sh
#
# Requisitos:
#   - Ubuntu / Debian con apt
#   - Permisos sudo
#   - Conexión a internet
#   - El repo clonado en /var/www/sfs/payments-service
#

set -euo pipefail

EXPECTED_DIR="/var/www/sfs/payments-service"

# ── Helpers ──────────────────────────────────────────────────────────────────
log()  { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
ok()   { printf '\033[1;32m  ok:\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m  warn:\033[0m %s\n' "$*"; }
err()  { printf '\033[1;31m  error:\033[0m %s\n' "$*" >&2; }

# ── Verificaciones previas ────────────────────────────────────────────────────
if [[ $EUID -ne 0 ]]; then
    err "Este script necesita permisos root. Ejecuta: sudo bash install.sh"
    exit 1
fi

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"
log "Directorio del proyecto: $PROJECT_DIR"

if [[ "$PROJECT_DIR" != "$EXPECTED_DIR" ]]; then
    warn "El proyecto NO está en la ubicación estándar ($EXPECTED_DIR)."
    warn "Ubicación actual: $PROJECT_DIR"
    warn "Recomendado: mover el repo a $EXPECTED_DIR antes de configurar nginx."
fi

if [[ ! -f "requirements.txt" ]]; then
    err "No se encontró requirements.txt en $PROJECT_DIR."
    exit 1
fi

if [[ ! -f "main.py" ]]; then
    err "No se encontró main.py en $PROJECT_DIR. ¿Estás en la carpeta correcta?"
    exit 1
fi

# ── Detectar versión de Python ────────────────────────────────────────────────
log "Detectando versión de Python disponible"

PYTHON_BIN=""
if command -v python3.12 >/dev/null 2>&1; then
    PYTHON_BIN="python3.12"
elif command -v python3.11 >/dev/null 2>&1; then
    PYTHON_BIN="python3.11"
elif command -v python3 >/dev/null 2>&1; then
    PY_VER=$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')
    PY_MINOR=$(echo "$PY_VER" | cut -d. -f2)
    if [[ "$PY_MINOR" -ge 11 ]]; then
        PYTHON_BIN="python3"
    else
        warn "python3 del sistema es $PY_VER (< 3.11). Se instalará Python 3.11."
    fi
fi

# ── Instalar Python 3.11 si no hay versión válida ────────────────────────────
if [[ -z "$PYTHON_BIN" ]]; then
    log "Intentando instalar Python 3.11 vía PPA deadsnakes"

    apt update
    apt install -y software-properties-common curl
    add-apt-repository -y ppa:deadsnakes/ppa || true
    apt update

    if apt install -y python3.11 python3.11-venv python3.11-dev 2>/dev/null; then
        PYTHON_BIN="python3.11"
        ok "Python 3.11 instalado desde deadsnakes"
    else
        warn "deadsnakes no disponible para esta distro. Compilando Python 3.11.9 desde fuente…"

        apt install -y build-essential zlib1g-dev libncurses5-dev libgdbm-dev \
            libnss3-dev libssl-dev libreadline-dev libffi-dev libsqlite3-dev \
            libbz2-dev liblzma-dev tk-dev uuid-dev wget

        PY_VERSION="3.11.9"
        PY_SRC="/tmp/Python-${PY_VERSION}"
        if [[ ! -x "/usr/local/bin/python3.11" ]]; then
            cd /tmp
            rm -rf "$PY_SRC" "Python-${PY_VERSION}.tgz"
            wget -q "https://www.python.org/ftp/python/${PY_VERSION}/Python-${PY_VERSION}.tgz"
            tar xzf "Python-${PY_VERSION}.tgz"
            cd "$PY_SRC"
            ./configure --enable-optimizations --prefix=/usr/local
            make -j"$(nproc)"
            make altinstall
            cd "$PROJECT_DIR"
        else
            ok "/usr/local/bin/python3.11 ya existe — saltando compilación"
        fi

        PYTHON_BIN="/usr/local/bin/python3.11"
    fi
fi

ok "Usando Python: $($PYTHON_BIN --version)  ($(command -v "$PYTHON_BIN"))"

# ── Paquetes del sistema ──────────────────────────────────────────────────────
# Nota: NO se instala servidor web aquí — usa el que ya tenga el servidor
# (Apache: deploy/apache.conf.example · nginx: deploy/nginx.conf.example).
log "Instalando paquetes del sistema (build tools, git)"
apt update
apt install -y build-essential pkg-config git

# Asegurar venv y dev para la versión elegida
if [[ "$PYTHON_BIN" == "python3.11" ]]; then
    apt install -y python3.11-venv python3.11-dev || true
elif [[ "$PYTHON_BIN" == "python3.12" ]]; then
    apt install -y python3.12-venv python3.12-dev || true
fi

# ── Crear / reusar venv ───────────────────────────────────────────────────────
VENV_DIR="$PROJECT_DIR/.venv"

if [[ -d "$VENV_DIR" ]]; then
    EXISTING_PY=$("$VENV_DIR/bin/python" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || echo "0.0")
    NEEDED_PY=$("$PYTHON_BIN" -c 'import sys; print("%d.%d" % sys.version_info[:2])')
    if [[ "$EXISTING_PY" != "$NEEDED_PY" ]]; then
        warn "venv existente usa Python $EXISTING_PY, recreando con $NEEDED_PY"
        rm -rf "$VENV_DIR"
    else
        ok "venv existente con Python $EXISTING_PY"
    fi
fi

if [[ ! -d "$VENV_DIR" ]]; then
    log "Creando entorno virtual en $VENV_DIR"
    "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

# ── Instalar dependencias ─────────────────────────────────────────────────────
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

log "Actualizando pip / setuptools / wheel"
python3 -m pip install --upgrade pip wheel setuptools

log "Instalando requirements.txt"
python3 -m pip install -r requirements.txt

log "Instalando gunicorn"
python3 -m pip install gunicorn

deactivate

# ── Directorio de logs ────────────────────────────────────────────────────────
LOG_DIR="$PROJECT_DIR/logs"
mkdir -p "$LOG_DIR"
ok "Directorio de logs: $LOG_DIR"

# ── Archivo .env ──────────────────────────────────────────────────────────────
if [[ ! -f "$PROJECT_DIR/.env" ]]; then
    if [[ -f "$PROJECT_DIR/.env.example" ]]; then
        cp "$PROJECT_DIR/.env.example" "$PROJECT_DIR/.env"
        warn ".env creado desde .env.example — edítalo antes de arrancar:"
        warn "  nano $PROJECT_DIR/.env"
        warn "  (SECRET_KEY — genérala con: openssl rand -hex 32)"
    else
        warn "No existe .env ni .env.example en $PROJECT_DIR."
    fi
else
    ok ".env presente"
fi

# ── Permisos ──────────────────────────────────────────────────────────────────
chown -R www-data:www-data "$PROJECT_DIR" 2>/dev/null || \
    warn "No se pudieron cambiar permisos a www-data (no crítico si corres como otro usuario)"

# ── Resumen ───────────────────────────────────────────────────────────────────
log "Instalación finalizada"
cat <<EOF

  Para arrancar el servidor en producción:
    cd $PROJECT_DIR
    sudo bash serve.sh start

  Para arrancarlo en modo debug (primer plano):
    bash serve.sh debug

  Para actualizar sin reinstalar:
    bash deploy.sh

  Próximos pasos recomendados:
    1. Editar .env: SECRET_KEY (genérala con: openssl rand -hex 32).
    2. Configurar el servidor web:
         Apache: ver deploy/apache.conf.example  (certbot --apache -d api.pagos.fierro.dev)
         nginx:  ver deploy/nginx.conf.example   (certbot --nginx  -d api.pagos.fierro.dev)
    3. Entrar al panel:   https://api.pagos.fierro.dev/admin (admin / 1234 — cámbiala al entrar)
       y configurar las cuentas Wompi + apps.
    4. Registrar la URL de eventos en Wompi (sandbox y producción):
       https://api.pagos.fierro.dev/wompi/webhook

EOF
