"""Subscription API helpers."""

from app.structured_log import mask_subscription_token


def test_mask_subscription_token() -> None:
    assert mask_subscription_token(None) == "***"
    m = mask_subscription_token("abcdefghijklmnop")
    assert "abcd" in m and "mnop" in m
