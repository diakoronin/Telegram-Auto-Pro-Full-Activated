"""Infer sold GB from plan display/name for sales reports (best-effort)."""

from __future__ import annotations

import re

from app.db.models import Plan
from app.services.plan_display import plan_display_label


def gb_from_plan(plan: Plan) -> float:
    """Parse first integer before «گیگ» / gig in label; default 1.0."""
    label = plan_display_label(plan)
    m = re.search(r"(\d+)\s*گیگ", label, re.IGNORECASE)
    if m:
        return float(int(m.group(1)))
    m2 = re.search(r"(\d+)\s*gig", label, re.IGNORECASE)
    if m2:
        return float(int(m2.group(1)))
    return 1.0
