import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import MinimalAgentSessionNav from "./MinimalAgentSessionNav";
import {
  resetSessionStoreForTests,
  toSession,
  type Session,
} from "../hooks/useSessions";
import { emitSessionEvent } from "../hooks/chatStore";
import type { OctopAgent } from "../../../context/AgentContext";

const { listMock, renameMock, patchMock, errorMock } = vi.hoisted(() => ({
  listMock: vi.fn(),
  renameMock: vi.fn(),
  patchMock: vi.fn(),
  errorMock: vi.fn(),
}));
vi.mock("../../../api/modules/octopThreads", () => ({
  octopThreadsApi: { list: listMock, rename: renameMock, patch: patchMock },
}));
vi.mock("../../../utils/antdMessage", () => ({
  message: { error: errorMock },
}));
vi.mock("../../Experts/components/iconForName", () => ({
  ExpertIcon: () => null,
}));
vi.mock("./SharedExpertHint", () => ({ default: () => null }));
vi.mock("./TeamChatBadge", () => ({ default: () => null }));

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((ok, fail) => {
    resolve = ok;
    reject = fail;
  });
  return { promise, resolve, reject };
}
const row = {
  thread_id: "thread-a",
  title: "Original",
  pinned: false,
  last_active: 1,
  channel_type: "dashboard",
  has_messages: true,
};
const agent = {
  agent_id: "a",
  id: 1,
  name: "Agent A",
  state: "running",
} as OctopAgent;
function navProps() {
  return {
    agents: [agent],
    activeId: null,
    activeAgentId: null as string | null,
    activeSessions: [] as Session[],
    onSelect: vi.fn(),
    onAgentSelect: vi.fn(),
    onNewChat: vi.fn(),
    onDeleteActive: vi.fn(),
    onRenameActive: vi.fn(),
    onPinActive: vi.fn(),
    onFork: vi.fn(),
  };
}
function mount(props = navProps()) {
  return render(
    <MemoryRouter>
      <MinimalAgentSessionNav {...props} />
    </MemoryRouter>,
  );
}
async function menu(title = "Original") {
  fireEvent.click(
    screen
      .getByText(title)
      .closest('[role="button"]')!
      .querySelector("button")!,
  );
  await screen.findByRole("menuitem", { name: "common.rename" });
}
async function rename(title: string) {
  await menu();
  fireEvent.click(screen.getByRole("menuitem", { name: "common.rename" }));
  const input = screen.getByRole("textbox");
  fireEvent.change(input, { target: { value: title } });
  await act(async () => {
    fireEvent.keyDown(input, { key: "Enter" });
    fireEvent.blur(input);
  });
}

