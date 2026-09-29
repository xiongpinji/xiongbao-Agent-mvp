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
