import { act, renderHook, waitFor } from "@testing-library/react";
import { MemoryRouter, useLocation, useNavigate } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { StrictMode, useCallback, type ReactNode } from "react";
import { useChatNavigation, useChatNavigationOwner } from "./useChatNavigation";
import { useChatSessionActions } from "./useChatSessionActions";
import type { Session } from "./useSessions";
import { octopAgentsApi } from "../../../api/modules/octopAgents";

const navigateMock = vi.fn();
const rebindMock = vi.fn().mockResolvedValue({});
const { routing } = vi.hoisted(() => ({ routing: { forward: false } }));

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual<typeof import("react-router-dom")>(
    "react-router-dom",
  );
  return {
    ...actual,
    useNavigate: () => {
      const navigate = actual.useNavigate();
      return useCallback(
        (path: string, options?: Parameters<typeof navigate>[1]) => {
          navigateMock(path, options);
          if (routing.forward) navigate(path, options);
        },
        [navigate],
      );
    },
  };
});

vi.mock("../../../api/modules/octopThreads", () => ({
  octopThreadsApi: {
    rebind: (...args: unknown[]) => rebindMock(...args),
  },
}));

vi.mock("../../../api/modules/octopAgents", () => ({
  octopAgentsApi: {
    markRead: vi.fn().mockResolvedValue(undefined),
  },
}));

type StoreSessionEvent = { kind: string; sessionId: string; agentId?: string };
const sessionListenerRef: {
  current: ((event: StoreSessionEvent) => void) | null;
} = { current: null };
const invalidateHistoryMock = vi.fn();

vi.mock("./chatStore", () => ({
  getSnapshot: () => ({ messages: [], isStreaming: false }),
  onStreamEvent: () => () => undefined,
  onSessionEvent: (listener: (event: StoreSessionEvent) => void) => {
    sessionListenerRef.current = listener;
    return () => {
      sessionListenerRef.current = null;
    };
  },
  invalidateHistory: (...args: unknown[]) => invalidateHistoryMock(...args),
}));

function wrapper({ children }: { children: ReactNode }) {
  return <MemoryRouter>{children}</MemoryRouter>;
}

function session(id: string): Session {
  return {
    id,
    name: "Chat",
    threadId: id,
    updatedAt: null,
    channelType: "dashboard",
    hasActivity: true,
  };
}

function useTestNavigation(
  params: Omit<Parameters<typeof useChatNavigation>[0], "navigationOwner">,
) {
  const owner = useChatNavigationOwner(
    useLocation(),
    params.routeAgentId,
    params.threadId,
  );
  return useChatNavigation({ ...params, navigationOwner: owner });
}

