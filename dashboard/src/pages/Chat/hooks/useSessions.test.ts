import {
  act,
  render,
  renderHook,
  waitFor,
  screen,
  fireEvent,
} from "@testing-library/react";
import { createElement } from "react";
import DataManagement from "../../Settings/DataManagement";
import { CurrentUserProvider } from "../../../hooks/useCurrentUser";
import type { OctopUser } from "../../../api/modules/auth";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  resetSessionStoreForTests,
  sortSessions,
  toSession,
  useSessions,
  syncSessionArtifacts,
  syncSessionConversationMode,
  syncSessionHitlPolicy,
  persistSessionArchive,
  onArchiveSaved,
  archiveMutationPending,
  type Session,
} from "./useSessions";
import { emitSessionEvent } from "./chatStore";
import { createInstance, type TFunction } from "i18next";

const listMock = vi.fn();
const metadataMock = vi.fn();
const archiveMock = vi.fn();
const archivesListMock = vi.fn();
vi.mock("../../../hooks/useServerTimezone", () => ({
  useServerTimezone: () => "Asia/Shanghai",
}));
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
    metadata: (...args: unknown[]) => metadataMock(...args),
    setArchived: (...args: unknown[]) => archiveMock(...args),
    listArchived: (...args: unknown[]) => archivesListMock(...args),
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

