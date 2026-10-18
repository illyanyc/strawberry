"""IBKR adapter (`broker-gateway-ibkr`): IB Gateway session supervision, orders, executions,
account data, and paced market data over ``ib_async`` (section 04; D1, D18; ADR-0018).

Business rules: ``docs/business-logic/brokers-ibkr.md`` (``IB-`` IDs) plus the cross-broker
``OE-``/``VP-`` rules this adapter enforces.
"""

__version__ = "0.1.0"
