import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ConfigProvider } from "antd";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import PlanBoard from "./PlanBoard";
import {
  deferred,
  makePlanCatalog,
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
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function boardProps() {
  const props = makePlanRendererProps({
    view: makePlanView({ type: "board" }),
    total: 247,
  });
  return {
    ...props,
    lanes: ["todo", "in_progress", "done"].map((status, index) => ({
      ...props.lanes[0],
      laneId: status,
      groupKey: { kind: "status" as const, id: status },
      items: index === 0 ? [makePlanTodo()] : [],
      serverCount: 120 + index,
      nextCursor: `${status}-opaque`,
    })),
  };
}
function dragTo(title: string, target: string) {
  const source = screen.getByRole("article", { name: title });
  const data = new Map<string, string>();
  const dataTransfer = {
    setData: (key: string, value: string) => data.set(key, value),
    getData: (key: string) => data.get(key) ?? "",
    effectAllowed: "",
    dropEffect: "",
  };
  fireEvent.dragStart(source, { dataTransfer });
  fireEvent.dragOver(screen.getByRole("region", { name: target }), {
    dataTransfer,
  });
  fireEvent.drop(screen.getByRole("region", { name: target }), {
    dataTransfer,
  });
}

describe("real bounded board", () => {
  it("uses complete server counts and independent per-column pagination", () => {
    const onLoadMore = vi.fn(async () => true);
    render(<PlanBoard {...boardProps()} onLoadMore={onLoadMore} />);
    expect(screen.getByText("247 条待办")).toBeVisible();
    expect(screen.getByRole("region", { name: "已完成" })).toHaveTextContent(
      "122",
    );
    fireEvent.click(
      within(screen.getByRole("region", { name: "待开始" })).getByRole(
        "button",
        { name: "加载更多" },
      ),
    );
    expect(onLoadMore).toHaveBeenCalledExactlyOnceWith("todo");
  });

  it("sends a versioned status PATCH without moving a card before the returned DTO", async () => {
    const pending = deferred<PlanTodoMutationResult>();
    const onProposeTodoPatch = vi.fn(() => pending.promise);
    render(
      <PlanBoard {...boardProps()} onProposeTodoPatch={onProposeTodoPatch} />,
    );
    dragTo("Synthetic todo", "已完成");
    await waitFor(() =>
      expect(onProposeTodoPatch).toHaveBeenCalledWith(
        expect.objectContaining({ todo_id: "todo1", version: 1 }),
        { changes: { status: "done" }, baseVersion: 1 },
      ),
    );
    expect(
      within(screen.getByRole("region", { name: "待开始" })).getByRole(
        "article",
        { name: "Synthetic todo" },
      ),
    ).toBeVisible();
    expect(
      within(screen.getByRole("region", { name: "已完成" })).queryByRole(
        "article",
      ),
    ).toBeNull();
    await act(async () =>
      pending.resolve({
        status: "saved",
        todo: makePlanTodo({ status: "done", version: 2 }),
      }),
    );
    expect(
      within(screen.getByRole("region", { name: "待开始" })).getByRole(
        "article",
        { name: "Synthetic todo" },
      ),
    ).toBeVisible();
  });

  it("retains a failed proposal and adopts new V only after refresh comparison and confirmation", async () => {
    const onProposeTodoPatch = vi
      .fn()
      .mockResolvedValueOnce({
        status: "conflict",
        messageKey: "projects.planViews.compareFirst",
      })
      .mockResolvedValueOnce({
        status: "saved",
        todo: makePlanTodo({ status: "done", version: 8 }),
      });
    const onRefreshTodoCompare = vi.fn(async () => ({
      state: "ready" as const,
      todo: makePlanTodo({ status: "in_progress", version: 7 }),
      catalogRevision: 1,
      serverToday: "2026-09-29",
      serverTimezone: "Asia/Shanghai",
    }));
    const props = { ...boardProps(), onProposeTodoPatch, onRefreshTodoCompare };
    const { rerender } = render(
      <ConfigProvider prefixCls="octop">
        <PlanBoard {...props} />
      </ConfigProvider>,
    );
    dragTo("Synthetic todo", "已完成");
    const dialog = await screen.findByRole("dialog", { name: "修改状态" });
    await waitFor(() =>
      expect(
        within(dialog).getByRole("button", { name: "保存更改" }),
      ).toBeDisabled(),
    );
    rerender(
      <ConfigProvider prefixCls="octop">
        <PlanBoard
          {...props}
          lanes={[
            { ...props.lanes[0], items: [makePlanTodo({ version: 99 })] },
            ...props.lanes.slice(1),
          ]}
        />
      </ConfigProvider>,
    );
    expect(within(dialog).getByRole("combobox", { name: "状态" })).toHaveValue(
      "done",
    );
    fireEvent.click(within(dialog).getByRole("button", { name: "刷新后比较" }));
    await waitFor(() =>
      expect(within(dialog).getByText("当前服务器值")).toBeVisible(),
    );
    expect(onProposeTodoPatch).toHaveBeenCalledTimes(1);
    fireEvent.click(
      within(dialog).getByRole("button", { name: "确认采用当前值继续编辑" }),
    );
    fireEvent.click(within(dialog).getByRole("button", { name: "保存更改" }));
    await waitFor(() =>
      expect(onProposeTodoPatch).toHaveBeenLastCalledWith(
        expect.objectContaining({ version: 7 }),
        { changes: { status: "done" }, baseVersion: 7 },
      ),
    );
  });

  it("has a keyboard status entry and restores focus after Escape", async () => {
    const user = userEvent.setup();
    render(
      <ConfigProvider prefixCls="octop">
        <PlanBoard {...boardProps()} />
      </ConfigProvider>,
    );
    const trigger = within(
      screen.getByRole("article", { name: "Synthetic todo" }),
    ).getByRole("button", { name: "修改状态" });
    trigger.focus();
    await user.keyboard("{Enter}");
    const dialog = await screen.findByRole("dialog", { name: "修改状态" });
    expect(dialog.closest(".octop-modal")).not.toBeNull();
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(trigger).toHaveFocus();
  });

  it.each(["tag", "source", "assignee"] as const)(
    "disables ambiguous or unauthorized %s dragging with an explanation",
    (group) => {
      const props = boardProps();
      const definition = { ...props.definition, group_by: group };
      const groupId =
        group === "tag"
          ? props.catalog!.tags[0].tag_id
          : group === "source"
          ? "manual"
          : "1";
      render(
        <PlanBoard
          {...props}
          isManager={false}
          definition={definition}
          lanes={[
            { ...props.lanes[0], groupKey: { kind: group, id: groupId } },
          ]}
        />,
      );
      expect(
        screen.getByRole("article", { name: "Synthetic todo" }),
      ).toHaveAttribute("draggable", "false");
      expect(screen.getByRole("note")).toBeVisible();
    },
  );

  it("refuses an archived priority landing but sends active priority with catalog revision", async () => {
    const props = boardProps();
    const catalog = makePlanCatalog();
    catalog.priorities[1] = { ...catalog.priorities[1], archived_at: 2 };
    const onProposeTodoPatch = vi.fn(async () => ({
      status: "saved" as const,
      todo: makePlanTodo({ version: 2 }),
    }));
    const definition = { ...props.definition, group_by: "priority" as const };
    const lanes = [
      null,
      catalog.priorities[0].priority_id,
      catalog.priorities[1].priority_id,
    ].map((id, index) => ({
      ...props.lanes[0],
      laneId: String(index),
      groupKey: { kind: "priority" as const, id },
      items: index === 0 ? [makePlanTodo()] : [],
    }));
    render(
      <PlanBoard
        {...props}
        definition={definition}
        catalog={catalog}
        lanes={lanes}
        onProposeTodoPatch={onProposeTodoPatch}
      />,
    );
    dragTo("Synthetic todo", `${catalog.priorities[1].name} · 已停用`);
    expect(onProposeTodoPatch).not.toHaveBeenCalled();
    dragTo("Synthetic todo", catalog.priorities[0].name);
    await waitFor(() =>
      expect(onProposeTodoPatch).toHaveBeenCalledWith(expect.anything(), {
        changes: { priority_id: catalog.priorities[0].priority_id },
        baseVersion: 1,
        catalogRevision: catalog.revision,
      }),
    );
  });

  it("retains a proposed priority when refreshed comparison archives it and blocks a new assignment", async () => {
    const catalog = makePlanCatalog();
    const priority = catalog.priorities[0];
    const refreshed = makePlanCatalog({ revision: 2 });
    refreshed.priorities[0] = { ...priority, archived_at: 2 };
    const onProposeTodoPatch = vi.fn(async () => ({
      status: "invalid" as const,
      messageKey: "projects.planViews.compareFirst",
    }));
    const onRefreshTodoCompare = vi.fn(async () => ({
      state: "ready" as const,
      todo: makePlanTodo({ version: 3, catalog_revision: 2 }),
      catalogRevision: 2,
      serverToday: "2026-09-29",
      serverTimezone: "Asia/Shanghai",
    }));
    const props = {
      ...boardProps(),
      catalog,
      onProposeTodoPatch,
      onRefreshTodoCompare,
    };
    const { rerender } = render(
      <ConfigProvider prefixCls="octop">
        <PlanBoard {...props} />
      </ConfigProvider>,
    );
    fireEvent.click(screen.getByRole("button", { name: "修改优先级" }));
    const dialog = await screen.findByRole("dialog", { name: "修改优先级" });
    fireEvent.change(within(dialog).getByRole("combobox", { name: "优先级" }), {
      target: { value: priority.priority_id },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "保存更改" }));
    await waitFor(() =>
      expect(
        within(dialog).getByRole("button", { name: "保存更改" }),
      ).toBeDisabled(),
    );
    rerender(
      <ConfigProvider prefixCls="octop">
        <PlanBoard {...props} catalog={refreshed} />
      </ConfigProvider>,
    );
    fireEvent.click(within(dialog).getByRole("button", { name: "刷新后比较" }));
    await waitFor(() =>
      expect(
        within(dialog).getByRole("button", { name: "确认采用当前值继续编辑" }),
      ).toBeVisible(),
    );
    fireEvent.click(
      within(dialog).getByRole("button", { name: "确认采用当前值继续编辑" }),
    );
    const select = within(dialog).getByRole("combobox", { name: "优先级" });
    expect(select).toHaveValue(priority.priority_id);
    expect(
      within(select).getByRole("option", { name: `${priority.name} · 已停用` }),
    ).toBeDisabled();
    expect(
      within(dialog).getByRole("button", { name: "保存更改" }),
    ).toBeDisabled();
    expect(onProposeTodoPatch).toHaveBeenCalledTimes(1);
  });

  it("restores keyboard focus to the originating card action after a successful save", async () => {
    const user = userEvent.setup();
    const onProposeTodoPatch = vi.fn(async () => ({
      status: "saved" as const,
      todo: makePlanTodo({ status: "done", version: 2 }),
    }));
    render(
      <ConfigProvider prefixCls="octop">
        <PlanBoard {...boardProps()} onProposeTodoPatch={onProposeTodoPatch} />
      </ConfigProvider>,
    );
    const trigger = screen.getByRole("button", { name: "修改状态" });
    trigger.focus();
    await user.keyboard("{Enter}");
    const dialog = await screen.findByRole("dialog", { name: "修改状态" });
    await user.selectOptions(
      within(dialog).getByRole("combobox", { name: "状态" }),
      "done",
    );
    await user.click(within(dialog).getByRole("button", { name: "保存更改" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(trigger).toHaveFocus();
  });

  it("loads only an intersecting unloaded column and keeps off-screen groups unloaded", () => {
    let notify: IntersectionObserverCallback = () => {};
    const observe = vi.fn();
    vi.stubGlobal(
      "IntersectionObserver",
      class {
        constructor(callback: IntersectionObserverCallback) {
          notify = callback;
        }
        observe = observe;
        disconnect() {}
      },
    );
    const onLoadFirst = vi.fn(async () => true);
    const props = boardProps();
    render(
      <PlanBoard
        {...props}
        lanes={props.lanes.map((lane) => ({
          ...lane,
          items: [],
          queryFingerprint: null,
          nextCursor: null,
        }))}
        onLoadFirst={onLoadFirst}
      />,
    );
    expect(observe).toHaveBeenCalledTimes(3);
    expect(onLoadFirst).not.toHaveBeenCalled();
    act(() =>
      notify(
        [
          {
            target: screen.getByRole("region", { name: "已完成" }),
            isIntersecting: true,
          },
          {
            target: screen.getByRole("region", { name: "待开始" }),
            isIntersecting: false,
          },
        ] as IntersectionObserverEntry[],
        {} as IntersectionObserver,
      ),
    );
    expect(onLoadFirst).toHaveBeenCalledExactlyOnceWith("done");
  });

  it("keeps legitimate cross-tag duplicates and deduplicates within each lane using the highest version", () => {
    const props = boardProps();
    const tags = props.catalog!.tags;
    const lanes = tags.slice(0, 2).map((tag, index) => ({
      ...props.lanes[0],
      laneId: tag.tag_id,
      groupKey: { kind: "tag" as const, id: tag.tag_id },
      items:
        index === 0
          ? [makePlanTodo({ version: 1 }), makePlanTodo({ version: 7 })]
          : [makePlanTodo({ version: 1 })],
    }));
    const onEditTodo = vi.fn();
    render(
      <PlanBoard
        {...props}
        definition={{ ...props.definition, group_by: "tag" }}
        lanes={lanes}
        onEditTodo={onEditTodo}
      />,
    );
    expect(
      screen.getAllByRole("article", { name: "Synthetic todo" }),
    ).toHaveLength(2);
    const firstLane = screen.getByRole("region", { name: tags[0].name });
    expect(within(firstLane).getAllByRole("article")).toHaveLength(1);
    fireEvent.click(
      within(firstLane).getByRole("button", { name: "编辑待办" }),
    );
    expect(onEditTodo).toHaveBeenCalledWith(
      expect.objectContaining({ version: 7 }),
    );
  });

  it("lets a manager drag to an assignee through a versioned proposal and offers the equivalent keyboard entry", async () => {
    const props = boardProps();
    const onProposeTodoPatch = vi.fn(async () => ({
      status: "saved" as const,
      todo: makePlanTodo({ assignee_user_id: 1, version: 2 }),
    }));
    const lanes = [null, "1"].map((id, index) => ({
      ...props.lanes[0],
      laneId: String(index),
      groupKey: { kind: "assignee" as const, id },
      items: index === 0 ? [makePlanTodo()] : [],
    }));
    render(
      <PlanBoard
        {...props}
        definition={{ ...props.definition, group_by: "assignee" }}
        lanes={lanes}
        onProposeTodoPatch={onProposeTodoPatch}
      />,
    );
    expect(screen.getByRole("button", { name: "修改处理人" })).toBeVisible();
    dragTo("Synthetic todo", "Synthetic member");
    await waitFor(() =>
      expect(onProposeTodoPatch).toHaveBeenCalledExactlyOnceWith(
        expect.objectContaining({ version: 1 }),
        { changes: { assignee_user_id: 1 }, baseVersion: 1 },
      ),
    );
  });
});