describe("minimal history metadata", () => {
  beforeEach(() => {
    resetSessionStoreForTests();
    vi.clearAllMocks();
    listMock.mockReset().mockResolvedValue([row]);
    renameMock.mockReset();
    patchMock.mockReset();
    localStorage.clear();
  });

  it("rolls back inactive pin, reports once and allows retry without selecting", async () => {
    const request = deferred<typeof row>();
    patchMock
      .mockReturnValueOnce(request.promise)
      .mockResolvedValue({ ...row, pinned: true });
    const props = navProps();
    mount(props);
    await screen.findByText("Original");
    await menu();
    fireEvent.click(screen.getByRole("menuitem", { name: "置顶" }));
    expect(
      screen.getByText("Original").closest('[role="button"]')?.className,
    ).toContain("sessionRowPinned");
    await act(async () => request.reject(new Error("offline")));
    expect(
      screen.getByText("Original").closest('[role="button"]')?.className,
    ).not.toContain("sessionRowPinned");
    expect(errorMock).toHaveBeenCalledTimes(1);
    expect(props.onSelect).not.toHaveBeenCalled();
    await menu();
    fireEvent.click(screen.getByRole("menuitem", { name: "置顶" }));
    await waitFor(() => expect(patchMock).toHaveBeenCalledTimes(2));
    expect(patchMock).toHaveBeenLastCalledWith("a", "thread-a", {
      pinned: true,
    });
  });

  it("rolls back inactive rename after Enter plus blur with one PATCH", async () => {
    const request = deferred<typeof row>();
    renameMock.mockReturnValue(request.promise);
    mount();
    await screen.findByText("Original");
    await rename("New name");
    expect(screen.getByText("New name")).toBeInTheDocument();
    expect(renameMock).toHaveBeenCalledTimes(1);
    await act(async () => request.reject(new Error("forbidden")));
    expect(screen.getByText("Original")).toBeInTheDocument();
    expect(errorMock).toHaveBeenCalledTimes(1);
  });

  it("keeps the successful field when the other field fails", async () => {
    const request = deferred<typeof row>();
    patchMock.mockReturnValue(request.promise);
    renameMock.mockResolvedValue({
      ...row,
      title: "Server normalized",
      pinned: false,
    });
    mount();
    await screen.findByText("Original");
    await menu();
    fireEvent.click(screen.getByRole("menuitem", { name: "置顶" }));
    await rename("New name");
    await screen.findByText("Server normalized");
    await act(async () => request.reject(new Error("pin failed")));
    expect(
      screen.getByText("Server normalized").closest('[role="button"]')
        ?.className,
    ).not.toContain("sessionRowPinned");
  });

  it("does not allow an old preview response to overwrite a saved field", async () => {
    mount();
    await screen.findByText("Original");
    const read = deferred<(typeof row)[]>();
    listMock.mockReturnValueOnce(read.promise);
    renameMock.mockResolvedValue({ ...row, title: "New name" });
    act(() =>
      emitSessionEvent({
        kind: "sessionsChanged",
        sessionId: "thread-a",
        agentId: "a",
      }),
    );
    await rename("New name");
    await waitFor(() => expect(renameMock).toHaveBeenCalledTimes(1));
    await act(async () => read.resolve([row]));
    expect(screen.getByText("New name")).toBeInTheDocument();
  });

  it("awaits active callbacks, rolls back failures and never repeats API or toast", async () => {
    const props = navProps();
    props.activeAgentId = "a";
    const request = deferred<{ status: "failed" }>();
    props.onRenameActive.mockReturnValue(request.promise);
    mount(props);
    await screen.findByText("Original");
    await rename("New name");
    expect(props.onRenameActive).toHaveBeenCalledWith("thread-a", "New name");
    expect(renameMock).not.toHaveBeenCalled();
    await act(async () => request.resolve({ status: "failed" }));
    expect(screen.getByText("Original")).toBeInTheDocument();
    expect(errorMock).not.toHaveBeenCalled();
  });

  it("preserves optimistic fields while activeSessions publishes an older snapshot", async () => {
    const props = navProps();
    props.activeAgentId = "a";
    props.activeSessions = [toSession(row)];
    const request = deferred<{ status: "saved"; value: string }>();
    props.onRenameActive.mockReturnValue(request.promise);
    const view = mount(props);
    await screen.findByText("Original");
    await rename("New name");
    view.rerender(
      <MemoryRouter>
        <MinimalAgentSessionNav
          {...props}
          activeSessions={[toSession({ ...row, last_active: 2 })]}
        />
      </MemoryRouter>,
    );
    expect(screen.getByText("New name")).toBeInTheDocument();
    await act(async () =>
      request.resolve({ status: "saved", value: "Canonical" }),
    );
    expect(screen.getByText("Canonical")).toBeInTheDocument();
  });

  it("ignores late failures after expert removal or unmount", async () => {
    const request = deferred<typeof row>();
    renameMock.mockReturnValue(request.promise);
    const props = navProps();
    const view = mount(props);
    await screen.findByText("Original");
    await rename("New name");
    view.rerender(
      <MemoryRouter>
        <MinimalAgentSessionNav {...props} agents={[]} />
      </MemoryRouter>,
    );
    await act(async () => request.reject(new Error("late")));
    expect(errorMock).not.toHaveBeenCalled();
    expect(screen.queryByText("New name")).not.toBeInTheDocument();
  });

  it("does not resurrect a deleted session from mutation or preview reads", async () => {
    const request = deferred<typeof row>();
    renameMock.mockReturnValue(request.promise);
    mount();
    await screen.findByText("Original");
    await rename("New name");
    const read = deferred<(typeof row)[]>();
    listMock.mockReturnValueOnce(read.promise);
    act(() => {
      emitSessionEvent({
        kind: "sessionsChanged",
        sessionId: "thread-a",
        agentId: "a",
      });
      emitSessionEvent({
        kind: "sessionDeleted",
        sessionId: "thread-a",
        agentId: "a",
      });
    });
    await act(async () => {
      request.resolve({ ...row, title: "New name" });
      read.resolve([row]);
    });
    expect(screen.queryByText("New name")).not.toBeInTheDocument();
    expect(screen.queryByText("Original")).not.toBeInTheDocument();
  });

  it("preserves live edited metadata when the initial preview returns late", async () => {
    const initial = deferred<(typeof row)[]>();
    listMock.mockReturnValueOnce(initial.promise);
    const props = navProps();
    props.activeAgentId = "a";
    props.activeSessions = [toSession(row)];
    props.onRenameActive.mockResolvedValue({
      status: "saved",
      value: "Canonical",
    });
    mount(props);
    await screen.findByText("Original");
    await rename("New name");
    await screen.findByText("Canonical");
    await act(async () => initial.resolve([row]));
    expect(screen.getByText("Canonical")).toBeInTheDocument();
    expect(listMock).toHaveBeenCalledWith("a", 10);
  });

  it("ignores active delegate results across A to B to A", async () => {
    const props = navProps();
    props.activeAgentId = "a";
    const request = deferred<{ status: "saved"; value: string }>();
    props.onRenameActive.mockReturnValue(request.promise);
    const view = mount(props);
    await screen.findByText("Original");
    await rename("New name");
    view.rerender(
      <MemoryRouter>
        <MinimalAgentSessionNav {...props} activeAgentId="b" />
      </MemoryRouter>,
    );
    view.rerender(
      <MemoryRouter>
        <MinimalAgentSessionNav
          {...props}
          activeAgentId="a"
          activeSessions={[toSession({ ...row, title: "New view" })]}
        />
      </MemoryRouter>,
    );
    await act(async () => request.resolve({ status: "saved", value: "Late" }));
    expect(screen.getByText("New view")).toBeInTheDocument();
    expect(screen.queryByText("Late")).not.toBeInTheDocument();
    expect(errorMock).not.toHaveBeenCalled();
  });

  it("never overwrites a later published preview with an earlier refresh", async () => {
    mount();
    await screen.findByText("Original");
    const earlier = deferred<(typeof row)[]>();
    listMock
      .mockReturnValueOnce(earlier.promise)
      .mockResolvedValueOnce([{ ...row, title: "Latest" }]);
    await act(async () => {
      emitSessionEvent({
        kind: "sessionsChanged",
        sessionId: "thread-a",
        agentId: "a",
      });
      emitSessionEvent({
        kind: "sessionsChanged",
        sessionId: "thread-a",
        agentId: "a",
      });
    });
    await screen.findByText("Latest");
    await act(async () => earlier.resolve([row]));
    expect(screen.getByText("Latest")).toBeInTheDocument();
  });

  it("keeps inactive writes valid when a different expert becomes active", async () => {
    const request = deferred<typeof row>();
    renameMock.mockReturnValue(request.promise);
    const props = navProps();
    const view = mount(props);
    await screen.findByText("Original");
    await rename("New name");
    view.rerender(
      <MemoryRouter>
        <MinimalAgentSessionNav {...props} activeAgentId="b" />
      </MemoryRouter>,
    );
    await act(async () => request.reject(new Error("offline")));
    expect(screen.getByText("Original")).toBeInTheDocument();
    expect(errorMock).toHaveBeenCalledTimes(1);
  });

  it("ignores empty, unchanged and Escape rename without writes", async () => {
    mount();
    await screen.findByText("Original");
    await rename(" ");
    await rename("Original");
    await menu();
    fireEvent.click(screen.getByRole("menuitem", { name: "common.rename" }));
    const input = screen.getByRole("textbox");
    fireEvent.change(input, { target: { value: "Cancelled" } });
    fireEvent.keyDown(input, { key: "Escape" });
    expect(screen.getByText("Original")).toBeInTheDocument();
    expect(renameMock).not.toHaveBeenCalled();
    expect(patchMock).not.toHaveBeenCalled();
  });
});
