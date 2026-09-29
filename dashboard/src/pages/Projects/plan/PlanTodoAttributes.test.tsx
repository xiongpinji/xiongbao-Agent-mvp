import {
  fireEvent,
  render as renderReact,
  screen,
  waitFor,
} from "@testing-library/react";
import { useState } from "react";
import type { ReactElement } from "react";
import { createInstance } from "i18next";
import { I18nextProvider } from "react-i18next";
import en from "../../../locales/en.json";
import zh from "../../../locales/zh.json";
import { describe, expect, it, vi } from "vitest";
import PlanTodoAttributes, {
  PlanTodoActions,
  PlanTodoPatchDialog,
} from "./PlanTodoAttributes";
import {
  makePlanCatalog,
  makePlanRendererProps,
  makePlanTodo,
} from "./planView.testFixtures";

vi.unmock("react-i18next");
function render(ui: ReactElement, language = "zh") {
  const i18n = createInstance();
  void i18n.init({
    lng: language,
    fallbackLng: "en",
    resources: { en: { translation: en }, zh: { translation: zh } },
    initImmediate: false,
    showSupportNotice: false,
    interpolation: { escapeValue: false },
  });
  const result = renderReact(
    <I18nextProvider i18n={i18n}>{ui}</I18nextProvider>,
  );
  return {
    ...result,
    rerender: (next: ReactElement) =>
      result.rerender(<I18nextProvider i18n={i18n}>{next}</I18nextProvider>),
  };
}

const catalog = makePlanCatalog();
const members = [{ user_id: 7, username: "七号成员", role: "member" as const }];
const base = {
  catalog,
  members,
  serverToday: "2026-09-29",
  serverTimezone: "Asia/Shanghai",
};