describe("useChatNavigation stale thread", () => {
  beforeEach(() => {
    navigateMock.mockReset();
    rebindMock.mockReset().mockResolvedValue({});
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("loads an existing private thread without marking its runtime as an ordinary agent", async () => {
    const loadHistory = vi.fn().mockResolvedValue(undefined);
    const refreshAgents = vi.fn().mockResolvedValue(undefined);
    renderHook(
      () =>
        useTestNavigation({
          routeAgentId: "runtime-private",
          threadId: "thread-existing",
          resolvedAgentId: "runtime-private",
          activeThreadId: "thread-existing",
          sessions: [session("thread-existing")],
          sessionsLoading: false,
          prefillInputRef: { current: "" },
          loadHistory,
          clearMessages: vi.fn(),
          ensureThreadInList: vi.fn().mockResolvedValue("found"),
          fetchSessions: vi.fn().mockResolvedValue([]),
          refreshAgents,
          internalTask: true,
        }),
      { wrapper },
    );

    await waitFor(() =>
      expect(loadHistory).toHaveBeenCalledWith("thread-existing"),
    );
    expect(octopAgentsApi.markRead).not.toHaveBeenCalled();
  });

  it("rewrites URL only after ensureThreadInList confirms missing", async () => {
    const ensureThreadInList = vi.fn().mockResolvedValue("missing");
    const prefillInputRef = { current: "" };

    renderHook(
      () =>
        useTestNavigation({
          routeAgentId: "agent-new",
          threadId: "thr_foreign",
          resolvedAgentId: "agent-new",
          activeThreadId: "thr_foreign",
          sessions: [],
          sessionsLoading: false,
          prefillInputRef,
          loadHistory: vi.fn().mockResolvedValue(undefined),
          clearMessages: vi.fn(),
          ensureThreadInList,
          fetchSessions: vi.fn().mockResolvedValue([]),
          refreshAgents: vi.fn().mockResolvedValue(undefined),
        }),
      { wrapper },
    );

    await waitFor(() => {
      expect(ensureThreadInList).toHaveBeenCalledWith("thr_foreign");
      expect(navigateMock).toHaveBeenCalledWith("/chat/agent-new", {
        replace: true,
      });
    });
  });

  it("does not rewrite URL when probe result is unknown", async () => {
    const ensureThreadInList = vi.fn().mockResolvedValue("unknown");
    const prefillInputRef = { current: "" };

    renderHook(
      () =>
        useTestNavigation({
          routeAgentId: "agent-new",
          threadId: "thr_maybe",
          resolvedAgentId: "agent-new",
          activeThreadId: "thr_maybe",
          sessions: [],
          sessionsLoading: false,
          prefillInputRef,
          loadHistory: vi.fn().mockResolvedValue(undefined),
          clearMessages: vi.fn(),
          ensureThreadInList,
          fetchSessions: vi.fn().mockResolvedValue([]),
          refreshAgents: vi.fn().mockResolvedValue(undefined),
        }),
      { wrapper },
    );

    await waitFor(() => {
      expect(ensureThreadInList).toHaveBeenCalledWith("thr_maybe");
    });
    expect(navigateMock).not.toHaveBeenCalledWith(
      "/chat/agent-new",
      expect.anything(),
    );
  });

  it("does not rewrite URL when probe finds the thread", async () => {
    const ensureThreadInList = vi.fn().mockResolvedValue("found");
    const prefillInputRef = { current: "" };

    renderHook(
      () =>
        useTestNavigation({
          routeAgentId: "agent-new",
          threadId: "thr_ok",
          resolvedAgentId: "agent-new",
          activeThreadId: "thr_ok",
          sessions: [],
          sessionsLoading: false,
          prefillInputRef,
          loadHistory: vi.fn().mockResolvedValue(undefined),
          clearMessages: vi.fn(),
          ensureThreadInList,
          fetchSessions: vi.fn().mockResolvedValue([]),
          refreshAgents: vi.fn().mockResolvedValue(undefined),
        }),
      { wrapper },
    );

    await waitFor(() => {
      expect(ensureThreadInList).toHaveBeenCalledWith("thr_ok");
    });
    expect(navigateMock).not.toHaveBeenCalled();
  });

  it("prefers an existing session when the URL thread is missing", async () => {
    const ensureThreadInList = vi.fn().mockResolvedValue("missing");
    const prefillInputRef = { current: "" };
    const sessions = [session("thr_ok")];

    renderHook(
      () =>
        useTestNavigation({
          routeAgentId: "agent-a",
          threadId: "thr_gone",
          resolvedAgentId: "agent-a",
          activeThreadId: "thr_gone",
          sessions,
          sessionsLoading: false,
          prefillInputRef,
          loadHistory: vi.fn().mockResolvedValue(undefined),
          clearMessages: vi.fn(),
          ensureThreadInList,
          fetchSessions: vi.fn().mockResolvedValue(sessions),
          refreshAgents: vi.fn().mockResolvedValue(undefined),
        }),
      { wrapper },
    );

    await waitFor(() => {
      expect(navigateMock).toHaveBeenCalledWith("/chat/agent-a/thr_ok", {
        replace: true,
      });
    });
  });

  it("uses the latest normal preferred row when a current missing probe settles after refresh", async () => {
    let resolve!: (result: "missing") => void;
    const probe = new Promise<"missing">((done) => {
      resolve = done;
    });
    const ensureThreadInList = vi
      .fn()
      .mockReturnValueOnce(probe)
      .mockResolvedValue("unknown");
    routing.forward = true;
    try {
      const { result, rerender } = routeProbeHook(ensureThreadInList);
      rerender({ rows: [session("new-preferred")] });
      await act(async () => {
        resolve("missing");
      });
      expect(result.current.path).toBe("/chat/agent-a/new-preferred");
      expect(rebindMock).toHaveBeenCalledExactlyOnceWith(
        "agent-a",
        "new-preferred",
      );
      expect(navigateMock).toHaveBeenCalledExactlyOnceWith(
        "/chat/agent-a/new-preferred",
        { replace: true },
      );
    } finally {
      routing.forward = false;
    }
  });
});

describe("useChatNavigation proactive session events", () => {
  beforeEach(() => {
    navigateMock.mockReset();
    rebindMock.mockReset().mockResolvedValue({});
    invalidateHistoryMock.mockReset();
    sessionListenerRef.current = null;
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  function renderWithMocks(
    loadHistory: ReturnType<typeof vi.fn>,
    fetchSessions: ReturnType<typeof vi.fn>,
  ) {
    const prefillInputRef = { current: "" };
    return renderHook(
      () =>
        useTestNavigation({
          routeAgentId: "agent-a",
          threadId: "thr_open",
          resolvedAgentId: "agent-a",
          activeThreadId: "thr_open",
          sessions: [session("thr_open")],
          sessionsLoading: false,
          prefillInputRef,
          loadHistory,
          clearMessages: vi.fn(),
          ensureThreadInList: vi.fn().mockResolvedValue("found"),
          fetchSessions,
          refreshAgents: vi.fn().mockResolvedValue(undefined),
        }),
      { wrapper },
    );
  }

  it("reloads history when the open thread changed server-side", async () => {
    const loadHistory = vi.fn().mockResolvedValue(undefined);
    const fetchSessions = vi.fn().mockResolvedValue([session("thr_open")]);
    renderWithMocks(loadHistory, fetchSessions);

    await waitFor(() => expect(sessionListenerRef.current).toBeTruthy());
    loadHistory.mockClear();
    fetchSessions.mockClear();

    sessionListenerRef.current?.({
      kind: "sessionsChanged",
      sessionId: "thr_open",
      agentId: "agent-a",
    });

    await waitFor(() => {
      expect(fetchSessions).toHaveBeenCalledWith("thr_open");
      expect(invalidateHistoryMock).toHaveBeenCalledWith("thr_open");
      expect(loadHistory).toHaveBeenCalledWith("thr_open");
    });
  });

  it("refreshes the list but not history for another thread", async () => {
    const loadHistory = vi.fn().mockResolvedValue(undefined);
    const fetchSessions = vi.fn().mockResolvedValue([session("thr_open")]);
    renderWithMocks(loadHistory, fetchSessions);

    await waitFor(() => expect(sessionListenerRef.current).toBeTruthy());
    loadHistory.mockClear();
    fetchSessions.mockClear();

    sessionListenerRef.current?.({
      kind: "sessionsChanged",
      sessionId: "thr_other",
      agentId: "agent-a",
    });

    await waitFor(() => {
      expect(fetchSessions).toHaveBeenCalledWith("thr_open");
    });
    expect(invalidateHistoryMock).not.toHaveBeenCalled();
    expect(loadHistory).not.toHaveBeenCalled();
  });

  it("ignores events for another agent and plain deletions", async () => {
    const loadHistory = vi.fn().mockResolvedValue(undefined);
    const fetchSessions = vi.fn().mockResolvedValue([session("thr_open")]);
    renderWithMocks(loadHistory, fetchSessions);

    await waitFor(() => expect(sessionListenerRef.current).toBeTruthy());
    loadHistory.mockClear();
    fetchSessions.mockClear();

    sessionListenerRef.current?.({
      kind: "sessionsChanged",
      sessionId: "thr_open",
      agentId: "agent-b",
    });
    sessionListenerRef.current?.({
      kind: "sessionDeleted",
      sessionId: "thr_open",
    });

    expect(fetchSessions).not.toHaveBeenCalled();
    expect(invalidateHistoryMock).not.toHaveBeenCalled();
    expect(loadHistory).not.toHaveBeenCalled();
  });
});

function deferredProbe() {
  let resolve!: (value: "missing" | "unknown" | "found") => void;
  const promise = new Promise<"missing" | "unknown" | "found">((done) => {
    resolve = done;
  });
  return { promise, resolve };
}
function routeWrapper({ children }: { children: ReactNode }) {
  return (
    <MemoryRouter initialEntries={["/chat/agent-a/thread-a"]}>
      {children}
    </MemoryRouter>
  );
}
function strictRouteWrapper({ children }: { children: ReactNode }) {
  return (
    <StrictMode>
      <MemoryRouter initialEntries={["/chat/agent-a/thread-a"]}>
        {children}
      </MemoryRouter>
    </StrictMode>
  );
}
function routeProbeHook(
  ensureThreadInList: ReturnType<typeof vi.fn>,
  strict = false,
) {
  const common = {
    sessions: [session("preferred")],
    sessionsLoading: false,
    prefillInputRef: { current: "" },
    loadHistory: vi.fn().mockResolvedValue(undefined),
    clearMessages: vi.fn(),
    ensureThreadInList,
    fetchSessions: vi.fn().mockResolvedValue([]),
    refreshAgents: vi.fn().mockResolvedValue(undefined),
  };
  return renderHook(
    ({ rows }) => {
      const location = useLocation();
      const go = useNavigate();
      const [, , agent, thread] = location.pathname.split("/");
      const owner = useChatNavigationOwner(location, agent, thread);
      const navigation = useChatNavigation({
        ...common,
        sessions: rows,
        navigationOwner: owner,
        routeAgentId: agent,
        threadId: thread,
        resolvedAgentId: agent,
        activeThreadId: thread ?? null,
      });
      const actions = useChatSessionActions({
        navigationOwner: owner,
        resolvedAgentId: agent,
        activeThreadId: thread ?? null,
        sessions: rows,
        isMobile: false,
        setActiveAgent: vi.fn(),
        setSidebarOpen: vi.fn(),
        setSelectedModel: vi.fn(),
        setHasBrowserTool: vi.fn(),
        deleteSession: vi.fn().mockResolvedValue(true),
        clearMessages: common.clearMessages,
        ...navigation,
      });
      return { go, owner, actions, path: location.pathname, key: location.key };
    },
    {
      wrapper: strict ? strictRouteWrapper : routeWrapper,
      initialProps: { rows: common.sessions },
    },
  );
}

describe("missing probe with actual route and lifetime ownership", () => {
  beforeEach(() => {
    routing.forward = true;
    navigateMock.mockClear();
    rebindMock.mockClear();
  });
  afterEach(() => {
    routing.forward = false;
  });

  it.each([
    ["missing", "missing"],
    ["unknown", "missing"],
    ["missing", "unknown"],
    ["unknown", "unknown"],
  ] as const)(
    "restarts the probe on an active-row click without changing route (%s -> %s)",
    async (oldResult, currentResult) => {
      const old = deferredProbe();
      const current = deferredProbe();
      const probe = vi
        .fn()
        .mockReturnValueOnce(old.promise)
        .mockReturnValueOnce(current.promise)
        .mockResolvedValue("unknown");
      const { result, rerender } = routeProbeHook(probe);
      const routeKey = result.current.key;
      expect(probe).toHaveBeenCalledTimes(1);
      act(() => result.current.actions.handleSelectSession("thread-a"));
      expect(result.current.path).toBe("/chat/agent-a/thread-a");
      expect(result.current.key).toBe(routeKey);
      expect(navigateMock).not.toHaveBeenCalled();
      expect(rebindMock).not.toHaveBeenCalled();
      expect(probe).toHaveBeenCalledTimes(2);

      await act(async () => {
        old.resolve(oldResult);
      });
      expect(result.current.path).toBe("/chat/agent-a/thread-a");
      expect(navigateMock).not.toHaveBeenCalled();
      expect(rebindMock).not.toHaveBeenCalled();

      rerender({ rows: [session("latest-preferred")] });
      expect(probe).toHaveBeenCalledTimes(2);
      await act(async () => {
        current.resolve(currentResult);
      });
      if (currentResult === "missing") {
        expect(result.current.path).toBe("/chat/agent-a/latest-preferred");
        expect(rebindMock).toHaveBeenCalledExactlyOnceWith(
          "agent-a",
          "latest-preferred",
        );
        expect(navigateMock).toHaveBeenCalledExactlyOnceWith(
          "/chat/agent-a/latest-preferred",
          { replace: true },
        );
      } else {
        expect(result.current.path).toBe("/chat/agent-a/thread-a");
        expect(result.current.key).toBe(routeKey);
        expect(navigateMock).not.toHaveBeenCalled();
        expect(rebindMock).not.toHaveBeenCalled();
      }
    },
  );

  it.each(["same-agent", "expert-ABA"])(
    "rejects old missing after %s route ABA and permits the current fallback",
    async (caseName) => {
      const first = deferredProbe();
      const second = deferredProbe();
      const third = deferredProbe();
      const probe = vi
        .fn()
        .mockReturnValueOnce(first.promise)
        .mockReturnValueOnce(second.promise)
        .mockReturnValueOnce(third.promise)
        .mockResolvedValue("unknown");
      const { result } = routeProbeHook(probe);
      act(() =>
        result.current.go(
          caseName === "same-agent"
            ? "/chat/agent-a/thread-b"
            : "/chat/agent-b/thread-b",
        ),
      );
      act(() => result.current.go("/chat/agent-a/thread-a"));
      expect(probe).toHaveBeenCalledTimes(3);
      navigateMock.mockClear();
      await act(async () => {
        first.resolve("missing");
        second.resolve("missing");
      });
      expect(result.current.path).toBe("/chat/agent-a/thread-a");
      expect(rebindMock).not.toHaveBeenCalled();
      expect(navigateMock).not.toHaveBeenCalled();
      await act(async () => {
        third.resolve("missing");
      });
      expect(result.current.path).toBe("/chat/agent-a/preferred");
      expect(rebindMock).toHaveBeenCalledExactlyOnceWith(
        "agent-a",
        "preferred",
      );
    },
  );

  it("treats a new same-URL location key as a new route read", async () => {
    const old = deferredProbe();
    const current = deferredProbe();
    const probe = vi
      .fn()
      .mockReturnValueOnce(old.promise)
      .mockReturnValueOnce(current.promise);
    const { result } = routeProbeHook(probe);
    act(() => result.current.go("/chat/agent-a/thread-a"));
    expect(probe).toHaveBeenCalledTimes(2);
    navigateMock.mockClear();
    await act(async () => {
      old.resolve("missing");
      current.resolve("unknown");
    });
    expect(rebindMock).not.toHaveBeenCalled();
    expect(navigateMock).not.toHaveBeenCalled();
    expect(result.current.path).toBe("/chat/agent-a/thread-a");
  });

  it("fences the first StrictMode lifetime while the replacement remains usable", async () => {
    const old = deferredProbe();
    const current = deferredProbe();
    const probe = vi
      .fn()
      .mockReturnValueOnce(old.promise)
      .mockReturnValueOnce(current.promise)
      .mockResolvedValue("unknown");
    const { result } = routeProbeHook(probe, true);
    expect(probe).toHaveBeenCalledTimes(2);
    await act(async () => {
      old.resolve("missing");
    });
    expect(rebindMock).not.toHaveBeenCalled();
    await act(async () => {
      current.resolve("missing");
    });
    expect(result.current.path).toBe("/chat/agent-a/preferred");
    expect(rebindMock).toHaveBeenCalledExactlyOnceWith("agent-a", "preferred");
  });

  it("does not rebind or navigate after final unmount", async () => {
    const old = deferredProbe();
    const hook = routeProbeHook(vi.fn().mockReturnValue(old.promise));
    hook.unmount();
    await act(async () => {
      old.resolve("missing");
    });
    expect(rebindMock).not.toHaveBeenCalled();
    expect(navigateMock).not.toHaveBeenCalled();
  });
});
