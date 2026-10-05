import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { OctopUser } from "../../../api/modules/auth";
import type {
  ThreadArchivePage,
  ThreadArchiveReceipt,
} from "../../../api/modules/octopThreads";
import { CurrentUserProvider } from "../../../hooks/useCurrentUser";

const mocks = vi.hoisted(() => ({
  list: vi.fn(),
  persist: vi.fn(),
  pending: vi.fn(),
  timezone: vi.fn(),
}));
type SavedEvent = {
  actorId: number;
  id: string;
  agentId: string;
  archivedAt: number | null;
  revision: number;
};
const listeners = new Set<(event: SavedEvent) => void>();
vi.mock("../../../api/modules/octopThreads", () => ({
  octopThreadsApi: { listArchived: mocks.list },
}));
vi.mock("../../../hooks/useServerTimezone", () => ({
  useServerTimezone: () => "Asia/Shanghai",
}));
vi.mock("../../Chat/hooks/useSessions", () => ({
  useArchiveMutationRevision: () => 0,
  archiveMutationPending: (...args: unknown[]) => mocks.pending(...args),
  persistSessionArchive: (...args: unknown[]) => mocks.persist(...args),
  onArchiveSaved: (callback: (event: SavedEvent) => void) => {
    listeners.add(callback);
    return () => {
      listeners.delete(callback);
    };
  },
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, options?: { title?: string }) =>
      key === "dataManagement.restoreNamed" ? `Restore ${options?.title}` : key,
  }),
}));

import DataManagementPage from "./index";

const user = (id: number): OctopUser => ({
  id,
  username: `user-${id}`,
  role: "user",
  display_name: null,
  locale: "en",
  permissions: [],
});
const item = (
  id: number,
  mode: "chat" | "files" = "chat",
): ThreadArchivePage["items"][number] => ({
  thread_id: `thread-${id}`,
  agent_id: `stopped-expert-${id}`,
  title: `Task ${id}`,
  channel_type: "dashboard",
  created_at: 1,
  last_active: 2,
  archived_at: 1700000000,
  mode,
});
const page = (
  items: ThreadArchivePage["items"],
  hasMore = false,
  offset = 0,
): ThreadArchivePage => ({ items, has_more: hasMore, limit: 20, offset });
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((done, fail) => {
    resolve = done;
    reject = fail;
  });
  return { promise, resolve, reject };
}
function mount(actor = user(1)) {
  const view = (value: OctopUser) => (
    <CurrentUserProvider user={value} setUser={() => {}}>
      <DataManagementPage />
    </CurrentUserProvider>
  );
  const result = render(view(actor));
  return {
    ...result,
    actor: (value: OctopUser) => result.rerender(view(value)),
  };
}
function saved(id: number) {
  const receipt: ThreadArchiveReceipt = {
    thread_id: `thread-${id}`,
    agent_id: `stopped-expert-${id}`,
    archived_at: null,
  };
  return { status: "saved" as const, receipt };
}
function emit(id: number, actorId = 1) {
  listeners.forEach((callback) =>
    callback({
      actorId,
      id: `thread-${id}`,
      agentId: `stopped-expert-${id}`,
      archivedAt: null,
      revision: id,
    }),
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  listeners.clear();
  mocks.pending.mockReturnValue(false);
  mocks.list.mockResolvedValue(page([]));
  mocks.persist.mockResolvedValue(saved(1));
});
afterEach(() => {
  cleanup();
  listeners.clear();
});

