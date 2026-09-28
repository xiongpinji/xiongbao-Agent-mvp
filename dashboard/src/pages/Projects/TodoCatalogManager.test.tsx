import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
const {
  createTag,
  updateTag,
  updatePriority,
  archiveTag,
  restoreTag,
  orderPriorities,
} = vi.hoisted(() => ({
  createTag: vi.fn(),
  updateTag: vi.fn(),
  updatePriority: vi.fn(),
  archiveTag: vi.fn(),
  restoreTag: vi.fn(),
  orderPriorities: vi.fn(),
}));
vi.mock("../../api/modules/projectTodoCatalog", async (importOriginal) => {
  const actual = await importOriginal<
    typeof import("../../api/modules/projectTodoCatalog")
  >();
  return {
    ...actual,
    projectTodoCatalogApi: {
      ...actual.projectTodoCatalogApi,
      createTag,
      updateTag,
      updatePriority,
      archiveTag,
      restoreTag,
      orderPriorities,
    },
  };
});
import TodoCatalogManager from "./TodoCatalogManager";
import { catalogFixture } from "./todoCatalog.testFixtures";
vi.mock("react-i18next", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-i18next")>();
  const { catalogTestTranslation } = await import("./todoCatalog.testFixtures");
  return { ...actual, useTranslation: catalogTestTranslation };
});
beforeEach(() => vi.resetAllMocks());
type CatalogKind = "priority" | "tag";
function optionCatalog(
  kind: CatalogKind,
  revision = 1,
  changes: { name?: string; color?: "red" | "blue" | "green" } = {},
) {
  const catalog = { ...catalogFixture, revision };
  return kind === "priority"
    ? {
        ...catalog,
        priorities: catalog.priorities.map((item, index) =>
          index === 0
            ? { ...item, name: "原名", color: "red" as const, ...changes }
            : item,
        ),
      }
    : {
        ...catalog,
        tags: catalog.tags.map((item, index) =>
          index === 0
            ? { ...item, name: "原名", color: "red" as const, ...changes }
            : item,
        ),
      };
}
function managerTree(
  catalog: typeof catalogFixture,
  kind: CatalogKind,
  refresh = vi.fn(),
  identity: { accountId?: number; projectId?: string } = {},
) {
  return (
    <TodoCatalogManager
      projectId={identity.projectId ?? "p1"}
      accountId={identity.accountId ?? 2}
      canManage
      catalog={catalog}
      kind={kind}
      onClose={vi.fn()}
      onRefresh={refresh}
    />
  );
}
describe("C1 persisted catalog management", () => {
  it.each([
    ["priority", "color"],
    ["priority", "name"],
    ["tag", "color"],
    ["tag", "name"],
  ] as const)(
    "keeps the original %s %s edit baseline across an automatic catalog refresh until an explicit comparison",
    async (kind, changed) => {
      const user = userEvent.setup(),
        refresh = vi.fn().mockResolvedValue(true);
      const update = kind === "priority" ? updatePriority : updateTag;
      update.mockResolvedValue({ revision: 3 });
      const initial = optionCatalog(kind);
      const { rerender } = render(managerTree(initial, kind, refresh));
      await user.click(screen.getByRole("button", { name: "编辑 原名" }));
      const name = screen.getByRole("textbox", { name: "选项名称" });
      const color = screen.getByRole("combobox", { name: "颜色" });
      if (changed === "color") await user.selectOptions(color, "blue");
      else {
        await user.clear(name);
        await user.type(name, "本地名称");
      }
      const current = optionCatalog(
        kind,
        2,
        changed === "color" ? { name: "他人改名" } : { color: "green" },
      );
      rerender(managerTree(current, kind, refresh));
      expect(name).toHaveValue(changed === "color" ? "原名" : "本地名称");
      expect(color).toHaveValue(changed === "color" ? "blue" : "red");
      const save = screen.getByRole("button", { name: "保存选项" });
      expect(save).toBeDisabled();
      fireEvent.click(save);
      expect(update).not.toHaveBeenCalled();
      expect(refresh).not.toHaveBeenCalled();
      await user.click(screen.getByRole("button", { name: "刷新后比较" }));
      await user.click(await screen.findByRole("button", { name: "确认比较" }));
      expect(name).toHaveValue(changed === "color" ? "他人改名" : "本地名称");
      expect(color).toHaveValue(changed === "color" ? "blue" : "green");
      await user.click(save);
      await waitFor(() =>
        expect(update).toHaveBeenCalledWith(
          "p1",
          kind === "priority" ? "pr1" : "tag1",
          {
            expected_revision: 2,
            ...(changed === "color" ? { color: "blue" } : { name: "本地名称" }),
          },
        ),
      );
    },
  );
  it.each(["revision-only", "other-kind"])(
    "locks an unsaved priority draft when the global catalog advances through %s",
    async (change) => {
      const user = userEvent.setup();
      const initial = optionCatalog("priority");
      const { rerender } = render(managerTree(initial, "priority"));
      await user.click(screen.getByRole("button", { name: "编辑 原名" }));
      await user.selectOptions(
        screen.getByRole("combobox", { name: "颜色" }),
        "blue",
      );
      rerender(
        managerTree(
          {
            ...initial,
            revision: 2,
            tags:
              change === "other-kind"
                ? initial.tags.map((item) => ({
                    ...item,
                    name: "其他目录已变",
                  }))
                : initial.tags,
          },
          "priority",
        ),
      );
      expect(screen.getByRole("button", { name: "保存选项" })).toBeDisabled();
      expect(screen.getByRole("textbox", { name: "选项名称" })).toHaveValue(
        "原名",
      );
      expect(updatePriority).not.toHaveBeenCalled();
    },
  );
  it.each(["compared", "confirmed"])(
    "requires a new explicit comparison when the revision advances after the draft was %s",
    async (stage) => {
      const user = userEvent.setup(),
        refresh = vi.fn().mockResolvedValue(true);
      const initial = optionCatalog("tag");
      const { rerender } = render(managerTree(initial, "tag", refresh));
      await user.click(screen.getByRole("button", { name: "编辑 原名" }));
      await user.selectOptions(
        screen.getByRole("combobox", { name: "颜色" }),
        "blue",
      );
      rerender(
        managerTree(
          optionCatalog("tag", 2, { name: "服务器二" }),
          "tag",
          refresh,
        ),
      );
      await user.click(screen.getByRole("button", { name: "刷新后比较" }));
      const confirm = await screen.findByRole("button", { name: "确认比较" });
      if (stage === "confirmed") await user.click(confirm);
      rerender(
        managerTree(
          optionCatalog("tag", 3, { name: "服务器三" }),
          "tag",
          refresh,
        ),
      );
      expect(screen.queryByRole("button", { name: "确认比较" })).toBeNull();
      expect(screen.getByRole("button", { name: "保存选项" })).toBeDisabled();
      expect(screen.getByRole("combobox", { name: "颜色" })).toHaveValue(
        "blue",
      );
      expect(updateTag).not.toHaveBeenCalled();
      await user.click(screen.getByRole("button", { name: "刷新后比较" }));
      await user.click(await screen.findByRole("button", { name: "确认比较" }));
      expect(screen.getByRole("textbox", { name: "选项名称" })).toHaveValue(
        "服务器三",
      );
      updateTag.mockResolvedValue({ revision: 4 });
      await user.click(screen.getByRole("button", { name: "保存选项" }));
      await waitFor(() =>
        expect(updateTag).toHaveBeenCalledWith("p1", "tag1", {
          expected_revision: 3,
          color: "blue",
        }),
      );
    },
  );
  it.each(["false", "rejected"])(
    "retains the edit baseline and draft when manual comparison is %s",
    async (failure) => {
      const user = userEvent.setup(),
        refresh = vi.fn();
      refresh.mockImplementationOnce(() =>
        failure === "false"
          ? Promise.resolve(false)
          : Promise.reject(new Error("503 - 比较失败")),
      );
      const { rerender } = render(
        managerTree(optionCatalog("tag"), "tag", refresh),
      );
      await user.click(screen.getByRole("button", { name: "编辑 原名" }));
      await user.selectOptions(
        screen.getByRole("combobox", { name: "颜色" }),
        "blue",
      );
      rerender(
        managerTree(
          optionCatalog("tag", 2, { name: "他人改名" }),
          "tag",
          refresh,
        ),
      );
      await user.click(screen.getByRole("button", { name: "刷新后比较" }));
      await waitFor(() =>
        expect(
          screen.getByRole("button", { name: "刷新后比较" }),
        ).toBeEnabled(),
      );
      expect(screen.queryByRole("button", { name: "确认比较" })).toBeNull();
      expect(screen.getByRole("button", { name: "保存选项" })).toBeDisabled();
      expect(screen.getByRole("textbox", { name: "选项名称" })).toHaveValue(
        "原名",
      );
      expect(screen.getByRole("combobox", { name: "颜色" })).toHaveValue(
        "blue",
      );
      expect(updateTag).not.toHaveBeenCalled();
    },
  );
  it("preserves the complete priority order baseline and proposal across a background reorder", async () => {
    const user = userEvent.setup(),
      refresh = vi.fn().mockResolvedValue(true);
    const initial = {
      ...catalogFixture,
      priorities: [
        catalogFixture.priorities[0],
        {
          ...catalogFixture.priorities[0],
          priority_id: "pr3",
          name: "次级",
          position: 1,
        },
        {
          ...catalogFixture.priorities[0],
          priority_id: "pr4",
          name: "末级",
          position: 2,
        },
      ],
    };
    const { rerender } = render(managerTree(initial, "priority", refresh));
    await user.click(screen.getByRole("button", { name: "上移 次级" }));
    const current = {
      ...initial,
      revision: 2,
      priorities: [
        initial.priorities[2],
        initial.priorities[0],
        initial.priorities[1],
      ],
    };
    rerender(managerTree(current, "priority", refresh));
    const section = screen.getByText("优先级顺序").closest("section")!;
    expect(
      within(section)
        .getAllByRole("listitem")
        .map((item) => item.textContent?.replace(/[↑↓]/g, "")),
    ).toEqual(["次级", "紧急", "末级"]);
    expect(screen.getByRole("button", { name: "保存顺序" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "刷新后比较" }));
    await user.click(await screen.findByRole("button", { name: "确认比较" }));
    orderPriorities.mockResolvedValue({ revision: 3, priorities: [] });
    await user.click(screen.getByRole("button", { name: "保存顺序" }));
    await waitFor(() =>
      expect(orderPriorities).toHaveBeenCalledWith("p1", {
        expected_revision: 2,
        priority_ids: ["pr3", "pr1", "pr4"],
      }),
    );
  });
  it("requires a complete current range after comparison when active priorities were added and archived", async () => {
    const user = userEvent.setup(),
      refresh = vi.fn().mockResolvedValue(true);
    const initial = {
      ...catalogFixture,
      priorities: [
        catalogFixture.priorities[0],
        {
          ...catalogFixture.priorities[0],
          priority_id: "pr3",
          name: "次级",
          position: 1,
        },
      ],
    };
    const { rerender } = render(managerTree(initial, "priority", refresh));
    await user.click(screen.getByRole("button", { name: "上移 次级" }));
    const current = {
      ...initial,
      revision: 2,
      priorities: [
        { ...initial.priorities[0], archived_at: 3 },
        initial.priorities[1],
        {
          ...initial.priorities[0],
          priority_id: "pr4",
          name: "新增",
          position: 2,
        },
      ],
    };
    rerender(managerTree(current, "priority", refresh));
    expect(screen.getByRole("button", { name: "保存顺序" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "刷新后比较" }));
    await user.click(await screen.findByRole("button", { name: "确认比较" }));
    expect(screen.getByRole("button", { name: "保存顺序" })).toBeDisabled();
    expect(orderPriorities).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "使用最新顺序" }));
    await user.click(screen.getByRole("button", { name: "上移 新增" }));
    orderPriorities.mockResolvedValue({ revision: 3, priorities: [] });
    await user.click(screen.getByRole("button", { name: "保存顺序" }));
    await waitFor(() =>
      expect(orderPriorities).toHaveBeenCalledWith("p1", {
        expected_revision: 2,
        priority_ids: ["pr4", "pr3"],
      }),
    );
  });
  it("discards only the canceled edit and starts a new edit from the latest server item", async () => {
    const user = userEvent.setup();
    const { rerender } = render(managerTree(optionCatalog("tag"), "tag"));
    await user.click(screen.getByRole("button", { name: "编辑 原名" }));
    await user.selectOptions(
      screen.getByRole("combobox", { name: "颜色" }),
      "blue",
    );
    rerender(managerTree(optionCatalog("tag", 2, { name: "他人改名" }), "tag"));
    await user.click(screen.getByRole("button", { name: /取\s*消/ }));
    await user.click(screen.getByRole("button", { name: "编辑 他人改名" }));
    await user.selectOptions(
      screen.getByRole("combobox", { name: "颜色" }),
      "green",
    );
    updateTag.mockResolvedValue({ revision: 3 });
    await user.click(screen.getByRole("button", { name: "保存选项" }));
    await waitFor(() =>
      expect(updateTag).toHaveBeenCalledWith("p1", "tag1", {
        expected_revision: 2,
        color: "green",
      }),
    );
  });
  it("does not authorize a stale field draft when the user discards only the priority order draft", async () => {
    const user = userEvent.setup();
    const initial = {
      ...optionCatalog("priority"),
      priorities: [
        optionCatalog("priority").priorities[0],
        {
          ...catalogFixture.priorities[0],
          priority_id: "pr3",
          name: "次级",
          position: 1,
        },
      ],
    };
    const { rerender } = render(managerTree(initial, "priority"));
    await user.click(screen.getByRole("button", { name: "编辑 原名" }));
    await user.selectOptions(
      screen.getByRole("combobox", { name: "颜色" }),
      "blue",
    );
    await user.click(screen.getByRole("button", { name: "上移 次级" }));
    rerender(managerTree({ ...initial, revision: 2 }, "priority"));
    await user.click(screen.getByRole("button", { name: "使用最新顺序" }));
    expect(screen.getByRole("button", { name: "保存选项" })).toBeDisabled();
    expect(screen.getByRole("combobox", { name: "颜色" })).toHaveValue("blue");
    expect(updatePriority).not.toHaveBeenCalled();
  });
  it("retains the edited values after a current-account 403 without clearing readable catalog data", async () => {
    const user = userEvent.setup();
    updateTag.mockRejectedValue(new Error("403 - 无权修改"));
    render(managerTree(optionCatalog("tag"), "tag"));
    await user.click(screen.getByRole("button", { name: "编辑 原名" }));
    await user.selectOptions(
      screen.getByRole("combobox", { name: "颜色" }),
      "blue",
    );
    await user.click(screen.getByRole("button", { name: "保存选项" }));
    expect(await screen.findByText(/无权修改/)).toBeVisible();
    expect(screen.getByRole("textbox", { name: "选项名称" })).toHaveValue(
      "原名",
    );
    expect(screen.getByRole("combobox", { name: "颜色" })).toHaveValue("blue");
    expect(screen.getByRole("button", { name: "编辑 原名" })).toBeVisible();
  });
  it.each(["account", "project", "kind"])(
    "ignores an old edit failure after the %s identity changes",
    async (identity) => {
      const user = userEvent.setup(),
        refresh = vi.fn();
      let rejectOld!: (error: unknown) => void;
      updateTag.mockReturnValue(
        new Promise((_resolve, reject) => {
          rejectOld = reject;
        }),
      );
      const { rerender } = render(
        managerTree(optionCatalog("tag"), "tag", refresh),
      );
      await user.click(screen.getByRole("button", { name: "编辑 原名" }));
      await user.selectOptions(
        screen.getByRole("combobox", { name: "颜色" }),
        "blue",
      );
      await user.click(screen.getByRole("button", { name: "保存选项" }));
      rerender(
        managerTree(
          optionCatalog(identity === "kind" ? "priority" : "tag"),
          identity === "kind" ? "priority" : "tag",
          refresh,
          identity === "account"
            ? { accountId: 9 }
            : identity === "project"
            ? { projectId: "p2" }
            : {},
        ),
      );
      await act(async () => rejectOld(new Error("409 - 旧编辑失败")));
      expect(screen.getByRole("textbox", { name: "选项名称" })).toHaveValue("");
      expect(screen.queryByText(/旧编辑失败/)).toBeNull();
      expect(screen.queryByRole("button", { name: "刷新后比较" })).toBeNull();
      expect(refresh).not.toHaveBeenCalled();
    },
  );
  it("blocks every catalog write while its snapshot is loading or failed without clearing the draft", async () => {
    const user = userEvent.setup();
    const tree = (loading = false, error: unknown = null) => (
      <TodoCatalogManager
        projectId="p1"
        accountId={2}
        canManage
        catalog={catalogFixture}
        kind="tag"
        loading={loading}
        error={error}
        onClose={vi.fn()}
        onRefresh={vi.fn()}
      />
    );
    const { rerender } = render(tree());
    const name = screen.getByRole("textbox", { name: "选项名称" });
    await user.type(name, "刷新期间的目录草稿");
    await user.selectOptions(
      screen.getByRole("combobox", { name: "颜色" }),
      "purple",
    );
    for (const state of [
      [true, null],
      [false, new Error("503 - 目录暂不可用")],
    ] as const) {
      rerender(tree(...state));
      expect(screen.getByRole("textbox", { name: "选项名称" })).toBe(name);
      expect(name).toHaveValue("刷新期间的目录草稿");
      expect(screen.getByRole("combobox", { name: "颜色" })).toHaveValue(
        "purple",
      );
      for (const label of [
        "新增选项",
        "编辑 设计",
        "停用 设计",
        "恢复 历史标签",
      ]) {
        const button = screen.getByRole("button", { name: label });
        expect(button).toBeDisabled();
        fireEvent.click(button);
      }
      expect(createTag).not.toHaveBeenCalled();
      expect(updateTag).not.toHaveBeenCalled();
      expect(archiveTag).not.toHaveBeenCalled();
      expect(restoreTag).not.toHaveBeenCalled();
    }
    rerender(tree());
    expect(name).toHaveValue("刷新期间的目录草稿");
    expect(screen.getByRole("button", { name: "新增选项" })).toBeEnabled();
  });
  it("uses the latest active priority order after a successful refresh while retaining an unsaved reordered draft", async () => {
    const user = userEvent.setup();
    const second = {
      ...catalogFixture.priorities[0],
      priority_id: "pr3",
      position: 1,
      name: "低",
    };
    const initial = {
      ...catalogFixture,
      priorities: [...catalogFixture.priorities, second],
    };
    const tree = (catalog: typeof initial) => (
      <TodoCatalogManager
        projectId="p1"
        accountId={2}
        canManage
        catalog={catalog}
        kind="priority"
        onClose={vi.fn()}
        onRefresh={vi.fn()}
      />
    );
    const { rerender } = render(tree(initial));
    const archived = {
      ...initial,
      revision: 2,
      priorities: initial.priorities.map((item) =>
        item.priority_id === "pr1" ? { ...item, archived_at: 3 } : item,
      ),
    };
    rerender(tree(archived));
    const orderSection = screen.getByText("优先级顺序").closest("section")!;
    await waitFor(() =>
      expect(within(orderSection).queryByText("紧急")).toBeNull(),
    );
    await waitFor(() =>
      expect(within(orderSection).getByText("低")).toBeVisible(),
    );
    rerender(tree({ ...initial, revision: 3 }));
    await user.click(screen.getByRole("button", { name: "上移 低" }));
    rerender(tree({ ...initial, revision: 4 }));
    expect(
      within(screen.getByText("优先级顺序").closest("section")!).getAllByRole(
        "listitem",
      )[0],
    ).toHaveTextContent("低");
  });
  it("edits colors, archives and restores tags with the read revision, retaining archived data", async () => {
    const user = userEvent.setup(),
      refresh = vi.fn();
    updateTag.mockResolvedValue({
      revision: 2,
      item: { ...catalogFixture.tags[0], color: "green" },
    });
    archiveTag.mockResolvedValue({
      revision: 3,
      item: { ...catalogFixture.tags[0], archived_at: 3 },
    });
    restoreTag.mockResolvedValue({
      revision: 4,
      item: { ...catalogFixture.tags[1], archived_at: null },
    });
    const { rerender } = render(
      <TodoCatalogManager
        projectId="p1"
        accountId={2}
        canManage
        catalog={catalogFixture}
        kind="tag"
        onClose={vi.fn()}
        onRefresh={refresh}
      />,
    );
    await user.click(screen.getByRole("button", { name: "编辑 设计" }));
    await user.selectOptions(
      screen.getByRole("combobox", { name: "颜色" }),
      "green",
    );
    await user.click(screen.getByRole("button", { name: "保存选项" }));
    await waitFor(() =>
      expect(updateTag).toHaveBeenCalledWith("p1", "tag1", {
        expected_revision: 1,
        color: "green",
      }),
    );
    const next = { ...catalogFixture, revision: 2 };
    rerender(
      <TodoCatalogManager
        projectId="p1"
        accountId={2}
        canManage
        catalog={next}
        kind="tag"
        onClose={vi.fn()}
        onRefresh={refresh}
      />,
    );
    await user.click(screen.getByRole("button", { name: "停用 设计" }));
    await waitFor(() =>
      expect(archiveTag).toHaveBeenCalledWith("p1", "tag1", {
        expected_revision: 2,
      }),
    );
    rerender(
      <TodoCatalogManager
        projectId="p1"
        accountId={2}
        canManage
        catalog={{ ...next, revision: 3 }}
        kind="tag"
        onClose={vi.fn()}
        onRefresh={refresh}
      />,
    );
    await user.click(screen.getByRole("button", { name: "恢复 历史标签" }));
    await waitFor(() =>
      expect(restoreTag).toHaveBeenCalledWith("p1", "tag2", {
        expected_revision: 3,
      }),
    );
    expect(screen.getByText(/历史标签/)).toBeVisible();
  });
  it("persists a complete active priority order without archived ids", async () => {
    const user = userEvent.setup();
    const catalog = {
      ...catalogFixture,
      priorities: [
        catalogFixture.priorities[0],
        {
          ...catalogFixture.priorities[0],
          priority_id: "pr3",
          position: 1,
          name: "低",
        },
        catalogFixture.priorities[1],
      ],
    };
    orderPriorities.mockResolvedValue({ revision: 2, priorities: [] });
    render(
      <TodoCatalogManager
        projectId="p1"
        accountId={2}
        canManage
        catalog={catalog}
        kind="priority"
        onClose={vi.fn()}
        onRefresh={vi.fn()}
      />,
    );
    await user.click(screen.getByRole("button", { name: "上移 低" }));
    await user.click(screen.getByRole("button", { name: "保存顺序" }));
    await waitFor(() =>
      expect(orderPriorities).toHaveBeenCalledWith("p1", {
        expected_revision: 1,
        priority_ids: ["pr3", "pr1"],
      }),
    );
  });
  it("does not publish old-account create callbacks after an account change and clears its old draft", async () => {
    const user = userEvent.setup(),
      created = vi.fn(),
      refresh = vi.fn();
    let resolveOld!: (response: unknown) => void;
    createTag.mockReturnValue(
      new Promise((resolve) => {
        resolveOld = resolve;
      }),
    );
    const tree = (accountId: number) => (
      <TodoCatalogManager
        projectId="p1"
        accountId={accountId}
        canManage
        catalog={catalogFixture}
        kind="tag"
        onClose={vi.fn()}
        onRefresh={refresh}
        onCreated={created}
      />
    );
    const { rerender } = render(tree(2));
    await user.type(
      screen.getByRole("textbox", { name: "选项名称" }),
      "旧账号目录草稿",
    );
    await user.click(screen.getByRole("button", { name: "新增选项" }));
    rerender(tree(9));
    expect(screen.getByRole("textbox", { name: "选项名称" })).toHaveValue("");
    resolveOld({
      revision: 2,
      item: { ...catalogFixture.tags[0], tag_id: "private", name: "私密" },
    });
    await waitFor(() => expect(created).not.toHaveBeenCalled());
    expect(refresh).not.toHaveBeenCalled();
  });
  it("creates an option with revision and all seven fixed colors, then refreshes and selects the returned id", async () => {
    const user = userEvent.setup(),
      refresh = vi.fn(),
      created = vi.fn();
    createTag.mockResolvedValue({
      revision: 2,
      item: {
        ...catalogFixture.tags[0],
        tag_id: "new",
        name: "研发",
        color: "purple",
      },
    });
    render(
      <TodoCatalogManager
        projectId="p1"
        accountId={2}
        canManage
        catalog={catalogFixture}
        kind="tag"
        onClose={vi.fn()}
        onRefresh={refresh}
        onCreated={created}
      />,
    );
    await user.type(screen.getByRole("textbox", { name: "选项名称" }), "研发");
    const color = screen.getByRole("combobox", { name: "颜色" });
    expect(color.querySelectorAll("option")).toHaveLength(7);
    await user.selectOptions(color, "purple");
    await user.click(screen.getByRole("button", { name: "新增选项" }));
    await waitFor(() =>
      expect(createTag).toHaveBeenCalledWith("p1", {
        expected_revision: 1,
        name: "研发",
        color: "purple",
      }),
    );
    expect(refresh).toHaveBeenCalledTimes(1);
    expect(created).toHaveBeenCalledWith("tag", "new");
  });
  it("keeps name/color drafts after 409 and requires a refreshed manual comparison", async () => {
    const user = userEvent.setup(),
      refresh = vi.fn();
    createTag.mockRejectedValue(
      new Error(
        '409 - {"error":{"code":"CONFLICT","details":{"reason":"catalog_revision_conflict"}}}',
      ),
    );
    render(
      <TodoCatalogManager
        projectId="p1"
        accountId={2}
        canManage
        catalog={catalogFixture}
        kind="tag"
        onClose={vi.fn()}
        onRefresh={refresh}
      />,
    );
    await user.type(
      screen.getByRole("textbox", { name: "选项名称" }),
      "研发草稿",
    );
    await user.click(screen.getByRole("button", { name: "新增选项" }));
    expect(
      await screen.findByText("目录已被修改，草稿已保留，请刷新后比较再保存。"),
    ).toBeVisible();
    expect(screen.getByRole("textbox", { name: "选项名称" })).toHaveValue(
      "研发草稿",
    );
    await user.click(screen.getByRole("button", { name: "刷新后比较" }));
    expect(refresh).toHaveBeenCalledTimes(1);
  });
  it("denies member writes even if invoked directly", () => {
    render(
      <TodoCatalogManager
        projectId="p1"
        accountId={2}
        canManage={false}
        catalog={catalogFixture}
        kind="tag"
        onClose={vi.fn()}
        onRefresh={vi.fn()}
      />,
    );
    expect(screen.getByRole("button", { name: "新增选项" })).toBeDisabled();
    expect(createTag).not.toHaveBeenCalled();
  });
});
