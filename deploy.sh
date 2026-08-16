#!/usr/bin/env bash
#
# deploy.sh — Actualiza payments-service sin reinstalar.
#
# Uso:
#   bash deploy.sh          # git pull + deps + restart
#   bash deploy.sh <rama>   # además hace checkout de la rama
#
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$PROJECT_DIR/.venv"

BRANCH="${1:-}"

if [[ ! -d "$VENV_DIR" ]]; then
    echo "No existe $VENV_DIR — ejecuta primero: sudo bash install.sh" >&2
    exit 1
fi

cd "$PROJECT_DIR"

echo "==> [1/3] Actualizando código"
if [[ -n "$BRANCH" ]]; then
    git checkout "$BRANCH"
fi
git pull

echo "==> [2/3] Actualizando dependencias"
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"
pip install -q --upgrade pip
pip install -q -r requirements.txt
deactivate

echo "==> [3/3] Reiniciando servidor"
bash "$PROJECT_DIR/serve.sh" restart

echo "Deploy completado."
