"""payments-service — dispatcher de webhooks Wompi + panel de administración.

Una sola cuenta Wompi para varias apps: este servicio es la única URL de
eventos registrada en Wompi y reenvía cada evento al backend dueño de la
transacción (ver routers/webhook.py). El panel (/admin) configura las llaves
y las URLs destino; credenciales por defecto admin / 1234 (cambiarla al entrar).
"""
import secrets
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from starlette.middleware.sessions import SessionMiddleware

from core.config import get_settings
from core.security import hash_password
from core.settings_store import get_setting
from database import Base, SessionLocal, engine
from models import AdminUser, App, WompiAccount
from routers import admin, api, webhook

DEFAULT_ADMIN_USER = "admin"
DEFAULT_ADMIN_PASSWORD = "1234"

DEFAULT_TARGET_BUILDER = "https://api.builder.fierro.dev/payments/wompi/webhook"
DEFAULT_TARGET_SASTRERIA = "https://api.sastreria.fierro.dev/payments/wompi/webhook"


def _seed() -> None:
    """Seed idempotente. Migra la config del esquema viejo (settings) si existe:
    despliegues previos conservan sus llaves Wompi y las API keys de las apps."""
    with SessionLocal() as db:
        if db.query(AdminUser).count() == 0:
            db.add(
                AdminUser(
                    username=DEFAULT_ADMIN_USER,
                    password_hash=hash_password(DEFAULT_ADMIN_PASSWORD),
                    password_changed=False,
                )
            )

        account = db.query(WompiAccount).order_by(WompiAccount.id).first()
        if account is None:
            account = WompiAccount(
                name="principal",
                sandbox_pub_key=get_setting(db, "wompi_sandbox_pub_key"),
                sandbox_prv_key=get_setting(db, "wompi_sandbox_prv_key"),
                sandbox_integrity_key=get_setting(db, "wompi_sandbox_integrity_key"),
                sandbox_events_key=get_setting(db, "wompi_sandbox_events_key"),
                prod_pub_key=get_setting(db, "wompi_prod_pub_key"),
                prod_prv_key=get_setting(db, "wompi_prod_prv_key"),
                prod_integrity_key=get_setting(db, "wompi_prod_integrity_key"),
                prod_events_key=get_setting(db, "wompi_prod_events_key"),
            )
            db.add(account)
            db.flush()

        if db.query(App).count() == 0:
            db.add(
                App(
                    name="website-seller",
                    api_key=get_setting(db, "api_key_builder") or secrets.token_hex(32),
                    webhook_url=get_setting(db, "target_builder_url") or DEFAULT_TARGET_BUILDER,
                    reference_prefix="WB-",
                    wompi_account_id=account.id,
                )
            )
            db.add(
                App(
                    name="sastreria",
                    api_key=get_setting(db, "api_key_sastreria") or secrets.token_hex(32),
                    webhook_url=get_setting(db, "target_sastreria_url") or DEFAULT_TARGET_SASTRERIA,
                    reference_prefix="",  # catch-all
                    wompi_account_id=account.id,
                )
            )
        db.commit()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    Base.metadata.create_all(bind=engine)
    _seed()
    yield


app = FastAPI(title="payments-service", lifespan=lifespan)

app.add_middleware(SessionMiddleware, secret_key=get_settings().SECRET_KEY, https_only=False)

app.include_router(webhook.router)
app.include_router(api.router)
app.include_router(admin.router)


@app.get("/")
def root():
    return RedirectResponse("/admin", status_code=303)
