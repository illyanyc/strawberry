import { afterEach, describe, expect, it, vi } from "vitest";

import { api, ApiError, csrfToken, setStepUpHandler, setUnauthenticatedHandler } from "./api";

function respond(status: number, body: unknown) {
  return new Response(status === 204 ? null : JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

afterEach(() => {
  vi.restoreAllMocks();
  setStepUpHandler(null);
  setUnauthenticatedHandler(null);
  document.cookie = "tb_csrf=; max-age=0";
});

describe("csrfToken", () => {
  it("reads either cookie name", () => {
    expect(csrfToken("a=1; __Host-tb_csrf=abc; b=2")).toBe("abc");
    expect(csrfToken("tb_csrf=xyz")).toBe("xyz");
    expect(csrfToken("other=1")).toBe("");
  });
});

describe("api", () => {
  it("SE-006: sends the CSRF token on state-changing requests only", async () => {
    document.cookie = "tb_csrf=tok123";
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async () => respond(200, { ok: true }));
    await api("/api/v1/settings");
    await api("/api/v1/settings/ui.theme", { method: "PUT", body: { value: "dark", version: 0 } });
    const [getInit, putInit] = fetchMock.mock.calls.map((c) => c[1] as RequestInit);
    expect((getInit?.headers as Record<string, string>)["X-CSRF-Token"]).toBeUndefined();
    expect((putInit?.headers as Record<string, string>)["X-CSRF-Token"]).toBe("tok123");
    expect(putInit?.credentials).toBe("same-origin");
  });

  it("SE-003: runs step-up on step_up_required and retries once", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(respond(403, { error: "step_up_required", method: "passkey", action: "trading.live.enable" }))
      .mockResolvedValueOnce(respond(200, { key: "trading.live_enabled", value: true }));
    const handler = vi.fn(async () => true);
    setStepUpHandler(handler);
    const out = await api<{ value: boolean }>("/api/v1/settings/trading.live_enabled", { method: "PUT", body: { value: true, version: 0 } });
    expect(handler).toHaveBeenCalledWith("passkey", "trading.live.enable");
    expect(out.value).toBe(true);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("does not loop when the retry still needs step-up", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async () => respond(403, { error: "step_up_required", method: "passkey" }));
    setStepUpHandler(async () => true);
    await expect(api("/x", { method: "POST" })).rejects.toMatchObject({ status: 403, code: "step_up_required" });
  });

  it("reports a cancelled step-up", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(respond(403, { error: "step_up_required", method: "any" }));
    setStepUpHandler(async () => false);
    await expect(api("/x", { method: "POST" })).rejects.toMatchObject({ code: "step_up_cancelled" });
  });

  it("calls the unauthenticated handler on 401 unless allowed", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async () => respond(401, { error: "unauthenticated", message: "login required" }));
    const onUnauth = vi.fn();
    setUnauthenticatedHandler(onUnauth);
    await expect(api("/auth/me")).rejects.toBeInstanceOf(ApiError);
    expect(onUnauth).toHaveBeenCalledTimes(1);
    await expect(api("/auth/me", { allow401: true })).rejects.toBeInstanceOf(ApiError);
    expect(onUnauth).toHaveBeenCalledTimes(1);
  });
});
