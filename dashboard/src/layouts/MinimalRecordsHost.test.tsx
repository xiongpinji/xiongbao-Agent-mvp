import { act, render, renderHook, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import MinimalRecordsHost from "./MinimalRecordsHost";
import {
  resetSessionStoreForTests,
  useSessions,
} from "../pages/Chat/hooks/useSessions";

const { capture, agentState, renameMock, patchMock, errorMock } = vi.hoisted(
  () => ({
    capture: { current: {} as Record<string, (...args: unknown[]) => unknown> },
    agentState: { activeAgentId: "a" },
    renameMock: vi.fn(),
    patchMock: vi.fn(),
    errorMock: vi.fn(),
  }),
);
vi.mock("../context/AgentContext", () => ({
  useAgent: () => ({
    agents: [],
    activeAgentId: agentState.activeAgentId,
    setActiveAgent: vi.fn(),
  }),
  selectEnabledExperts: () => [],
}));
vi.mock("../pages/Chat/components/MinimalAgentSessionNav", () => ({
  default: (props: typeof capture.current) => {
    capture.current = props;
    return null;
  },
}));
vi.mock("../api/modules/octopThreads", () => ({
  octopThreadsApi: {
    rename: renameMock,
    patch: patchMock,
    list: async () => [
      { thread_id: "thread", title: "Old", pinned: false, last_active: 1 },
    ],
  },
}));
vi.mock("../utils/antdMessage", () => ({ message: { error: errorMock } }));
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((ok, fail) => {
    resolve = ok;
    reject = fail;
  });
  return { promise, resolve, reject };
}
function mount() {
  return render(
    <MemoryRouter initialEntries={["/experts"]}>
      <MinimalRecordsHost />
    </MemoryRouter>,
  );
}

describe("standalone records metadata results", () => {
  beforeEach(() => {
    resetSessionStoreForTests();
    renameMock.mockReset();
    patchMock.mockReset();
    errorMock.mockReset();
    agentState.activeAgentId = "a";
  });

  it.each(["onRenameActive", "onPinActive"])(
    "returns failed and reports once for %s",
    async (callback) => {
      const request = deferred<{
        thread_id: string;
        title: string;
        pinned: boolean;
      }>();
      renameMock.mockReturnValue(request.promise);
      patchMock.mockReturnValue(request.promise);
      mount();
      let pending: unknown;
      act(() => {
        pending = capture.current[callback](
          "thread",
          callback === "onRenameActive" ? "New" : true,
        );
      });
      await act(async () => {
        request.reject(new Error("offline"));
        expect(await pending).toEqual({ status: "failed" });
      });
      expect(errorMock).toHaveBeenCalledTimes(1);
    },
  );

  it("returns authoritative saved values and prevents same-field overlap", async () => {
    const request = deferred<{
      thread_id: string;
      title: string;
      pinned: boolean;
    }>();
    renameMock.mockReturnValue(request.promise);
    patchMock.mockResolvedValue({
      thread_id: "thread",
      title: "Old",
      pinned: true,
    });
    mount();
    let pending: unknown;
    act(() => {
      pending = capture.current.onRenameActive("thread", "Client");
    });
    await act(async () => {
      expect(await capture.current.onRenameActive("thread", "Second")).toEqual({
        status: "ignored",
        reason: "busy",
      });
      expect(await capture.current.onPinActive("thread", true)).toMatchObject({
        status: "saved",
        value: true,
      });
    });
    await act(async () => {
      request.resolve({
        thread_id: "thread",
        title: "Canonical",
        pinned: false,
      });
      expect(await pending).toMatchObject({
        status: "saved",
        value: "Canonical",
      });
    });
    expect(renameMock).toHaveBeenCalledTimes(1);
  });

  it("keeps the lane after unmount until the real request settles and emits no obsolete error", async () => {
    const request = deferred<{ thread_id: string; title: string }>();
    renameMock
      .mockReturnValueOnce(request.promise)
      .mockResolvedValue({ thread_id: "thread", title: "New" });
    const first = mount();
    let pending: unknown;
    act(() => {
      pending = capture.current.onRenameActive("thread", "New");
    });
    first.unmount();
    mount();
    await act(async () => {
      expect(
        await capture.current.onRenameActive("thread", "New"),
      ).toMatchObject({ status: "ignored", reason: "busy" });
      request.reject(new Error("late"));
      expect(await pending).toMatchObject({
        status: "ignored",
        reason: "stale",
      });
    });
    expect(errorMock).not.toHaveBeenCalled();
    await act(async () =>
      expect(
        await capture.current.onRenameActive("thread", "New"),
      ).toMatchObject({ status: "saved" }),
    );
  });

  it("isolates A to B to A callback results", async () => {
    const request = deferred<{ thread_id: string; title: string }>();
    renameMock.mockReturnValue(request.promise);
    const view = mount();
    let pending: unknown;
    act(() => {
      pending = capture.current.onRenameActive("thread", "New");
    });
    agentState.activeAgentId = "b";
    view.rerender(
      <MemoryRouter>
        <MinimalRecordsHost />
      </MemoryRouter>,
    );
    agentState.activeAgentId = "a";
    view.rerender(
      <MemoryRouter>
        <MinimalRecordsHost />
      </MemoryRouter>,
    );
    await act(async () => {
      request.reject(new Error("late"));
      expect(await pending).toMatchObject({
        status: "ignored",
        reason: "stale",
      });
    });
    expect(errorMock).not.toHaveBeenCalled();
  });

  it("rejects invalid metadata targets without API writes", async () => {
    mount();
    await act(async () => {
      expect(await capture.current.onRenameActive("thread", " ")).toMatchObject(
        { status: "ignored", reason: "invalid" },
      );
      expect(await capture.current.onPinActive("", true)).toMatchObject({
        status: "ignored",
        reason: "invalid",
      });
    });
    expect(renameMock).not.toHaveBeenCalled();
    expect(patchMock).not.toHaveBeenCalled();
  });

  it("shares a write lane with a chat hook until its unmounted request settles", async () => {
    const request = deferred<{
      thread_id: string;
      title: string;
      pinned: boolean;
    }>();
    patchMock
      .mockReturnValueOnce(request.promise)
      .mockResolvedValue({ thread_id: "thread", title: "Old", pinned: true });
    const hook = renderHook(() => useSessions("a"));
    await waitFor(() => expect(hook.result.current.loading).toBe(false));
    let pending: unknown;
    act(() => {
      pending = hook.result.current.pinSession("thread", true);
    });
    hook.unmount();
    mount();
    await act(async () => {
      expect(await capture.current.onPinActive("thread", true)).toMatchObject({
        status: "ignored",
        reason: "busy",
      });
      request.reject(new Error("late"));
      expect(await pending).toMatchObject({
        status: "ignored",
        reason: "stale",
      });
    });
    expect(errorMock).not.toHaveBeenCalled();
    expect(patchMock).toHaveBeenCalledTimes(1);
    await act(async () =>
      expect(await capture.current.onPinActive("thread", true)).toMatchObject({
        status: "saved",
      }),
    );
    expect(patchMock).toHaveBeenCalledTimes(2);
  });
});
