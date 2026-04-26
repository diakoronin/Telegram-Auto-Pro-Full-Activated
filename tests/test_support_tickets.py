"""Support ticket status enum values."""

from app.db.models import SupportTicketStatus


def test_ticket_status_values() -> None:
    assert SupportTicketStatus.OPEN.value == "open"
    assert SupportTicketStatus.CLOSED.value == "closed"
