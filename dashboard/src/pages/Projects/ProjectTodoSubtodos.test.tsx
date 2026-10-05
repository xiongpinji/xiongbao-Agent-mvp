import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ComponentProps } from "react";
import type { ProjectTodo } from "../../api/modules/projectTodos";

const { listChildren, createChild, remove, language } = vi.hoisted(() => ({
  listChildren: vi.fn(),
  createChild: vi.fn(),
  remove: vi.fn(),
  language: { value: "zh" as "zh" | "en" },
}));

vi.mock("../../api/modules/projectTodos", async (importOriginal) => {
  const actual = await importOriginal<
    typeof import("../../api/modules/projectTodos")
  >();
  return {
    ...actual,
    projectTodosApi: {
      ...actual.projectTodosApi,
      listChildren,
      createChild,
      remove,
    },
  };
});

vi.mock("react-i18next", async () => {
  const resources = {
    zh: (await import("../../locales/zh.json")).default,
    en: (await import("../../locales/en.json")).default,
  };
  return {
    useTranslation: () => ({
      t: (key: string, fallback: string, vars?: Record<string, unknown>) => {
        let found: unknown = resources[language.value];
        for (const part of key.split("."))
          found =
            found && typeof found === "object"
              ? (found as Record<string, unknown>)[part]
              : undefined;
        const text = typeof found === "string" ? found : fallback;
        return vars
          ? text.replace(/{{(\w+)}}/g, (_, name: string) => String(vars[name]))
          : text;
      },
    }),
  };
});

import ProjectTodoSubtodos from "./ProjectTodoSubtodos";

const parent: ProjectTodo = {
  todo_id: "root1",
  project_id: "p1",
  title: "Root",
  description: "",
  description_format: "plain",
  status: "todo",
  start_date: null,
  due_date: null,
  priority_id: null,
  tag_ids: [],
  catalog_revision: 1,
  display_revision: 4,
  creator_user_id: 1,
  assignee_user_id: null,
  version: 3,
  parent_todo_id: null,
  children_count: 1,
  done_children_count: 0,
  children_revision: 2,
  created_at: 1,
  updated_at: 1,
};

const child: ProjectTodo = {
  ...parent,
  todo_id: "child1",
  title: "Child",
  creator_user_id: 2,
  version: 1,
  display_revision: 1,
  parent_todo_id: "root1",
  children_count: 0,
  done_children_count: 0,
  children_revision: null,
};

function setup(
  overrides: Partial<ComponentProps<typeof ProjectTodoSubtodos>> = {},
) {
  const props = {
    projectId: "p1",
    parent,
    role: "owner" as const,
    currentUserId: 1,
    onOpenChild: vi.fn(),
    onParentRefresh: vi.fn().mockResolvedValue(parent),
    onAccessLost: vi.fn(),
    ...overrides,
  };
  const view = render(<ProjectTodoSubtodos {...props} />);
  return { ...props, view };
}

beforeEach(() => {
  vi.clearAllMocks();
  language.value = "zh";
  listChildren.mockResolvedValue({
    items: [child],
    limit: 50,
    has_more: false,
    next_cursor: null,
    children_revision: 2,
    parent_display_revision: 4,
    active_count: 1,
    done_count: 0,
  });
});

