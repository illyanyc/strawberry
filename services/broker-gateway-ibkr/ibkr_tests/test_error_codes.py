"""IBKR error-code categories: IB-060."""

from __future__ import annotations

import pytest
from tb_broker_ibkr.error_codes import CODE_TABLE, Action, Category, classify, is_pacing_message


@pytest.mark.parametrize(
    ("code", "category", "action"),
    [
        (1100, Category.CONNECTIVITY, Action.DEGRADE),
        (1101, Category.CONNECTIVITY, Action.RESYNC_RESUBSCRIBE),
        (1102, Category.CONNECTIVITY, Action.RESYNC),
        (1300, Category.CONNECTIVITY, Action.RECONNECT),
        (2110, Category.CONNECTIVITY, Action.DEGRADE),
        (2104, Category.FARM_STATUS, Action.LOG),
        (2158, Category.FARM_STATUS, Action.LOG),
        (162, Category.PACING, Action.PACE),
        (354, Category.MARKET_DATA, Action.SUBSCRIPTION_ERROR),
        (101, Category.MARKET_DATA, Action.SUBSCRIPTION_ERROR),
        (201, Category.ORDER_REJECT, Action.REJECT_ORDER),
        (110, Category.ORDER_REJECT, Action.REJECT_ORDER),
        (202, Category.ORDER_CANCEL, Action.CANCEL_NOTICE),
        (200, Category.CONTRACT, Action.REJECT_ORDER),
        (10197, Category.SESSION, Action.DEGRADE),
        (2199, Category.INFORMATIONAL, Action.LOG),  # range fallback
        (99999, Category.UNCLASSIFIED, Action.LOG),
    ],
)
def test_ib060_classification(code: int, category: Category, action: Action) -> None:
    info = classify(code)
    assert (info.category, info.action) == (category, action)


def test_ib060_codes_unique_and_described() -> None:
    assert all(info.meaning for info in CODE_TABLE.values())
    assert all(code == info.code for code, info in CODE_TABLE.items())


def test_ib050_162_is_pacing_only_with_pacing_text() -> None:
    assert is_pacing_message(
        162, "Historical Market Data Service error message:API historical data query pacing violation"
    )
    assert not is_pacing_message(162, "Historical Market Data Service error message:HMDS query returned no data")
    assert is_pacing_message(100, "Max rate of messages per second has been exceeded")
    assert not is_pacing_message(201, "Order rejected")
