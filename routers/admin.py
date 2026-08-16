"""Panel de administración: login, cuentas Wompi, apps y contraseña."""
import secrets

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from core.audit import audit
from core.security import current_admin, hash_password, login_redirect, verify_password
from database import get_db
from models import AdminUser, App, AuditLog, EventLog, Transaction, WompiAccount

router = APIRouter(prefix="/admin")

templates = Jinja2Templates(directory="templates")

MIN_PASSWORD_LEN = 6

ACCOUNT_KEY_FIELDS = [
    "sandbox_pub_key",
    "sandbox_prv_key",
    "sandbox_integrity_key",
    "sandbox_events_key",
    "prod_pub_key",
    "prod_prv_key",
    "prod_integrity_key",
    "prod_events_key",
]


def _format_cop(amount_in_cents: int | None) -> str:
    if amount_in_cents is None:
        return "—"
    pesos = amount_in_cents // 100
    return "$ " + f"{pesos:,}".replace(",", ".")


templates.env.filters["cop"] = _format_cop


def _redirect_error(message: str) -> RedirectResponse:
    return RedirectResponse(f"/admin?error={message}", status_code=303)


@router.get("/login")
def login_form(request: Request):
    if current_admin(request):
        return RedirectResponse("/admin", status_code=303)
    return templates.TemplateResponse(request, "login.html", {"error": None})


@router.post("/login")
def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    user = db.query(AdminUser).filter(AdminUser.username == username.strip()).first()
    if user is None or not verify_password(password, user.password_hash):
        audit(db, request, actor=username.strip(), actor_type="anon", action="login.failed")
        db.commit()
        return templates.TemplateResponse(
            request, "login.html", {"error": "Usuario o contraseña incorrectos."}, status_code=401
        )
    request.session["admin_user"] = user.username
    audit(db, request, actor=user.username, actor_type="admin", action="login")
    db.commit()
    return RedirectResponse("/admin", status_code=303)


@router.post("/logout")
def logout(request: Request, db: Session = Depends(get_db)):
    username = current_admin(request)
    if username:
        audit(db, request, actor=username, actor_type="admin", action="logout")
        db.commit()
    request.session.clear()
    return login_redirect()


@router.get("")
def dashboard(request: Request, db: Session = Depends(get_db)):
    username = current_admin(request)
    if not username:
        return login_redirect()
    user = db.query(AdminUser).filter(AdminUser.username == username).first()
    if user is None:
        request.session.clear()
        return login_redirect()

    accounts = db.query(WompiAccount).order_by(WompiAccount.id).all()
    apps = db.query(App).order_by(App.id).all()
    events = db.query(EventLog).order_by(EventLog.id.desc()).limit(50).all()
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "username": username,
            "default_password": not user.password_changed,
            "accounts": accounts,
            "apps": apps,
            "account_key_fields": ACCOUNT_KEY_FIELDS,
            "events": events,
            "saved": request.query_params.get("saved"),
            "error": request.query_params.get("error"),
        },
    )


# ── Cuentas Wompi ─────────────────────────────────────────────────────────────

@router.post("/accounts")
def create_account(request: Request, name: str = Form(...), db: Session = Depends(get_db)):
    username = current_admin(request)
    if not username:
        return login_redirect()
    name = name.strip()
    if not name:
        return _redirect_error("El nombre de la cuenta no puede estar vacío")
    if db.query(WompiAccount).filter(WompiAccount.name == name).first():
        return _redirect_error(f"Ya existe una cuenta llamada {name}")
    db.add(WompiAccount(name=name))
    audit(db, request, actor=username, actor_type="admin", action="account.create", target=name)
    db.commit()
    return RedirectResponse("/admin?saved=1", status_code=303)