describe("real visible plan attributes", () => {
  it("renders only the requested fields in their saved order", () => {
    const todo = makePlanTodo({
      title: "属性顺序",
      assignee_user_id: 7,
      due_date: "2026-10-03",
    });
    const { container } = render(
      <PlanTodoAttributes
        {...base}
        todo={todo}
        fields={["title", "due_date", "assignee", "status"]}
      />,
    );
    expect(
      Array.from(container.querySelectorAll("[data-plan-field]"), (node) =>
        node.getAttribute("data-plan-field"),
      ),
    ).toEqual(["title", "due_date", "assignee", "status"]);
    expect(screen.getByText("属性顺序")).toBeVisible();
    expect(screen.getByText("2026-10-03")).toBeVisible();
    expect(screen.getByText("七号成员")).toBeVisible();
    expect(screen.queryByText("开始日期")).toBeNull();
    expect(screen.queryByText("标签")).toBeNull();
  });

  it("shows actual manual source without requiring a fictional DTO field", () => {
    const todo = makePlanTodo({ creator_user_id: 7 });
    const onOpenTodo = vi.fn();
    render(
      <PlanTodoAttributes
        {...base}
        todo={todo}
        fields={["title", "source"]}
        onOpenTodo={onOpenTodo}
      />,
    );
    expect(screen.getByText("手动创建")).toBeVisible();
    const title = screen.getByRole("button", { name: /Synthetic todo/ });
    fireEvent.click(title);
    expect(onOpenTodo).toHaveBeenCalledWith(todo, title);
    expect("source" in todo).toBe(false);
  });

  it("uses catalog names and preserves explicit archived markers", () => {
    const priority = catalog.priorities[0];
    const tag = catalog.tags[0];
    const archived = makePlanCatalog({
      priorities: [{ ...priority, archived_at: 2 }],
      tags: [{ ...tag, archived_at: 2 }],
    });
    render(
      <PlanTodoAttributes
        {...base}
        catalog={archived}
        todo={makePlanTodo({
          priority_id: priority.priority_id,
          tag_ids: [tag.tag_id],
        })}
        fields={["priority", "tags"]}
      />,
    );
    expect(screen.getByText(`${priority.name} · 已停用`)).toBeVisible();
    expect(screen.getByText(`${tag.name} · 已停用`)).toBeVisible();
  });

  it("withholds stale catalog names until the todo revision is understood", () => {
    const priority = catalog.priorities[0];
    render(
      <PlanTodoAttributes
        {...base}
        todo={makePlanTodo({
          priority_id: priority.priority_id,
          catalog_revision: catalog.revision + 1,
        })}
        fields={["priority"]}
      />,
    );
    expect(screen.queryByText(priority.name)).toBeNull();
    expect(
      screen.getByText(zh.projects.planViews.catalogUnavailable),
    ).toBeVisible();
  });

  it("formats audit seconds in the server timezone and keeps plan days literal", () => {
    const instant = Date.UTC(2026, 8, 28, 17, 5) / 1000;
    const { container } = render(
      <PlanTodoAttributes
        {...base}
        todo={makePlanTodo({ created_at: instant, due_date: "2026-09-29" })}
        fields={["created_at", "due_date"]}
      />,
    );
    const audit = container.querySelector('[data-plan-field="created_at"] dd');
    expect(audit?.textContent).toMatch(/29/);
    expect(audit?.textContent).toMatch(/01:05/);
    expect(screen.getByText("2026-09-29")).toBeVisible();
  });

  it.each(["zh", "en"])(
    "uses actual %s locale resources for visible fields and manual source",
    (language) => {
      render(
        <PlanTodoAttributes
          {...base}
          todo={makePlanTodo()}
          fields={["title", "status", "source"]}
        />,
        language,
      );
      expect(
        screen.getByText(language === "en" ? "Manual" : "手动创建"),
      ).toBeVisible();
      expect(
        screen.getByText(language === "en" ? "Not started" : "待开始"),
      ).toBeVisible();
      expect(
        screen.getByText(language === "en" ? "Source" : "来源"),
      ).toBeVisible();
      const i18n = createInstance();
      void i18n.init({
        lng: language,
        resources: { en: { translation: en }, zh: { translation: zh } },
        initImmediate: false,
        showSupportNotice: false,
      });
      for (const key of [
        "source.manual",
        "dateErrors.pastDue",
        "refreshCompare",
        "confirmCompare",
        "missingDateConfirmation",
        "weekdays.mon",
      ])
        expect(i18n.exists(`projects.planViews.${key}`)).toBe(true);
    },
  );

  it("closes a missing todo field draft after mutation 404 and restores focus within the legal project", async () => {
    const todo = makePlanTodo({ title: "已删除的字段待办", version: 3 });
    const onProposeTodoPatch = vi.fn(async () => ({
      status: "failed" as const,
      messageKey: "projects.todoDetail.notFound",
    }));
    const renderer = makePlanRendererProps({ onProposeTodoPatch });
    const onClose = vi.fn();
    function FieldDraft() {
      const [trigger, setTrigger] = useState<HTMLElement | null>(null);
      return (
        <>
          <p>合法项目内容</p>
          <button
            type="button"
            onClick={(event) => setTrigger(event.currentTarget)}
          >
            打开字段修改
          </button>
          {trigger && (
            <PlanTodoPatchDialog
              request={{ todo, renderer, mode: "status", trigger }}
              renderer={renderer}
              onClose={() => {
                onClose();
                setTrigger(null);
              }}
            />
          )}
        </>
      );
    }
    render(<FieldDraft />);
    const trigger = screen.getByRole("button", { name: "打开字段修改" });
    fireEvent.click(trigger);
    await waitFor(() => expect(screen.getByText(todo.title)).toBeVisible());
    fireEvent.change(
      screen.getByRole("combobox", {
        name: zh.projects.planViews.fields.status,
      }),
      { target: { value: "done" } },
    );
    fireEvent.click(
      screen.getByRole("button", { name: zh.projects.planViews.saveChanges }),
    );
    await waitFor(() => expect(screen.queryByText(todo.title)).toBeNull());
    expect(onProposeTodoPatch).toHaveBeenCalledExactlyOnceWith(todo, {
      changes: { status: "done" },
      baseVersion: 3,
    });
    expect(onClose).toHaveBeenCalledOnce();
    expect(screen.getByText("合法项目内容")).toBeVisible();
    await waitFor(() => expect(trigger).toHaveFocus());
  });

  it("asks once before deleting and freezes the original selected todo version", async () => {
    const onDeleteTodo = vi.fn();
    const props = makePlanRendererProps({ onDeleteTodo });
    const todo = makePlanTodo({ version: 3 });
    const { rerender } = render(
      <PlanTodoActions todo={todo} renderer={props} />,
    );
    fireEvent.click(screen.getByRole("button", { name: "删除待办" }));
    expect(onDeleteTodo).not.toHaveBeenCalled();
    rerender(
      <PlanTodoActions todo={makePlanTodo({ version: 9 })} renderer={props} />,
    );
    fireEvent.click(await screen.findByRole("button", { name: "确认删除" }));
    expect(onDeleteTodo).toHaveBeenCalledExactlyOnceWith(
      expect.objectContaining({ version: 3 }),
    );
  });
});
