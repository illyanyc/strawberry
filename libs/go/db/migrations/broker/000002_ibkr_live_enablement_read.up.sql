-- The IBKR adapter reads the audited live enablement (BR-013, OE-010, IB-007) and nothing else here.
GRANT USAGE ON SCHEMA broker TO svc_broker_gateway_ibkr;
GRANT SELECT ON broker.live_enablement TO svc_broker_gateway_ibkr;