@router.post("/accounts/{account_id}")
async def update_account(request: Request, account_id: int, db: Session = Depends(get_db)):
    username = current_admin(request)
    if not username:
        return login_redirect()
    account = db.get(WompiAccount, account_id)
    if account is None:
        return _redirect_error("Cuenta no encontrada")
    form = await request.form()
    name = str(form.get("name", account.name)).strip()
    if not name:
        return _redirect_error("El nombre de la cuenta no puede estar vacío")
    other = db.query(WompiAccount).filter(WompiAccount.name == name, WompiAccount.id != account_id).first()
    if other:
        return _redirect_error(f"Ya existe una cuenta llamada {name}")

    # Auditoría: solo QUÉ cambió, nunca los valores de las llaves
    changes = []
    if name != account.name:
        changes.append(f"nombre: {account.name} → {name}")
    account.name = name
    changed_keys = []
    for field in ACCOUNT_KEY_FIELDS:
        if field in form:
            new_value = str(form[field]).strip()
            if new_value != getattr(account, field):
                changed_keys.append(field)
            setattr(account, field, new_value)
    if changed_keys:
        changes.append("llaves modificadas: " + ", ".join(changed_keys))
    if changes:
        audit(db, request, actor=username, actor_type="admin", action="account.update",
              target=name, detail="; ".join(changes))
    db.commit()
    return RedirectResponse("/admin?saved=1", status_code=303)


@router.post("/accounts/{account_id}/delete")
def delete_account(request: Request, account_id: int, db: Session = Depends(get_db)):
    username = current_admin(request)
    if not username:
        return login_redirect()
    account = db.get(WompiAccount, account_id)
    if account is None:
        return _redirect_error("Cuenta no encontrada")
    if account.apps:
        names = ", ".join(a.name for a in account.apps)
        return _redirect_error(f"La cuenta {account.name} está en uso por: {names}")
    audit(db, request, actor=username, actor_type="admin", action="account.delete", target=account.name)
    db.delete(account)
    db.commit()
    return RedirectResponse("/admin?saved=1", status_code=303)


# ── Apps ──────────────────────────────────────────────────────────────────────

@router.post("/apps")
def create_app(request: Request, name: str = Form(...), db: Session = Depends(get_db)):
    username = current_admin(request)
    if not username:
        return login_redirect()
    name = name.strip()
    if not name:
        return _redirect_error("El nombre de la app no puede estar vacío")
    if db.query(App).filter(App.name == name).first():
        return _redirect_error(f"Ya existe una app llamada {name}")
    db.add(App(name=name, api_key=secrets.token_hex(32)))
    audit(db, request, actor=username, actor_type="admin", action="app.create", target=name)
    db.commit()
    return RedirectResponse("/admin?saved=1", status_code=303)


@router.post("/apps/{app_id}")
async def update_app(request: Request, app_id: int, db: Session = Depends(get_db)):
    username = current_admin(request)
    if not username:
        return login_redirect()
    app_row = db.get(App, app_id)
    if app_row is None:
        return _redirect_error("App no encontrada")
    form = await request.form()
    name = str(form.get("name", app_row.name)).strip()
    if not name:
        return _redirect_error("El nombre de la app no puede estar vacío")
    if db.query(App).filter(App.name == name, App.id != app_id).first():
        return _redirect_error(f"Ya existe una app llamada {name}")

    changes = []
    if name != app_row.name:
        changes.append(f"nombre: {app_row.name} → {name}")
    app_row.name = name
    if "webhook_url" in form:
        new_url = str(form["webhook_url"]).strip()
        if new_url != app_row.webhook_url:
            changes.append(f"webhook_url: {app_row.webhook_url or '—'} → {new_url or '—'}")
        app_row.webhook_url = new_url
    if "reference_prefix" in form:
        new_prefix = str(form["reference_prefix"]).strip()
        if new_prefix != app_row.reference_prefix:
            changes.append(f"prefijo: {app_row.reference_prefix or '—'} → {new_prefix or '—'}")
        app_row.reference_prefix = new_prefix
    if "wompi_account_id" in form:
        raw = str(form["wompi_account_id"]).strip()
        if not raw:
            new_account = None
        else:
            new_account = db.get(WompiAccount, int(raw))
            if new_account is None:
                return _redirect_error("Cuenta Wompi no encontrada")
        new_account_id = new_account.id if new_account else None
        if new_account_id != app_row.wompi_account_id:
            old_name = app_row.wompi_account.name if app_row.wompi_account else "—"
            changes.append(f"cuenta: {old_name} → {new_account.name if new_account else '—'}")
        app_row.wompi_account_id = new_account_id
    if changes:
        audit(db, request, actor=username, actor_type="admin", action="app.update",
              target=name, detail="; ".join(changes))
    db.commit()
    return RedirectResponse("/admin?saved=1", status_code=303)


