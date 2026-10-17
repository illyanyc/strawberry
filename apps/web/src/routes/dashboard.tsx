import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";

import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Alert, Badge } from "@/components/ui/form";
import { api, type Me, type TradingStatus } from "@/lib/api";
import { formatTime } from "@/lib/utils";

const phases = [
  { n: 0, name: "Foundation", detail: "Auth, secrets, audit, local stack, observability", done: true },
  { n: 1, name: "Data and brokers", detail: "Alpaca + IBKR paper, market data, charts", done: false },
  { n: 2, name: "Trading core on paper", detail: "Ledgers, OMS, risk, kill switches", done: false },
  { n: 3, name: "Research core", detail: "Backtester, validation, context data", done: false },
  { n: 4, name: "ML and optimization", detail: "Model zoo, studies, assistant", done: false },
];

export function DashboardPage() {
  const me = useQuery({ queryKey: ["me"], queryFn: () => api<Me>("/auth/me") });
  const status = useQuery({ queryKey: ["status"], queryFn: () => api<TradingStatus>("/api/v1/system/status") });

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Welcome{me.data ? `, ${me.data.owner.display_name}` : ""}</h1>
        <p className="mt-1 text-sm text-muted">Phase 0: the platform is secured and observable. Trading features arrive in later phases.</p>
      </div>
      {me.data && me.data.session.auth_method !== "passkey" && (
        <Alert tone="warn">
          You signed in with a {me.data.session.auth_method === "totp" ? "TOTP code" : "recovery code"}. Dangerous actions need a passkey —{" "}
          <Link to="/settings/security" className="underline">
            register one now
          </Link>
          .
        </Alert>
      )}
      {me.data && me.data.passkeys.length < 2 && (
        <Alert tone="warn">Only one passkey is registered. Add a second one (e.g. your phone) under Security so a lost device can't lock you out.</Alert>
      )}
      <div className="grid gap-4 md:grid-cols-3">
        <Card>
          <CardHeader title="Trading mode" />
          <CardBody className="space-y-2 text-sm">
            {status.isPending ? <p className="text-muted">Loading…</p> : status.isError ? <p className="text-live">Status unavailable</p> : (
              <>
                <Badge tone={status.data.mode === "live" ? "live" : "paper"}>{status.data.mode}</Badge>
                <p className="text-muted">Live trading flag: {status.data.live_enabled ? "on" : "off"}</p>
              </>
            )}
          </CardBody>
        </Card>
        <Card>
          <CardHeader title="Session" />
          <CardBody className="space-y-1 text-sm text-muted">
            {me.data ? (
              <>
                <p>
                  Method: <span className="text-fg">{me.data.session.auth_method}</span>
                </p>
                <p>Idle timeout: {formatTime(me.data.session.idle_expires_at)}</p>
                <p>Ends at: {formatTime(me.data.session.absolute_expires_at)}</p>
              </>
            ) : (
              <p>Loading…</p>
            )}
          </CardBody>
        </Card>
        <Card>
          <CardHeader title="Account security" />
          <CardBody className="space-y-1 text-sm text-muted">
            {me.data ? (
              <>
                <p>
                  Passkeys: <span className="text-fg">{me.data.passkeys.length}</span>
                </p>
                <p>
                  Authenticator app: <span className="text-fg">{me.data.totp_enabled ? "enabled" : "not set up"}</span>
                </p>
                <p>
                  Recovery codes left: <span className="text-fg">{me.data.recovery_codes_left}</span>
                </p>
              </>
            ) : (
              <p>Loading…</p>
            )}
          </CardBody>
        </Card>
      </div>
      <Card>
        <CardHeader title="Build progress" description="Each phase ends with an acceptance gate." />
        <CardBody>
          <ol className="space-y-3">
            {phases.map((p) => (
              <li key={p.n} className="flex items-center gap-3 text-sm">
                <Badge tone={p.done ? "ok" : "neutral"}>{p.done ? "built" : "planned"}</Badge>
                <span className="font-medium">
                  Phase {p.n}: {p.name}
                </span>
                <span className="hidden text-muted sm:inline">— {p.detail}</span>
              </li>
            ))}
          </ol>
        </CardBody>
      </Card>
    </div>
  );
}
