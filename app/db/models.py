from __future__ import annotations

import enum
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy import JSON
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class AdminRole(str, enum.Enum):
    OWNER = "owner"
    MANAGER = "manager"
    SELLER = "seller"


class PaymentRequestStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class LinkStatus(str, enum.Enum):
    UNUSED = "unused"
    USED = "used"
    RETURNED = "returned"


class PurchaseStatus(str, enum.Enum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"
    REFUNDED = "refunded"


class WalletTransactionType(str, enum.Enum):
    CHARGE_APPROVED = "charge_approved"
    PURCHASE = "purchase"
    REFUND = "refund"
    MANUAL_ADJUST = "manual_adjust"


class UserServiceStatus(str, enum.Enum):
    ACTIVE = "active"
    LIMITED = "limited"
    EXPIRED = "expired"
    DISABLED = "disabled"
    REFUNDED = "refunded"
    MIGRATING = "migrating"
    ERROR = "error"


class PanelType(str, enum.Enum):
    MARZBAN = "marzban"
    SANAEI_3XUI = "sanaei_3xui"
    XUI = "xui"


class PanelAccountStatus(str, enum.Enum):
    ACTIVE = "active"
    DISABLED = "disabled"
    DELETED = "deleted"
    MIGRATED = "migrated"
    FAILED = "failed"


class SupportTicketStatus(str, enum.Enum):
    OPEN = "open"
    ANSWERED = "answered"
    CLOSED = "closed"


class Admin(Base):
    __tablename__ = "admins"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False, unique=True)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    role: Mapped[AdminRole] = mapped_column(Enum(AdminRole), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (Index("ix_admins_telegram_id", "telegram_id"),)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False, unique=True)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_blocked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    card_view_allowed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    card_payment_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    wallet_balance: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint("wallet_balance >= 0", name="ck_users_wallet_non_negative"),
        Index("ix_users_telegram_id", "telegram_id"),
    )


class PaymentCard(Base):
    __tablename__ = "payment_cards"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    card_number_masked: Mapped[str] = mapped_column(String(32), nullable=False)
    card_number_full: Mapped[str | None] = mapped_column(String(32), nullable=True)
    card_holder: Mapped[str] = mapped_column(String(120), nullable=False)
    bank_name: Mapped[str] = mapped_column(String(120), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_public: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Server(Base):
    __tablename__ = "servers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    location_label: Mapped[str | None] = mapped_column(String(120), nullable=True)
    panel_id: Mapped[int | None] = mapped_column(ForeignKey("panels.id"), nullable=True)
    panel_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    inbound_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    template_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_visible_to_users: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    supports_location_change: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    panel: Mapped["Panel | None"] = relationship(back_populates="servers")


class Plan(Base):
    __tablename__ = "plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    server_id: Mapped[int] = mapped_column(ForeignKey("servers.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    price: Mapped[int] = mapped_column(BigInteger, nullable=False)
    volume_gb: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    is_visible_to_users: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    low_stock_rearm: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )
    duration_days: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    server: Mapped[Server] = relationship()

    __table_args__ = (
        CheckConstraint("price > 0", name="ck_plans_price_positive"),
        Index("ix_plans_server_id", "server_id"),
    )


class Link(Base):
    __tablename__ = "links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    server_id: Mapped[int] = mapped_column(ForeignKey("servers.id"), nullable=False)
    plan_id: Mapped[int] = mapped_column(ForeignKey("plans.id"), nullable=False)
    link_text: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[LinkStatus] = mapped_column(
        Enum(LinkStatus), nullable=False, default=LinkStatus.UNUSED
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "server_id", "plan_id", "link_text", name="uq_links_server_plan_text"
        ),
        Index("ix_links_server_plan_status", "server_id", "plan_id", "status"),
    )


