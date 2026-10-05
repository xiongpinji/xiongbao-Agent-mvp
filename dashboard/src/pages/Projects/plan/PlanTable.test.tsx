import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import PlanTable from "./PlanTable";
import { makePlanRendererProps, makePlanTodo } from "./planView.testFixtures";

const computedStyle = window.getComputedStyle.bind(window);
beforeEach(() => {
  vi.spyOn(window, "getComputedStyle").mockImplementation((node) =>
    computedStyle(node),
  );
});
afterEach(() => vi.restoreAllMocks());

describe("real plan table", () => {
  it("renders flat child rows with real parent text without adjacency or count changes", () => {
    const onOpenParentTodo = vi.fn();
    const props = makePlanRendererProps({ onOpenParentTodo, total: 73 });
    props.definition = { ...props.definition, show_subtodos: true };
    const child = makePlanTodo({
      todo_id: "child",
      title: "Filtered child",
      parent_todo_id: "absent-parent",
      parent_title: '<img src=x onerror="alert(1)">',
      children_revision: null,
    });
    const root = makePlanTodo({
      title: "Unrelated root",
      children_count: 2,
      done_children_count: 1,
    });
    props.lanes[0] = {
      ...props.lanes[0],
      items: [child, root],
      serverCount: 73,
    };
    const { container } = render(<PlanTable {...props} />);
    const table = screen.getByRole("table");
    const titles = within(table).getAllByRole("button", {
      name: /^查看待办：/,
    });
    expect(titles.map((button) => button.textContent)).toEqual([
      "Filtered child",
      "Unrelated root",
    ]);
    const parent = screen.getByRole("button", {
      name: '父待办：<img src=x onerror="alert(1)">',
    });
    expect(parent.parentElement?.className).toContain("childTitle");
    expect(container.querySelector("img")).toBeNull();
    expect(screen.getByText("73 条待办")).toBeVisible();
    expect(screen.getByText("1/2 已完成")).toBeVisible();
    fireEvent.click(parent);
    expect(onOpenParentTodo).toHaveBeenCalledExactlyOnceWith(
      "absent-parent",
      parent,
    );
  });

  it("uses a localized generic parent action after a write lacks the query projection", () => {
    const props = makePlanRendererProps();
    const child = makePlanTodo({
      parent_todo_id: "parent",
      children_revision: null,
      parent_title: "Former title",
    });
    props.definition = { ...props.definition, show_subtodos: true };
    props.lanes[0] = { ...props.lanes[0], items: [child] };
    const { rerender } = render(<PlanTable {...props} />);
    expect(
      screen.getByRole("button", { name: "父待办：Former title" }),
    ).toBeVisible();
    const { parent_title: _projection, ...written } = child;
    rerender(
      <PlanTable
        {...props}
        lanes={[{ ...props.lanes[0], items: [written] }]}
      />,
    );
    expect(screen.queryByText("父待办：Former title")).toBeNull();
    expect(screen.getByRole("button", { name: "查看父待办" })).toBeVisible();
    rerender(
      <PlanTable
        {...props}
        definition={{ ...props.definition, show_subtodos: false }}
      />,
    );
    expect(
      screen.queryByRole("button", { name: /^父待办：|查看父待办/ }),
    ).toBeNull();
  });

  it("shows root subtodo counts in the title cell without changing columns or actions", () => {
    const onOpenTodo = vi.fn();
    const onTemporaryDefinitionChange = vi.fn();
    const todo = makePlanTodo({ children_count: 3, done_children_count: 2 });
    const props = makePlanRendererProps({
      onOpenTodo,
      onTemporaryDefinitionChange,
    });
    props.definition = {
      ...props.definition,
      fields: ["title", "due_date", "status"],
    };
    props.lanes[0] = { ...props.lanes[0], items: [todo] };
    render(<PlanTable {...props} />);

    const table = screen.getByRole("table");
    const title = within(table).getByRole("button", {
      name: "查看待办：Synthetic todo",
    });
    const titleCell = title.closest("td")!;
    expect(within(titleCell).getByText("2/3 已完成")).toBeVisible();
    expect(within(titleCell).getByText("子待办").closest("dl")).not.toBeNull();
    expect(
      within(table)
        .getAllByRole("columnheader")
        .map((header) => header.textContent),
    ).toEqual(["选择", "标题", "截止日期", "状态", "操作"]);
    expect(
      within(table)
        .getAllByRole("cell")
        .map((cell) => cell.getAttribute("data-plan-field")),
    ).toEqual([null, "title", "due_date", "status", null]);
    expect(within(table).getAllByRole("row")).toHaveLength(2);
    fireEvent.click(title);
    expect(onOpenTodo).toHaveBeenCalledExactlyOnceWith(todo, title);
    fireEvent.click(screen.getByRole("button", { name: "按标题排序" }));
    expect(onTemporaryDefinitionChange).toHaveBeenCalledExactlyOnceWith({
      ...props.definition,
      sort: [{ field: "title", direction: "asc" }, ...props.definition.sort],
    });
    expect(
      within(table).getByRole("button", { name: "修改状态" }),
    ).toBeVisible();
  });

  it("hides subtodo counts for roots without children and for child snapshots", () => {
    const props = makePlanRendererProps();
    props.lanes[0] = {
      ...props.lanes[0],
      items: [
        makePlanTodo({ title: "Empty root" }),
        makePlanTodo({
          todo_id: "child1",
          title: "Child snapshot",
          parent_todo_id: "parent1",
          children_count: 3,
          done_children_count: 2,
        }),
      ],
    };
    render(<PlanTable {...props} />);
    expect(screen.queryByText("子待办")).toBeNull();
    expect(screen.queryByText(/\d+\/\d+ 已完成/)).toBeNull();
  });

  it("updates root subtodo counts from newer props and opens the current snapshot", () => {
    const onOpenTodo = vi.fn();
    const props = makePlanRendererProps({ onOpenTodo });
    props.lanes[0] = {
      ...props.lanes[0],
      items: [makePlanTodo({ children_count: 3, done_children_count: 2 })],
    };
    const { rerender } = render(<PlanTable {...props} />);
    expect(screen.getByText("2/3 已完成")).toBeVisible();
    const newer = makePlanTodo({
      children_count: 4,
      done_children_count: 3,
      children_revision: 2,
      display_revision: 2,
    });
    rerender(
      <PlanTable {...props} lanes={[{ ...props.lanes[0], items: [newer] }]} />,
    );
    expect(screen.queryByText("2/3 已完成")).toBeNull();
    expect(screen.getByText("3/4 已完成")).toBeVisible();
    const title = screen.getByRole("button", {
      name: "查看待办：Synthetic todo",
    });
    fireEvent.click(title);
    expect(onOpenTodo).toHaveBeenCalledExactlyOnceWith(newer, title);
  });

  it("shows root subtodo counts to viewers while retaining manager-only controls", () => {
    const props = makePlanRendererProps({
      isManager: false,
      canEdit: () => false,
      canDelete: () => false,
    });
    props.lanes[0] = {
      ...props.lanes[0],
      items: [makePlanTodo({ children_count: 3, done_children_count: 2 })],
    };
    render(<PlanTable {...props} />);
    expect(screen.getByText("2/3 已完成")).toBeVisible();
    expect(screen.queryByRole("checkbox")).toBeNull();
    expect(screen.queryByRole("button", { name: "修改状态" })).toBeNull();
    expect(screen.queryByRole("button", { name: "编辑" })).toBeNull();
    expect(screen.queryByRole("button", { name: "删除" })).toBeNull();
    expect(screen.queryByRole("columnheader", { name: "选择" })).toBeNull();
  });

  it("uses the ordered visible fields as actual columns", () => {
    const props = makePlanRendererProps();
    props.definition = {
      ...props.definition,
      fields: ["title", "due_date", "status"],
    };
    props.lanes[0] = {
      ...props.lanes[0],
      items: [makePlanTodo({ due_date: "2026-10-02" })],
    };
    render(<PlanTable {...props} />);
    const headers = within(screen.getByRole("table")).getAllByRole(
      "columnheader",
    );
    expect(headers.map((header) => header.textContent)).toEqual([
      "选择",
      "标题",
      "截止日期",
      "状态",
      "操作",
    ]);
    expect(screen.queryByRole("columnheader", { name: "标签" })).toBeNull();
    expect(screen.getByText("2026-10-02")).toBeVisible();
  });

  it("changes full server-query sorting rather than reordering the loaded page", () => {
    const onTemporaryDefinitionChange = vi.fn();
    const props = makePlanRendererProps({ onTemporaryDefinitionChange });
    render(<PlanTable {...props} />);
    fireEvent.click(screen.getByRole("button", { name: "按标题排序" }));
    expect(onTemporaryDefinitionChange).toHaveBeenCalledWith({
      ...props.definition,
      sort: [{ field: "title", direction: "asc" }, ...props.definition.sort],
    });
    expect(
      screen.getByRole("button", { name: "查看待办：Synthetic todo" }),
    ).toBeVisible();
  });

  it("paginates the requested group independently", () => {
    const onLoadMore = vi.fn(async () => true);
    const props = makePlanRendererProps({ onLoadMore });
    props.definition = { ...props.definition, group_by: "status" };
    props.lanes = ["todo", "done"].map((status) => ({
      ...props.lanes[0],
      laneId: status,
      groupKey: { kind: "status", id: status },
      serverCount: 160,
      nextCursor: `${status}-opaque`,
    }));
    render(<PlanTable {...props} />);
    fireEvent.click(screen.getByRole("button", { name: "待开始 160" }));
    fireEvent.click(
      within(screen.getByRole("region", { name: "待开始" })).getByRole(
        "button",
        { name: "加载更多" },
      ),
    );
    expect(onLoadMore).toHaveBeenCalledExactlyOnceWith("todo");
  });

  it("keeps a selected version immutable after a background row update", async () => {
    const onBulkTodo = vi.fn(async () => {});
    const props = makePlanRendererProps({ onBulkTodo });
    const { rerender } = render(<PlanTable {...props} />);
    fireEvent.click(
      screen.getByRole("checkbox", { name: "选择待办：Synthetic todo" }),
    );
    const newer = {
      ...props,
      lanes: [{ ...props.lanes[0], items: [makePlanTodo({ version: 9 })] }],
    };
    rerender(<PlanTable {...newer} />);
    fireEvent.change(screen.getByRole("combobox", { name: "批量状态" }), {
      target: { value: "done" },
    });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "应用批量修改" }));
    });
    expect(onBulkTodo).toHaveBeenCalledWith(
      [{ todo_id: "todo1", expected_version: 1 }],
      { status: "done" },
    );
  });

  it("limits bulk to fifty unique todo IDs even when multiple groups repeat IDs", () => {
    const props = makePlanRendererProps();
    const todos = Array.from({ length: 51 }, (_, index) =>
      makePlanTodo({ todo_id: `id${index}`, title: `待办${index}` }),
    );
    props.lanes[0] = { ...props.lanes[0], items: todos, serverCount: 247 };
    render(<PlanTable {...props} />);
    const checkboxes = screen.getAllByRole("checkbox");
    act(() => {
      for (let index = 0; index < 50; index += 1)
        fireEvent.click(checkboxes[index]);
    });
    expect(
      screen.getByRole("checkbox", { name: "选择待办：待办50" }),
    ).toBeDisabled();
    expect(screen.getByText("已选择 50 条待办，最多 50 条")).toBeVisible();
  });

  it("keeps selection and the batch draft when a real callback rejects", async () => {
    const onBulkTodo = vi.fn(async () => {
      throw new Error("conflict");
    });
    render(<PlanTable {...makePlanRendererProps({ onBulkTodo })} />);
    fireEvent.click(
      screen.getByRole("checkbox", { name: "选择待办：Synthetic todo" }),
    );
    fireEvent.change(screen.getByRole("combobox", { name: "批量状态" }), {
      target: { value: "done" },
    });
    fireEvent.click(screen.getByRole("button", { name: "应用批量修改" }));
    await waitFor(() => expect(screen.getByRole("alert")).toBeVisible());
    expect(
      screen.getByRole("checkbox", { name: "选择待办：Synthetic todo" }),
    ).toBeChecked();
    expect(screen.getByRole("combobox", { name: "批量状态" })).toHaveValue(
      "done",
    );
  });

  it("offers a row status shortcut with the captured version", async () => {
    const onProposeTodoPatch = vi.fn(async () => ({
      status: "saved" as const,
      todo: makePlanTodo({ status: "done", version: 2 }),
    }));
    const props = makePlanRendererProps({ onProposeTodoPatch });
    const { rerender } = render(<PlanTable {...props} />);
    fireEvent.click(screen.getByRole("button", { name: "修改状态" }));
    const dialog = await screen.findByRole("dialog", { name: "修改状态" });
    fireEvent.change(within(dialog).getByRole("combobox", { name: "状态" }), {
      target: { value: "done" },
    });
    rerender(
      <PlanTable
        {...props}
        lanes={[{ ...props.lanes[0], items: [makePlanTodo({ version: 9 })] }]}
      />,
    );
    fireEvent.click(within(dialog).getByRole("button", { name: "保存更改" }));
    await waitFor(() =>
      expect(onProposeTodoPatch).toHaveBeenCalledWith(
        expect.objectContaining({ version: 1 }),
        { changes: { status: "done" }, baseVersion: 1 },
      ),
    );
  });
});
