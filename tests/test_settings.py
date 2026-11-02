"""The settings as `meridian.run` delivers them (open-meridian 0.35.2):
typed, a table's rows a tuple."""

from __future__ import annotations

from tradier.settings import DECLARED, config_from


def test_the_account_currency_is_read_from_a_typed_delivery() -> None:
    values = {
        "environment": "sandbox",
        "poll_seconds": 60,
        "stale_after_hours": 36,
        "account_currency": [{"account": "*", "currency": "USD"}],
        "sample_data": True,
    }
    delivered = DECLARED._of(values, (), ())  # type: ignore[attr-defined]
    assert isinstance(delivered.values["account_currency"], tuple)
    typed = config_from(delivered.values, delivered.missing)
    assert typed == config_from(values, ())
    assert typed.account_currency == ({"account": "*", "currency": "USD"},)
