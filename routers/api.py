"""API interna para las apps: toda la lógica Wompi centralizada aquí.

Autenticación server-to-server con el header `X-Api-Key` (una key por app,
generada y visible en el panel /admin). Las apps no guardan ninguna llave
Wompi: solo PAYMENTS_SERVICE_URL + PAYMENTS_SERVICE_API_KEY.
"""
import hmac

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from core import wompi
from core.audit import audit
from database import get_db
from models import App, WompiAccount

router = APIRouter(prefix="/api", tags=["internal-api"])


def require_api_key(request: Request, db: Session = Depends(get_db)) -> App:
    """Valida X-Api-Key y devuelve la app autenticada."""
    provided = request.headers.get("X-Api-Key", "")
    if provided:
        for app_row in db.query(App).all():
            if app_row.api_key and hmac.compare_digest(provided, app_row.api_key):
                return app_row
    audit(db, request, actor="", actor_type="anon", action="api.auth_failed",
          detail=f"{request.method} {request.url.path}")
    db.commit()
    raise HTTPException(status_code=401, detail="API key inválida")


def _account_for(app: App) -> WompiAccount:
    if app.wompi_account is None:
        raise HTTPException(
            status_code=503,
            detail=f"La app {app.name!r} no tiene cuenta Wompi asignada (panel /admin)",
        )
    return app.wompi_account


def _wompi_http_error(exc: wompi.WompiError) -> HTTPException:
    # 400 modo inválido / 503 llaves sin configurar se propagan tal cual;
    # cualquier error del lado de Wompi se reporta como 502.
    if exc.status_code in (400, 503):
        return HTTPException(status_code=exc.status_code, detail=exc.detail)
    return HTTPException(status_code=502, detail={"wompi": exc.detail})


class CheckoutUrlIn(BaseModel):
    mode: str
    reference: str
    amount_cents: int = Field(gt=0)
    redirect_url: str
    currency: str = "COP"


class PaymentLinkIn(BaseModel):
    mode: str
    name: str
    amount_cents: int = Field(gt=0)
    reference: str
    redirect_url: str


class NequiTransactionIn(BaseModel):
    mode: str
    phone_number: str
    amount_cents: int = Field(gt=0)
    reference: str
    customer_email: str


@router.post("/checkout-urls")
def checkout_urls(
    body: CheckoutUrlIn,
    request: Request,
    app: App = Depends(require_api_key),
    db: Session = Depends(get_db),
):
    detail = f"mode={body.mode}, amount_cents={body.amount_cents}"
    try:
        url = wompi.build_checkout_url(
            _account_for(app),
            body.mode,
            reference=body.reference,
            amount_cents=body.amount_cents,
            redirect_url=body.redirect_url,
            currency=body.currency,
        )
    except wompi.WompiError as exc:
        audit(db, request, actor=app.name, actor_type="app", action="api.checkout_url.failed",
              target=body.reference, detail=f"{detail}, error: {exc.detail}")
        db.commit()
        raise _wompi_http_error(exc) from exc
    audit(db, request, actor=app.name, actor_type="app", action="api.checkout_url",
          target=body.reference, detail=detail)
    db.commit()
    return {"url": url}


@router.post("/payment-links")
def payment_links(
    body: PaymentLinkIn,
    request: Request,
    app: App = Depends(require_api_key),
    db: Session = Depends(get_db),
):
    detail = f"mode={body.mode}, amount_cents={body.amount_cents}"
    try:
        url, link_id = wompi.create_payment_link(
            _account_for(app),
            body.mode,
            name=body.name,
            amount_cents=body.amount_cents,
            reference=body.reference,
            redirect_url=body.redirect_url,
        )
    except wompi.WompiError as exc:
        audit(db, request, actor=app.name, actor_type="app", action="api.payment_link.failed",
              target=body.reference, detail=f"{detail}, error: {exc.detail}")
        db.commit()
        raise _wompi_http_error(exc) from exc
    audit(db, request, actor=app.name, actor_type="app", action="api.payment_link",
          target=body.reference, detail=f"{detail}, link_id={link_id}")
    db.commit()
    return {"url": url, "link_id": link_id}


@router.post("/nequi-transactions")
def nequi_transactions(
    body: NequiTransactionIn,
    request: Request,
    app: App = Depends(require_api_key),
    db: Session = Depends(get_db),
):
    detail = f"mode={body.mode}, amount_cents={body.amount_cents}"
    try:
        transaction_id = wompi.create_nequi_transaction(
            _account_for(app),
            body.mode,
            phone_number=body.phone_number,
            amount_cents=body.amount_cents,
            reference=body.reference,
            customer_email=body.customer_email,
        )
    except wompi.WompiError as exc:
        audit(db, request, actor=app.name, actor_type="app", action="api.nequi_transaction.failed",
              target=body.reference, detail=f"{detail}, error: {exc.detail}")
        db.commit()
        raise _wompi_http_error(exc) from exc
    audit(db, request, actor=app.name, actor_type="app", action="api.nequi_transaction",
          target=body.reference, detail=f"{detail}, transaction_id={transaction_id}")
    db.commit()
    return {"transaction_id": transaction_id}


@router.get("/transactions/{transaction_id}")
def transaction_detail(
    transaction_id: str,
    mode: str,
    app: App = Depends(require_api_key),
    db: Session = Depends(get_db),
):
    try:
        return wompi.get_transaction(_account_for(app), mode, transaction_id)
    except wompi.WompiError as exc:
        raise _wompi_http_error(exc) from exc


@router.get("/keys-status")
def keys_status(app: App = Depends(require_api_key), db: Session = Depends(get_db)):
    """Qué ambientes tienen llaves configuradas en la cuenta de ESTA app.

    Incluye la pub key (pública por naturaleza) para que los admins de las apps
    puedan mostrar qué llave está activa.
    """
    account = _account_for(app)
    result = {}
    for mode in wompi.MODES:
        keys = wompi.keys_for(account, mode)
        result[mode] = {
            "configured": bool(keys["pub_key"] and keys["prv_key"] and keys["integrity_key"]),
            "pub_key": keys["pub_key"],
        }
    return result