class PaymentRequest(Base):
    __tablename__ = "payment_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    receipt_file_id: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    receipt_kind: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    assigned_card_id: Mapped[int | None] = mapped_column(
        ForeignKey("payment_cards.id"), nullable=True
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[PaymentRequestStatus] = mapped_column(
        Enum(PaymentRequestStatus), nullable=False, default=PaymentRequestStatus.PENDING
    )
    reviewed_by_admin_id: Mapped[int | None] = mapped_column(
        ForeignKey("admins.id"), nullable=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    user: Mapped[User] = relationship()
    reviewer: Mapped[Admin | None] = relationship()
    assigned_card: Mapped[PaymentCard | None] = relationship()

    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_payment_requests_amount_positive"),
        Index("ix_payment_requests_status", "status"),
        Index("ix_payment_requests_user_status", "user_id", "status"),
    )


class WalletTransaction(Base):
    __tablename__ = "wallet_transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    type: Mapped[WalletTransactionType] = mapped_column(
        Enum(WalletTransactionType), nullable=False
    )
    amount_delta: Mapped[int] = mapped_column(BigInteger, nullable=False)
    balance_before: Mapped[int] = mapped_column(BigInteger, nullable=False)
    balance_after: Mapped[int] = mapped_column(BigInteger, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    related_payment_request_id: Mapped[int | None] = mapped_column(
        ForeignKey("payment_requests.id"), nullable=True
    )
    related_purchase_id: Mapped[int | None] = mapped_column(
        ForeignKey("purchases.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_wallet_transactions_user_id", "user_id"),
        CheckConstraint("balance_after >= 0", name="ck_wallet_tx_balance_after_nn"),
    )


class Purchase(Base):
    __tablename__ = "purchases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    server_id: Mapped[int] = mapped_column(ForeignKey("servers.id"), nullable=False)
    plan_id: Mapped[int] = mapped_column(ForeignKey("plans.id"), nullable=False)
    link_id: Mapped[int | None] = mapped_column(ForeignKey("links.id"), nullable=True)
    user_service_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    custom_service_name: Mapped[str] = mapped_column(String(120), nullable=False)
    amount_paid: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[PurchaseStatus] = mapped_column(
        Enum(PurchaseStatus), nullable=False, default=PurchaseStatus.PENDING
    )
    is_refunded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    refund_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    refunded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_purchases_user_id", "user_id"),
        Index("ix_purchases_created_at", "created_at"),
        Index("ix_purchases_user_service_id", "user_service_id"),
    )


class Delivery(Base):
    __tablename__ = "deliveries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    link_id: Mapped[int] = mapped_column(ForeignKey("links.id"), nullable=False)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    admin_id: Mapped[int | None] = mapped_column(ForeignKey("admins.id"), nullable=True)
    purchase_id: Mapped[int | None] = mapped_column(
        ForeignKey("purchases.id"), nullable=True
    )
    channel: Mapped[str] = mapped_column(String(32), nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (Index("ix_deliveries_admin_id", "admin_id"),)


class SupportTicket(Base):
    __tablename__ = "support_tickets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    user_service_id: Mapped[int | None] = mapped_column(
        ForeignKey("user_services.id"), nullable=True
    )
    status: Mapped[SupportTicketStatus] = mapped_column(
        Enum(SupportTicketStatus), nullable=False, default=SupportTicketStatus.OPEN
    )
    message: Mapped[str] = mapped_column(Text, nullable=False)
    admin_reply: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_support_tickets_user_id", "user_id"),
        Index("ix_support_tickets_status", "status"),
    )


class Panel(Base):
    """Backend VPN panel (Marzban, 3x-ui, etc.)."""

    __tablename__ = "panels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    type: Mapped[PanelType] = mapped_column(Enum(PanelType), nullable=False)
    base_url: Mapped[str] = mapped_column(String(512), nullable=False)
    web_base_path: Mapped[str | None] = mapped_column(String(128), nullable=True)
    username: Mapped[str] = mapped_column(String(255), nullable=False)
    password_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    api_token_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Marzban: optional JSON for UserCreate proxies/inbounds (see Marzban API docs).
    marzban_proxies_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    marzban_inbounds_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    verify_ssl: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_test_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_test_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_test_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    servers: Mapped[list["Server"]] = relationship(back_populates="panel")


