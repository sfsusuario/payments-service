"""Autenticación del panel: bcrypt + sesión firmada (SessionMiddleware)."""
import bcrypt
from fastapi import Request
from fastapi.responses import RedirectResponse


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except ValueError:
        return False


def current_admin(request: Request) -> str | None:
    return request.session.get("admin_user")


def login_redirect() -> RedirectResponse:
    return RedirectResponse("/admin/login", status_code=303)