describe("archived selected metadata and live archive lane", () => {
  beforeEach(() => {
    resetSessionStoreForTests();
    listMock.mockReset().mockResolvedValue([]);
    metadataMock.mockReset();
    archiveMock.mockReset();
  });
  afterEach(() => resetSessionStoreForTests());

  it("keeps a real pending lane across query changes and publishes data invalidation without stale UI success", async () => {
    const pending = deferred<{
      thread_id: string;
      agent_id: string;
      archived_at: null;
    }>();
    archiveMock.mockReturnValue(pending.promise);
    let current = true;
    const saved = vi.fn();
    const error = vi.fn();
    const unsubscribe = onArchiveSaved(saved);
    const request = persistSessionArchive({
      actorId: 1,
      agentId: "a",
      id: "t",
      archived: false,
      isCurrent: () => current,
      isIdentityCurrent: () => true,
      onError: error,
    });
    current = false;
    expect(archiveMutationPending(1, "a", "t")).toBe(true);
    expect(
      await persistSessionArchive({
        actorId: 1,
        agentId: "a",
        id: "t",
        archived: false,
        isCurrent: () => true,
        onError: error,
      }),
    ).toEqual({ status: "ignored", reason: "busy" });
    pending.resolve({ thread_id: "t", agent_id: "a", archived_at: null });
    expect(await request).toEqual({ status: "ignored", reason: "stale" });
    expect(saved).toHaveBeenCalledOnce();
    expect(error).not.toHaveBeenCalled();
    expect(archiveMutationPending(1, "a", "t")).toBe(false);
    unsubscribe();
  });

  it("does not publish an old actor receipt across actor ABA and retains its lane until settlement", async () => {
    const pending = deferred<{
      thread_id: string;
      agent_id: string;
      archived_at: number;
    }>();
    archiveMock.mockReturnValue(pending.promise);
    const events = vi.fn();
    const unsubscribe = onArchiveSaved(events);
    const { result, rerender } = renderHook(
      ({ actor }) => useSessions("a", { actorId: actor }),
      { initialProps: { actor: 1 } },
    );
    await waitFor(() => expect(result.current.loading).toBe(false));
    let request!: ReturnType<typeof persistSessionArchive>;
    act(() => {
      request = result.current.archiveSession("t", true);
    });
    rerender({ actor: 2 });
    rerender({ actor: 1 });
    expect(archiveMutationPending(1, "a", "t")).toBe(true);
    await act(async () => {
      pending.resolve({ thread_id: "t", agent_id: "a", archived_at: 100 });
      expect(await request).toEqual({ status: "ignored", reason: "stale" });
    });
    expect(events).not.toHaveBeenCalled();
    expect(archiveMutationPending(1, "a", "t")).toBe(false);
    unsubscribe();
  });

  it("retains the live write lane across unmount and rejects its receipt in a new lifetime", async () => {
    const pending = deferred<{
      thread_id: string;
      agent_id: string;
      archived_at: number;
    }>();
    archiveMock.mockReturnValue(pending.promise);
    const saved = vi.fn();
    const unsubscribe = onArchiveSaved(saved);
    const first = renderHook(() => useSessions("a", { actorId: 1 }));
    await waitFor(() => expect(first.result.current.loading).toBe(false));
    let request!: ReturnType<typeof persistSessionArchive>;
    act(() => {
      request = first.result.current.archiveSession("t", true);
    });
    first.unmount();
    const second = renderHook(() => useSessions("a", { actorId: 1 }));
    expect(archiveMutationPending(1, "a", "t")).toBe(true);
    await act(async () => {
      expect(await second.result.current.archiveSession("t", true)).toEqual({
        status: "ignored",
        reason: "busy",
      });
    });
    await act(async () => {
      pending.resolve({ thread_id: "t", agent_id: "a", archived_at: 100 });
      expect(await request).toEqual({ status: "ignored", reason: "stale" });
    });
    expect(saved).not.toHaveBeenCalled();
    expect(archiveMutationPending(1, "a", "t")).toBe(false);
    expect(archiveMock).toHaveBeenCalledOnce();
    second.unmount();
    unsubscribe();
  });

  it("settles independent rows in receipt order without unlocking the other lane", async () => {
    const first = deferred<{
      thread_id: string;
      agent_id: string;
      archived_at: number;
    }>();
    const second = deferred<{
      thread_id: string;
      agent_id: string;
      archived_at: null;
    }>();
    archiveMock
      .mockReturnValueOnce(first.promise)
      .mockReturnValueOnce(second.promise);
    const saved = vi.fn();
    const unsubscribe = onArchiveSaved(saved);
    const options = {
      actorId: 1,
      agentId: "a",
      isCurrent: () => true,
      onError: vi.fn(),
    };
    const p1 = persistSessionArchive({
      ...options,
      id: "first",
      archived: true,
    });
    const p2 = persistSessionArchive({
      ...options,
      id: "second",
      archived: false,
    });
    second.resolve({ thread_id: "second", agent_id: "a", archived_at: null });
    expect((await p2).status).toBe("saved");
    expect(archiveMutationPending(1, "a", "first")).toBe(true);
    first.resolve({ thread_id: "first", agent_id: "a", archived_at: 100 });
    expect((await p1).status).toBe("saved");
    expect(
      saved.mock.calls.map(([event]) => [
        event.id,
        event.archivedAt,
        event.revision,
      ]),
    ).toEqual([
      ["second", null, 1],
      ["first", 100, 2],
    ]);
    expect(archiveMutationPending(1, "a", "first")).toBe(false);
    unsubscribe();
  });

  it.each(["aba", "remount"] as const)(
    "updates the actual data page after a stale %s lane settles and permits a normal retry",
    async (mode) => {
      const item = {
        thread_id: "lane",
        agent_id: "stopped",
        title: "Lane record",
        channel_type: "dashboard",
        created_at: 1,
        last_active: 1,
        archived_at: 100,
        mode: "chat" as const,
      };
      const page = { items: [item], limit: 20, offset: 0, has_more: false };
      archivesListMock.mockReset().mockResolvedValue(page);
      errorMock.mockClear();
      const old = deferred<{
        thread_id: string;
        agent_id: string;
        archived_at: null;
      }>();
      archiveMock
        .mockReturnValueOnce(old.promise)
        .mockRejectedValueOnce(new Error("current failure"))
        .mockResolvedValue({
          thread_id: "lane",
          agent_id: "stopped",
          archived_at: null,
        });
      const saved = vi.fn();
      const unsubscribe = onArchiveSaved(saved);
      const tree = (id: number) =>
        createElement(CurrentUserProvider, {
          user: { id } as OctopUser,
          setUser: vi.fn(),
          children: createElement(DataManagement),
        });
      const first = render(tree(1));
      await screen.findByText("Lane record");
      fireEvent.click(
        screen.getByRole("button", { name: "dataManagement.restoreNamed" }),
      );
      let active = first;
      if (mode === "aba") {
        first.rerender(tree(2));
        await waitFor(() => expect(archivesListMock).toHaveBeenCalledTimes(2));
        first.rerender(tree(1));
        await waitFor(() => expect(archivesListMock).toHaveBeenCalledTimes(3));
      } else {
        first.unmount();
        active = render(tree(1));
        await waitFor(() => expect(archivesListMock).toHaveBeenCalledTimes(2));
      }
      const button = await screen.findByRole("button", {
        name: "dataManagement.restoreNamed",
      });
      expect(button).toBeDisabled();
      fireEvent.click(button);
      expect(archiveMock).toHaveBeenCalledTimes(1);
      await act(async () => {
        old.resolve({
          thread_id: "lane",
          agent_id: "stopped",
          archived_at: null,
        });
      });
      expect(archiveMutationPending(1, "stopped", "lane")).toBe(false);
      expect(saved).not.toHaveBeenCalled();
      expect(errorMock).not.toHaveBeenCalled();
      await waitFor(() =>
        expect(
          screen.getByRole("button", { name: "dataManagement.restoreNamed" }),
        ).toBeEnabled(),
      );
      fireEvent.click(
        screen.getByRole("button", { name: "dataManagement.restoreNamed" }),
      );
      await screen.findByText("dataManagement.restoreFailed");
      expect(
        screen.getByRole("button", { name: "dataManagement.restoreNamed" }),
      ).toBeEnabled();
      archivesListMock.mockResolvedValue({ ...page, items: [] });
      fireEvent.click(
        screen.getByRole("button", { name: "dataManagement.restoreNamed" }),
      );
      await waitFor(() =>
        expect(screen.queryByText("Lane record")).not.toBeInTheDocument(),
      );
      expect(archiveMock).toHaveBeenCalledTimes(3);
      expect(saved).toHaveBeenCalledOnce();
      active.unmount();
      unsubscribe();
    },
  );

  it.each(["aba", "remount"] as const)(
    "releases an old failed %s lane without publishing its failure into the actual data page",
    async (mode) => {
      const item = {
        thread_id: "failed-lane",
        agent_id: "stopped",
        title: "Failed lane",
        channel_type: "dashboard",
        created_at: 1,
        last_active: 1,
        archived_at: 100,
        mode: "chat" as const,
      };
      const page = { items: [item], limit: 20, offset: 0, has_more: false };
      archivesListMock.mockReset().mockResolvedValue(page);
      const old = deferred<{
        thread_id: string;
        agent_id: string;
        archived_at: null;
      }>();
      archiveMock.mockReturnValueOnce(old.promise);
      errorMock.mockClear();
      const saved = vi.fn();
      const unsubscribe = onArchiveSaved(saved);
      const tree = (id: number) =>
        createElement(CurrentUserProvider, {
          user: { id } as OctopUser,
          setUser: vi.fn(),
          children: createElement(DataManagement),
        });
      const first = render(tree(1));
      await screen.findByText("Failed lane");
      fireEvent.click(
        screen.getByRole("button", { name: "dataManagement.restoreNamed" }),
      );
      let active = first;
      if (mode === "aba") {
        first.rerender(tree(2));
        await waitFor(() => expect(archivesListMock).toHaveBeenCalledTimes(2));
        first.rerender(tree(1));
        await waitFor(() => expect(archivesListMock).toHaveBeenCalledTimes(3));
      } else {
        first.unmount();
        active = render(tree(1));
        await waitFor(() => expect(archivesListMock).toHaveBeenCalledTimes(2));
      }
      expect(
        await screen.findByRole("button", {
          name: "dataManagement.restoreNamed",
        }),
      ).toBeDisabled();
      await act(async () => {
        old.reject(new Error("old failure"));
      });
      expect(archiveMutationPending(1, "stopped", "failed-lane")).toBe(false);
      await waitFor(() =>
        expect(
          screen.getByRole("button", { name: "dataManagement.restoreNamed" }),
        ).toBeEnabled(),
      );
      expect(saved).not.toHaveBeenCalled();
      expect(errorMock).not.toHaveBeenCalled();
      expect(
        screen.queryByText("dataManagement.restoreFailed"),
      ).not.toBeInTheDocument();
      archiveMock.mockResolvedValue({
        thread_id: "failed-lane",
        agent_id: "stopped",
        archived_at: null,
      });
      archivesListMock.mockResolvedValue({ ...page, items: [] });
      fireEvent.click(
        screen.getByRole("button", { name: "dataManagement.restoreNamed" }),
      );
      await waitFor(() =>
        expect(screen.queryByText("Failed lane")).not.toBeInTheDocument(),
      );
      expect(archiveMock).toHaveBeenCalledTimes(2);
      active.unmount();
      unsubscribe();
    },
  );

  it("observes another host starting and failing the shared lane without mounting the Chat store", async () => {
    const item = {
      thread_id: "external",
      agent_id: "stopped",
      title: "External host row",
      channel_type: "dashboard",
      created_at: 1,
      last_active: 1,
      archived_at: 100,
      mode: "chat" as const,
    };
    archivesListMock.mockReset().mockResolvedValue({
      items: [item],
      limit: 20,
      offset: 0,
      has_more: false,
    });
    const pending = deferred<{
      thread_id: string;
      agent_id: string;
      archived_at: null;
    }>();
    archiveMock.mockReturnValueOnce(pending.promise);
    const mounted = render(
      createElement(CurrentUserProvider, {
        user: { id: 1 } as OctopUser,
        setUser: vi.fn(),
        children: createElement(DataManagement),
      }),
    );
    await screen.findByText("External host row");
    expect(
      screen.getByRole("button", { name: "dataManagement.restoreNamed" }),
    ).toBeEnabled();
    const error = vi.fn();
    let request!: ReturnType<typeof persistSessionArchive>;
    act(() => {
      request = persistSessionArchive({
        actorId: 1,
        agentId: "stopped",
        id: "external",
        archived: false,
        isCurrent: () => true,
        onError: error,
      });
    });
    expect(
      screen.getByRole("button", { name: "dataManagement.restoreNamed" }),
    ).toBeDisabled();
    await act(async () => {
      pending.reject(new Error("external failure"));
      expect(await request).toEqual({ status: "failed" });
    });
    expect(
      screen.getByRole("button", { name: "dataManagement.restoreNamed" }),
    ).toBeEnabled();
    expect(error).toHaveBeenCalledOnce();
    expect(listMock).not.toHaveBeenCalled();
    expect(archivesListMock).toHaveBeenCalledOnce();
    mounted.unmount();
  });

  it("integrates the actual data page with the real lane/event helper and rejects a pre-restore page", async () => {
    const item = {
      thread_id: "t",
      agent_id: "stopped",
      title: "Stopped record",
      channel_type: "dashboard",
      created_at: 1,
      last_active: 1,
      archived_at: 100,
      mode: "chat" as const,
    };
    const page = { items: [item], limit: 20, offset: 0, has_more: false };
    const before = deferred<typeof page>();
    const fresh = deferred<typeof page>();
    archivesListMock
      .mockReset()
      .mockResolvedValueOnce(page)
      .mockReturnValueOnce(before.promise)
      .mockReturnValueOnce(fresh.promise);
    const pending = deferred<{
      thread_id: string;
      agent_id: string;
      archived_at: null;
    }>();
    archiveMock.mockReturnValue(pending.promise);
    render(
      createElement(CurrentUserProvider, {
        user: { id: 1 } as OctopUser,
        setUser: vi.fn(),
        children: createElement(DataManagement),
      }),
    );
    await screen.findByText("Stopped record");
    fireEvent.click(
      screen.getByRole("button", { name: "dataManagement.restoreNamed" }),
    );
    expect(archiveMutationPending(1, "stopped", "t")).toBe(true);
    const input = screen.getByRole("textbox", {
      name: "dataManagement.search",
    });
    fireEvent.change(input, { target: { value: "Stopped" } });
    fireEvent.click(
      screen.getByRole("button", { name: "dataManagement.search" }),
    );
    await act(async () => {
      pending.resolve({
        thread_id: "t",
        agent_id: "stopped",
        archived_at: null,
      });
    });
    expect(archivesListMock).toHaveBeenCalledTimes(3);
    await act(async () => {
      fresh.resolve({ ...page, items: [] });
    });
    await act(async () => {
      before.resolve(page);
    });
    expect(screen.queryByText("Stopped record")).not.toBeInTheDocument();
    expect(archiveMock).toHaveBeenCalledExactlyOnceWith("t", false);
  });

  it("keeps an archived deep link independent of the ordinary page and syncs its composer fields", async () => {
    metadataMock.mockResolvedValue({
      ...threadRow("archived", { title: "kept title" }),
      archived_at: 100,
      conversation_mode: "plan",
      artifacts: ["plan.md"],
    });
    const { result } = renderHook(() =>
      useSessions("a", { actorId: 1, selectedThreadId: "archived" }),
    );
    await waitFor(() => expect(result.current.loading).toBe(false));
    await act(async () => {
      expect(await result.current.ensureThreadInList("archived")).toBe("found");
    });
    expect(result.current.sessions).toEqual([]);
    expect(result.current.selectedSession?.archivedAt).toBe(100);
    act(() => {
      syncSessionArtifacts("archived", ["new.md"]);
      syncSessionConversationMode("archived", "craft", null);
      syncSessionHitlPolicy("archived", {
        mode: "allow_tools",
        tools: ["read"],
      });
    });
    expect(result.current.selectedSession).toMatchObject({
      name: "kept title",
      artifacts: ["new.md"],
      conversationMode: "craft",
      hitlPolicy: { mode: "allow_tools", tools: ["read"] },
      archivedAt: 100,
    });
  });

  it("keeps concurrent name and pin saves on the archived composer anchor after a restore receipt", async () => {
    const row = {
      ...threadRow("t", { title: "Original" }),
      archived_at: 100,
      pinned: false,
      conversation_mode: "plan",
      artifacts: ["plan.md"],
    };
    metadataMock.mockResolvedValue(row);
    const { result } = renderHook(() =>
      useSessions("a", { actorId: 1, selectedThreadId: "t" }),
    );
    await waitFor(() => expect(result.current.loading).toBe(false));
    await act(async () => {
      await result.current.ensureThreadInList("t");
    });
    const name = deferred<{ thread_id: string; title: string }>();
    const pin = deferred<{ thread_id: string; pinned: boolean }>();
    renameMock.mockReturnValueOnce(name.promise);
    patchMock.mockReturnValueOnce(pin.promise);
    let pName!: ReturnType<typeof result.current.renameSession>;
    let pPin!: ReturnType<typeof result.current.pinSession>;
    act(() => {
      pName = result.current.renameSession("t", "Saved title");
      pPin = result.current.pinSession("t", true);
    });
    archiveMock.mockResolvedValue({
      thread_id: "t",
      agent_id: "a",
      archived_at: null,
    });
    metadataMock.mockResolvedValue({ ...row, archived_at: null });
    await act(async () => {
      expect((await result.current.archiveSession("t", false)).status).toBe(
        "saved",
      );
    });
    await act(async () => {
      name.resolve({ thread_id: "t", title: "Saved title" });
      pin.resolve({ thread_id: "t", pinned: true });
      expect((await pName).status).toBe("saved");
      expect((await pPin).status).toBe("saved");
    });
    expect(result.current.selectedSession).toMatchObject({
      id: "t",
      name: "Saved title",
      pinned: true,
      archivedAt: null,
      conversationMode: "plan",
      artifacts: ["plan.md"],
    });
    expect(archiveMock).toHaveBeenCalledExactlyOnceWith("t", false);
  });

  it("rejects a pre-save metadata 404 without forgetting the successful archived selection", async () => {
    const row = { ...threadRow("t"), archived_at: 100 };
    metadataMock.mockResolvedValue(row);
    const { result } = renderHook(() =>
      useSessions("a", { actorId: 1, selectedThreadId: "t" }),
    );
    await waitFor(() => expect(result.current.loading).toBe(false));
    await act(async () => {
      expect(await result.current.ensureThreadInList("t")).toBe("found");
    });
    const before = deferred<typeof row>();
    metadataMock
      .mockReturnValueOnce(before.promise)
      .mockResolvedValue({ ...row, archived_at: 200 });
    let probe!: Promise<string>;
    act(() => {
      probe = result.current.ensureThreadInList("t");
    });
    archiveMock.mockResolvedValue({
      thread_id: "t",
      agent_id: "a",
      archived_at: 200,
    });
    await act(async () => {
      expect((await result.current.archiveSession("t", true)).status).toBe(
        "saved",
      );
    });
    await act(async () => {
      before.reject(new Error("Request failed: 404 Not Found"));
      expect(await probe).toBe("unknown");
    });
    expect(result.current.selectedSession).toMatchObject({
      id: "t",
      archivedAt: 200,
    });
    expect(result.current.sessions).toEqual([]);
  });

  it("archives with a narrow receipt, fences old expansion, and keeps the selected thread", async () => {
    listMock.mockResolvedValue(
      Array.from({ length: 11 }, (_, i) => ({
        ...threadRow(`t${i}`),
        archived_at: null,
      })),
    );
    const { result } = renderHook(() =>
      useSessions("a", { actorId: 1, selectedThreadId: "t0" }),
    );
    await waitFor(() => expect(result.current.loading).toBe(false));
    const old = deferred<ReturnType<typeof threadRow>[]>();
    listMock.mockReturnValueOnce(old.promise).mockResolvedValue([]);
    let expansion!: Promise<void>;
    act(() => {
      expansion = result.current.loadMoreSessions("t0");
    });
    archiveMock.mockResolvedValue({
      thread_id: "t0",
      agent_id: "a",
      archived_at: 200,
    });
    await act(async () => {
      expect((await result.current.archiveSession("t0", true)).status).toBe(
        "saved",
      );
    });
    await act(async () => {
      old.resolve(Array.from({ length: 21 }, (_, i) => threadRow(`t${i}`)));
      await expansion;
    });
    expect(result.current.sessions).toEqual([]);
    expect(result.current.selectedSession).toMatchObject({
      id: "t0",
      archivedAt: 200,
    });
    expect(archiveMock).toHaveBeenCalledExactlyOnceWith("t0", true);
  });
});

