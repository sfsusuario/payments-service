"""Recibe los eventos de las cuentas Wompi y los reenvía a la app dueña.

Multi-cuenta y multi-app (tablas wompi_accounts y apps):
  1. La firma del evento se prueba contra las events keys de TODAS las cuentas
     (sandbox y producción); la que verifica identifica la cuenta dueña.
  2. Candidatas = apps de esa cuenta (o todas si no se pudo identificar).
  3. Enrutamiento por prefijo de referencia (coincidencia más larga); sin match
     o sin referencia → las apps catch-all (prefijo vacío) de las candidatas.

El body se reenvía CRUDO con el header `X-Forward-Key: <api_key de la app>`,
que la app valida en su webhook (las apps no tienen llaves Wompi).
"""
import json

import httpx
from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from core.wompi import MODES, keys_for, verify_signature_with_key
from database import get_db
from models import App, EventLog, Transaction, WompiAccount

router = APIRouter()

FORWARD_TIMEOUT = 10.0


def _identify_account(payload: dict | None, accounts: list[WompiAccount]):
    """(hay_llaves_configuradas, cuenta_que_verifica | None)."""
    any_keys = False
    for account in accounts:
        for mode in MODES:
            events_key = keys_for(account, mode)["events_key"]
            if not events_key:
                continue
            any_keys = True
            if payload is not None and verify_signature_with_key(payload, events_key):
                return any_keys, account
    # seguir marcando any_keys aunque la primera cuenta no verifique
    return any_keys, None


def _upsert_transaction(db: Session, payload: dict, app_name: str) -> None:
    """Registra/actualiza la transacción del evento para la vista del panel."""
    tx = (payload.get("data") or {}).get("transaction") or {}
    wompi_id = tx.get("id")
    if not isinstance(wompi_id, str) or not wompi_id:
        return
    row = db.query(Transaction).filter(Transaction.wompi_id == wompi_id).first()
    if row is None:
        row = Transaction(wompi_id=wompi_id)
        db.add(row)
    row.reference = str(tx.get("reference") or row.reference or "")
    row.app = app_name
    row.status = str(tx.get("status") or row.status or "")
    if isinstance(tx.get("amount_in_cents"), int):
        row.amount_in_cents = tx["amount_in_cents"]
    row.currency = str(tx.get("currency") or row.currency or "COP")
    row.payment_method = str(tx.get("payment_method_type") or row.payment_method or "")
    row.customer_email = str(tx.get("customer_email") or row.customer_email or "")
    row.environment = str(payload.get("environment") or row.environment or "")


def _route(reference: str | None, candidates: list[App]) -> list[App]:
    if reference:
        prefixed = [a for a in candidates if a.reference_prefix and reference.startswith(a.reference_prefix)]
        if prefixed:
            best = max(len(a.reference_prefix) for a in prefixed)
            return [a for a in prefixed if len(a.reference_prefix) == best][:1]
    return [a for a in candidates if not a.reference_prefix]


@router.get("/health")
def health():
    return {"status": "ok"}


@router.post("/wompi/webhook")
async def wompi_webhook(request: Request, db: Session = Depends(get_db)):
    raw = await request.body()

    payload = None
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            payload = parsed
    except (ValueError, UnicodeDecodeError):
        pass

    event_type = (payload or {}).get("event") or ""
    reference = None
    if payload is not None:
        tx = (payload.get("data") or {}).get("transaction") or {}
        ref = tx.get("reference")
        if isinstance(ref, str) and ref:
            reference = ref

    accounts = db.query(WompiAccount).all()
    any_keys, account = _identify_account(payload, accounts)

    unverified_note = ""
    if any_keys and account is None:
        db.add(
            EventLog(
                event_type=event_type,
                reference=reference or "",
                target="-",
                status_code=None,
                ok=False,
                detail="firma inválida (ninguna cuenta verifica) — evento rechazado",
            )
        )
        db.commit()
        return Response(status_code=403)
    if not any_keys:
        unverified_note = "sin events keys configuradas — reenviado sin verificar. "

    candidates = list(account.apps) if account is not None else db.query(App).all()
    targets = _route(reference, candidates)

    if not targets:
        db.add(
            EventLog(
                event_type=event_type,
                reference=reference or "",
                target="-",
                status_code=None,
                ok=False,
                detail=unverified_note + "sin app destino para esta referencia",
            )
        )
        db.commit()
        return Response(status_code=200)

    if payload is not None and len(targets) == 1:
        _upsert_transaction(db, payload, targets[0].name)

    all_ok = True
    async with httpx.AsyncClient(timeout=FORWARD_TIMEOUT) as client:
        for app_row in targets:
            if not app_row.webhook_url:
                all_ok = False
                db.add(
                    EventLog(
                        event_type=event_type,
                        reference=reference or "",
                        target=app_row.name,
                        status_code=None,
                        ok=False,
                        detail=unverified_note + "URL de webhook no configurada",
                    )
                )
                continue
            # X-Forward-Key: la app destino valida este header en lugar de la
            # firma Wompi (las apps no tienen events key)
            headers = {"Content-Type": "application/json", "X-Forward-Key": app_row.api_key}
            try:
                resp = await client.post(app_row.webhook_url, content=raw, headers=headers)
                ok = 200 <= resp.status_code < 300
                all_ok = all_ok and ok
                db.add(
                    EventLog(
                        event_type=event_type,
                        reference=reference or "",
                        target=app_row.name,
                        status_code=resp.status_code,
                        ok=ok,
                        detail=unverified_note + (f"HTTP {resp.status_code}" if not ok else ""),
                    )
                )
            except httpx.HTTPError as exc:
                all_ok = False
                db.add(
                    EventLog(
                        event_type=event_type,
                        reference=reference or "",
                        target=app_row.name,
                        status_code=None,
                        ok=False,
                        detail=unverified_note + f"error de conexión: {exc}",
                    )
                )
    db.commit()

    # 502 hace que Wompi reintente el evento más tarde
    return Response(status_code=200 if all_ok else 502)
