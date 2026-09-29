import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import PlanCalendar from "./PlanCalendar";
import {
  deferred,
  makePlanRendererProps,
  makePlanTodo,
  makePlanView,
} from "./planView.testFixtures";
import type { PlanTodoMutationResult } from "./ProjectPlanViews";

const computedStyle = window.getComputedStyle.bind(window);
beforeEach(() => {
  vi.spyOn(window, "getComputedStyle").mockImplementation((node) =>
    computedStyle(node),
  );
});
afterEach(() => vi.restoreAllMocks());

function calendarProps() {
  const props = makePlanRendererProps({
    view: makePlanView({ type: "calendar" }),
    window: null,
    total: 247,
    matchedTotal: 100,
    unscheduledTotal: 19,
  });
  return {
    ...props,
    lanes: [
      {
        ...props.lanes[0],
        laneId: "scheduled",
        bucket: "scheduled" as const,
        items: [
          makePlanTodo({
            start_date: "2026-09-28",
            due_date: "2026-09-29",
            version: 4,
          }),
        ],
        serverCount: 100,
      },
      {
        ...props.lanes[0],
        laneId: "unscheduled",
        bucket: "unscheduled" as const,
        items: [makePlanTodo({ todo_id: "none", title: "未排期待办" })],
        serverCount: 19,
      },
    ],
  };
}

