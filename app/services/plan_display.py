"""User-facing plan label (short display name)."""

from __future__ import annotations

from app.db.models import Plan


def plan_display_label(plan: Plan) -> str:
    """Prefer plan.display_name; else internal name."""
    d = (plan.display_name or "").strip()
    if d:
        return d
    return (plan.name or "").strip() or "پلن"