describe("ProjectTodoSubtodos", () => {
  it.each(["zh", "en"] as const)(
    "uses safe localized errors in %s without raw transport or unknown reason echo",
    async (locale) => {
      language.value = locale;
      listChildren.mockRejectedValueOnce(
        new Error("PRIVATE network /internal/path secret-value"),
      );
      setup({ currentUserId: 31 });
      fireEvent.click(
        screen.getByRole("button", {
          name: locale === "zh" ? "展开子待办" : "Show subtasks",
        }),
      );
      const fallback =
        locale === "zh" ? "加载子待办失败" : "Failed to load subtasks";
      expect(await screen.findByText(fallback)).toBeVisible();
      expect(
        screen.queryByText(/PRIVATE|internal\/path|secret-value/),
      ).toBeNull();
      listChildren.mockRejectedValueOnce(
        new Error(
          '400 - {"error":{"code":"INVITE_INVALID","message":"PRIVATE server message","details":{"reason":"PRIVATE_unknown_reason"}}}',
        ),
      );
      fireEvent.click(
        screen.getByRole("button", {
          name: locale === "zh" ? "刷新子待办" : "Refresh subtasks",
        }),
      );
      await waitFor(() => expect(listChildren).toHaveBeenCalledTimes(2));
      expect(await screen.findByText(fallback)).toBeVisible();
      expect(screen.queryByText(/PRIVATE/)).toBeNull();
    },
  );

  it.each(["zh", "en"] as const)(
    "translates a known safe reason in %s",
    async (locale) => {
      language.value = locale;
      listChildren.mockRejectedValueOnce(
        new Error(
          '409 - {"error":{"code":"INVITE_INVALID","message":"PRIVATE server message","details":{"reason":"query_changed"}}}',
        ),
      );
      setup({ currentUserId: 32 });
      fireEvent.click(
        screen.getByRole("button", {
          name: locale === "zh" ? "展开子待办" : "Show subtasks",
        }),
      );
      expect(
        await screen.findByText(
          locale === "zh"
            ? "子待办列表已变化，请刷新后重试。"
            : "The subtask list changed. Refresh and try again.",
        ),
      ).toBeVisible();
      expect(screen.queryByText(/PRIVATE|query_changed/)).toBeNull();
    },
  );

  it("expands children, opens a child detail, and deletes through the child old API before refreshing parent", async () => {
    const props = setup();
    fireEvent.click(screen.getByRole("button", { name: "展开子待办" }));
    expect(await screen.findByRole("button", { name: "Child" })).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Child" }));
    expect(props.onOpenChild).toHaveBeenCalledWith(child);

    fireEvent.click(screen.getByRole("button", { name: "删除子待办" }));
    fireEvent.click(await screen.findByRole("button", { name: /^删\s*除$/ }));
    await waitFor(() =>
      expect(remove).toHaveBeenCalledWith("p1", "child1", child.version),
    );
    await waitFor(() => expect(props.onParentRefresh).toHaveBeenCalled());
  });

  it("keeps the original create-child UUID and payload while an unknown submit is retried", async () => {
    createChild
      .mockRejectedValueOnce(
        new Error("Network lost after commit state unknown"),
      )
      .mockResolvedValueOnce({
        item: child,
        children_revision: 2,
        parent_display_revision: 4,
        active_count: 1,
        done_count: 0,
        replayed: true,
      });
    const props = setup();
    fireEvent.click(screen.getByRole("button", { name: "展开子待办" }));
    await screen.findByRole("button", { name: "Child" });

    fireEvent.change(screen.getByPlaceholderText("子待办标题"), {
      target: { value: "first title" },
    });
    fireEvent.change(screen.getByPlaceholderText("描述"), {
      target: { value: "first body" },
    });
    fireEvent.click(screen.getByRole("button", { name: "创建子待办" }));
    await screen.findByText(/上一次请求可能仍在提交/);
    const firstBody = createChild.mock.calls[0][2];

    expect(screen.getByPlaceholderText("子待办标题")).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "重试待确认请求" }));
    await waitFor(() => expect(createChild).toHaveBeenCalledTimes(2));
    expect(createChild.mock.calls[1][2]).toEqual(firstBody);
    await waitFor(() => expect(props.onParentRefresh).toHaveBeenCalled());
  });

  it("retains unknown intent across parent revision refresh and retries the original expected revision", async () => {
    createChild.mockRejectedValueOnce(new Error("Network lost"));
    const props = setup({ currentUserId: 21 });
    fireEvent.click(screen.getByRole("button", { name: "展开子待办" }));
    fireEvent.change(screen.getByPlaceholderText("子待办标题"), {
      target: { value: "original" },
    });
    fireEvent.click(screen.getByRole("button", { name: "创建子待办" }));
    await screen.findByText(/上一次请求可能仍在提交/);
    const original = createChild.mock.calls[0][2];
    props.view.rerender(
      <ProjectTodoSubtodos
        {...props}
        parent={{ ...parent, children_revision: 3, display_revision: 5 }}
      />,
    );
    expect(screen.getByPlaceholderText("子待办标题")).toHaveValue("original");
    expect(screen.getByPlaceholderText("子待办标题")).toBeDisabled();
    createChild.mockResolvedValueOnce({
      item: child,
      children_revision: 3,
      parent_display_revision: 5,
      active_count: 1,
      done_count: 0,
      replayed: true,
    });
    fireEvent.click(screen.getByRole("button", { name: "重试待确认请求" }));
    await waitFor(() => expect(createChild).toHaveBeenCalledTimes(2));
    expect(createChild.mock.calls[1][2]).toEqual(original);
    expect(original.expected_children_revision).toBe(2);
  });

  it("resolves a valid creation receipt even when its parent refresh fails", async () => {
    createChild.mockResolvedValueOnce({
      item: child,
      children_revision: 2,
      parent_display_revision: 4,
      active_count: 1,
      done_count: 0,
      replayed: false,
    });
    const props = setup({
      currentUserId: 22,
      onParentRefresh: vi
        .fn()
        .mockRejectedValue(new Error("Parent GET unavailable")),
    });
    fireEvent.click(screen.getByRole("button", { name: "展开子待办" }));
    fireEvent.change(screen.getByPlaceholderText("子待办标题"), {
      target: { value: "accepted" },
    });
    fireEvent.click(screen.getByRole("button", { name: "创建子待办" }));
    await waitFor(() => expect(props.onParentRefresh).toHaveBeenCalledTimes(1));
    await waitFor(() =>
      expect(screen.getByPlaceholderText("子待办标题")).not.toBeDisabled(),
    );
    expect(screen.queryByRole("button", { name: "重试待确认请求" })).toBeNull();
    expect(screen.getByPlaceholderText("子待办标题")).toHaveValue("");
    expect(createChild).toHaveBeenCalledTimes(1);
  });

  it("drops a late create callback across actor ABA while keeping unresolved creation blocked", async () => {
    let reject!: (error: Error) => void;
    createChild.mockReturnValueOnce(
      new Promise((_resolve, rejection) => {
        reject = rejection;
      }),
    );
    const props = setup({ currentUserId: 23 });
    fireEvent.click(screen.getByRole("button", { name: "展开子待办" }));
    fireEvent.change(screen.getByPlaceholderText("子待办标题"), {
      target: { value: "private original" },
    });
    fireEvent.click(screen.getByRole("button", { name: "创建子待办" }));
    props.view.rerender(<ProjectTodoSubtodos {...props} currentUserId={24} />);
    props.view.rerender(<ProjectTodoSubtodos {...props} />);
    fireEvent.click(screen.getByRole("button", { name: "展开子待办" }));
    await act(async () => reject(new Error("Unknown after exit")));
    expect(screen.getByPlaceholderText("子待办标题")).toHaveValue("");
    expect(screen.getByPlaceholderText("子待办标题")).toBeDisabled();
    expect(screen.getByText(/此前创建结果尚未确认/)).toBeVisible();
    expect(props.onParentRefresh).not.toHaveBeenCalled();
    expect(props.onAccessLost).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "创建子待办" }));
    expect(createChild).toHaveBeenCalledTimes(1);
  });

  it("drops a late list response across actor ABA", async () => {
    let resolve!: (value: unknown) => void;
    listChildren.mockReturnValueOnce(
      new Promise((resolution) => {
        resolve = resolution;
      }),
    );
    const props = setup({ currentUserId: 25 });
    fireEvent.click(screen.getByRole("button", { name: "展开子待办" }));
    props.view.rerender(<ProjectTodoSubtodos {...props} currentUserId={26} />);
    props.view.rerender(<ProjectTodoSubtodos {...props} />);
    await act(async () =>
      resolve({
        items: [child],
        limit: 50,
        has_more: false,
        next_cursor: null,
        children_revision: 2,
        parent_display_revision: 4,
        active_count: 1,
        done_count: 0,
      }),
    );
    expect(screen.queryByRole("button", { name: "Child" })).toBeNull();
  });

  it.each([
    { children_revision: 3 },
    { parent_display_revision: 5 },
    { active_count: 101 },
    { active_count: 2 },
    { done_count: -1 },
    { has_more: true, next_cursor: null },
    { items: [child, child] },
  ])("rejects an incoherent child page %j", async (invalid) => {
    listChildren.mockResolvedValueOnce({
      items: [child],
      limit: 50,
      has_more: false,
      next_cursor: null,
      children_revision: 2,
      parent_display_revision: 4,
      active_count: 1,
      done_count: 0,
      ...invalid,
    });
    setup({ currentUserId: 27 });
    fireEvent.click(screen.getByRole("button", { name: "展开子待办" }));
    await screen.findByRole("alert");
    expect(screen.queryByRole("button", { name: "Child" })).toBeNull();
  });

  it("loads two pages of the same collection without merging a changed watermark", async () => {
    const first = Array.from({ length: 50 }, (_, index) => ({
      ...child,
      todo_id: `c${String(index).padStart(3, "0")}`,
      title: `Page child ${index}`,
    }));
    const root = { ...parent, children_count: 51 };
    const page = {
      items: first,
      limit: 50,
      has_more: true,
      next_cursor: "opaque-page-two",
      children_revision: 2,
      parent_display_revision: 4,
      active_count: 51,
      done_count: 0,
    };
    listChildren.mockResolvedValueOnce(page).mockResolvedValueOnce({
      ...page,
      items: [{ ...child, todo_id: "c050", title: "Late child" }],
      has_more: false,
      next_cursor: null,
      children_revision: 3,
    });
    setup({ currentUserId: 28, parent: root });
    fireEvent.click(screen.getByRole("button", { name: "展开子待办" }));
    await screen.findByRole("button", { name: "Page child 0" });
    fireEvent.click(screen.getByRole("button", { name: "加载更多子待办" }));
    await screen.findByRole("alert");
    expect(listChildren).toHaveBeenLastCalledWith("p1", "root1", {
      limit: 50,
      cursor: "opaque-page-two",
    });
    expect(screen.queryByRole("button", { name: "Late child" })).toBeNull();
    expect(
      screen.getAllByRole("button", { name: /^Page child / }),
    ).toHaveLength(50);
  });

  it("uses the freshly fetched full parent for the child page after creation", async () => {
    const latest = { ...parent, children_revision: 3, display_revision: 5 };
    const page = {
      items: [child],
      limit: 50,
      has_more: false,
      next_cursor: null,
      children_revision: 3,
      parent_display_revision: 5,
      active_count: 1,
      done_count: 0,
    };
    createChild.mockResolvedValueOnce({
      item: child,
      children_revision: 3,
      parent_display_revision: 5,
      active_count: 1,
      done_count: 0,
      replayed: false,
    });
    const props = setup({
      currentUserId: 29,
      onParentRefresh: vi.fn().mockResolvedValue(latest),
    });
    fireEvent.click(screen.getByRole("button", { name: "展开子待办" }));
    await screen.findByRole("button", { name: "Child" });
    listChildren.mockResolvedValueOnce(page);
    fireEvent.change(screen.getByPlaceholderText("子待办标题"), {
      target: { value: "new child" },
    });
    fireEvent.click(screen.getByRole("button", { name: "创建子待办" }));
    await waitFor(() => expect(props.onParentRefresh).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(listChildren).toHaveBeenCalledTimes(2));
    props.view.rerender(<ProjectTodoSubtodos {...props} parent={latest} />);
    expect(await screen.findByRole("button", { name: "Child" })).toBeVisible();
    expect(screen.queryByRole("alert")).toBeNull();
  });
});