describe("real server-day calendar", () => {
  it("renders Monday-first 42 month cells and queries the entire visible cross-month window", () => {
    const onWindowChange = vi.fn();
    render(
      <PlanCalendar {...calendarProps()} onWindowChange={onWindowChange} />,
    );
    expect(
      within(screen.getByRole("grid", { name: "计划日历" })).getAllByRole(
        "gridcell",
      ),
    ).toHaveLength(42);
    expect(screen.getAllByRole("columnheader")[0]).toHaveTextContent("周一");
    expect(onWindowChange).toHaveBeenCalledWith({
      start_date: "2026-08-31",
      end_date: "2026-10-11",
    });
    expect(
      within(screen.getByRole("gridcell", { name: "2026-09-29" })).getByRole(
        "article",
        { name: "Synthetic todo" },
      ),
    ).toBeVisible();
    expect(
      screen.getByRole("complementary", { name: "未排期" }),
    ).toHaveTextContent("19");
  });

  it("switches to a seven-cell week through a complete temporary definition", () => {
    const onTemporaryDefinitionChange = vi.fn();
    const props = calendarProps();
    const { rerender } = render(
      <PlanCalendar
        {...props}
        onTemporaryDefinitionChange={onTemporaryDefinitionChange}
      />,
    );
    fireEvent.change(screen.getByRole("combobox", { name: "日历模式" }), {
      target: { value: "week" },
    });
    expect(onTemporaryDefinitionChange).toHaveBeenCalledWith({
      ...props.definition,
      calendar: { date_basis: "due_date", mode: "week" },
    });
    rerender(
      <PlanCalendar
        {...props}
        definition={{
          ...props.definition,
          calendar: { date_basis: "due_date", mode: "week" },
        }}
      />,
    );
    expect(
      within(screen.getByRole("grid", { name: "计划日历" })).getAllByRole(
        "gridcell",
      ),
    ).toHaveLength(7);
  });

  it("moves only the configured date basis using the selected todo version", async () => {
    const props = calendarProps();
    props.lanes[0] = {
      ...props.lanes[0],
      items: [
        makePlanTodo({
          start_date: "2026-09-28",
          due_date: "2026-10-03",
          version: 4,
        }),
      ],
    };
    const onProposeTodoPatch = vi.fn(async () => ({
      status: "saved" as const,
      todo: makePlanTodo({ version: 5 }),
    }));
    render(
      <PlanCalendar
        {...props}
        definition={{
          ...props.definition,
          calendar: { date_basis: "start_date", mode: "month" },
        }}
        onProposeTodoPatch={onProposeTodoPatch}
      />,
    );
    const dataTransfer = {
      setData: vi.fn(),
      effectAllowed: "",
      dropEffect: "",
    };
    fireEvent.dragStart(
      screen.getByRole("article", { name: "Synthetic todo" }),
      { dataTransfer },
    );
    fireEvent.drop(screen.getByRole("gridcell", { name: "2026-09-30" }), {
      dataTransfer,
    });
    await waitFor(() =>
      expect(onProposeTodoPatch).toHaveBeenCalledWith(
        expect.objectContaining({ due_date: "2026-10-03", version: 4 }),
        { changes: { start_date: "2026-09-30" }, baseVersion: 4 },
      ),
    );
  });

  it("uses server today for navigation rather than the browser clock", () => {
    const onWindowChange = vi.fn();
    render(
      <PlanCalendar {...calendarProps()} onWindowChange={onWindowChange} />,
    );
    fireEvent.click(screen.getByRole("button", { name: "上一个时间窗口" }));
    expect(onWindowChange).toHaveBeenLastCalledWith({
      start_date: "2026-07-27",
      end_date: "2026-09-06",
    });
    fireEvent.click(screen.getByRole("button", { name: "今天" }));
    expect(onWindowChange).toHaveBeenLastCalledWith({
      start_date: "2026-08-31",
      end_date: "2026-10-11",
    });
  });

  it("keeps upper-bound null cells and clips its closed query window", () => {
    const onWindowChange = vi.fn();
    render(
      <PlanCalendar
        {...calendarProps()}
        serverToday="9999-12-31"
        onWindowChange={onWindowChange}
      />,
    );
    const grid = screen.getByRole("grid", { name: "计划日历" });
    expect(within(grid).getAllByRole("gridcell")).toHaveLength(42);
    expect(
      grid.querySelectorAll('[data-plan-null="true"]').length,
    ).toBeGreaterThan(0);
    expect(onWindowChange).toHaveBeenCalledWith({
      start_date: "9999-11-29",
      end_date: "9999-12-31",
    });
    expect(
      screen.getByRole("button", { name: "下一个时间窗口" }),
    ).toBeDisabled();
  });

  it("preserves read-only cards while disabling date moves without server metadata", () => {
    render(
      <PlanCalendar
        {...calendarProps()}
        serverToday={null}
        serverTimezone={null}
        window={{ start_date: "2026-09-01", end_date: "2026-09-30" }}
      />,
    );
    expect(
      screen.getByRole("article", { name: "Synthetic todo" }),
    ).toHaveAttribute("draggable", "false");
    for (const button of screen.getAllByRole("button", { name: "修改日期" }))
      expect(button).toBeDisabled();
    expect(
      screen.getByRole("button", { name: "查看待办：Synthetic todo" }),
    ).toBeEnabled();
  });

  it("retains a midnight-rejected date and uses refreshed server metadata before another save", async () => {
    const props = calendarProps();
    const onProposeTodoPatch = vi.fn(async () => ({
      status: "invalid" as const,
      messageKey: "projects.planViews.dateErrors.pastDue",
    }));
    const onRefreshTodoCompare = vi.fn(async () => ({
      state: "ready" as const,
      todo: makePlanTodo({
        start_date: "2026-09-28",
        due_date: "2026-09-29",
        version: 5,
      }),
      catalogRevision: 1,
      serverToday: "2026-10-01",
      serverTimezone: "Asia/Shanghai",
    }));
    const { rerender } = render(
      <PlanCalendar
        {...props}
        onProposeTodoPatch={onProposeTodoPatch}
        onRefreshTodoCompare={onRefreshTodoCompare}
      />,
    );
    const dataTransfer = {
      setData: vi.fn(),
      effectAllowed: "",
      dropEffect: "",
    };
    fireEvent.dragStart(
      screen.getByRole("article", { name: "Synthetic todo" }),
      { dataTransfer },
    );
    fireEvent.drop(screen.getByRole("gridcell", { name: "2026-09-30" }), {
      dataTransfer,
    });
    const dialog = await screen.findByRole("dialog", { name: "修改日期" });
    await waitFor(() =>
      expect(
        within(dialog).getByRole("button", { name: "刷新后比较" }),
      ).toBeVisible(),
    );
    expect(
      within(dialog).getByRole("textbox", { name: "目标日期" }),
    ).toHaveValue("2026-09-30");
    rerender(
      <PlanCalendar
        {...props}
        serverToday="2026-10-01"
        onProposeTodoPatch={onProposeTodoPatch}
        onRefreshTodoCompare={onRefreshTodoCompare}
      />,
    );
    expect(
      within(dialog).getByText("拟议更改").parentElement,
    ).toHaveTextContent("2026-09-30");
    fireEvent.click(within(dialog).getByRole("button", { name: "刷新后比较" }));
    const confirm = await within(dialog).findByRole("button", {
      name: "确认采用当前值继续编辑",
    });
    expect(
      within(dialog).getByText("当前服务器值").parentElement,
    ).toHaveTextContent("2026-09-29");
    fireEvent.click(confirm);
    expect(
      within(dialog).getByRole("button", { name: "保存更改" }),
    ).toBeDisabled();
    expect(
      within(dialog).getByText("新的截止日期不得早于服务器今天。"),
    ).toBeVisible();
    expect(onProposeTodoPatch).toHaveBeenCalledTimes(1);
  });

  it("discards old-account pending outcomes across an account ABA switch", async () => {
    const pending = deferred<PlanTodoMutationResult>();
    const props = {
      ...calendarProps(),
      onProposeTodoPatch: vi.fn(() => pending.promise),
    };
    const { rerender } = render(<PlanCalendar {...props} />);
    const dataTransfer = {
      setData: vi.fn(),
      effectAllowed: "",
      dropEffect: "",
    };
    fireEvent.dragStart(
      screen.getByRole("article", { name: "Synthetic todo" }),
      { dataTransfer },
    );
    fireEvent.drop(screen.getByRole("gridcell", { name: "2026-09-30" }), {
      dataTransfer,
    });
    await waitFor(() =>
      expect(props.onProposeTodoPatch).toHaveBeenCalledTimes(1),
    );
    rerender(<PlanCalendar {...props} accountId={8} />);
    rerender(<PlanCalendar {...props} />);
    await act(async () =>
      pending.resolve({
        status: "conflict",
        messageKey: "projects.planViews.compareFirst",
      }),
    );
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(
      screen.queryByText("更改未保存，请刷新后比较并明确确认。"),
    ).toBeNull();
  });

  it("keeps CRUD actions on scheduled cards with a separate delete policy", () => {
    const onEditTodo = vi.fn();
    render(
      <PlanCalendar
        {...calendarProps()}
        onEditTodo={onEditTodo}
        canDelete={(todo) => todo.todo_id === "none"}
      />,
    );
    const scheduled = screen.getByRole("article", { name: "Synthetic todo" });
    fireEvent.click(
      within(scheduled).getByRole("button", { name: "编辑待办" }),
    );
    expect(onEditTodo).toHaveBeenCalledExactlyOnceWith(
      expect.objectContaining({ todo_id: "todo1", version: 4 }),
    );
    expect(
      within(scheduled).queryByRole("button", { name: "删除待办" }),
    ).toBeNull();
    expect(
      screen.getByRole("complementary", { name: "未排期" }),
    ).toHaveTextContent("删除待办");
  });
});
