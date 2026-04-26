"""Daily report CSV contains service status keys."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from app.services.sales_csv import export_daily_report_csv


@pytest.mark.asyncio
async def test_daily_report_csv_metrics() -> None:
    session = AsyncMock()

    agg_obj = type("A", (), {})()
    agg_obj.total_gb = 10.0
    agg_obj.total_orders = 2
    agg_obj.total_revenue = 5000
    agg_obj.by_plan_label = {}
    agg_obj.by_server = {}
    agg_obj.user_channel_gb = 10.0
    agg_obj.user_channel_orders = 2
    agg_obj.admin_channel_gb = 0.0
    agg_obj.admin_channel_orders = 0

    pay = type("P", (), {"approved_count": 1, "approved_amount": 1000})()
    svc = type(
        "S",
        (),
        {"active": 3, "limited": 1, "expired": 0, "disabled": 0, "error": 0},
    )()

    with patch(
        "app.services.sales_report.aggregate_sales",
        new=AsyncMock(return_value=agg_obj),
    ):
        with patch(
            "app.services.reports_aggregate.aggregate_payments_approved",
            new=AsyncMock(return_value=pay),
        ):
            with patch(
                "app.services.reports_aggregate.count_user_services_by_status",
                new=AsyncMock(return_value=svc),
            ):
                with patch(
                    "app.services.reports_aggregate.count_completed_purchases",
                    new=AsyncMock(return_value=2),
                ):
                    now = datetime.now(tz=UTC)
                    s = await export_daily_report_csv(
                        session,
                        start_utc=now - timedelta(days=1),
                        end_utc=now,
                        jalali_date="1403/01/01",
                        window_label="test",
                    )
    assert "services_active" in s
    assert "revenue_toman" in s
