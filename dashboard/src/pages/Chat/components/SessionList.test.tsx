import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import SessionList from "./SessionList";
import { toSession, type SessionSearch } from "../hooks/useSessions";
import type { OctopAgent } from "../../../context/AgentContext";
const { hiddenState } = vi.hoisted(() => ({ hiddenState: { enabled: false } }));

vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

vi.mock("../../Experts/components/iconForName", () => ({
  ExpertIcon: () => null,
}));
vi.mock("./SharedExpertHint", () => ({ default: () => null }));
vi.mock("./TeamChatBadge", () => ({ default: () => null }));
vi.mock("../hooks/useHiddenSharedExperts", () => ({
  useHiddenSharedExperts: () => ({
    filterVisible: (agents: unknown[]) => agents,
    pickHidden: (agents: unknown[]) =>
      hiddenState.enabled ? agents.slice(1) : [],
    canHide: () => false,
  }),
}));

const agent = {
  agent_id: "a",
  id: 1,
  name: "A",
  state: "running",
} as OctopAgent;
const row = toSession({
  thread_id: "thread-56",
  title: "Straße",
  last_active: 1,
});
const emptySearch = (): SessionSearch => ({
  query: "",
  sessions: [],
  loading: false,
  hasMore: false,
  error: null,
});
function props(search = emptySearch()) {
  return {
    agents: [agent, { ...agent, agent_id: "b", id: 2, name: "B" }],
    sessions: [row],
    activeId: null,
    activeAgentId: "a",
    hasMore: true,
    loadingMore: false,
    search,
    onSearchChange: vi.fn(),
    onLoadMoreSearch: vi.fn(),
    onRetrySearch: vi.fn(),
    onLoadMore: vi.fn(),
    onSelect: vi.fn(),
    onAgentSelect: vi.fn(),
    onNewChat: vi.fn(),
    onDelete: vi.fn(),
    onRename: vi.fn(),
    onPin: vi.fn(),
    onFork: vi.fn(),
  };
}
function mount(value = props()) {
  return render(
    <MemoryRouter>
      <SessionList {...value} />
    </MemoryRouter>,
  );
}

describe("classic search rows", () => {
  it("keeps valid server Unicode matches and expands only the current expert", () => {
    const value = props({
      ...emptySearch(),
      query: "STRASSE",
      sessions: [row],
      hasMore: true,
    });
    mount(value);
    expect(screen.getAllByText("Straße")).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "chat.expandMore" }));
    expect(value.onLoadMoreSearch).toHaveBeenCalledTimes(1);
    expect(value.onLoadMore).not.toHaveBeenCalled();
  });
  it("distinguishes initial loading and failure from a successful empty result", () => {
    const value = props({ ...emptySearch(), query: "q", loading: true });
    const { rerender } = mount(value);
    expect(screen.getByRole("status")).toBeInTheDocument();
    expect(screen.queryByText("chat.noSearchResults")).not.toBeInTheDocument();
    rerender(
      <MemoryRouter>
        <SessionList
          {...value}
          search={{
            ...value.search,
            loading: false,
            error: new Error("offline"),
          }}
        />
      </MemoryRouter>,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("offline");
    fireEvent.click(screen.getByRole("button", { name: "common.retry" }));
    expect(value.onRetrySearch).toHaveBeenCalledTimes(1);
    expect(screen.queryByText("chat.noSearchResults")).not.toBeInTheDocument();
    rerender(
      <MemoryRouter>
        <SessionList {...value} search={{ ...value.search, loading: false }} />
      </MemoryRouter>,
    );
    expect(screen.getByText("chat.noSearchResults")).toBeInTheDocument();
  });
  it("waits for IME composition and clears through Escape or the clear button", () => {
    const value = props({ ...emptySearch(), query: "q" });
    mount(value);
    const input = screen.getByRole("searchbox");
    fireEvent.compositionStart(input);
    fireEvent.change(input, { target: { value: "熊" } });
    expect(value.onSearchChange).not.toHaveBeenCalled();
    fireEvent.compositionEnd(input);
    expect(value.onSearchChange).toHaveBeenLastCalledWith("熊");
    fireEvent.keyDown(input, { key: "Escape" });
    expect(value.onSearchChange).toHaveBeenLastCalledWith("");
    fireEvent.change(input, { target: { value: "again" } });
    fireEvent.click(screen.getByRole("button", { name: "chat.clearSearch" }));
    expect(value.onSearchChange).toHaveBeenLastCalledWith("");
    expect(value.onSelect).not.toHaveBeenCalled();
  });
  it("selects rows exactly once using Enter or Space and keeps edit keys inside the input", async () => {
    const value = props();
    mount(value);
    const element = screen.getByText("Straße").closest('[role="button"]')!;
    fireEvent.keyDown(element, { key: "Enter" });
    fireEvent.keyDown(element, { key: " " });
    expect(value.onSelect.mock.calls).toEqual([
      ["thread-56", "a"],
      ["thread-56", "a"],
    ]);
    fireEvent.click(element.querySelector("button")!);
    fireEvent.click(
      await screen.findByRole("menuitem", { name: "common.rename" }),
    );
    const edit = screen.getByRole("textbox");
    fireEvent.change(edit, { target: { value: "New name" } });
    fireEvent.keyDown(edit, { key: "Enter" });
    expect(value.onRename).toHaveBeenCalledExactlyOnceWith(
      "thread-56",
      "New name",
    );
    expect(value.onSelect).toHaveBeenCalledTimes(2);
  });
  it("keeps nested menu keyboard activation from selecting the row", () => {
    const value = props();
    mount(value);
    const more = screen.getByRole("button", { name: "common.more" });
    fireEvent.keyDown(more, { key: "Enter" });
    fireEvent.keyDown(more, { key: " " });
    expect(value.onSelect).not.toHaveBeenCalled();
  });
  it("invalidates the live query when entering the hidden expert view", () => {
    hiddenState.enabled = true;
    const value = props({ ...emptySearch(), query: "q", sessions: [row] });
    mount(value);
    fireEvent.click(
      screen.getByRole("button", { name: "chat.expertListHidden" }),
    );
    expect(value.onSearchChange).toHaveBeenCalledExactlyOnceWith("");
    expect(screen.queryByRole("searchbox")).not.toBeInTheDocument();
    hiddenState.enabled = false;
  });
  it("discards an unfinished composition when the current expert changes", () => {
    const value = props({ ...emptySearch(), query: "q" });
    const { rerender } = mount(value);
    const input = screen.getByRole("searchbox");
    fireEvent.compositionStart(input);
    fireEvent.change(input, { target: { value: "熊" } });
    rerender(
      <MemoryRouter>
        <SessionList {...value} activeAgentId="b" search={emptySearch()} />
      </MemoryRouter>,
    );
    expect(screen.getByRole("searchbox")).toHaveValue("");
    expect(value.onSearchChange).not.toHaveBeenCalled();
  });
});