@router.post("/apps/{app_id}/delete")
def delete_app(request: Request, app_id: int, db: Session = Depends(get_db)):
    username = current_admin(request)
    if not username:
        return login_redirect()
    app_row = db.get(App, app_id)
    if app_row is None:
        return _redirect_error("App no encontrada")
    audit(db, request, actor=username, actor_type="admin", action="app.delete", target=app_row.name)
    db.delete(app_row)
    db.commit()
    return RedirectResponse("/admin?saved=1", status_code=303)


@router.post("/apps/{app_id}/regenerate-key")
def regenerate_app_key(request: Request, app_id: int, db: Session = Depends(get_db)):
    username = current_admin(request)
    if not username:
        return login_redirect()
    app_row = db.get(App, app_id)
    if app_row is None:
        return _redirect_error("App no encontrada")
    app_row.api_key = secrets.token_hex(32)
    audit(db, request, actor=username, actor_type="admin", action="app.regenerate_key", target=app_row.name)
    db.commit()
    return RedirectResponse("/admin?saved=1", status_code=303)


# ── Transacciones ─────────────────────────────────────────────────────────────

@router.get("/transactions")
def transactions(request: Request, db: Session = Depends(get_db)):
    if not current_admin(request):
        return login_redirect()

    app_filter = request.query_params.get("app") or ""
    status_filter = request.query_params.get("status") or ""

    query = db.query(Transaction)
    if app_filter:
        query = query.filter(Transaction.app == app_filter)
    if status_filter:
        query = query.filter(Transaction.status == status_filter)
    txs = query.order_by(Transaction.updated_at.desc()).limit(200).all()

    # Resumen general (sin filtros): totales por estado y por app
    all_txs = db.query(Transaction).all()
    by_status: dict[str, int] = {}
    by_app: dict[str, dict[str, int | None]] = {}
    for t in all_txs:
        by_status[t.status] = by_status.get(t.status, 0) + 1
        app_stats = by_app.setdefault(t.app, {"count": 0, "approved_cents": 0})
        app_stats["count"] += 1
        if t.status == "APPROVED" and t.amount_in_cents:
            app_stats["approved_cents"] += t.amount_in_cents

    statuses = sorted(by_status)
    apps = sorted(by_app)
    return templates.TemplateResponse(
        request,
        "transactions.html",
        {
            "username": current_admin(request),
            "txs": txs,
            "total": len(all_txs),
            "by_status": by_status,
            "by_app": by_app,
            "statuses": statuses,
            "apps": apps,
            "app_filter": app_filter,
            "status_filter": status_filter,
        },
    )


# ── Auditoría ─────────────────────────────────────────────────────────────────

@router.get("/audit")
def audit_page(request: Request, db: Session = Depends(get_db)):
    if not current_admin(request):
        return login_redirect()

    actor_filter = request.query_params.get("actor_type") or ""
    query = db.query(AuditLog)
    if actor_filter:
        query = query.filter(AuditLog.actor_type == actor_filter)
    entries = query.order_by(AuditLog.id.desc()).limit(200).all()
    return templates.TemplateResponse(
        request,
        "audit.html",
        {
            "username": current_admin(request),
            "entries": entries,
            "total": db.query(AuditLog).count(),
            "actor_filter": actor_filter,
        },
    )


# ── Contraseña ────────────────────────────────────────────────────────────────

@router.post("/password")
def change_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    db: Session = Depends(get_db),
):
    username = current_admin(request)
    if not username:
        return login_redirect()
    user = db.query(AdminUser).filter(AdminUser.username == username).first()
    if user is None or not verify_password(current_password, user.password_hash):
        audit(db, request, actor=username, actor_type="admin", action="password.change_failed",
              detail="contraseña actual incorrecta")
        db.commit()
        return _redirect_error("La contraseña actual no es correcta")
    if len(new_password) < MIN_PASSWORD_LEN:
        return _redirect_error(f"La nueva contraseña debe tener al menos {MIN_PASSWORD_LEN} caracteres")
    if new_password != confirm_password:
        return _redirect_error("Las contraseñas no coinciden")
    user.password_hash = hash_password(new_password)
    user.password_changed = True
    audit(db, request, actor=username, actor_type="admin", action="password.change")
    db.commit()
    return RedirectResponse("/admin?saved=1", status_code=303)
