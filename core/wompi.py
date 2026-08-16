"""Cliente Wompi completo del servicio central de pagos.

Toda la lógica Wompi vive aquí: construir checkouts, crear payment links,
transacciones Nequi, consultar transacciones y verificar firmas de eventos.
Las llaves se leen del settings store (BD, configuradas desde el panel),
no de variables de entorno.
"""
import hashlib
import hmac
import logging
import os
import time
from urllib.parse import urlencode

import httpx

logger = logging.getLogger(__name__)

CHECKOUT_BASE = "https://checkout.wompi.co/p/"

MODES = ("sandbox", "production")


def api_base(mode: str) -> str:
    """Base del API Wompi; sobreescribible por env para tests con un Wompi simulado."""
    if mode == "production":
        return os.environ.get("WOMPI_PROD_API_BASE", "https://api.wompi.co/v1")
    return os.environ.get("WOMPI_SANDBOX_API_BASE", "https://sandbox.wompi.co/v1")


class WompiError(Exception):
    def __init__(self, status_code: int, detail: object) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(str(detail))


def keys_for(account, mode: str) -> dict:
    """Llaves de un WompiAccount para un modo dado."""
    if mode not in MODES:
        raise WompiError(400, f"Modo inválido: {mode!r} (sandbox | production)")
    prefix = "prod" if mode == "production" else "sandbox"
    integrity = getattr(account, f"{prefix}_integrity_key")
    return {
        "pub_key": getattr(account, f"{prefix}_pub_key"),
        "prv_key": getattr(account, f"{prefix}_prv_key"),
        "integrity_key": integrity,
        "events_key": getattr(account, f"{prefix}_events_key") or integrity,
    }


def _require(keys: dict, mode: str, *names: str) -> None:
    missing = [n for n in names if not keys.get(n)]
    if missing:
        raise WompiError(
            503, f"Llaves Wompi de {mode} sin configurar en el panel: {', '.join(missing)}"
        )


def _wompi_detail(r: httpx.Response) -> object:
    try:
        return r.json().get("error", {}).get("messages", r.text)
    except Exception:
        return r.text


def integrity_signature(reference: str, amount_cents: int, currency: str, integrity_key: str) -> str:
    raw = f"{reference}{amount_cents}{currency}{integrity_key}"
    return hashlib.sha256(raw.encode()).hexdigest()


def build_checkout_url(
    account,
    mode: str,
    *,
    reference: str,
    amount_cents: int,
    redirect_url: str,
    currency: str = "COP",
) -> str:
    """URL del Web Checkout de Wompi (redirect), firmada con la integrity key."""
    keys = keys_for(account, mode)
    _require(keys, mode, "pub_key", "integrity_key")
    params = {
        "public-key": keys["pub_key"],
        "currency": currency,
        "amount-in-cents": str(amount_cents),
        "reference": reference,
        "signature:integrity": integrity_signature(
            reference, amount_cents, currency, keys["integrity_key"]
        ),
        "redirect-url": redirect_url,
    }
    return CHECKOUT_BASE + "?" + urlencode(params)


def create_payment_link(
    account,
    mode: str,
    *,
    name: str,
    amount_cents: int,
    reference: str,
    redirect_url: str,
) -> tuple[str, str]:
    """Crea un payment link single-use. Devuelve (permalink, link_id)."""
    keys = keys_for(account, mode)
    _require(keys, mode, "prv_key")
    # Sufijo de timestamp: Wompi rechaza referencias duplicadas en links single-use
    unique_ref = f"{reference}-{int(time.time())}"
    payload = {
        "name": name,
        "description": name,
        "single_use": True,
        "currency": "COP",
        "amount_in_cents": amount_cents,
        "reference": unique_ref,
        "redirect_url": redirect_url,
        "collect_shipping": False,
        "taxes": [],
    }
    logger.info("Wompi create_payment_link ref=%s amount=%s", unique_ref, amount_cents)
    try:
        r = httpx.post(
            f"{api_base(mode)}/payment_links",
            json=payload,
            headers={"Authorization": f"Bearer {keys['prv_key']}"},
            timeout=15,
        )
    except httpx.HTTPError as exc:
        raise WompiError(502, f"Error de conexión con Wompi: {exc}") from exc
    if not r.is_success:
        logger.error("Wompi error %s: %s", r.status_code, r.text)
        raise WompiError(r.status_code, _wompi_detail(r))
    data = r.json().get("data", {})
    link_id = data["id"]
    permalink = data.get("permalink") or f"https://checkout.wompi.co/l/{link_id}"
    return permalink, link_id