describe("classic server title search", () => {
  beforeEach(() => {
    resetSessionStoreForTests();
    listMock
      .mockReset()
      .mockImplementation(async (_agent, limit, q) =>
        q
          ? [threadRow("thread-56", { title: "Straße" })]
          : Array.from({ length: 21 }, (_, i) =>
              threadRow(`normal-${i}`),
            ).slice(0, limit),
      );
    metadataMock.mockReset();
    renameMock.mockReset();
    patchMock.mockReset();
    errorMock.mockReset();
  });
  afterEach(() => resetSessionStoreForTests());

  it("searches beyond the normal prefix without replacing its window", async () => {
    const { result } = renderHook(() =>
      useSessions("a", { searchEnabled: true }),
    );
    await waitFor(() => expect(result.current.loading).toBe(false));
    const ids = result.current.sessions.map((s) => s.id);
    act(() => result.current.setSearchQuery("STRASSE"));
    await waitFor(() =>
      expect(result.current.search.sessions[0]?.id).toBe("thread-56"),
    );
    expect(listMock).toHaveBeenLastCalledWith("a", 11, "STRASSE");
    expect(result.current.sessions.map((s) => s.id)).toEqual(ids);
    act(() => result.current.setSearchQuery(""));
    expect(result.current.sessions.map((s) => s.id)).toEqual(ids);
    expect(result.current.search.sessions).toEqual([]);
  });

  it("reads missing-window metadata once and keeps its selected anchor through refresh", async () => {
    metadataMock.mockResolvedValue(threadRow("thread-56", { title: "Beyond" }));
    const { result } = renderHook(() => useSessions("a"));
    await waitFor(() => expect(result.current.loading).toBe(false));
    await act(async () => {
      expect(await result.current.ensureThreadInList("thread-56")).toBe(
        "found",
      );
      await result.current.fetchSessions("thread-56");
    });
    expect(metadataMock).toHaveBeenCalledExactlyOnceWith("a", "thread-56");
    expect(result.current.sessions).toHaveLength(10);
    expect(result.current.sessions.some((s) => s.id === "thread-56")).toBe(
      true,
    );
    expect(result.current.hasMore).toBe(true);
  });

  it("keeps the current selected anchor when an older normal refresh requested another active row", async () => {
    metadataMock.mockResolvedValue(
      threadRow("thread-56", { title: "Selected beyond" }),
    );
    const { result, rerender } = renderHook(
      ({ selected }) =>
        useSessions("a", {
          searchEnabled: true,
          selectedThreadId: selected,
        }),
      { initialProps: { selected: "normal-0" } },
    );
    await waitFor(() => expect(result.current.loading).toBe(false));
    const oldRefresh = deferred<ReturnType<typeof threadRow>[]>();
    listMock.mockReturnValueOnce(oldRefresh.promise);
    let pending: unknown;
    act(() => {
      pending = result.current.fetchSessions("normal-0");
    });
    rerender({ selected: "thread-56" });
    await act(async () => {
      expect(await result.current.ensureThreadInList("thread-56")).toBe(
        "found",
      );
    });
    await act(async () => {
      oldRefresh.resolve(
        Array.from({ length: 11 }, (_, i) => threadRow(`normal-${i}`)),
      );
      await pending;
    });
    act(() => result.current.setSearchQuery(""));
    expect(result.current.sessions).toHaveLength(10);
    expect(
      result.current.sessions.find((row) => row.id === "thread-56")?.name,
    ).toBe("Selected beyond");
    expect(result.current.hasMore).toBe(true);
  });

  it("retains current history metadata in the selected anchor through finite normal refresh", async () => {
    metadataMock.mockResolvedValue({
      ...threadRow("thread-56"),
      artifacts: ["old.txt"],
    });
    const { result } = renderHook(() =>
      useSessions("a", { selectedThreadId: "thread-56" }),
    );
    await waitFor(() => expect(result.current.loading).toBe(false));
    await act(async () => {
      await result.current.ensureThreadInList("thread-56");
    });
    act(() => {
      syncSessionArtifacts("thread-56", ["new.txt"]);
      syncSessionConversationMode("thread-56", "plan", "plan.md");
      syncSessionHitlPolicy("thread-56", { mode: "allow_all" });
    });
    await act(async () => {
      await result.current.fetchSessions();
    });
    expect(
      result.current.sessions.find((row) => row.id === "thread-56"),
    ).toMatchObject({
      artifacts: ["new.txt"],
      conversationMode: "plan",
      pendingPlanPath: "plan.md",
      hitlPolicy: { mode: "allow_all" },
    });
    expect(result.current.sessions).toHaveLength(10);
    expect(result.current.hasMore).toBe(true);
  });

  it("distinguishes a failed first read and retries the same prefix", async () => {
    listMock.mockImplementation(async (_agent, _limit, q) => {
      if (q) throw new Error("offline");
      return [];
    });
    const { result } = renderHook(() =>
      useSessions("a", { searchEnabled: true }),
    );
    await waitFor(() => expect(result.current.loading).toBe(false));
    act(() => result.current.setSearchQuery("q"));
    await waitFor(() =>
      expect(result.current.search.error).toBeInstanceOf(Error),
    );
    expect(result.current.search.sessions).toEqual([]);
    listMock.mockResolvedValue([]);
    await act(async () => result.current.retrySearch());
    expect(listMock).toHaveBeenLastCalledWith("a", 11, "q");
    expect(result.current.search.error).toBeNull();
  });

  it("retains successful rows and retries a failed expansion without incrementing twice", async () => {
    const rows = Array.from({ length: 21 }, (_, i) =>
      threadRow(`matched-${i}`),
    );
    listMock.mockImplementation(async (_agent, limit, q) =>
      q ? rows.slice(0, limit) : [],
    );
    const { result } = renderHook(() =>
      useSessions("a", { searchEnabled: true }),
    );
    await waitFor(() => expect(result.current.loading).toBe(false));
    act(() => result.current.setSearchQuery("q"));
    await waitFor(() =>
      expect(result.current.search.sessions).toHaveLength(10),
    );
    const expansion = deferred<typeof rows>();
    listMock.mockReturnValueOnce(expansion.promise);
    act(() => {
      result.current.loadMoreSearch();
      result.current.loadMoreSearch();
    });
    await act(async () => expansion.reject(new Error("more offline")));
    expect(result.current.search.sessions).toHaveLength(10);
    expect(result.current.search.hasMore).toBe(true);
    await act(async () => result.current.retrySearch());
    expect(listMock).toHaveBeenLastCalledWith("a", 21, "q");
    expect(result.current.search.sessions).toHaveLength(20);
    await act(async () => result.current.loadMoreSearch());
    expect(result.current.search.sessions).toHaveLength(21);
    expect(result.current.search.hasMore).toBe(false);
  });

  it("does not accept old A responses or their finally across query A B A", async () => {
    const first = deferred<ReturnType<typeof threadRow>[]>();
    const third = deferred<ReturnType<typeof threadRow>[]>();
    const { result } = renderHook(() =>
      useSessions("a", { searchEnabled: true }),
    );
    await waitFor(() => expect(result.current.loading).toBe(false));
    listMock.mockReturnValueOnce(first.promise);
    act(() => result.current.setSearchQuery("A"));
    act(() => result.current.setSearchQuery("B"));
    listMock.mockReturnValueOnce(third.promise);
    act(() => result.current.setSearchQuery("A"));
    await act(async () => first.reject(new Error("obsolete")));
    expect(result.current.search.loading).toBe(true);
    expect(result.current.search.error).toBeNull();
    await act(async () => third.resolve([threadRow("latest")]));
    expect(result.current.search.sessions[0].id).toBe("latest");
  });

  it("protects canonical search-only fields after eviction from an older normal read", async () => {
    const { result } = renderHook(() =>
      useSessions("a", { searchEnabled: true }),
    );
    await waitFor(() => expect(result.current.loading).toBe(false));
    act(() => result.current.setSearchQuery("STRASSE"));
    await waitFor(() =>
      expect(result.current.search.sessions[0]?.id).toBe("thread-56"),
    );
    const normal = deferred<ReturnType<typeof threadRow>[]>();
    listMock.mockReturnValueOnce(normal.promise);
    let pending: unknown;
    act(() => {
      pending = result.current.fetchSessions();
    });
    renameMock.mockResolvedValue({
      thread_id: "thread-56",
      title: "Canonical",
    });
    await act(async () => result.current.renameSession("thread-56", "Client"));
    act(() => result.current.setSearchQuery(""));
    await act(async () => {
      normal.resolve([threadRow("thread-56", { title: "Old" })]);
      await pending;
    });
    expect(result.current.sessions[0].name).toBe("Canonical");
    listMock.mockResolvedValue([
      threadRow("thread-56", { title: "External newer" }),
    ]);
    await act(async () => result.current.fetchSessions());
    expect(result.current.sessions[0].name).toBe("External newer");
  });

  it("keeps the real field lane after query clear and protects a pending metadata read", async () => {
    const { result } = renderHook(() =>
      useSessions("a", { searchEnabled: true }),
    );
    await waitFor(() => expect(result.current.loading).toBe(false));
    act(() => result.current.setSearchQuery("q"));
    await waitFor(() => expect(result.current.search.sessions).toHaveLength(1));
    const read = deferred<ReturnType<typeof threadRow>>();
    metadataMock.mockReturnValue(read.promise);
    let probe: unknown;
    act(() => {
      probe = result.current.ensureThreadInList("thread-56");
    });
    const save = deferred<{ thread_id: string; title: string }>();
    renameMock.mockReturnValue(save.promise);
    let pending: unknown;
    act(() => {
      pending = result.current.renameSession("thread-56", "Client");
    });
    act(() => result.current.setSearchQuery(""));
    await act(async () => {
      save.resolve({ thread_id: "thread-56", title: "Server canonical" });
      await pending;
      read.resolve(threadRow("thread-56", { title: "Old" }));
      await probe;
    });
    expect(
      result.current.sessions.find((s) => s.id === "thread-56")?.name,
    ).toBe("Server canonical");
    expect(renameMock).toHaveBeenCalledTimes(1);
  });

  it("invalidates in-flight expansion on save and uses that same requested prefix", async () => {
    const rows = Array.from({ length: 21 }, (_, i) =>
      threadRow(i === 0 ? "thread-56" : `s-${i}`, { title: "Old" }),
    );
    listMock.mockImplementation(async (_agent, limit, q) =>
      q ? rows.slice(0, limit) : [],
    );
    const { result } = renderHook(() =>
      useSessions("a", { searchEnabled: true }),
    );
    await waitFor(() => expect(result.current.loading).toBe(false));
    act(() => result.current.setSearchQuery("q"));
    await waitFor(() =>
      expect(result.current.search.sessions).toHaveLength(10),
    );
    const oldMore = deferred<typeof rows>();
    listMock.mockReturnValueOnce(oldMore.promise);
    act(() => result.current.loadMoreSearch());
    renameMock.mockResolvedValue({ thread_id: "thread-56", title: "Gone" });
    const newRead = deferred<typeof rows>();
    listMock.mockReturnValueOnce(newRead.promise);
    await act(async () => result.current.renameSession("thread-56", "Gone"));
    expect(listMock).toHaveBeenLastCalledWith("a", 21, "q");
    await act(async () => oldMore.resolve(rows));
    expect(result.current.search.loading).toBe(true);
    await act(async () => newRead.resolve([]));
    expect(result.current.search.sessions).toEqual([]);
    expect(result.current.search.loading).toBe(false);
  });

  it("requires a newer read after a second save and preserves successful fields on refresh failure", async () => {
    const { result } = renderHook(() =>
      useSessions("a", { searchEnabled: true }),
    );
    await waitFor(() => expect(result.current.loading).toBe(false));
    act(() => result.current.setSearchQuery("q"));
    await waitFor(() => expect(result.current.search.sessions).toHaveLength(1));
    renameMock.mockResolvedValue({ thread_id: "thread-56", title: "Saved" });
    patchMock.mockResolvedValue({ thread_id: "thread-56", pinned: true });
    const beforeSecond = deferred<ReturnType<typeof threadRow>[]>();
    const afterSecond = deferred<ReturnType<typeof threadRow>[]>();
    listMock
      .mockReturnValueOnce(beforeSecond.promise)
      .mockReturnValueOnce(afterSecond.promise);
    await act(async () => result.current.renameSession("thread-56", "Saved"));
    await act(async () => result.current.pinSession("thread-56", true));
    await act(async () => beforeSecond.resolve([]));
    expect(result.current.search.loading).toBe(true);
    await act(async () => afterSecond.reject(new Error("refresh unavailable")));
    expect(result.current.search.sessions[0]).toMatchObject({
      name: "Saved",
      pinned: true,
    });
    expect(result.current.search.error).toBeInstanceOf(Error);
    expect(errorMock).not.toHaveBeenCalled();
  });

  it("coalesces same-tick saves into one new authoritative search read", async () => {
    const { result } = renderHook(() =>
      useSessions("a", { searchEnabled: true }),
    );
    await waitFor(() => expect(result.current.loading).toBe(false));
    act(() => result.current.setSearchQuery("q"));
    await waitFor(() => expect(result.current.search.sessions).toHaveLength(1));
    renameMock.mockResolvedValue({ thread_id: "thread-56", title: "Saved" });
    patchMock.mockResolvedValue({ thread_id: "thread-56", pinned: true });
    listMock.mockResolvedValue([
      { ...threadRow("thread-56", { title: "Saved" }), pinned: true },
    ]);
    const before = listMock.mock.calls.length;
    await act(async () => {
      await Promise.all([
        result.current.renameSession("thread-56", "Saved"),
        result.current.pinSession("thread-56", true),
      ]);
    });
    expect(listMock.mock.calls.length - before).toBe(1);
    expect(result.current.search.sessions[0]).toMatchObject({
      name: "Saved",
      pinned: true,
    });
  });

  it("keeps a same-field lane busy after eviction until the real save settles", async () => {
    const { result } = renderHook(() =>
      useSessions("a", { searchEnabled: true }),
    );
    await waitFor(() => expect(result.current.loading).toBe(false));
    act(() => result.current.setSearchQuery("q"));
    await waitFor(() => expect(result.current.search.sessions).toHaveLength(1));
    const response = deferred<{ thread_id: string; title: string }>();
    renameMock.mockReturnValue(response.promise);
    let pending: unknown;
    act(() => {
      pending = result.current.renameSession("thread-56", "First");
    });
    act(() => result.current.setSearchQuery(""));
    act(() => result.current.setSearchQuery("again"));
    await waitFor(() => expect(result.current.search.sessions).toHaveLength(1));
    await act(async () =>
      expect(await result.current.renameSession("thread-56", "Second")).toEqual(
        { status: "ignored", reason: "busy" },
      ),
    );
    await act(async () => {
      response.resolve({ thread_id: "thread-56", title: "Canonical" });
      await pending;
    });
    expect(renameMock).toHaveBeenCalledTimes(1);
  });

  it("cleans query and anchor across expert ABA and ignores an old read's error/finally", async () => {
    const oldRead = deferred<ReturnType<typeof threadRow>[]>();
    const { result, rerender } = renderHook(
      ({ agent }) => useSessions(agent, { searchEnabled: true }),
      { initialProps: { agent: "a" } },
    );
    await waitFor(() => expect(result.current.loading).toBe(false));
    listMock.mockReturnValueOnce(oldRead.promise);
    act(() => result.current.setSearchQuery("old"));
    rerender({ agent: "b" });
    await waitFor(() => expect(result.current.loading).toBe(false));
    rerender({ agent: "a" });
    await waitFor(() => expect(result.current.loading).toBe(false));
    act(() => result.current.setSearchQuery("new"));
    await waitFor(() => expect(result.current.search.sessions).toHaveLength(1));
    await act(async () => oldRead.reject(new Error("old failure")));
    expect(result.current.search.query).toBe("new");
    expect(result.current.search.error).toBeNull();
    expect(result.current.search.loading).toBe(false);
  });

  it("retains an old lifetime's busy lane without freezing fresh lifetime server fields", async () => {
    listMock.mockResolvedValue([threadRow("row", { title: "Original" })]);
    const { result, rerender } = renderHook(({ agent }) => useSessions(agent), {
      initialProps: { agent: "a" },
    });
    await waitFor(() => expect(result.current.loading).toBe(false));
    const save = deferred<{ thread_id: string; title: string }>();
    renameMock.mockReturnValue(save.promise);
    let pending: unknown;
    act(() => {
      pending = result.current.renameSession("row", "Old optimistic");
    });
    rerender({ agent: "b" });
    await waitFor(() => expect(result.current.loading).toBe(false));
    rerender({ agent: "a" });
    await waitFor(() => expect(result.current.loading).toBe(false));
    listMock.mockResolvedValue([
      threadRow("row", { title: "New lifetime external" }),
    ]);
    await act(async () => {
      await result.current.fetchSessions();
    });
    expect(result.current.sessions[0].name).toBe("New lifetime external");
    await act(async () => {
      expect(await result.current.renameSession("row", "Other")).toEqual({
        status: "ignored",
        reason: "busy",
      });
      save.resolve({ thread_id: "row", title: "Old canonical" });
      await pending;
    });
    expect(result.current.sessions[0].name).toBe("New lifetime external");
  });

  it("does not reconstruct an anchor after a newer selection or deletion", async () => {
    const metadata = deferred<ReturnType<typeof threadRow>>();
    metadataMock.mockReturnValue(metadata.promise);
    const { result, rerender } = renderHook(
      ({ selected }) => useSessions("a", { selectedThreadId: selected }),
      { initialProps: { selected: "thread-56" } },
    );
    await waitFor(() => expect(result.current.loading).toBe(false));
    let pending: unknown;
    act(() => {
      pending = result.current.ensureThreadInList("thread-56");
    });
    rerender({ selected: "normal-0" });
    await act(async () => {
      metadata.resolve(threadRow("thread-56"));
      await pending;
    });
    expect(await pending).toBe("unknown");
    expect(result.current.sessions.some((s) => s.id === "thread-56")).toBe(
      false,
    );
  });

  it("does not treat a mismatched metadata DTO as found", async () => {
    metadataMock.mockResolvedValue(threadRow("another-id"));
    const { result } = renderHook(() => useSessions("a"));
    await waitFor(() => expect(result.current.loading).toBe(false));
    await act(async () =>
      expect(await result.current.ensureThreadInList("target")).toBe("unknown"),
    );
    expect(result.current.sessions.some((s) => s.id === "target")).toBe(false);
  });

  it("does not revive a thread confirmed missing by metadata from an older normal read", async () => {
    metadataMock.mockRejectedValue(new Error("Request failed: 404 Not Found"));
    const { result } = renderHook(() =>
      useSessions("a", { selectedThreadId: "thread-56" }),
    );
    await waitFor(() => expect(result.current.loading).toBe(false));
    const old = deferred<ReturnType<typeof threadRow>[]>();
    listMock.mockReturnValueOnce(old.promise);
    let pending: unknown;
    act(() => {
      pending = result.current.fetchSessions();
    });
    await act(async () => {
      expect(await result.current.ensureThreadInList("thread-56")).toBe(
        "missing",
      );
    });
    await act(async () => {
      old.resolve([threadRow("thread-56")]);
      await pending;
    });
    expect(result.current.sessions).toEqual([]);
  });

  it.each(["403", "401", "500"])(
    "keeps a %s metadata failure unknown",
    async (status) => {
      metadataMock.mockRejectedValue(
        new Error(`Request failed: ${status} rejected`),
      );
      const { result } = renderHook(() => useSessions("a"));
      await waitFor(() => expect(result.current.loading).toBe(false));
      await act(async () =>
        expect(await result.current.ensureThreadInList("missing")).toBe(
          "unknown",
        ),
      );
    },
  );
});

describe("session metadata persistence", () => {
  beforeEach(() => {
    resetSessionStoreForTests();
    translationState.t = undefined;
    listMock.mockReset();
    metadataMock
      .mockReset()
      .mockRejectedValue(new Error("Request failed: 404 Not Found"));
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
      if (operation === "ensureThreadInList")
        metadataMock.mockReturnValueOnce(
          read.promise.then((result) =>
            result.find((row) => row.thread_id === "thread-15"),
          ),
        );
      else listMock.mockReturnValueOnce(read.promise);
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
    metadataMock.mockReset();
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
    metadataMock.mockRejectedValue(new Error("network down"));

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
    metadataMock.mockRejectedValue(new Error("Request failed: 404 Not Found"));

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