describe("ordinary user archive management", () => {
  it("includes stopped-expert and files records without an active expert or admin permission, using server timezone", async () => {
    mocks.list.mockResolvedValue(page([item(1), item(2, "files")]));
    const format = vi.spyOn(Date.prototype, "toLocaleString");
    mount();
    expect(await screen.findByText("Task 1")).toBeInTheDocument();
    expect(screen.getByText("dataManagement.files")).toBeInTheDocument();
    expect(mocks.list).toHaveBeenCalledWith({ q: "", limit: 20, offset: 0 });
    expect(format).toHaveBeenCalledWith(
      undefined,
      expect.objectContaining({ timeZone: "Asia/Shanghai" }),
    );
    format.mockRestore();
  });

  it("loads twenty-row pages and retains a successful prefix on a later failure and retry", async () => {
    mocks.list.mockResolvedValueOnce(
      page(
        Array.from({ length: 20 }, (_, id) => item(id)),
        true,
      ),
    );
    mocks.list.mockRejectedValueOnce(new Error("offline"));
    mocks.list.mockResolvedValueOnce(page([item(20)], false, 20));
    mount();
    await screen.findByText("Task 0");
    fireEvent.click(
      screen.getByRole("button", { name: "dataManagement.loadMore" }),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "dataManagement.loadFailed",
    );
    expect(screen.getByText("Task 19")).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", { name: "dataManagement.retry" }),
    );
    expect(await screen.findByText("Task 20")).toBeInTheDocument();
    expect(mocks.list).toHaveBeenLastCalledWith({
      q: "",
      limit: 20,
      offset: 20,
    });
    expect(
      screen.queryByRole("button", { name: "dataManagement.loadMore" }),
    ).not.toBeInTheDocument();
  });

  it("retries a first-page failure and renders an authoritative empty result", async () => {
    mocks.list
      .mockRejectedValueOnce(new Error("offline"))
      .mockResolvedValueOnce(page([]));
    mount();
    fireEvent.click(
      await screen.findByRole("button", { name: "dataManagement.retry" }),
    );
    expect(await screen.findByText("dataManagement.empty")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("fences query ABA and supports IME, Enter and clearing without sending a composition", async () => {
    const older = deferred<ThreadArchivePage>();
    mocks.list
      .mockResolvedValueOnce(page([item(1)]))
      .mockReturnValueOnce(older.promise)
      .mockResolvedValueOnce(page([item(3)]));
    mount();
    await screen.findByText("Task 1");
    const input = screen.getByRole("textbox", {
      name: "dataManagement.search",
    });
    fireEvent.compositionStart(input);
    fireEvent.change(input, { target: { value: "查询" } });
    fireEvent.submit(input.closest("form")!);
    expect(mocks.list).toHaveBeenCalledTimes(1);
    fireEvent.compositionEnd(input);
    fireEvent.submit(input.closest("form")!);
    expect(mocks.list).toHaveBeenLastCalledWith({
      q: "查询",
      limit: 20,
      offset: 0,
    });
    fireEvent.click(
      screen.getByRole("button", { name: "dataManagement.clear" }),
    );
    await screen.findByText("Task 3");
    await act(async () => {
      older.resolve(page([item(2)]));
    });
    expect(screen.queryByText("Task 2")).not.toBeInTheDocument();
    expect(input).toHaveValue("");
  });

  it("uses the canonical agent/thread, holds busy until settlement and moves focus after restore without navigation", async () => {
    const save = deferred<ReturnType<typeof saved>>();
    mocks.list.mockResolvedValue(page([item(1), item(2)]));
    mocks.persist.mockReturnValue(save.promise);
    mount();
    const button = await screen.findByRole("button", {
      name: "Restore Task 1",
    });
    button.focus();
    fireEvent.click(button);
    expect(button).toBeDisabled();
    fireEvent.click(button);
    expect(mocks.persist).toHaveBeenCalledTimes(1);
    expect(mocks.persist).toHaveBeenCalledWith(
      expect.objectContaining({
        actorId: 1,
        agentId: "stopped-expert-1",
        id: "thread-1",
        archived: false,
      }),
    );
    await act(async () => {
      save.resolve(saved(1));
    });
    expect(screen.queryByText("Task 1")).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Restore Task 2" }),
    ).toHaveFocus();
  });

  it("preserves a failed restore row and allows retry", async () => {
    mocks.list.mockResolvedValue(page([item(1)]));
    mocks.persist.mockImplementationOnce(async (options) => {
      options.onError(new Error("denied"));
      return { status: "failed" };
    });
    mount();
    fireEvent.click(
      await screen.findByRole("button", { name: "Restore Task 1" }),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "dataManagement.restoreFailed",
    );
    expect(
      screen.getByRole("button", { name: "Restore Task 1" }),
    ).not.toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Restore Task 1" }));
    await waitFor(() =>
      expect(screen.queryByText("Task 1")).not.toBeInTheDocument(),
    );
  });

  it("invalidates an old page and earlier refresh when two independent restores succeed", async () => {
    const oldPage = deferred<ThreadArchivePage>();
    const oldRefresh = deferred<ThreadArchivePage>();
    const newest = deferred<ThreadArchivePage>();
    mocks.list
      .mockResolvedValueOnce(page([item(1), item(2)], true))
      .mockReturnValueOnce(oldPage.promise)
      .mockReturnValueOnce(oldRefresh.promise)
      .mockReturnValueOnce(newest.promise);
    mount();
    await screen.findByText("Task 1");
    fireEvent.click(
      screen.getByRole("button", { name: "dataManagement.loadMore" }),
    );
    act(() => {
      emit(1);
      emit(2);
    });
    await act(async () => {
      newest.resolve(page([item(3)]));
      oldPage.resolve(page([item(1)], false, 2));
      oldRefresh.resolve(page([item(2)]));
    });
    expect(screen.getByText("Task 3")).toBeInTheDocument();
    expect(screen.queryByText("Task 1")).not.toBeInTheDocument();
    expect(screen.queryByText("Task 2")).not.toBeInTheDocument();
  });

  it("isolates real current-user A to B to A generations for both reads and pending writes", async () => {
    const oldRead = deferred<ThreadArchivePage>();
    const oldSave = deferred<ReturnType<typeof saved>>();
    mocks.list
      .mockResolvedValueOnce(page([item(1)]))
      .mockReturnValueOnce(oldRead.promise)
      .mockResolvedValueOnce(page([item(3)]));
    mocks.persist.mockReturnValueOnce(oldSave.promise);
    const result = mount();
    fireEvent.click(
      await screen.findByRole("button", { name: "Restore Task 1" }),
    );
    const current = mocks.persist.mock.calls[0][0].isCurrent;
    result.actor(user(2));
    expect(screen.queryByText("Task 1")).not.toBeInTheDocument();
    result.actor(user(1));
    await screen.findByText("Task 3");
    expect(current()).toBe(false);
    await act(async () => {
      oldRead.resolve(page([item(2)]));
      oldSave.resolve(saved(1));
    });
    expect(screen.getByText("Task 3")).toBeInTheDocument();
    expect(screen.queryByText("Task 2")).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("unsubscribes on unmount and rejects late save callbacks", async () => {
    mocks.list.mockResolvedValue(page([item(1)]));
    const pendingSave = deferred<ReturnType<typeof saved>>();
    mocks.persist.mockReturnValue(pendingSave.promise);
    const result = mount();
    fireEvent.click(
      await screen.findByRole("button", { name: "Restore Task 1" }),
    );
    const current = mocks.persist.mock.calls[0][0].isCurrent;
    result.unmount();
    expect(listeners.size).toBe(0);
    expect(current()).toBe(false);
    await act(async () => {
      pendingSave.resolve(saved(1));
    });
  });

  it("keeps a restore lane during query changes while allowing successful identity-bound invalidation", async () => {
    const save = deferred<ReturnType<typeof saved>>();
    mocks.list.mockResolvedValue(page([item(1)]));
    mocks.persist.mockReturnValue(save.promise);
    mount();
    fireEvent.click(
      await screen.findByRole("button", { name: "Restore Task 1" }),
    );
    const options = mocks.persist.mock.calls[0][0];
    fireEvent.click(
      screen.getByRole("button", { name: "dataManagement.clear" }),
    );
    await screen.findByText("Task 1");
    expect(options.isCurrent()).toBe(false);
    expect(options.isIdentityCurrent()).toBe(true);
    expect(
      screen.getByRole("button", { name: "Restore Task 1" }),
    ).toBeDisabled();
    mocks.list.mockResolvedValue(page([]));
    await act(async () => {
      emit(1);
      save.resolve(saved(1));
    });
    expect(await screen.findByText("dataManagement.empty")).toBeInTheDocument();
    expect(screen.queryByText("Task 1")).not.toBeInTheDocument();
  });

  it("rejects invalid search without discarding the last successful page", async () => {
    mocks.list.mockResolvedValue(page([item(1)]));
    mount();
    await screen.findByText("Task 1");
    const input = screen.getByRole("textbox", {
      name: "dataManagement.search",
    });
    fireEvent.change(input, { target: { value: "bad\0query" } });
    fireEvent.submit(input.closest("form")!);
    expect(screen.getByRole("alert")).toHaveTextContent(
      "dataManagement.searchInvalid",
    );
    expect(mocks.list).toHaveBeenCalledTimes(1);
    expect(screen.getByText("Task 1")).toBeInTheDocument();
  });
});
