/**
 * Typed client for the api-gateway REST API (contracts/openapi/gateway.yaml).
 *
 * - Same-origin cookies only; the session cookie is HttpOnly and never touched here.
 * - State-changing requests send the double-submit CSRF token from the tb_csrf cookie (SE-006).
 * - A 403 `step_up_required` response invokes the registered step-up handler and retries once
 *   (SE-003); a 401 invokes the unauthenticated handler.
 */

export type StepUpMethod = "passkey" | "any";

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  constructor(status: number, code: string, message: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

type StepUpHandler = (method: StepUpMethod, action: string) => Promise<boolean>;
type UnauthenticatedHandler = () => void;

let stepUpHandler: StepUpHandler | null = null;
let unauthenticatedHandler: UnauthenticatedHandler | null = null;

/** Register the UI's step-up dialog. It resolves true when a step-up succeeded. */
export function setStepUpHandler(h: StepUpHandler | null): void {
  stepUpHandler = h;
}

/** Register what happens when the session is missing or expired. */
export function setUnauthenticatedHandler(h: UnauthenticatedHandler | null): void {
  unauthenticatedHandler = h;
}

/** Read the CSRF token cookie (`__Host-tb_csrf` over HTTPS, `tb_csrf` otherwise). */
export function csrfToken(cookie: string = document.cookie): string {
  for (const part of cookie.split(";")) {
    const [rawName, ...rest] = part.trim().split("=");
    if (rawName === "__Host-tb_csrf" || rawName === "tb_csrf") return rest.join("=");
  }
  return "";
}

export interface RequestOptions {
  method?: "GET" | "POST" | "PUT" | "DELETE";
  body?: unknown;
  /** Skip the step-up retry (used by the step-up endpoints themselves). */
  noStepUp?: boolean;
  /** Do not call the unauthenticated handler on 401 (used by login screens). */
  allow401?: boolean;
}

/** Call the gateway and decode JSON. Throws ApiError on non-2xx. */
export async function api<T = unknown>(path: string, opts: RequestOptions = {}): Promise<T> {
  const method = opts.method ?? "GET";
  const headers: Record<string, string> = { Accept: "application/json" };
  if (method !== "GET") {
    headers["Content-Type"] = "application/json";
    headers["X-CSRF-Token"] = csrfToken();
  }
  const res = await fetch(path, {
    method,
    headers,
    credentials: "same-origin",
    body: opts.body === undefined ? undefined : JSON.stringify(opts.body),
  });
  if (res.status === 204) return undefined as T;
  const data = (await res.json().catch(() => ({}))) as Record<string, unknown>;
  if (res.ok) return data as T;

  const code = typeof data.error === "string" ? data.error : "error";
  const message = typeof data.message === "string" ? data.message : res.statusText;
  if (res.status === 403 && code === "step_up_required" && !opts.noStepUp && stepUpHandler) {
    const method_ = data.method === "passkey" ? "passkey" : "any";
    const ok = await stepUpHandler(method_, typeof data.action === "string" ? data.action : "");
    if (ok) return api<T>(path, { ...opts, noStepUp: true });
    throw new ApiError(403, "step_up_cancelled", "Step-up was cancelled");
  }
  if (res.status === 401 && !opts.allow401) unauthenticatedHandler?.();
  throw new ApiError(res.status, code, message);
}

export interface AuthState {
  setup_required: boolean;
  authenticated: boolean;
  rp_id: string;
}

export interface Me {
  owner: { id: string; display_name: string };
  session: {
    id: string;
    auth_method: "passkey" | "totp" | "recovery";
    step_up_method: string;
    step_up_until: string | null;
    idle_expires_at: string;
    absolute_expires_at: string;
  };
  passkeys: { id: string; name: string; created_at: string; last_used_at: string | null }[];
  totp_enabled: boolean;
  recovery_codes_left: number;
}

export interface TradingStatus {
  environment: string;
  live_enabled: boolean;
  mode: "paper" | "live";
  server_time: string;
  session_expires: string;
}
