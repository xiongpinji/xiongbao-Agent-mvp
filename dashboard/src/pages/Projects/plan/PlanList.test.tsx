import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import PlanList from "./PlanList";
import {
  makePlanRendererProps,
  makePlanTodo,
  makePlanView,
} from "./planView.testFixtures";

describe("bounded plan list", () => {
  it("renders continuous list rows in server order without becoming a table", () => {
    const props = makePlanRendererProps({
      view: makePlanView({ type: "list" }),
      total: 247,
    });
    props.lanes[0] = {
      ...props.lanes[0],
      serverCount: 247,
      items: [
        makePlanTodo({ todo_id: "b", title: "服务器第一项" }),
        makePlanTodo({ todo_id: "a", title: "服务器第二项" }),
      ],
    };
    render(<PlanList {...props} />);
    expect(screen.queryByRole("table")).toBeNull();
    const rows = screen.getAllByRole("listitem");
    expect(rows[0]).toHaveTextContent("服务器第一项");
    expect(rows[1]).toHaveTextContent("服务器第二项");
    expect(screen.getByText("247 条待办")).toBeVisible();
  });

  it("loads only the explicitly expanded group and keeps each server count", () => {
    const onLoadFirst = vi.fn(async () => true);
    const props = makePlanRendererProps({
      view: makePlanView({ type: "list" }),
      onLoadFirst,
    });
    props.definition = { ...props.definition, group_by: "status" };
    props.lanes = ["todo", "done"].map((status, index) => ({
      ...props.lanes[0],
      laneId: status,
      groupKey: { kind: "status", id: status },
      items: [],
      queryFingerprint: null,
      serverCount: 123 + index,
    }));
    render(<PlanList {...props} />);
    expect(onLoadFirst).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "待开始 123" }));
    expect(onLoadFirst).toHaveBeenCalledExactlyOnceWith("todo");
    expect(screen.getByRole("button", { name: "已完成 124" })).toHaveAttribute(
      "aria-expanded",
      "false",
    );
  });

  it("de-duplicates only within a lane and allows a tagged todo in two groups", () => {
    const props = makePlanRendererProps({
      view: makePlanView({ type: "list" }),
    });
    const todo = makePlanTodo();
    const tags = props.catalog!.tags.slice(0, 2);
    props.definition = { ...props.definition, group_by: "tag" };
    props.lanes = tags.map((tag, index) => ({
      ...props.lanes[0],
      laneId: tag.tag_id,
      groupKey: { kind: "tag", id: tag.tag_id },
      items: [todo, todo],
      serverCount: 3 + index,
    }));
    render(<PlanList {...props} />);
    for (const tag of tags)
      fireEvent.click(
        screen.getByRole("button", { name: new RegExp(tag.name) }),
      );
    expect(screen.getAllByRole("listitem")).toHaveLength(2);
    expect(
      screen.getAllByRole("button", { name: "查看待办：Synthetic todo" }),
    ).toHaveLength(2);
  });

  it("keeps assignee edit and creator delete permissions distinct", () => {
    const onEditTodo = vi.fn();
    const props = makePlanRendererProps({
      isManager: false,
      canDelete: () => false,
      onEditTodo,
    });
    render(<PlanList {...props} />);
    const row = screen.getByRole("listitem");
    fireEvent.click(within(row).getByRole("button", { name: "编辑待办" }));
    expect(onEditTodo).toHaveBeenCalledWith(props.lanes[0].items[0]);
    expect(within(row).queryByRole("button", { name: "删除待办" })).toBeNull();
    expect(screen.queryByRole("checkbox")).toBeNull();
    expect(screen.queryByRole("button", { name: "修改处理人" })).toBeNull();
  });
});
