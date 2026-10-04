import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  resetSessionStoreForTests,
  sortSessions,
  toSession,
  useSessions,
  syncSessionArtifacts,
  type Session,
} from "./useSessions";
import { emitSessionEvent } from "./chatStore";
import { createInstance, type TFunction } from "i18next";

const listMock = vi.fn();
const { createMock, patchMock, renameMock, errorMock, translationState } =
  vi.hoisted(() => ({
    createMock: vi.fn(),
    patchMock: vi.fn(),
    renameMock: vi.fn(),
    errorMock: vi.fn(),
    translationState: { t: undefined as TFunction | undefined },
  }));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: translationState.t ?? ((key: string) => key) }),
}));
vi.mock("../../../utils/antdMessage", () => ({
  message: { error: errorMock },
}));

vi.mock("../../../api/modules/octopThreads", () => ({
  octopThreadsApi: {
    list: (...args: unknown[]) => listMock(...args),
    create: (...args: unknown[]) => createMock(...args),
    delete: vi.fn(),
    patch: (...args: unknown[]) => patchMock(...args),
    rename: (...args: unknown[]) => renameMock(...args),
    rebind: vi.fn(),
  },
}));

function threadRow(threadId: string, agentExtra?: Partial<{ title: string }>) {
  return {
    thread_id: threadId,
    title: agentExtra?.title ?? null,
    last_active: 1,
    created_at: 1,
    channel_type: "dashboard",
    is_active: false,
    has_messages: true,
    pinned: false,
  };
}