def _get_acceptance_token(mode: str, pub_key: str) -> str:
    try:
        r = httpx.get(f"{api_base(mode)}/merchants/{pub_key}", timeout=10)
    except httpx.HTTPError as exc:
        raise WompiError(502, f"Error de conexión con Wompi: {exc}") from exc
    if not r.is_success:
        raise WompiError(r.status_code, _wompi_detail(r))
    return r.json()["data"]["presigned_acceptance"]["acceptance_token"]


def create_nequi_transaction(
    account,
    mode: str,
    *,
    phone_number: str,
    amount_cents: int,
    reference: str,
    customer_email: str,
) -> str:
    """Inicia una transacción Nequi push. Devuelve el ID de transacción Wompi."""
    keys = keys_for(account, mode)
    _require(keys, mode, "pub_key", "prv_key", "integrity_key")
    acceptance_token = _get_acceptance_token(mode, keys["pub_key"])
    payload = {
        "amount_in_cents": amount_cents,
        "currency": "COP",
        "customer_email": customer_email,
        "reference": reference,
        "payment_method": {"type": "NEQUI", "phone_number": phone_number},
        "acceptance_token": acceptance_token,
        "signature": integrity_signature(reference, amount_cents, "COP", keys["integrity_key"]),
    }
    logger.info("Wompi create_nequi_transaction ref=%s amount=%s", reference, amount_cents)
    try:
        r = httpx.post(
            f"{api_base(mode)}/transactions",
            json=payload,
            headers={"Authorization": f"Bearer {keys['prv_key']}"},
            timeout=15,
        )
    except httpx.HTTPError as exc:
        raise WompiError(502, f"Error de conexión con Wompi: {exc}") from exc
    if not r.is_success:
        logger.error("Wompi Nequi error %s: %s", r.status_code, r.text)
        raise WompiError(r.status_code, _wompi_detail(r))
    return str(r.json()["data"]["id"])


def get_transaction(account, mode: str, transaction_id: str) -> dict:
    """Consulta una transacción en Wompi (para polling de respaldo de las apps)."""
    keys_for(account, mode)  # valida el modo
    try:
        r = httpx.get(f"{api_base(mode)}/transactions/{transaction_id}", timeout=10)
    except httpx.HTTPError as exc:
        raise WompiError(502, f"Error de conexión con Wompi: {exc}") from exc
    if not r.is_success:
        raise WompiError(r.status_code, _wompi_detail(r))
    return r.json().get("data", {})


def verify_signature_with_key(payload: dict, events_key: str) -> bool:
    """Recalcula el checksum con signature.properties + timestamp + events_key."""
    signature = payload.get("signature") or {}
    properties = signature.get("properties") or []
    checksum = signature.get("checksum") or ""
    timestamp = payload.get("timestamp")
    if not properties or not checksum or timestamp is None:
        return False

    parts = []
    for prop in properties:
        value = payload.get("data") or {}
        for key in prop.split("."):
            if not isinstance(value, dict):
                return False
            value = value.get(key)
            if value is None:
                return False
        parts.append(str(value))
    raw = "".join(parts) + str(timestamp) + events_key
    expected = hashlib.sha256(raw.encode()).hexdigest()
    return hmac.compare_digest(expected, checksum.lower())
