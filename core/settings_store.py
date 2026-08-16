"""Configuración key-value (tabla settings).

Las llaves Wompi, API keys y URLs de webhook viven ahora en las tablas
wompi_accounts y apps (ver models.py); esta tabla queda para configuración
suelta futura. Los keys del esquema viejo (wompi_*, api_key_*, target_*)
solo se leen durante la migración del seed en main.py.
"""
from sqlalchemy.orm import Session

from models import Setting

DEFAULTS: dict[str, str] = {}


def get_setting(db: Session, key: str, default: str = "") -> str:
    row = db.get(Setting, key)
    if row is not None:
        return row.value
    return DEFAULTS.get(key, default)


def set_setting(db: Session, key: str, value: str) -> None:
    row = db.get(Setting, key)
    if row is None:
        db.add(Setting(key=key, value=value))
    else:
        row.value = value