describe("toSession / sortSessions ordering", () => {
  it("sorts empty new chats by created_at above older active ones", () => {
    const olderActive = toSession({
      thread_id: "thr_old",
      title: "old",
      last_active: 100,
      created_at: 10,
      has_messages: true,
    });
    const emptyNew = toSession({
      thread_id: "thr_new",
      title: null,
      last_active: 0,
      created_at: 200,
      has_messages: false,
    });
    expect(
      sortSessions([olderActive, emptyNew]).map((s: Session) => s.id),
    ).toEqual(["thr_new", "thr_old"]);
  });

  it("keeps pinned sessions first", () => {
    const pinned = toSession({
      thread_id: "thr_pin",
      title: "pin",
      last_active: 1,
      created_at: 1,
      pinned: true,
    });
    const recent = toSession({
      thread_id: "thr_recent",
      title: "recent",
      last_active: 999,
      created_at: 999,
    });
    expect(sortSessions([recent, pinned]).map((s) => s.id)).toEqual([
      "thr_pin",
      "thr_recent",
    ]);
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

describe("session metadata persistence", () => {
  beforeEach(() => {
    resetSessionStoreForTests();
    translationState.t = undefined;
    listMock.mockReset();
    patchMock.mockReset();
    renameMock.mockReset();
    errorMock.mockReset();
    listMock.mockResolvedValue([
      threadRow("old", { title: "Old" }),
      threadRow("recent", { title: "Recent" }),
    ]);
  });
  afterEach(() => resetSessionStoreForTests());

  async function ready(options = {}) {
    const hook = renderHook(() => useSessions("a", options));
    await waitFor(() => expect(hook.result.current.loading).toBe(false));
    return hook;
  }
  const savedPin = { thread_id: "old", title: "Old", pinned: true };

  it("rolls back failed pin and order, reports once, and allows a retry", async () => {
    const request = deferred<typeof savedPin>();
    patchMock.mockReturnValueOnce(request.promise);
    const { result } = await ready();
    const originalOrder = result.current.sessions.map((s) => s.id);
    let pending: unknown;
    act(() => {
      pending = result.current.pinSession("old", true);
    });
    expect(result.current.sessions[0].id).toBe("old");
    await act(async () => {
      request.reject(new Error("offline"));
      await pending;
    });
    expect(result.current.sessions.find((s) => s.id === "old")?.pinned).toBe(
      false,
    );
    expect(result.current.sessions.map((s) => s.id)).toEqual(originalOrder);
    expect(await pending).toMatchObject({ status: "failed" });
    expect(errorMock).toHaveBeenCalledTimes(1);
    patchMock.mockResolvedValue(savedPin);
    await act(async () => {
      expect(await result.current.pinSession("old", true)).toMatchObject({
        status: "saved",
      });
    });
    expect(result.current.sessions[0].pinned).toBe(true);
  });

  it("rolls back rename alone while preserving a successful concurrent pin and artifacts", async () => {
    const request = deferred<typeof savedPin>();
    renameMock.mockReturnValue(request.promise);
    patchMock.mockResolvedValue(savedPin);
    const { result } = await ready();
    let pending: unknown;
    act(() => {
      pending = result.current.renameSession("old", "New");
    });
    await act(async () => {
      await result.current.pinSession("old", true);
      syncSessionArtifacts("old", ["new.txt"]);
    });
    await act(async () => {
      request.reject(new Error("rename failed"));
      await pending;
    });
    expect(result.current.sessions.find((s) => s.id === "old")).toMatchObject({
      name: "Old",
      pinned: true,
      artifacts: ["new.txt"],
    });
    expect(errorMock).toHaveBeenCalledTimes(1);
  });

  it("rolls back pin alone while preserving server-normalized rename", async () => {
    const request = deferred<typeof savedPin>();
    patchMock.mockReturnValue(request.promise);
    renameMock.mockResolvedValue({
      thread_id: "old",
      title: "Server title",
      pinned: false,
    });
    const { result } = await ready();
    let pending: unknown;
    act(() => {
      pending = result.current.pinSession("old", true);
    });
    await act(async () => {
      await result.current.renameSession("old", "Client title");
    });
    await act(async () => {
      request.reject(new Error("pin failed"));
      await pending;
    });
    expect(result.current.sessions.find((s) => s.id === "old")).toMatchObject({
      name: "Server title",
      pinned: false,
    });
  });

  it("does not merge the unrelated title from a pin response", async () => {
    const request = deferred<typeof savedPin>();
    patchMock.mockReturnValue(request.promise);
    renameMock.mockResolvedValue({
      thread_id: "old",
      title: "New",
      pinned: true,
    });
    const { result } = await ready();
    let pending: unknown;
    act(() => {
      pending = result.current.pinSession("old", true);
    });
    await act(async () => {
      await result.current.renameSession("old", "New");
      request.resolve(savedPin);
      await pending;
    });
    expect(result.current.sessions.find((s) => s.id === "old")).toMatchObject({
      name: "New",
      pinned: true,
    });
  });

  it.each([
    ["zh", "保存失败"],
    ["en", "Failed to save"],
  ])(
    "uses the real %s locale fallback for unknown persistence errors",
    async (language, text) => {
      const bundle =
        language === "zh"
          ? (await import("../../../locales/zh.json")).default
          : (await import("../../../locales/en.json")).default;
      const instance = createInstance();
      await instance.init({
        lng: language,
        resources: { [language]: { translation: bundle } },
        initImmediate: false,
      });
      translationState.t = instance.t.bind(instance);
      patchMock.mockRejectedValue({});
      const { result } = await ready();
      await act(async () => {
        expect(await result.current.pinSession("old", true)).toMatchObject({
          status: "failed",
        });
      });
      expect(errorMock).toHaveBeenCalledWith(text);
    },
  );

  it.each(["403", "404"])(
    "returns failed and restores the field on HTTP %s",
    async (status) => {
      renameMock.mockRejectedValue(new Error(status));
      const { result } = await ready();
      await act(async () => {
        expect(
          await result.current.renameSession("old", "Denied"),
        ).toMatchObject({ status: "failed" });
      });
      expect(
        result.current.sessions.find((session) => session.id === "old")?.name,
      ).toBe("Old");
      expect(errorMock).toHaveBeenCalledTimes(1);
    },
  );

  it("rejects same-field overlap without changing the first optimistic value", async () => {
    const request = deferred<typeof savedPin>();
    patchMock.mockReturnValue(request.promise);
    const { result } = await ready();
    let pending: unknown;
    act(() => {
      pending = result.current.pinSession("old", true);
    });
    await act(async () => {
      expect(await result.current.pinSession("old", false)).toEqual({
        status: "ignored",
        reason: "busy",
      });
    });
    expect(result.current.sessions.find((s) => s.id === "old")?.pinned).toBe(
      true,
    );
    expect(patchMock).toHaveBeenCalledTimes(1);
    await act(async () => {
      request.resolve(savedPin);
      await pending;
    });
  });

  it("ignores missing, pending, empty and unchanged targets but permits automatic internal title", async () => {
    const { result } = await ready({ internal: true });
    await act(async () => {
      for (const [id, title] of [
        ["missing", "New"],
        ["__pending__", "New"],
        ["old", "  "],
        ["old", "Old"],
      ]) {
        expect(await result.current.renameSession(id, title)).toMatchObject({
          status: "ignored",
        });
      }
    });
    expect(renameMock).not.toHaveBeenCalled();
    renameMock.mockResolvedValue({
      thread_id: "old",
      title: "Automatic",
      pinned: false,
    });
    await act(async () => {
      expect(
        await result.current.renameSession("old", "Automatic"),
      ).toMatchObject({ status: "saved" });
    });
    expect(renameMock).toHaveBeenCalledWith("a", "old", "Automatic");
  });

  it.each([
    "fetchSessions",
    "loadMoreSessions",
    "fetchAllSessions",
    "ensureThreadInList",
  ] as const)(
    "protects a saved field from an earlier %s response and permits a fresh read",
    async (operation) => {
      const rows = Array.from({ length: 21 }, (_, i) => ({
        ...threadRow(i === 0 ? "old" : `thread-${i}`, {
          title: i === 0 ? "Old" : `Row ${i}`,
        }),
        last_active: i === 0 ? 100 : 1,
      }));
      listMock.mockResolvedValueOnce(rows.slice(0, 11));
      const read = deferred<typeof rows>();
      listMock.mockReturnValueOnce(read.promise);
      renameMock.mockResolvedValue({ thread_id: "old", title: "New" });
      const { result } = await ready();
      let pending: unknown;
      act(() => {
        pending = result.current[operation]("thread-15");
      });
      await act(async () => {
        await result.current.renameSession("old", "New");
        read.resolve(rows);
        await pending;
      });
      expect(result.current.sessions.find((s) => s.id === "old")?.name).toBe(
        "New",
      );
      if (operation === "loadMoreSessions")
        expect(result.current.sessions).toHaveLength(20);
      listMock.mockResolvedValue([threadRow("old", { title: "External" })]);
      await act(async () => {
        await result.current.fetchSessions();
      });
      expect(result.current.sessions.find((s) => s.id === "old")?.name).toBe(
        "External",
      );
    },
  );

  it("keeps a pending optimistic field across a read and still rolls back", async () => {
    const request = deferred<typeof savedPin>();
    patchMock.mockReturnValue(request.promise);
    const { result } = await ready();
    let pending: unknown;
    act(() => {
      pending = result.current.pinSession("old", true);
    });
    await act(async () => {
      await result.current.fetchSessions();
    });
    expect(result.current.sessions.find((s) => s.id === "old")?.pinned).toBe(
      true,
    );
    await act(async () => {
      request.reject(new Error("offline"));
      await pending;
    });
    expect(result.current.sessions.find((s) => s.id === "old")?.pinned).toBe(
      false,
    );
  });

  it("does not allow earlier reads to overwrite a newer published page", async () => {
    const { result } = await ready();
    const first = deferred<ReturnType<typeof threadRow>[]>();
    listMock
      .mockReturnValueOnce(first.promise)
      .mockResolvedValueOnce([threadRow("old", { title: "Latest" })]);
    let pending: unknown;
    act(() => {
      pending = result.current.fetchSessions();
    });
    await act(async () => {
      await result.current.fetchSessions();
      first.resolve([threadRow("old", { title: "Obsolete" })]);
      await pending;
    });
    expect(result.current.sessions[0].name).toBe("Latest");
  });

  it("fences old A writes and reads across A to B to A", async () => {
    const request = deferred<typeof savedPin>();
    patchMock.mockReturnValue(request.promise);
    const { result, rerender } = renderHook(
      ({ agentId }) => useSessions(agentId),
      { initialProps: { agentId: "a" } },
    );
    await waitFor(() => expect(result.current.loading).toBe(false));
    let pending: unknown;
    act(() => {
      pending = result.current.pinSession("old", true);
    });
    const read = deferred<ReturnType<typeof threadRow>[]>();
    listMock.mockReturnValueOnce(read.promise);
    let oldRead: unknown;
    act(() => {
      oldRead = result.current.fetchSessions();
    });
    rerender({ agentId: "b" });
    await waitFor(() => expect(result.current.loading).toBe(false));
    listMock.mockResolvedValue([threadRow("old", { title: "New A view" })]);
    rerender({ agentId: "a" });
    await waitFor(() =>
      expect(result.current.sessions[0]?.name).toBe("New A view"),
    );
    await act(async () => {
      request.reject(new Error("old failure"));
      read.resolve([threadRow("old", { title: "Obsolete A" })]);
      await pending;
      await oldRead;
    });
    expect(result.current.sessions[0]).toMatchObject({
      name: "New A view",
      pinned: false,
    });
    expect(errorMock).not.toHaveBeenCalled();
    expect(await pending).toEqual({ status: "ignored", reason: "stale" });
  });

  it("keeps the shared lane through unmount, releases only on settle, and resets it between tests", async () => {
    const request = deferred<typeof savedPin>();
    patchMock.mockReturnValueOnce(request.promise).mockResolvedValue(savedPin);
    const first = await ready();
    let pending: unknown;
    act(() => {
      pending = first.result.current.pinSession("old", true);
    });
    first.unmount();
    const second = await ready();
    await act(async () => {
      expect(await second.result.current.pinSession("old", true)).toMatchObject(
        { status: "ignored", reason: "busy" },
      );
    });
    await act(async () => {
      request.reject(new Error("obsolete"));
      await pending;
    });
    expect(errorMock).not.toHaveBeenCalled();
    await act(async () => {
      expect(await second.result.current.pinSession("old", true)).toMatchObject(
        { status: "saved" },
      );
    });
    second.unmount();
    resetSessionStoreForTests();
    const third = await ready();
    await act(async () => {
      expect(await third.result.current.pinSession("old", true)).toMatchObject({
        status: "saved",
      });
    });
  });

  it("does not invalidate an existing subscriber when another mounts or unmounts", async () => {
    const first = await ready();
    const request = deferred<typeof savedPin>();
    patchMock.mockReturnValue(request.promise);
    let pending: unknown;
    act(() => {
      pending = first.result.current.pinSession("old", true);
    });
    const second = await ready();
    second.unmount();
    await act(async () => {
      request.resolve(savedPin);
      expect(await pending).toMatchObject({ status: "saved" });
    });
    expect(
      first.result.current.sessions.find((s) => s.id === "old")?.pinned,
    ).toBe(true);
  });

  it("finishes shared initial loading when the initiating subscriber unmounts", async () => {
    const read = deferred<ReturnType<typeof threadRow>[]>();
    listMock.mockReturnValue(read.promise);
    const first = renderHook(() => useSessions("a"));
    const second = renderHook(() => useSessions("a"));
    first.unmount();
    await act(async () => read.resolve([threadRow("survivor")]));
    expect(second.result.current.loading).toBe(false);
    expect(second.result.current.sessions[0]?.id).toBe("survivor");
    expect(listMock).toHaveBeenCalledTimes(1);
  });

  it("does not let an old load-more finally clear a new agent's pending load", async () => {
    const aRows = Array.from({ length: 21 }, (_, i) => threadRow(`a-${i}`));
    const bRows = Array.from({ length: 21 }, (_, i) => threadRow(`b-${i}`));
    const oldRead = deferred<typeof aRows>();
    const newRead = deferred<typeof bRows>();
    listMock
      .mockReset()
      .mockResolvedValueOnce(aRows.slice(0, 11))
      .mockReturnValueOnce(oldRead.promise)
      .mockResolvedValueOnce(bRows.slice(0, 11))
      .mockReturnValueOnce(newRead.promise);
    const { result, rerender } = renderHook(
      ({ agentId }) => useSessions(agentId),
      { initialProps: { agentId: "a" } },
    );
    await waitFor(() => expect(result.current.loading).toBe(false));
    let oldLoad: unknown;
    act(() => {
      oldLoad = result.current.loadMoreSessions();
    });
    rerender({ agentId: "b" });
    await waitFor(() => expect(result.current.loading).toBe(false));
    let newLoad: unknown;
    act(() => {
      newLoad = result.current.loadMoreSessions();
    });
    await act(async () => {
      oldRead.resolve(aRows);
      await oldLoad;
    });
    expect(result.current.loadingMore).toBe(true);
    expect(
      result.current.sessions.every((session) => session.id.startsWith("b-")),
    ).toBe(true);
    await act(async () => {
      newRead.resolve(bRows);
      await newLoad;
    });
    expect(result.current.loadingMore).toBe(false);
    expect(result.current.sessions).toHaveLength(20);
  });

  it("retains a requested expansion even if a later smaller refresh publishes first", async () => {
    const rows = Array.from({ length: 21 }, (_, i) => ({
      ...threadRow(`thread-${String(i).padStart(2, "0")}`, { title: "Old" }),
      last_active: 100 - i,
    }));
    listMock.mockReset().mockResolvedValueOnce(rows.slice(0, 11));
    const { result } = await ready();
    const expanded = deferred<typeof rows>();
    listMock
      .mockReturnValueOnce(expanded.promise)
      .mockResolvedValueOnce(
        rows.slice(0, 11).map((row) => ({ ...row, title: "Fresh" })),
      );
    let pending: unknown;
    act(() => {
      pending = result.current.loadMoreSessions();
    });
    await act(async () => {
      await result.current.fetchSessions();
      expanded.resolve(rows);
      await pending;
    });
    expect(result.current.sessions).toHaveLength(20);
    expect(result.current.sessions[0].name).toBe("Fresh");
  });

  it("does not revive deleted rows from a pending mutation or old list", async () => {
    const request = deferred<typeof savedPin>();
    patchMock.mockReturnValue(request.promise);
    const { result } = await ready();
    let pending: unknown;
    act(() => {
      pending = result.current.pinSession("old", true);
    });
    const read = deferred<ReturnType<typeof threadRow>[]>();
    listMock.mockReturnValue(read.promise);
    let pendingRead: unknown;
    act(() => {
      pendingRead = result.current.fetchSessions();
      emitSessionEvent({
        kind: "sessionDeleted",
        sessionId: "old",
        agentId: "a",
      });
    });
    await act(async () => {
      request.reject(new Error("404"));
      read.resolve([threadRow("old")]);
      await pending;
      await pendingRead;
    });
    expect(result.current.sessions).toEqual([]);
    expect(errorMock).not.toHaveBeenCalled();
  });

  it("retains initial page, load-more and search limits and active probe semantics", async () => {
    const rows = Array.from({ length: 60 }, (_, i) =>
      threadRow(`thread-${String(i).padStart(2, "0")}`),
    );
    listMock.mockImplementation(async (_agent: string, limit: number) =>
      rows.slice(0, limit),
    );
    const { result } = await ready();
    expect(listMock).toHaveBeenLastCalledWith("a", 11);
    expect(result.current.sessions).toHaveLength(10);
    expect(result.current.hasMore).toBe(true);
    await act(async () => {
      await result.current.loadMoreSessions();
    });
    expect(listMock).toHaveBeenLastCalledWith("a", 21);
    expect(result.current.sessions).toHaveLength(20);
    await act(async () => {
      await result.current.fetchSessions();
    });
    expect(listMock).toHaveBeenLastCalledWith("a", 21);
    await act(async () => {
      await result.current.fetchAllSessions();
    });
    expect(listMock).toHaveBeenLastCalledWith("a", 51);
    expect(result.current.sessions).toHaveLength(50);
    expect(result.current.hasMore).toBe(true);
  });
});

describe("useSessions agent switch", () => {
  beforeEach(() => {
    resetSessionStoreForTests();
    listMock.mockReset();
    createMock.mockReset();
  });

  afterEach(() => {
    resetSessionStoreForTests();
  });

  it("clears previous-agent threads on the first render of a new agent", async () => {
    listMock.mockImplementation(async (agentId: string) => {
      if (agentId === "agent-a") {
        return [threadRow("thr_from_a")];
      }
      // Keep B's fetch pending so we can observe the sync clear.
      return new Promise(() => {});
    });

    const { result, rerender } = renderHook(
      ({ agentId }: { agentId: string | null }) => useSessions(agentId),
      { initialProps: { agentId: "agent-a" } },
    );

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
      expect(result.current.sessions.map((s) => s.id)).toEqual(["thr_from_a"]);
    });

    rerender({ agentId: "agent-b" });

    // Critical: no one-frame leak of agent-a's thr_* into agent-b.
    expect(result.current.sessions).toEqual([]);
    expect(result.current.loading).toBe(true);
    expect(result.current.sessions.some((s) => s.id === "thr_from_a")).toBe(
      false,
    );
  });

  it("ensureThreadInList returns unknown on probe network errors", async () => {
    listMock
      .mockResolvedValueOnce([]) // initial fetch for agent
      .mockRejectedValueOnce(new Error("network down")); // probe

    const { result } = renderHook(() => useSessions("agent-new"));

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    let probe: string | undefined;
    await act(async () => {
      probe = await result.current.ensureThreadInList("thr_foreign");
    });
    expect(probe).toBe("unknown");
  });

  it("ensureThreadInList returns missing when probe confirms absence", async () => {
    listMock.mockResolvedValue([]);

    const { result } = renderHook(() => useSessions("agent-new"));

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    let probe: string | undefined;
    await act(async () => {
      probe = await result.current.ensureThreadInList("thr_foreign");
    });
    expect(probe).toBe("missing");
  });

  it("ensureThreadInList returns found when the thread is already listed", async () => {
    listMock.mockResolvedValue([threadRow("thr_ok")]);

    const { result } = renderHook(() => useSessions("agent-new"));

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
      expect(result.current.sessions.map((s) => s.id)).toEqual(["thr_ok"]);
    });

    let probe: string | undefined;
    await act(async () => {
      probe = await result.current.ensureThreadInList("thr_ok");
    });
    expect(probe).toBe("found");
    expect(listMock).toHaveBeenCalledTimes(1);
  });

  it("never creates a new thread for an internal runtime", async () => {
    listMock.mockResolvedValue([]);
    const { result } = renderHook(() =>
      useSessions("runtime-1", { internal: true }),
    );

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    const { session, resolvedId } = result.current.createSession();
    expect(session.id).toBe("");
    await expect(resolvedId).resolves.toBe("");
    expect(createMock).not.toHaveBeenCalled();
  });

  it("still creates a new thread for an ordinary agent", async () => {
    listMock.mockResolvedValue([]);
    createMock.mockResolvedValue({
      thread_id: "thr_new",
      session_key: "agent-new:dashboard:1:dm",
    });
    const { result } = renderHook(() => useSessions("agent-new"));

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    await act(async () => {
      const { resolvedId } = result.current.createSession();
      await expect(resolvedId).resolves.toBe("thr_new");
    });
    expect(createMock).toHaveBeenCalledWith("agent-new");
  });

  it("never rewrites an explicit internal thread from a list gap", async () => {
    listMock.mockResolvedValue([]);
    const { result } = renderHook(() =>
      useSessions("runtime-1", { internal: true }),
    );

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    let probe: string | undefined;
    await act(async () => {
      probe = await result.current.ensureThreadInList("thr_private");
    });
    // "unknown" keeps the URL; "missing" would redirect to a blank chat.
    expect(probe).toBe("unknown");
  });
});
