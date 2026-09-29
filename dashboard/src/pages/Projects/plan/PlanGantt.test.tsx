import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import PlanGantt from "./PlanGantt";
import {
  makePlanRendererProps,
  makePlanTodo,
  makePlanView,
} from "./planView.testFixtures";

const computedStyle = window.getComputedStyle.bind(window);
beforeEach(() => {
  vi.spyOn(window, "getComputedStyle").mockImplementation((node) =>
    computedStyle(node),
  );
});
afterEach(() => vi.restoreAllMocks());

function ganttProps() {
  const props = makePlanRendererProps({
    view: makePlanView({ type: "gantt" }),
    window: { start_date: "2026-09-28", end_date: "2026-10-11" },
    total: 247,
    matchedTotal: 3,
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
            todo_id: "interval",
            title: "真实区间",
            start_date: "2026-09-29",
            due_date: "2026-10-01",
            version: 3,
          }),
          makePlanTodo({
            todo_id: "single",
            title: "单日节点",
            start_date: "2026-09-30",
          }),
        ],
        serverCount: 3,
      },
      {
        ...props.lanes[0],
        laneId: "unscheduled",
        bucket: "unscheduled" as const,
        items: [makePlanTodo({ todo_id: "none", title: "无日期待办" })],
        serverCount: 19,
      },
    ],
  };
}
function drag(source: HTMLElement, targetDay: string) {
  const dataTransfer = { setData: vi.fn(), effectAllowed: "", dropEffect: "" };
  fireEvent.dragStart(source, { dataTransfer });
  fireEvent.drop(screen.getByRole("columnheader", { name: targetDay }), {
    dataTransfer,
  });
}

describe("real gantt renderer", () => {
  it("offers CRUD for dated rows and keeps deletion separate from ordinary editing", () => {
    const onEditTodo = vi.fn();
    render(
      <PlanGantt
        {...ganttProps()}
        onEditTodo={onEditTodo}
        canDelete={(todo) => todo.todo_id === "none"}
      />,
    );
    const editButtons = screen.getAllByRole("button", { name: "编辑待办" });
    expect(editButtons).toHaveLength(3);
    fireEvent.click(editButtons[0]);
    expect(onEditTodo).toHaveBeenCalledExactlyOnceWith(
      expect.objectContaining({ todo_id: "interval", version: 3 }),
    );
    expect(screen.getAllByRole("button", { name: "删除待办" })).toHaveLength(1);
    expect(
      screen.getByRole("complementary", { name: "未排期" }),
    ).toHaveTextContent("删除待办");
  });

  it("draws inclusive real intervals, single-day nodes and an unscheduled side lane", () => {
    render(<PlanGantt {...ganttProps()} />);
    const bar = screen.getByTestId("gantt-bar-interval");
    expect(bar).toHaveAttribute("data-start-date", "2026-09-29");
    expect(bar).toHaveAttribute("data-end-date", "2026-10-01");
    expect(bar).toHaveAttribute("data-span-days", "3");
    expect(screen.getByTestId("gantt-bar-single")).toHaveAttribute(
      "data-span-days",
      "1",
    );
    expect(screen.getByTestId("gantt-bar-single")).toHaveAttribute(
      "data-single-day",
      "true",
    );
    expect(
      screen.getByRole("complementary", { name: "未排期" }),
    ).toHaveTextContent("19");
    expect(screen.queryByTestId("gantt-bar-none")).toBeNull();
  });

  it("shifts a whole interval through one versioned proposal while preserving span", async () => {
    const onProposeTodoPatch = vi.fn(async () => ({
      status: "saved" as const,
      todo: makePlanTodo({ version: 4 }),
    }));
    render(
      <PlanGantt {...ganttProps()} onProposeTodoPatch={onProposeTodoPatch} />,
    );
    drag(screen.getByTestId("gantt-bar-interval"), "2026-10-01");
    await waitFor(() =>
      expect(onProposeTodoPatch).toHaveBeenCalledWith(
        expect.objectContaining({ todo_id: "interval", version: 3 }),
        {
          changes: { start_date: "2026-10-01", due_date: "2026-10-03" },
          baseVersion: 3,
        },
      ),
    );
    expect(screen.getByTestId("gantt-bar-interval")).toHaveAttribute(
      "data-end-date",
      "2026-10-01",
    );
  });

  it("requires explicit confirmation when resizing a single-date node adds its other edge", async () => {
    const onProposeTodoPatch = vi.fn(async () => ({
      status: "saved" as const,
      todo: makePlanTodo({ version: 2 }),
    }));
    render(
      <PlanGantt {...ganttProps()} onProposeTodoPatch={onProposeTodoPatch} />,
    );
    drag(
      within(screen.getByTestId("gantt-bar-single")).getByRole("button", {
        name: "调整截止日期",
      }),
      "2026-10-02",
    );
    const dialog = await screen.findByRole("dialog", { name: "修改日期" });
    expect(onProposeTodoPatch).not.toHaveBeenCalled();
    expect(
      within(dialog).getByRole("button", { name: "保存更改" }),
    ).toBeDisabled();
    fireEvent.click(
      within(dialog).getByRole("checkbox", { name: "确认补全另一日期" }),
    );
    await act(async () =>
      fireEvent.click(within(dialog).getByRole("button", { name: "保存更改" })),
    );
    expect(onProposeTodoPatch).toHaveBeenCalledWith(
      expect.objectContaining({ todo_id: "single" }),
      { changes: { due_date: "2026-10-02" }, baseVersion: 1 },
    );
  });

  it("changes true zoom and navigates a bounded window through query callbacks", () => {
    const onWindowChange = vi.fn();
    const onTemporaryDefinitionChange = vi.fn();
    const props = ganttProps();
    render(
      <PlanGantt
        {...props}
        onWindowChange={onWindowChange}
        onTemporaryDefinitionChange={onTemporaryDefinitionChange}
      />,
    );
    fireEvent.change(screen.getByRole("combobox", { name: "缩放" }), {
      target: { value: "month" },
    });
    expect(onTemporaryDefinitionChange).toHaveBeenCalledWith({
      ...props.definition,
      gantt: { zoom: "month" },
    });
    fireEvent.click(screen.getByRole("button", { name: "下一个时间窗口" }));
    expect(onWindowChange).toHaveBeenCalledWith({
      start_date: "2026-10-12",
      end_date: "2026-10-25",
    });
  });

  it("blocks date writes without metadata while keeping detail readable", () => {
    render(
      <PlanGantt {...ganttProps()} serverToday={null} serverTimezone={null} />,
    );
    expect(screen.getByTestId("gantt-bar-interval")).toHaveAttribute(
      "draggable",
      "false",
    );
    for (const button of screen.getAllByRole("button", { name: "修改日期" }))
      expect(button).toBeDisabled();
    expect(
      screen.getByRole("button", { name: "查看待办：真实区间" }),
    ).toBeEnabled();
  });
});
