import { renderHook, act, waitFor } from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useCallback, useRef, type ReactNode } from "react";
import { useChatSessionActions } from "./useChatSessionActions";
import { useChatNavigation, useChatNavigationOwner } from "./useChatNavigation";
import { octopThreadsApi } from "../../../api/modules/octopThreads";
import { toSession } from "./useSessions";
import {
  appendUserMessage,
  getSnapshot,
  removeSession,
  type ChatMessage,
} from "./chatStore";
import { EMPTY_CHAT_SESSION_KEY } from "../constants";

const navigateMock = vi.fn();
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
    rebind: vi.fn().mockResolvedValue({}),
    list: vi.fn().mockResolvedValue([]),
  },
}));
vi.mock("../../../api/modules/octopAgents", () => ({
  octopAgentsApi: { markRead: vi.fn().mockResolvedValue(undefined) },
}));

function wrapper({ children }: { children: ReactNode }) {
  return <MemoryRouter>{children}</MemoryRouter>;
}

const THREAD = "thr_leaving";

function makeMessage(id: string): ChatMessage {
  return { id, role: "user", content: `message ${id}`, timestamp: Date.now() };
}

function renderActions() {
  return renderHook(
    () => {
      const navigationOwner = useChatNavigationOwner(
        useLocation(),
        "agent-a",
        THREAD,
      );
      return useChatSessionActions({
        navigationOwner,
        resolvedAgentId: "agent-a",
        activeThreadId: THREAD,
        sessions: [],
        isMobile: false,
        setActiveAgent: vi.fn(),
        setSidebarOpen: vi.fn(),
        setSelectedModel: vi.fn(),
        setHasBrowserTool: vi.fn(),
        deleteSession: vi.fn().mockResolvedValue(true),
        clearMessages: vi.fn(),
        resetNavForAgentSwitch: vi.fn(),
        markInitialNavDone: vi.fn(),
      });
    },
    { wrapper },
  );
}

describe("navigateToAgent", () => {
  beforeEach(() => {
    routing.forward = false;
  });
  afterEach(() => {
    removeSession(THREAD);
    removeSession(EMPTY_CHAT_SESSION_KEY);
    vi.clearAllMocks();
  });

  it("clears the new-chat view without wiping the thread being left", async () => {
    appendUserMessage(THREAD, makeMessage("m1"));
    appendUserMessage(EMPTY_CHAT_SESSION_KEY, makeMessage("draft"));

    const { result } = renderActions();
    await act(async () => {
      result.current.navigateToAgent("agent-b");
    });

    expect(getSnapshot(THREAD).messages).toHaveLength(1);
    expect(getSnapshot(EMPTY_CHAT_SESSION_KEY).messages).toHaveLength(0);
  });
});

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((ok, fail) => {
    resolve = ok;
    reject = fail;
  });
  return { promise, resolve, reject };
}
const metadata = (id: string) => ({
  thread_id: id,
  title: id,
  last_active: 1,
  created_at: 1,
});
function realWrapper({ children }: { children: ReactNode }) {
  return (
    <MemoryRouter initialEntries={[`/chat/agent-a/${THREAD}`]}>
      {children}
    </MemoryRouter>
  );
}
function jointActions(deleteSession = vi.fn().mockResolvedValue(true)) {
  return renderHook(
    () => {
      const location = useLocation();
      const [, , agent, thread] = location.pathname.split("/");
      const owner = useChatNavigationOwner(location, agent, thread);
      const prefill = useRef("");
      const normalSessions = [toSession(metadata(`${agent}-normal`))];
      const nav = useChatNavigation({
        navigationOwner: owner,
        routeAgentId: agent,
        threadId: thread,
        resolvedAgentId: agent,
        activeThreadId: thread ?? null,
        sessions: normalSessions,
        sessionsLoading: false,
        prefillInputRef: prefill,
        loadHistory: vi.fn().mockResolvedValue(undefined),
        clearMessages: vi.fn(),
        ensureThreadInList: vi.fn().mockResolvedValue("unknown"),
        fetchSessions: vi.fn().mockResolvedValue([]),
        refreshAgents: vi.fn().mockResolvedValue(undefined),
      });
      const actions = useChatSessionActions({
        navigationOwner: owner,
        resolvedAgentId: agent,
        activeThreadId: thread ?? null,
        sessions: normalSessions,
        isMobile: false,
        setActiveAgent: vi.fn(),
        setSidebarOpen: vi.fn(),
        setSelectedModel: vi.fn(),
        setHasBrowserTool: vi.fn(),
        deleteSession,
        clearMessages: vi.fn(),
        ...nav,
      });
      return { actions, owner, path: location.pathname };
    },
    { wrapper: realWrapper },
  );
}

