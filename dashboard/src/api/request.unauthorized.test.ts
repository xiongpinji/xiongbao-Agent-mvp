import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./config", () => ({
  getApiUrl: (path: string) => `/api${path}`,
}));

vi.mock("../i18n", () => ({
  default: { language: "zh" },
}));

function unauthorizedResponse(): Response {
  return new Response("{}", {
    status: 401,
    headers: { "content-type": "application/json" },
  });
}

describe("401 handling", () => {
  const replace = vi.fn();
  let mod: typeof import("./request");

  beforeEach(async () => {
    vi.resetModules();
    localStorage.clear();
    replace.mockClear();
    Object.defineProperty(window, "location", {
      configurable: true,
      value: { pathname: "/chat", replace },
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve(unauthorizedResponse())),
    );
    mod = await import("./request");
    mod.setAuthToken("expired-token");
  });

  it("lets an in-app listener take over the redirect instead of reloading", async () => {
    const listener = vi.fn((event: Event) => event.preventDefault());
    window.addEventListener(mod.UNAUTHORIZED_EVENT, listener);

    await expect(mod.request("/agents")).rejects.toThrow();

    expect(listener).toHaveBeenCalledOnce();
    expect(replace).not.toHaveBeenCalled();
    expect(mod.getAuthToken()).toBe("");

    window.removeEventListener(mod.UNAUTHORIZED_EVENT, listener);
  });

  it("falls back to a full-page navigation when nothing handles the event", async () => {
    await expect(mod.request("/agents")).rejects.toThrow();

    expect(replace).toHaveBeenCalledWith("/login");
  });

  it("redirects only once when several requests fail concurrently", async () => {
    const listener = vi.fn((event: Event) => event.preventDefault());
    window.addEventListener(mod.UNAUTHORIZED_EVENT, listener);

    await Promise.allSettled([
      mod.request("/agents"),
      mod.request("/skills"),
      mod.request("/cron"),
    ]);

    expect(listener).toHaveBeenCalledOnce();

    window.removeEventListener(mod.UNAUTHORIZED_EVENT, listener);
  });

  const transports = [
    ["json", (api: typeof mod) => api.request("/projects/p1/todos")],
    ["blob", (api: typeof mod) => api.requestBlob("/projects/p1/image")],
    ["probe", (api: typeof mod) => api.probeAuthResource("/projects/p1/file")],
    ["stream", (api: typeof mod) => api.requestStream("/projects/p1/stream")],
  ] as const;

  it.each(transports)(
    "%s: a late 401 cannot clear the newly signed-in account",
    async (_name, start) => {
      let finish!: (response: Response) => void;
      vi.stubGlobal(
        "fetch",
        vi.fn(
          () =>
            new Promise<Response>((resolve) => {
              finish = resolve;
            }),
        ),
      );
      const listener = vi.fn((event: Event) => event.preventDefault());
      window.addEventListener(mod.UNAUTHORIZED_EVENT, listener);
      try {
        const outcome = expect(start(mod)).rejects.toThrow();
        mod.setAuthToken("account-b-token");
        finish(unauthorizedResponse());
        await outcome;
        expect(mod.getAuthToken()).toBe("account-b-token");
        expect(listener).not.toHaveBeenCalled();
        expect(replace).not.toHaveBeenCalled();
      } finally {
        window.removeEventListener(mod.UNAUTHORIZED_EVENT, listener);
      }
    },
  );

  it.each(transports)(
    "%s: a late renewal cannot replace the newly signed-in account",
    async (_name, start) => {
      let finish!: (response: Response) => void;
      vi.stubGlobal(
        "fetch",
        vi.fn(
          () =>
            new Promise<Response>((resolve) => {
              finish = resolve;
            }),
        ),
      );
      const pending = start(mod);
      mod.setAuthToken("account-b-token");
      finish(
        new Response("{}", {
          status: 200,
          headers: {
            "content-type": "application/json",
            [mod.ACCESS_TOKEN_RESPONSE_HEADER]: "account-a-renewed",
          },
        }),
      );
      await pending;
      expect(mod.getAuthToken()).toBe("account-b-token");
      expect(replace).not.toHaveBeenCalled();
    },
  );

  it("does not accept an old response after signing in again with the same token", async () => {
    let finish!: (response: Response) => void;
    vi.stubGlobal(
      "fetch",
      vi.fn(
        () =>
          new Promise<Response>((resolve) => {
            finish = resolve;
          }),
      ),
    );
    const outcome = expect(mod.request("/projects/p1/todos")).rejects.toThrow();
    mod.clearAuthToken();
    mod.setAuthToken("expired-token");
    finish(unauthorizedResponse());
    await outcome;
    expect(mod.getAuthToken()).toBe("expired-token");
    expect(replace).not.toHaveBeenCalled();
  });

  it("keeps a renewal from the current request session", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve(
          new Response("{}", {
            status: 200,
            headers: {
              "content-type": "application/json",
              [mod.ACCESS_TOKEN_RESPONSE_HEADER]: "current-account-renewed",
            },
          }),
        ),
      ),
    );
    await mod.request("/projects/p1/todos");
    expect(mod.getAuthToken()).toBe("current-account-renewed");
  });

  it("an older 401 cannot clear a newer sliding renewal", async () => {
    let finishOld!: (response: Response) => void;
    const mockFetch = vi
      .fn()
      .mockImplementationOnce(
        () =>
          new Promise<Response>((resolve) => {
            finishOld = resolve;
          }),
      )
      .mockResolvedValueOnce(
        new Response("{}", {
          status: 200,
          headers: {
            "content-type": "application/json",
            [mod.ACCESS_TOKEN_RESPONSE_HEADER]: "current-account-renewed",
          },
        }),
      );
    vi.stubGlobal("fetch", mockFetch);
    const outcome = expect(mod.request("/projects/p1/todos")).rejects.toThrow();
    await mod.request("/projects/p1/plan/catalog");
    finishOld(unauthorizedResponse());
    await outcome;
    expect(mod.getAuthToken()).toBe("current-account-renewed");
    expect(replace).not.toHaveBeenCalled();
  });

  it("does not dispatch an old account's agent-forbidden event", async () => {
    let finish!: (response: Response) => void;
    vi.stubGlobal(
      "fetch",
      vi.fn(
        () =>
          new Promise<Response>((resolve) => {
            finish = resolve;
          }),
      ),
    );
    const listener = vi.fn();
    window.addEventListener(mod.FORBIDDEN_EVENT, listener);
    try {
      const outcome = expect(
        mod.request("/agents/a1/status"),
      ).rejects.toThrow();
      mod.setAuthToken("account-b-token");
      finish(new Response("{}", { status: 403 }));
      await outcome;
      expect(listener).not.toHaveBeenCalled();
      expect(mod.getAuthToken()).toBe("account-b-token");
    } finally {
      window.removeEventListener(mod.FORBIDDEN_EVENT, listener);
    }
  });

  it("still dispatches the current account's forbidden event with a renewal", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve(
          new Response("{}", {
            status: 403,
            headers: {
              [mod.ACCESS_TOKEN_RESPONSE_HEADER]: "current-account-renewed",
            },
          }),
        ),
      ),
    );
    const listener = vi.fn();
    window.addEventListener(mod.FORBIDDEN_EVENT, listener);
    try {
      await expect(mod.request("/agents/a1/status")).rejects.toThrow();
      expect(listener).toHaveBeenCalledOnce();
      expect(mod.getAuthToken()).toBe("current-account-renewed");
    } finally {
      window.removeEventListener(mod.FORBIDDEN_EVENT, listener);
    }
  });

  it.each([401, 200])(
    "upload: a late %i response cannot alter the new account",
    async (status) => {
      class DeferredUpload {
        static current: DeferredUpload;
        status = 200;
        responseText = "{}";
        upload = { onprogress: null };
        onload: (() => void) | null = null;
        onerror: (() => void) | null = null;
        onabort: (() => void) | null = null;
        constructor() {
          DeferredUpload.current = this;
        }
        open() {}
        setRequestHeader() {}
        getResponseHeader() {
          return "account-a-renewed";
        }
        send() {}
        abort() {
          this.onabort?.();
        }
      }
      vi.stubGlobal("XMLHttpRequest", DeferredUpload);
      const pending = mod.requestUpload("/projects/p1/images", new FormData());
      const outcome =
        status === 401
          ? expect(pending).rejects.toThrow()
          : expect(pending).resolves.toEqual({});
      mod.setAuthToken("account-b-token");
      DeferredUpload.current.status = status;
      DeferredUpload.current.onload?.();
      await outcome;
      expect(mod.getAuthToken()).toBe("account-b-token");
      expect(replace).not.toHaveBeenCalled();
    },
  );
});
