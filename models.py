from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


class AdminUser(Base):
    __tablename__ = "admin_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    # False mientras siga la contraseña por defecto (1234) — el panel muestra un aviso
    password_changed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="", nullable=False)


class WompiAccount(Base):
    """Una cuenta Wompi con sus llaves de sandbox y producción."""

    __tablename__ = "wompi_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    sandbox_pub_key: Mapped[str] = mapped_column(Text, default="", nullable=False)
    sandbox_prv_key: Mapped[str] = mapped_column(Text, default="", nullable=False)
    sandbox_integrity_key: Mapped[str] = mapped_column(Text, default="", nullable=False)
    sandbox_events_key: Mapped[str] = mapped_column(Text, default="", nullable=False)
    prod_pub_key: Mapped[str] = mapped_column(Text, default="", nullable=False)
    prod_prv_key: Mapped[str] = mapped_column(Text, default="", nullable=False)
    prod_integrity_key: Mapped[str] = mapped_column(Text, default="", nullable=False)
    prod_events_key: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    apps: Mapped[list["App"]] = relationship(back_populates="wompi_account")


class App(Base):
    """Una app consumidora: API key, webhook y regla de enrutamiento por prefijo."""

    __tablename__ = "apps"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    api_key: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    webhook_url: Mapped[str] = mapped_column(Text, default="", nullable=False)
    # Prefijo de las referencias de esta app (p. ej. "WB-"); vacío = catch-all
    reference_prefix: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    wompi_account_id: Mapped[int | None] = mapped_column(
        ForeignKey("wompi_accounts.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    wompi_account: Mapped[WompiAccount | None] = relationship(back_populates="apps")


class Transaction(Base):
    """Transacciones vistas por el dispatcher, construidas desde los webhooks."""

    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    wompi_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    reference: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    app: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    amount_in_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    currency: Mapped[str] = mapped_column(String(8), default="COP", nullable=False)
    payment_method: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    customer_email: Mapped[str] = mapped_column(String(256), default="", nullable=False)
    environment: Mapped[str] = mapped_column(String(16), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )


class AuditLog(Base):
    """Auditoría: quién hizo qué, cuándo y desde qué IP."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    actor: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    actor_type: Mapped[str] = mapped_column(String(16), default="", nullable=False)  # admin | app | anon
    action: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    target: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    detail: Mapped[str] = mapped_column(Text, default="", nullable=False)
    ip: Mapped[str] = mapped_column(String(64), default="", nullable=False)


class EventLog(Base):
    __tablename__ = "event_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    reference: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    target: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ok: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    detail: Mapped[str] = mapped_column(Text, default="", nullable=False)
