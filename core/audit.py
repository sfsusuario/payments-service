"""Registro de auditoría: quién hizo qué, cuándo y desde qué IP.

Nunca se registran valores de llaves/secretos — solo QUÉ campos cambiaron.
El commit lo hace el endpoint que llama (misma transacción que el cambio).
"""
from fastapi import Request
from sqlalchemy.orm import Session

from models import AuditLog


def client_ip(request: Request) -> str:
    """IP real del cliente; detrás de nginx llega en X-Forwarded-For."""
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()[:64]
    return (request.client.host if request.client else "")[:64]


def audit(
    db: Session,
    request: Request,
    *,
    actor: str,
    actor_type: str,
    action: str,
    target: str = "",
    detail: str = "",
) -> None:
    db.add(
        AuditLog(
            actor=actor[:64],
            actor_type=actor_type,
            action=action,
            target=target[:128],
            detail=detail,
            ip=client_ip(request),
        )
    )
