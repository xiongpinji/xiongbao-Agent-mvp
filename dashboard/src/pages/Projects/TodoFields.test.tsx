import { useState } from "react";
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import TodoFields, {
  type TodoFieldsProps,
  type TodoFieldValues,
} from "./TodoFields";
import { catalogFixture } from "./todoCatalog.testFixtures";
vi.mock("react-i18next", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-i18next")>();
  const { catalogTestTranslation } = await import("./todoCatalog.testFixtures");
  return { ...actual, useTranslation: catalogTestTranslation };
});
const empty: TodoFieldValues = {
  start_date: null,
  due_date: null,
  priority_id: null,
  tag_ids: [],
};
function Harness(props: Partial<TodoFieldsProps>) {
  const [values, onChange] = useState(props.values ?? empty);
  return (
    <>
      <TodoFields
        projectId="p1"
        accountId={2}
        catalog={catalogFixture}
        canManage={false}
        {...props}
        values={values}
        onChange={onChange}
      />
      <output>{JSON.stringify(values)}</output>
    </>
  );
}
describe("C1 shared field controls", () => {
  it.each(["account", "project", "404"] as const)(
    "keeps management mounted for a pending snapshot but clears its private draft after %s changes",
    async (clearReason) => {
      const user = userEvent.setup();
      const props: TodoFieldsProps = {
        projectId: "p1",
        accountId: 2,
        canManage: true,
        catalog: catalogFixture,
        values: empty,
      };
      const { rerender } = render(<TodoFields {...props} />);
      await user.click(screen.getByRole("button", { name: "选择优先级" }));
      await user.click(screen.getByRole("button", { name: "管理目录" }));
      const name = screen.getByRole("textbox", { name: "选项名称" });
      await user.type(name, "私有目录草稿");
      await user.selectOptions(
        screen.getByRole("combobox", { name: "颜色" }),
        "purple",
      );
      rerender(<TodoFields {...props} catalog={null} loading />);
      expect(screen.getByRole("textbox", { name: "选项名称" })).toBe(name);
      expect(name).toHaveValue("私有目录草稿");
      expect(screen.getByRole("button", { name: "新增选项" })).toBeDisabled();
      const next =
        clearReason === "account"
          ? { accountId: 9, loading: true }
          : clearReason === "project"
          ? { projectId: "p2", loading: true }
          : { error: new Error('404 - {"error":{"code":"NOT_FOUND"}}') };
      rerender(<TodoFields {...props} catalog={null} {...next} />);
      await waitFor(() =>
        expect(screen.queryByRole("dialog", { name: "管理目录" })).toBeNull(),
      );
      expect(screen.queryByDisplayValue("私有目录草稿")).toBeNull();
    },
  );
  const unavailableStates: Array<[string, Partial<TodoFieldsProps>]> = [
    ["loading", { loading: true }],
    ["failed metadata", { error: new Error("503") }],
    ["disabled", { disabled: true }],
  ];
  it.each(unavailableStates)(
    "blocks mutations in an already open calendar when %s, preserving the draft",
    async (_name, unavailable) => {
      const user = userEvent.setup();
      const onChange = vi.fn();
      const onRetry = vi.fn();
      const props: TodoFieldsProps = {
        projectId: "p1",
        accountId: 2,
        catalog: catalogFixture,
        canManage: false,
        values: { ...empty, due_date: "2026-09-30" },
        onChange,
        onRetry,
      };
      const { rerender } = render(<TodoFields {...props} />);
      await user.click(screen.getByRole("button", { name: "选择截止日期" }));
      rerender(<TodoFields {...props} {...unavailable} />);
      const dialog = screen.getByRole("dialog", { name: "截止日期" });
      const controls = [
        within(dialog).getByRole("button", { name: "2026-09-29" }),
        within(dialog).getByRole("button", { name: /^今\s*天$/ }),
        within(dialog).getByRole("button", { name: /^清\s*空$/ }),
      ];
      for (const control of controls) {
        expect(control).toBeDisabled();
        fireEvent.click(control);
      }
      expect(onChange).not.toHaveBeenCalled();
      if (unavailable.error) {
        const retry = within(dialog).getByRole("button", { name: "重试" });
        expect(retry).toBeEnabled();
        await user.click(retry);
        expect(onRetry).toHaveBeenCalledOnce();
      }
      const cancel = within(dialog).getByRole("button", { name: /^取\s*消$/ });
      expect(cancel).toBeEnabled();
      cancel.focus();
      await user.keyboard("{Escape}");
      await waitFor(() =>
        expect(screen.queryByRole("dialog", { name: "截止日期" })).toBeNull(),
      );
      expect(screen.getByLabelText("截止日期")).toHaveValue("2026-09-30");
      expect(onChange).not.toHaveBeenCalled();
    },
  );
  it("blocks past due-month navigation while keeping start-month navigation available", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.click(screen.getByRole("button", { name: "选择截止日期" }));
    let dialog = screen.getByRole("dialog", { name: "截止日期" });
    expect(
      within(dialog).getByRole("button", { name: "上个月" }),
    ).toBeDisabled();
    await user.click(within(dialog).getByRole("button", { name: "下个月" }));
    expect(within(dialog).getByRole("grid", { name: "2026-10" })).toBeVisible();
    await user.click(within(dialog).getByRole("button", { name: "上个月" }));
    expect(within(dialog).getByRole("grid", { name: "2026-09" })).toBeVisible();
    expect(
      within(dialog).getByRole("button", { name: "下个月" }),
    ).toHaveFocus();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog", { name: "截止日期" })).toBeNull();
    await user.click(screen.getByRole("button", { name: "选择截止日期" }));
    dialog = screen.getByRole("dialog", { name: "截止日期" });
    expect(
      within(dialog).getByRole("button", { name: "2026-09-27" }),
    ).toBeDisabled();
    expect(
      within(dialog).getByRole("button", { name: "2026-09-28" }),
    ).toBeEnabled();
    await waitFor(() =>
      expect(
        within(dialog).getByText("2026-09-28 · Asia/Shanghai"),
      ).toBeVisible(),
    );
    await user.click(
      within(dialog).getByRole("button", { name: "2026-09-28" }),
    );
    expect(screen.getByLabelText("截止日期")).toHaveValue("2026-09-28");
    await user.click(screen.getByRole("button", { name: "选择开始日期" }));
    expect(screen.getByRole("button", { name: "上个月" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "2026-09-27" })).toBeEnabled();
    await user.click(screen.getByRole("button", { name: "2026-09-27" }));
    expect(screen.getByLabelText("开始日期")).toHaveValue("2026-09-27");
  });
  it("lets the old overdue calendar day remain selected while rejecting another past day", async () => {
    const user = userEvent.setup(),
      values = { ...empty, due_date: "2020-01-01" };
    render(<Harness values={values} original={values} />);
    await user.click(screen.getByRole("button", { name: "选择截止日期" }));
    expect(screen.getByRole("button", { name: "2020-01-01" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "2020-01-02" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "下个月" }));
    expect(screen.getByRole("button", { name: "上个月" })).toBeEnabled();
    await user.click(screen.getByRole("button", { name: "上个月" }));
    expect(screen.getByRole("button", { name: "2020-01-01" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "下个月" })).toHaveFocus();
    await user.keyboard("{Escape}");
    await waitFor(() =>
      expect(screen.queryByRole("dialog", { name: "截止日期" })).toBeNull(),
    );
    expect(screen.getByRole("button", { name: "选择截止日期" })).toHaveFocus();
  });
  it("uses server today, allows past starts and keeps full tag selection", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    const start = screen.getByLabelText("开始日期");
    const due = screen.getByLabelText("截止日期");
    expect(start).toHaveAttribute("min", "1900-01-01");
    expect(due).toHaveAttribute("min", "2026-09-28");
    fireEvent.change(start, { target: { value: "2020-01-01" } });
    await user.click(screen.getByRole("button", { name: "选择优先级" }));
    await user.click(screen.getByRole("button", { name: "紧急" }));
    await user.click(screen.getByRole("button", { name: "选择标签" }));
    await user.click(screen.getByRole("checkbox", { name: "设计" }));
    await user.click(screen.getByRole("button", { name: "完成选择" }));
    expect(screen.getByRole("status").textContent).toContain(
      '"start_date":"2020-01-01"',
    );
    expect(screen.getByRole("status").textContent).toContain(
      '"priority_id":"pr1"',
    );
    expect(screen.getByRole("status").textContent).toContain(
      '"tag_ids":["tag1"]',
    );
  });
  it("disables dates when metadata fails and exposes a real retry", async () => {
    const retry = vi.fn();
    render(<Harness catalog={null} error={new Error("503")} onRetry={retry} />);
    expect(screen.getByLabelText("开始日期")).toBeDisabled();
    expect(screen.getByLabelText("截止日期")).toBeDisabled();
    expect(
      screen.getByText("服务器日期加载失败，重试后才能编辑日期。"),
    ).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    expect(retry).toHaveBeenCalledTimes(1);
  });
  it("only offers current archived references for retention/removal and never readds them", async () => {
    const user = userEvent.setup();
    const original = {
      ...empty,
      due_date: "2020-01-01",
      priority_id: "pr2",
      tag_ids: ["tag2"],
    };
    render(<Harness values={original} original={original} />);
    expect(screen.getByLabelText("截止日期")).toHaveValue("2020-01-01");
    await user.click(screen.getByRole("button", { name: "选择标签" }));
    const old = screen.getByRole("checkbox", { name: /历史标签.*已停用/ });
    expect(old).toBeChecked();
    await user.click(old);
    expect(old).toBeDisabled();
    expect(screen.getByRole("status").textContent).toContain('"tag_ids":[]');
    await user.click(screen.getByRole("button", { name: "完成选择" }));
    await user.click(screen.getByRole("button", { name: "选择优先级" }));
    await user.click(screen.getByRole("button", { name: "无" }));
    expect(screen.getByRole("status").textContent).toContain(
      '"priority_id":null',
    );
  });
  it("searches names, explains management permissions, and returns focus on Escape", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    const trigger = screen.getByRole("button", { name: "选择优先级" });
    await user.click(trigger);
    const popup = screen.getByRole("dialog", { name: "优先级" });
    expect(
      within(popup).queryByRole("button", { name: "编辑 紧急" }),
    ).toBeNull();
    expect(within(popup).queryByText(/历史优先级/)).toBeNull();
    expect(
      within(popup).getByRole("button", { name: "管理目录" }),
    ).toBeDisabled();
    await user.type(
      within(popup).getByRole("searchbox", { name: "搜索选项" }),
      "missing",
    );
    expect(within(popup).queryByRole("button", { name: "紧急" })).toBeNull();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog", { name: "优先级" })).toBeNull();
    expect(trigger).toHaveFocus();
  });
  it("opens a priority's edit form directly without selecting or saving it", async () => {
    const user = userEvent.setup();
    render(<Harness canManage values={{ ...empty, priority_id: "pr1" }} />);
    await user.click(screen.getByRole("button", { name: "选择优先级" }));
    const picker = screen.getByRole("dialog", { name: "优先级" });
    await user.click(within(picker).getByRole("button", { name: "编辑 紧急" }));
    const manager = screen
      .getByRole("button", { name: "返回字段选择" })
      .closest('[role="dialog"]')!;
    expect(
      within(manager).getByRole("textbox", { name: "选项名称" }),
    ).toHaveValue("紧急");
    expect(within(manager).getByRole("combobox", { name: "颜色" })).toHaveValue(
      "red",
    );
    expect(screen.getByRole("status").textContent).toContain(
      '"priority_id":"pr1"',
    );
    await user.click(
      within(manager).getByRole("button", { name: "返回字段选择" }),
    );
    expect(screen.queryByRole("button", { name: "返回字段选择" })).toBeNull();
    expect(screen.getByRole("status").textContent).toContain(
      '"priority_id":"pr1"',
    );
  });
});