class PanelTemplate(Base):
    __tablename__ = "panel_templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    panel_id: Mapped[int] = mapped_column(ForeignKey("panels.id"), nullable=False)
    server_id: Mapped[int | None] = mapped_column(ForeignKey("servers.id"), nullable=True)
    inbound_id: Mapped[int] = mapped_column(Integer, nullable=False)
    protocol: Mapped[str] = mapped_column(String(32), nullable=False)
    security: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    flow: Mapped[str | None] = mapped_column(String(64), nullable=True)
    remark: Mapped[str | None] = mapped_column(String(255), nullable=True)
    config_template_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class UserService(Base):
    """
    One purchased service = one row = one unique subscription_token.
    Multiple services for the same Telegram user = multiple rows (multiple stable links).
    """

    __tablename__ = "user_services"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    public_service_code: Mapped[str] = mapped_column(String(16), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    user_telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    purchase_id: Mapped[int | None] = mapped_column(ForeignKey("purchases.id"), nullable=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("plans.id"), nullable=False)
    custom_service_name: Mapped[str] = mapped_column(String(120), nullable=False)
    total_quota_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    used_traffic_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    remaining_traffic_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    expire_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[UserServiceStatus] = mapped_column(
        Enum(UserServiceStatus), nullable=False, default=UserServiceStatus.ACTIVE
    )
    # Unique per purchased service (not per Telegram user).
    subscription_token: Mapped[str] = mapped_column(String(64), nullable=False)
    subscription_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    location_change_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    location_change_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    location_change_month_key: Mapped[str] = mapped_column(String(7), nullable=False, default="")
    location_change_month_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_location_change_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    current_server_id: Mapped[int | None] = mapped_column(ForeignKey("servers.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("public_service_code", name="uq_user_services_public_code"),
        UniqueConstraint("subscription_token", name="uq_user_services_subscription_token"),
        Index("ix_user_services_user_id", "user_id"),
        Index("ix_user_services_status", "status"),
    )


class PanelAccount(Base):
    __tablename__ = "panel_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_service_id: Mapped[int] = mapped_column(ForeignKey("user_services.id"), nullable=False)
    panel_id: Mapped[int] = mapped_column(ForeignKey("panels.id"), nullable=False)
    server_id: Mapped[int] = mapped_column(ForeignKey("servers.id"), nullable=False)
    panel_type: Mapped[str] = mapped_column(String(32), nullable=False)
    panel_account_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    username: Mapped[str] = mapped_column(String(128), nullable=False)
    config_links_json: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    raw_subscription_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    quota_bytes_assigned: Mapped[int] = mapped_column(BigInteger, nullable=False)
    usage_baseline_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    upload_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    download_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    total_used_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    final_used_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    status: Mapped[PanelAccountStatus] = mapped_column(
        Enum(PanelAccountStatus), nullable=False, default=PanelAccountStatus.ACTIVE
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_panel_accounts_user_service_id", "user_service_id"),
        Index("ix_panel_accounts_is_active", "is_active"),
        Index("ix_panel_accounts_status", "status"),
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
    source_panel: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class RateLimitBucket(Base):
    __tablename__ = "rate_limit_buckets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(128), nullable=False)
    window_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (UniqueConstraint("key", "window_start", name="uq_rl_key_window"),)


class PendingConfirmation(Base):
    __tablename__ = "pending_confirmations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    admin_telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    action_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (Index("ix_pending_confirm_admin", "admin_telegram_id"),)


class AppSetting(Base):
    """Key-value runtime settings (e.g. legacy admin tools toggles)."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    actor_telegram_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    actor_role: Mapped[str | None] = mapped_column(String(32), nullable=True)
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    target_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    target_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    ip_address: Mapped[bytes | None] = mapped_column(LargeBinary(16), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (Index("ix_audit_logs_created_at", "created_at"),)
