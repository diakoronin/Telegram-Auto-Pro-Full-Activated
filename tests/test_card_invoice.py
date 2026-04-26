from bot_app.services.card_invoice import build_user_invoice, is_full_card_number


def test_full_card_ok():
    ok, body = build_user_invoice(
        card_number="6037701573119390",
        card_holder_name="تست",
        bank_name="ملت",
        amount=100000,
        payment_request_id=42,
        expire_minutes=30,
    )
    assert ok
    assert "6037701573119390" in body
    assert "****" not in body


def test_masked_blocks():
    ok, _ = build_user_invoice(
        card_number="6037****9390",
        card_holder_name="تست",
        bank_name="ملت",
        amount=100000,
        payment_request_id=1,
        expire_minutes=30,
    )
    assert not ok


def test_is_full_card():
    assert is_full_card_number("6037701573119390")
    assert not is_full_card_number("6037")
