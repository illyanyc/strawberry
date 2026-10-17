"""Test doubles for IB Gateway (no real credentials or network needed).

* :mod:`.gateway` - in-process fake gateway behind the :class:`~tb_broker_ibkr.port.IbPort` seam:
  orders, fills, commissions, manual orders, outages, 2FA, timeouts, duplicates, truncation.
* :mod:`.wire` - a TCP server speaking enough of the TWS API socket protocol for the real
  ``ib_async`` client to connect, sync, heartbeat, and receive connectivity codes.
* :mod:`.replay` - loads recorded-format message fixtures and replays them into an adapter.
"""