describe("shared Chat navigation owner with real routes", () => {
  beforeEach(() => {
    routing.forward = true;
    vi.mocked(octopThreadsApi.list).mockReset();
    vi.mocked(octopThreadsApi.rebind).mockClear();
    navigateMock.mockClear();
  });
  afterEach(() => {
    routing.forward = false;
  });

  it("allows the own blank-route ack and does not let automatic initial selection race it", async () => {
    const response = deferred<ReturnType<typeof metadata>[]>();
    vi.mocked(octopThreadsApi.list).mockReturnValue(response.promise);
    const { result } = jointActions();
    act(() => result.current.actions.navigateToAgent("agent-b"));
    await waitFor(() => expect(result.current.path).toBe("/chat/agent-b"));
    expect(octopThreadsApi.rebind).not.toHaveBeenCalled();
    await act(async () => {
      response.resolve([metadata("chosen-b")]);
    });
    expect(result.current.path).toBe("/chat/agent-b/chosen-b");
    expect(octopThreadsApi.rebind).toHaveBeenCalledExactlyOnceWith(
      "agent-b",
      "chosen-b",
    );
  });

  it("releases a failed preferred read for the latest normal-list fallback", async () => {
    const response = deferred<ReturnType<typeof metadata>[]>();
    vi.mocked(octopThreadsApi.list).mockReturnValue(response.promise);
    const { result } = jointActions();
    act(() => result.current.actions.navigateToAgent("agent-b"));
    await act(async () => {
      response.reject(new Error("offline"));
    });
    await waitFor(() =>
      expect(result.current.path).toBe("/chat/agent-b/agent-b-normal"),
    );
    expect(octopThreadsApi.rebind).toHaveBeenCalledExactlyOnceWith(
      "agent-b",
      "agent-b-normal",
    );
  });

  it.each(["select", "new", "active", "unmount"])(
    "blocks a late preferred read after %s",
    async (next) => {
      const response = deferred<ReturnType<typeof metadata>[]>();
      vi.mocked(octopThreadsApi.list).mockReturnValue(response.promise);
      const hook = jointActions();
      act(() => hook.result.current.actions.navigateToAgent("agent-b"));
      if (next === "select")
        act(() =>
          hook.result.current.actions.handleSelectSession("new-choice"),
        );
      if (next === "new")
        act(() => hook.result.current.actions.handleNewChat());
      if (next === "active")
        act(() =>
          hook.result.current.actions.handleSelectSession("new-choice"),
        );
      if (next === "active")
        act(() =>
          hook.result.current.actions.handleSelectSession("new-choice"),
        );
      if (next === "unmount") hook.unmount();
      vi.mocked(octopThreadsApi.rebind).mockClear();
      navigateMock.mockClear();
      await act(async () => {
        response.resolve([metadata("old-b")]);
      });
      expect(octopThreadsApi.rebind).not.toHaveBeenCalled();
      expect(navigateMock).not.toHaveBeenCalled();
    },
  );

  it("does not navigate back after a real deletion finishes under a newer selection", async () => {
    const deletion = deferred<boolean>();
    const { result } = jointActions(vi.fn().mockReturnValue(deletion.promise));
    let pending: unknown;
    act(() => {
      pending = result.current.actions.handleDeleteSession(THREAD);
    });
    act(() => result.current.actions.handleSelectSession("next"));
    await act(async () => {
      deletion.resolve(true);
      await pending;
    });
    expect(result.current.path).toBe("/chat/agent-a/next");
  });

  it("marks a successful empty expert list complete and keeps the blank chat", async () => {
    const response = deferred<ReturnType<typeof metadata>[]>();
    vi.mocked(octopThreadsApi.list).mockReturnValue(response.promise);
    const { result } = jointActions();
    act(() => result.current.actions.navigateToAgent("agent-b"));
    await act(async () => {
      response.resolve([]);
    });
    expect(result.current.path).toBe("/chat/agent-b");
    expect(octopThreadsApi.rebind).not.toHaveBeenCalled();
  });

  it("does not resurrect an old preferred intent when returning to the same expert", async () => {
    const first = deferred<ReturnType<typeof metadata>[]>();
    const latest = deferred<ReturnType<typeof metadata>[]>();
    vi.mocked(octopThreadsApi.list)
      .mockReturnValueOnce(first.promise)
      .mockReturnValueOnce(latest.promise);
    const { result } = jointActions();
    act(() => result.current.actions.navigateToAgent("agent-b"));
    act(() => result.current.actions.navigateToAgent("agent-a"));
    navigateMock.mockClear();
    await act(async () => {
      first.resolve([metadata("old-b")]);
    });
    expect(octopThreadsApi.rebind).not.toHaveBeenCalled();
    expect(navigateMock).not.toHaveBeenCalled();
    await act(async () => {
      latest.resolve([metadata("current-a")]);
    });
    expect(result.current.path).toBe("/chat/agent-a/current-a");
    expect(octopThreadsApi.rebind).toHaveBeenCalledExactlyOnceWith(
      "agent-a",
      "current-a",
    );
  });

  it("invalidates a pending preferred read even when the user clicks the already active row", async () => {
    routing.forward = false;
    const response = deferred<ReturnType<typeof metadata>[]>();
    vi.mocked(octopThreadsApi.list).mockReturnValue(response.promise);
    const { result } = renderActions();
    act(() => result.current.navigateToAgent("agent-b"));
    act(() => result.current.handleSelectSession(THREAD));
    navigateMock.mockClear();
    await act(async () => {
      response.resolve([metadata("old-b")]);
    });
    expect(octopThreadsApi.rebind).not.toHaveBeenCalled();
    expect(navigateMock).not.toHaveBeenCalled();
  });
});
