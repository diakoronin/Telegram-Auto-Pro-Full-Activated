"""SQLAlchemy ORM models."""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON, TypeDecorator

from bot_app.db.base import Base


class StringJSON(TypeDecorator):
    """JSON that works on SQLite (TEXT) and PostgreSQL."""

    impl = Text
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(JSONB())
        return dialect.type_descriptor(Text())

    def process_bind_param(self, value, dialect):
        if value is None:
            return value
        if dialect.name == "postgresql":
            return value
        import json

        return json.dumps(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return value
        if dialect.name == "postgresql":
            return value
        import json

        return json.loads(value)


class AdminRole(str, enum.Enum):
    owner = "owner"
    manager = "manager"
    seller = "seller"


class PurchaseType(str, enum.Enum):
    api = "api"
    manual = "manual"


class PurchaseStatus(str, enum.Enum):
    pending = "pending"
    completed = "completed"
    failed = "failed"
    refunded = "refunded"
    cancelled = "cancelled"


class WalletTxType(str, enum.Enum):
    deposit_pending = "deposit_pending"
    deposit_approved = "deposit_approved"
    deposit_rejected = "deposit_rejected"
    purchase_api = "purchase_api"
    purchase_manual = "purchase_manual"
    refund = "refund"
    manual_adjustment = "manual_adjustment"
    location_change_fee = "location_change_fee"
    location_change_refund = "location_change_refund"


class PaymentRequestStatus(str, enum.Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"
    expired = "expired"
    cancelled = "cancelled"


class TicketStatus(str, enum.Enum):
    open = "open"
    answered = "answered"
    closed = "closed"


class PanelType(str, enum.Enum):
    marzban = "marzban"
    sanaei_3xui = "sanaei_3xui"
    xui = "xui"


class UserServiceStatus(str, enum.Enum):
    active = "active"
    limited = "limited"
    expired = "expired"
    disabled = "disabled"
    refunded = "refunded"
    migrating = "migrating"
    error = "error"


class PanelAccountStatus(str, enum.Enum):
    active = "active"
    disabled = "disabled"
    deleted = "deleted"
    migrated = "migrated"
    failed = "failed"


class ManualLinkStatus(str, enum.Enum):
    unused = "unused"
    used = "used"
    deleted = "deleted"


class ManualDeliveryStatus(str, enum.Enum):
    delivered = "delivered"
    returned = "returned"
    cancelled = "cancelled"


class LocationChangeRequestStatus(str, enum.Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"
    completed = "completed"
    failed = "failed"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False, index=True)
    username: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    first_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    last_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    phone: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    wallet_balance: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    is_blocked: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    card_payment_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    admin_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Admin(Base):
    __tablename__ = "admins"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AppSetting(Base):
    __tablename__ = "app_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Panel(Base):
    __tablename__ = "panels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    base_url: Mapped[str] = mapped_column(String(512), nullable=False)
    web_base_path: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    username: Mapped[str] = mapped_column(String(255), nullable=False)
    password_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    api_token_encrypted: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    verify_ssl: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False, server_default="30")
    inbound_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    marzban_proxies_json: Mapped[Optional[Any]] = mapped_column(StringJSON, nullable=True)
    marzban_inbounds_json: Mapped[Optional[Any]] = mapped_column(StringJSON, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    last_test_status: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    last_test_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    last_test_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Server(Base):
    __tablename__ = "servers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    location_label: Mapped[str] = mapped_column(String(255), nullable=False)
    panel_id: Mapped[int] = mapped_column(ForeignKey("panels.id"), nullable=False)
    panel_type: Mapped[str] = mapped_column(String(32), nullable=False)
    inbound_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    template_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    is_visible_to_users: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    supports_location_change: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Plan(Base):
    __tablename__ = "plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    server_id: Mapped[int] = mapped_column(ForeignKey("servers.id"), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    volume_gb: Mapped[int] = mapped_column(Integer, nullable=False)
    total_quota_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    duration_days: Mapped[int] = mapped_column(Integer, nullable=False)
    price: Mapped[int] = mapped_column(BigInteger, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    is_visible_to_users: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class UserService(Base):
    __tablename__ = "user_services"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    public_service_code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    user_telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    purchase_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("plans.id"), nullable=False)
    current_server_id: Mapped[int] = mapped_column(ForeignKey("servers.id"), nullable=False)
    custom_service_name: Mapped[str] = mapped_column(String(255), nullable=False)
    total_quota_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    used_traffic_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    remaining_traffic_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    expire_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    subscription_token: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    subscription_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    location_change_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    location_change_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    location_change_month_key: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    last_location_change_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    sync_failure_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    last_sync_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (Index("ix_user_services_user_status", "user_id", "status"),)


class PanelAccount(Base):
    __tablename__ = "panel_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_service_id: Mapped[int] = mapped_column(ForeignKey("user_services.id"), nullable=False, index=True)
    panel_id: Mapped[int] = mapped_column(ForeignKey("panels.id"), nullable=False)
    server_id: Mapped[int] = mapped_column(ForeignKey("servers.id"), nullable=False)
    panel_type: Mapped[str] = mapped_column(String(32), nullable=False)
    panel_account_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    username: Mapped[str] = mapped_column(String(255), nullable=False)
    config_links_json: Mapped[Optional[Any]] = mapped_column(StringJSON, nullable=True)
    raw_subscription_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    quota_bytes_assigned: Mapped[int] = mapped_column(BigInteger, nullable=False)
    usage_baseline_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    upload_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    download_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    total_used_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    final_used_bytes: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    last_synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    activated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    disabled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true", index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        Index("ix_panel_accounts_user_service_active", "user_service_id", "is_active"),
    )


class TrafficUsageSnapshot(Base):
    __tablename__ = "traffic_usage_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_service_id: Mapped[int] = mapped_column(ForeignKey("user_services.id"), nullable=False)
    panel_account_id: Mapped[int] = mapped_column(ForeignKey("panel_accounts.id"), nullable=False)
    upload_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    download_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    total_used_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    calculated_service_used_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    remaining_traffic_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_panel: Mapped[str] = mapped_column(String(255), nullable=False)
    request_id: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LocationChangeRequest(Base):
    __tablename__ = "location_change_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_service_id: Mapped[int] = mapped_column(ForeignKey("user_services.id"), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    from_server_id: Mapped[int] = mapped_column(ForeignKey("servers.id"), nullable=False)
    to_server_id: Mapped[int] = mapped_column(ForeignKey("servers.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    fee_amount: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    admin_id: Mapped[Optional[int]] = mapped_column(ForeignKey("admins.id"), nullable=True)
    request_id: Mapped[str] = mapped_column(String(64), nullable=False)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ManualServer(Base):
    __tablename__ = "manual_servers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ManualPlan(Base):
    __tablename__ = "manual_plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    manual_server_id: Mapped[int] = mapped_column(ForeignKey("manual_servers.id"), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    volume_label: Mapped[str] = mapped_column(String(64), nullable=False)
    price: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    is_visible_to_admins: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ManualLink(Base):
    __tablename__ = "manual_links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    manual_server_id: Mapped[int] = mapped_column(ForeignKey("manual_servers.id"), nullable=False, index=True)
    manual_plan_id: Mapped[int] = mapped_column(ForeignKey("manual_plans.id"), nullable=False, index=True)
    link_text: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    imported_by_admin_id: Mapped[int] = mapped_column(ForeignKey("admins.id"), nullable=False)
    used_by_user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    used_by_admin_id: Mapped[Optional[int]] = mapped_column(ForeignKey("admins.id"), nullable=True)
    used_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (Index("ix_manual_links_server_plan_status", "manual_server_id", "manual_plan_id", "status"),)


class ManualDelivery(Base):
    __tablename__ = "manual_deliveries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    manual_link_id: Mapped[int] = mapped_column(ForeignKey("manual_links.id"), nullable=False, unique=True)
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    user_telegram_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    admin_id: Mapped[int] = mapped_column(ForeignKey("admins.id"), nullable=False)
    customer_info: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    manual_server_id: Mapped[int] = mapped_column(ForeignKey("manual_servers.id"), nullable=False)
    manual_plan_id: Mapped[int] = mapped_column(ForeignKey("manual_plans.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    delivered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    returned_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    return_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class Purchase(Base):
    __tablename__ = "purchases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    user_telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    purchase_type: Mapped[str] = mapped_column(String(16), nullable=False)
    user_service_id: Mapped[Optional[int]] = mapped_column(ForeignKey("user_services.id"), nullable=True)
    manual_delivery_id: Mapped[Optional[int]] = mapped_column(ForeignKey("manual_deliveries.id"), nullable=True)
    server_id: Mapped[Optional[int]] = mapped_column(ForeignKey("servers.id"), nullable=True)
    plan_id: Mapped[Optional[int]] = mapped_column(ForeignKey("plans.id"), nullable=True)
    manual_server_id: Mapped[Optional[int]] = mapped_column(ForeignKey("manual_servers.id"), nullable=True)
    manual_plan_id: Mapped[Optional[int]] = mapped_column(ForeignKey("manual_plans.id"), nullable=True)
    price: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class WalletTransaction(Base):
    __tablename__ = "wallet_transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    balance_before: Mapped[int] = mapped_column(BigInteger, nullable=False)
    balance_after: Mapped[int] = mapped_column(BigInteger, nullable=False)
    reference: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    purchase_id: Mapped[Optional[int]] = mapped_column(ForeignKey("purchases.id"), nullable=True)
    payment_request_id: Mapped[Optional[int]] = mapped_column(ForeignKey("payment_requests.id"), nullable=True)
    request_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PaymentCard(Base):
    __tablename__ = "payment_cards"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    card_number: Mapped[str] = mapped_column(String(32), nullable=False)
    card_holder_name: Mapped[str] = mapped_column(String(255), nullable=False)
    bank_name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class PaymentRequest(Base):
    __tablename__ = "payment_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    card_id: Mapped[int] = mapped_column(ForeignKey("payment_cards.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    receipt_file_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    approved_by_admin_id: Mapped[Optional[int]] = mapped_column(ForeignKey("admins.id"), nullable=True)
    locked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    request_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class SupportTicket(Base):
    __tablename__ = "support_tickets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    user_service_id: Mapped[Optional[int]] = mapped_column(ForeignKey("user_services.id"), nullable=True)
    manual_delivery_id: Mapped[Optional[int]] = mapped_column(ForeignKey("manual_deliveries.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    admin_reply: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    admin_telegram_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    user_telegram_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    details: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    request_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
