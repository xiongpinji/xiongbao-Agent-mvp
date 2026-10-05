import { ConfigProvider } from "antd";
import { useId } from "react";
import { createRequire } from "node:module";
import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {
  afterAll,
  beforeAll,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";
import type { PlanView } from "../../../api/modules/projectPlanViews";
import PlanViewManager, { type PlanViewManagerProps } from "./PlanViewManager";

const localeState = vi.hoisted(() => ({ language: "zh" }));
vi.mock("react-i18next", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-i18next")>();
  const { default: zh } = await import("../../../locales/zh.json");
  const { default: en } = await import("../../../locales/en.json");
  return {
    ...actual,
    useTranslation: () => ({
      t: (
        key: string,
        fallback?: string | Record<string, unknown>,
        args?: Record<string, unknown>,
      ) => {
        const options = typeof fallback === "object" ? fallback : args;
        const value = key
          .split(".")
          .reduce<unknown>(
            (node, part) =>
              node && typeof node === "object"
                ? (node as Record<string, unknown>)[part]
                : undefined,
            localeState.language === "en" ? en : zh,
          );
        const template =
          typeof value === "string"
            ? value
            : typeof fallback === "string"
            ? fallback
            : String(options?.defaultValue ?? key);
        return template.replace(/\{\{(\w+)\}\}/g, (match, name: string) =>
          options && name in options ? String(options[name]) : match,
        );
      },
      i18n: {
        language: localeState.language,
        changeLanguage: () => Promise.resolve(),
      },
    }),
  };
});
beforeEach(() => {
  localeState.language = "zh";
});

// rc-util hardcodes all IDs to test-id in test mode. Its runtime path uses
// React.useId; use that same hook so nested real Antd dialogs have distinct names.
const loadDependency = createRequire(import.meta.url);
const runtimeIds = loadDependency("rc-util/lib/hooks/useId") as {
  default: (id?: string) => string;
};

// jsdom cannot measure pseudo-elements; keep real Antd and its real controls.
const getElementStyle = window.getComputedStyle.bind(window);
beforeAll(() => {
  vi.spyOn(runtimeIds, "default").mockImplementation(function useRuntimeId(
    id?: string,
  ) {
    const generatedId = useId();
    return id ?? generatedId;
  });
  vi.spyOn(window, "getComputedStyle").mockImplementation((element) =>
    getElementStyle(element),
  );
});
afterAll(() => vi.restoreAllMocks());

function tableView(): PlanView & { type: "table" } {
  return {
    view_id: "v-table",
    project_id: "p1",
    name: "表格",
    type: "table",
    definition: {
      schema_version: 1,
      fields: [
        "title",
        "status",
        "assignee",
        "priority",
        "tags",
        "start_date",
        "due_date",
      ],
      group_by: null,
      filters: [{ field: "title", op: "contains", value: "原条件" }],
      sort: [{ field: "updated_at", direction: "desc" }],
    },
    version: 3,
    position: 0,
    archived_at: null,
    created_at: 1,
    updated_at: 2,
  };
}

function boardView(): PlanView {
  return {
    ...tableView(),
    view_id: "v-board",
    name: "看板",
    type: "board",
    definition: {
      schema_version: 1,
      fields: ["title", "status", "assignee", "priority", "tags"],
      group_by: "status",
      filters: [],
      sort: [{ field: "updated_at", direction: "desc" }],
    },
    position: 1,
  };
}

function managerProps(
  overrides: Partial<PlanViewManagerProps> = {},
): PlanViewManagerProps {
  return {
    views: [tableView(), boardView()],
    selectedViewId: "v-table",
    defaultViewId: "v-table",
    role: "owner",
    busy: false,
    revision: 7,
    catalogRevision: 5,
    onSelect: vi.fn(),
    onCreate: vi.fn().mockResolvedValue({ state: "saved" }),
    onRename: vi.fn().mockResolvedValue({ state: "saved" }),
    onChangeType: vi.fn().mockResolvedValue({ state: "saved" }),
    onOrder: vi.fn().mockResolvedValue({ state: "saved" }),
    onDefault: vi.fn().mockResolvedValue({ state: "saved" }),
    onArchive: vi.fn().mockResolvedValue({ state: "saved" }),
    onRestore: vi.fn().mockResolvedValue({ state: "saved" }),
    onRefreshCompare: vi.fn().mockResolvedValue({
      state: "failed",
      messageKey: "projects.planViews.refreshFailed",
    }),
    onConfirmCompare: vi.fn().mockReturnValue(false),
    ...overrides,
  };
}

function managerTree(props: PlanViewManagerProps) {
  return (
    <ConfigProvider prefixCls="octop" theme={{ token: { motion: false } }}>
      <PlanViewManager {...props} />
    </ConfigProvider>
  );
}

async function openManager(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("button", { name: "管理视图" }));
  return screen.getByRole("dialog", { name: "管理视图" });
}

describe("C2 shared view manager behavior", () => {
  it("collects a real accessible manager and lets members select an active shared view", async () => {
    const user = userEvent.setup();
    const props = managerProps({ role: "member" });
    render(managerTree(props));
    await user.click(screen.getByRole("button", { name: "选择视图 看板" }));
    expect(props.onSelect).toHaveBeenCalledWith("v-board");
    expect(await openManager(user)).toBeInTheDocument();
  });

  it("disables shared creation for members while keeping the readable manager open", async () => {
    const user = userEvent.setup();
    const props = managerProps({ role: "member" });
    render(managerTree(props));
    const dialog = await openManager(user);
    const create = within(dialog).getByRole("button", { name: "新建视图" });
    expect(create).toBeDisabled();
    fireEvent.click(create);
    expect(props.onCreate).not.toHaveBeenCalled();
    expect(
      within(dialog).getByText("只有项目所有者或管理员可以修改共享视图。"),
    ).toBeInTheDocument();
  });

  it("submits a renamed view with the complete immutable modal baseline after another view changes", async () => {
    const user = userEvent.setup();
    const props = managerProps();
    const original = tableView();
    const { rerender } = render(managerTree(props));
    const dialog = await openManager(user);
    await user.click(
      within(dialog).getByRole("button", { name: "重命名 表格" }),
    );
    const name = within(dialog).getByRole("textbox", { name: "视图名称" });
    await user.clear(name);
    await user.type(name, "本地名称");
    rerender(
      managerTree({
        ...props,
        revision: 12,
        views: [
          tableView(),
          { ...boardView(), version: 10, name: "另一个看板" },
        ],
      }),
    );
    await user.click(within(dialog).getByRole("button", { name: "保存名称" }));
    expect(props.onRename).toHaveBeenCalledWith({
      baseline: { view: original, collectionRevision: 7, catalogRevision: 5 },
      name: "本地名称",
    });
  });

  it("locks a same-view version advance and keeps the local name until explicit comparison", async () => {
    const user = userEvent.setup();
    const props = managerProps();
    const { rerender } = render(managerTree(props));
    const dialog = await openManager(user);
    await user.click(
      within(dialog).getByRole("button", { name: "重命名 表格" }),
    );
    const name = within(dialog).getByRole("textbox", { name: "视图名称" });
    await user.clear(name);
    await user.type(name, "未保存名称");
    rerender(
      managerTree({
        ...props,
        revision: 8,
        views: [{ ...tableView(), version: 4, name: "别人名称" }, boardView()],
      }),
    );
    expect(name).toHaveValue("未保存名称");
    expect(
      within(dialog).getByRole("button", { name: "保存名称" }),
    ).toBeDisabled();
    expect(props.onRefreshCompare).not.toHaveBeenCalled();
    expect(props.onConfirmCompare).not.toHaveBeenCalled();
  });

  it("creates calendar with its complete date definition and the collection captured when opening", async () => {
    const user = userEvent.setup();
    const props = managerProps();
    render(managerTree(props));
    const dialog = await openManager(user);
    await user.click(within(dialog).getByRole("button", { name: "新建视图" }));
    await user.type(
      within(dialog).getByRole("textbox", { name: "视图名称" }),
      "排期",
    );
    await user.click(
      within(dialog).getByRole("combobox", { name: "视图类型" }),
    );
    await user.click(screen.getByRole("option", { name: "日历" }));
    await user.click(within(dialog).getByRole("button", { name: "创建视图" }));
    expect(props.onCreate).toHaveBeenCalledWith({
      baseline: { revision: 7, catalogRevision: 5 },
      name: "排期",
      type: "calendar",
      definition: {
        schema_version: 1,
        fields: ["title", "status", "assignee", "priority"],
        group_by: null,
        filters: [],
        sort: [{ field: "updated_at", direction: "desc" }],
        calendar: { date_basis: "due_date", mode: "month" },
      },
    });
  });

  it("protects the last active view from archive without treating archived items as active", async () => {
    const user = userEvent.setup();
    const props = managerProps({
      views: [tableView(), { ...boardView(), archived_at: 10 }],
    });
    render(managerTree(props));
    const dialog = await openManager(user);
    const archive = within(dialog).getByRole("button", { name: "停用 表格" });
    expect(archive).toBeDisabled();
    fireEvent.click(archive);
    expect(props.onArchive).not.toHaveBeenCalled();
  });

  it("orders exactly the active view IDs using the opening collection rather than newer props", async () => {
    const user = userEvent.setup();
    const props = managerProps();
    const { rerender } = render(managerTree(props));
    const dialog = await openManager(user);
    await user.click(within(dialog).getByRole("button", { name: "上移 看板" }));
    rerender(managerTree({ ...props, revision: 9 }));
    await user.click(within(dialog).getByRole("button", { name: "保存顺序" }));
    expect(props.onOrder).toHaveBeenCalledWith({
      baseline: { revision: 7, catalogRevision: 5 },
      viewIds: ["v-board", "v-table"],
    });
  });

  it.each(["list", "table", "board", "gantt"] as const)(
    "creates %s with a complete type-valid definition",
    async (type) => {
      const user = userEvent.setup();
      const props = managerProps();
      render(managerTree(props));
      const dialog = await openManager(user);
      await user.click(
        within(dialog).getByRole("button", { name: "新建视图" }),
      );
      fireEvent.change(
        within(dialog).getByRole("textbox", { name: "视图名称" }),
        { target: { value: "新增视图" } },
      );
      const labels = {
        list: "列表",
        table: "表格",
        board: "看板",
        gantt: "甘特",
      };
      await user.click(
        within(dialog).getByRole("combobox", { name: "视图类型" }),
      );
      await user.click(screen.getByRole("option", { name: labels[type] }));
      await user.click(
        within(dialog).getByRole("button", { name: "创建视图" }),
      );
      const expected =
        type === "board"
          ? boardView().definition
          : type === "gantt"
          ? {
              schema_version: 1,
              fields: ["title", "status", "assignee", "priority"],
              group_by: null,
              filters: [],
              sort: [{ field: "updated_at", direction: "desc" }],
              gantt: { zoom: "week" },
            }
          : type === "table"
          ? { ...tableView().definition, filters: [], show_subtodos: false }
          : { ...tableView().definition, filters: [] };
      expect(props.onCreate).toHaveBeenCalledWith({
        baseline: { revision: 7, catalogRevision: 5 },
        name: "新增视图",
        type,
        definition: expected,
      });
    },
  );

  it("changes type with an immutable full baseline and removes the previous type's date options", async () => {
    const user = userEvent.setup();
    const view: PlanView = {
      ...tableView(),
      type: "calendar",
      definition: {
        ...tableView().definition,
        group_by: null,
        calendar: { date_basis: "start_date", mode: "week" },
      },
    };
    const props = managerProps({ views: [view, boardView()] });
    render(managerTree(props));
    const dialog = await openManager(user);
    await user.click(
      within(dialog).getByRole("button", { name: "更改类型 表格" }),
    );
    await user.click(
      within(dialog).getByRole("combobox", { name: "视图类型" }),
    );
    await user.click(screen.getByRole("option", { name: "看板" }));
    await user.click(within(dialog).getByRole("button", { name: "保存类型" }));
    expect(props.onChangeType).toHaveBeenCalledWith({
      baseline: { view, collectionRevision: 7, catalogRevision: 5 },
      type: "board",
      definition: { ...tableView().definition, group_by: "status" },
    });
  });

  it("uses the independent original collection for default, distinct from the selected view", async () => {
    const user = userEvent.setup();
    const props = managerProps({ selectedViewId: "v-board" });
    render(managerTree(props));
    const dialog = await openManager(user);
    expect(
      within(dialog).getByRole("button", { name: "设为默认 表格" }),
    ).toBeDisabled();
    await user.click(
      within(dialog).getByRole("button", { name: "设为默认 看板" }),
    );
    expect(props.onDefault).toHaveBeenCalledWith({
      baseline: { revision: 7, catalogRevision: 5 },
      viewId: "v-board",
    });
  });

  it("archives using the captured full view and never changes readable data after a failed write", async () => {
    const user = userEvent.setup();
    const props = managerProps({
      onArchive: vi.fn().mockResolvedValue({
        state: "failed",
        messageKey: "projects.planViews.saveFailed",
      }),
    });
    render(managerTree(props));
    const dialog = await openManager(user);
    await user.click(within(dialog).getByRole("button", { name: "停用 看板" }));
    expect(props.onArchive).toHaveBeenCalledWith({
      view: boardView(),
      collectionRevision: 7,
      catalogRevision: 5,
    });
    expect(
      within(dialog).getByText("保存失败，草稿已保留，请重试。"),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "选择视图 看板" }),
    ).toBeInTheDocument();
  });

  it("restores an archived view using its captured baseline", async () => {
    const user = userEvent.setup();
    const archived = { ...boardView(), archived_at: 10 };
    const props = managerProps({ views: [tableView(), archived] });
    render(managerTree(props));
    const dialog = await openManager(user);
    await user.click(within(dialog).getByRole("button", { name: "停用视图" }));
    await user.click(within(dialog).getByRole("button", { name: "恢复 看板" }));
    expect(props.onRestore).toHaveBeenCalledWith({
      view: archived,
      collectionRevision: 7,
      catalogRevision: 5,
    });
  });

  it.each(["active", "total"] as const)(
    "blocks creation at the %s view limit",
    async (limit) => {
      const user = userEvent.setup();
      const views = Array.from(
        { length: limit === "active" ? 30 : 100 },
        (_, index) => ({
          ...tableView(),
          view_id: `v-${index}`,
          name: `视图${index}`,
          position: index,
          archived_at: limit === "total" && index > 0 ? 10 : null,
        }),
      );
      const props = managerProps({ views });
      render(managerTree(props));
      const dialog = await openManager(user);
      expect(
        within(dialog).getByRole("button", { name: "新建视图" }),
      ).toBeDisabled();
      expect(props.onCreate).not.toHaveBeenCalled();
    },
  );

  it("retains a failed name and original baseline, then retries only on an explicit save", async () => {
    const user = userEvent.setup();
    const props = managerProps({
      onRename: vi
        .fn()
        .mockResolvedValueOnce({
          state: "failed",
          messageKey: "projects.planViews.saveFailed",
        })
        .mockResolvedValue({ state: "saved" }),
    });
    render(managerTree(props));
    const dialog = await openManager(user);
    await user.click(
      within(dialog).getByRole("button", { name: "重命名 表格" }),
    );
    const name = within(dialog).getByRole("textbox", { name: "视图名称" });
    fireEvent.change(name, { target: { value: "保留草稿" } });
    await user.click(within(dialog).getByRole("button", { name: "保存名称" }));
    expect(name).toHaveValue("保留草稿");
    expect(props.onRename).toHaveBeenCalledTimes(1);
    await user.click(within(dialog).getByRole("button", { name: "保存名称" }));
    expect(props.onRename).toHaveBeenCalledTimes(2);
    expect(props.onRename).toHaveBeenLastCalledWith({
      baseline: {
        view: tableView(),
        collectionRevision: 7,
        catalogRevision: 5,
      },
      name: "保留草稿",
    });
  });

  it("keeps catalog advancement locked until explicit successful comparison confirmation", async () => {
    const user = userEvent.setup();
    const props = managerProps();
    const comparison = {
      baseline: {
        view: { ...tableView(), version: 4 },
        collectionRevision: 8,
        catalogRevision: 6,
      },
      scope: {
        lifetime: 1,
        accountId: 2,
        projectId: "p1",
        viewId: "v-table",
        queryGeneration: 1,
        channel: "view-compare",
        operationGeneration: 1,
      },
    };
    props.onRefreshCompare = vi
      .fn()
      .mockResolvedValue({ state: "ready", comparison });
    props.onConfirmCompare = vi.fn().mockReturnValue(true);
    const { rerender } = render(managerTree(props));
    const dialog = await openManager(user);
    await user.click(
      within(dialog).getByRole("button", { name: "重命名 表格" }),
    );
    fireEvent.change(
      within(dialog).getByRole("textbox", { name: "视图名称" }),
      { target: { value: "我的名称" } },
    );
    rerender(
      managerTree({
        ...props,
        views: [comparison.baseline.view, boardView()],
        revision: 8,
        catalogRevision: 6,
      }),
    );
    expect(
      within(dialog).getByRole("button", { name: "保存名称" }),
    ).toBeDisabled();
    await user.click(
      within(dialog).getByRole("button", { name: "刷新后比较" }),
    );
    expect(props.onConfirmCompare).not.toHaveBeenCalled();
    await user.click(
      within(dialog).getByRole("button", { name: "确认采用当前值继续编辑" }),
    );
    expect(
      within(dialog).getByRole("textbox", { name: "视图名称" }),
    ).toHaveValue("我的名称");
    await user.click(within(dialog).getByRole("button", { name: "保存名称" }));
    expect(props.onRename).toHaveBeenCalledWith({
      baseline: comparison.baseline,
      name: "我的名称",
    });
  });

  it("renders real English resources with a distinct project default marker", async () => {
    localeState.language = "en";
    const user = userEvent.setup();
    render(managerTree(managerProps()));
    await user.click(screen.getByRole("button", { name: "Manage views" }));
    expect(
      screen.getByRole("dialog", { name: "Manage views" }),
    ).toBeInTheDocument();
    expect(screen.getAllByText("Project default").length).toBeGreaterThan(0);
  });
});
describe("C2 manager conflict and permission boundaries", () => {
  function readyComparison(
    version = 4,
    collectionRevision = 8,
    catalogRevision = 6,
  ) {
    return {
      baseline: {
        view: { ...tableView(), version },
        collectionRevision,
        catalogRevision,
      },
      scope: {
        lifetime: 1,
        accountId: 2,
        projectId: "p1",
        viewId: "v-table",
        queryGeneration: 1,
        channel: "view-compare",
        operationGeneration: 1,
      },
    };
  }

  it("keeps all shared writes disabled for a member, including archived restore and order", async () => {
    const user = userEvent.setup();
    const props = managerProps({
      role: "member",
      views: [
        tableView(),
        boardView(),
        { ...boardView(), view_id: "v-old", name: "旧看板", archived_at: 10 },
      ],
    });
    render(managerTree(props));
    const dialog = await openManager(user);
    for (const name of [
      "新建视图",
      "重命名 表格",
      "更改类型 看板",
      "上移 看板",
      "设为默认 看板",
      "停用 看板",
      "保存顺序",
    ]) {
      const control = within(dialog).getByRole("button", { name });
      expect(control).toBeDisabled();
      fireEvent.click(control);
    }
    await user.click(within(dialog).getByRole("button", { name: "停用视图" }));
    const restore = within(dialog).getByRole("button", { name: "恢复 旧看板" });
    expect(restore).toBeDisabled();
    fireEvent.click(restore);
    for (const handler of [
      props.onCreate,
      props.onRename,
      props.onChangeType,
      props.onOrder,
      props.onDefault,
      props.onArchive,
      props.onRestore,
    ])
      expect(handler).not.toHaveBeenCalled();
  });

  it("retains the open name when the role becomes member and never dispatches a forced save", async () => {
    const user = userEvent.setup();
    const props = managerProps();
    const { rerender } = render(managerTree(props));
    const dialog = await openManager(user);
    await user.click(
      within(dialog).getByRole("button", { name: "重命名 表格" }),
    );
    fireEvent.change(
      within(dialog).getByRole("textbox", { name: "视图名称" }),
      { target: { value: "仍然保留" } },
    );
    rerender(managerTree({ ...props, role: "member" }));
    const save = within(dialog).getByRole("button", { name: "保存名称" });
    expect(save).toBeDisabled();
    fireEvent.click(save);
    expect(props.onRename).not.toHaveBeenCalled();
    expect(
      within(dialog).getByRole("textbox", { name: "视图名称" }),
    ).toHaveValue("仍然保留");
  });

  it("deeply captures the opening full view rather than keeping mutable props references", async () => {
    const user = userEvent.setup();
    const props = managerProps();
    const opening = structuredClone(props.views[0]);
    render(managerTree(props));
    const dialog = await openManager(user);
    await user.click(
      within(dialog).getByRole("button", { name: "重命名 表格" }),
    );
    const clause = props.views[0].definition.filters[0];
    if (clause.field === "title") clause.value = "随后被修改的引用";
    props.views[0].name = "后来名称";
    fireEvent.change(
      within(dialog).getByRole("textbox", { name: "视图名称" }),
      { target: { value: "我的名称" } },
    );
    await user.click(within(dialog).getByRole("button", { name: "保存名称" }));
    expect(props.onRename).toHaveBeenCalledWith({
      baseline: { view: opening, collectionRevision: 7, catalogRevision: 5 },
      name: "我的名称",
    });
  });

  it("preserves a type proposal after failure and retries its entire definition on an explicit save", async () => {
    const user = userEvent.setup();
    const props = managerProps({
      onChangeType: vi
        .fn()
        .mockResolvedValueOnce({
          state: "failed",
          messageKey: "projects.planViews.saveFailed",
        })
        .mockResolvedValue({ state: "saved" }),
    });
    render(managerTree(props));
    const dialog = await openManager(user);
    await user.click(
      within(dialog).getByRole("button", { name: "更改类型 表格" }),
    );
    await user.click(
      within(dialog).getByRole("combobox", { name: "视图类型" }),
    );
    await user.click(screen.getByRole("option", { name: "甘特" }));
    await user.click(within(dialog).getByRole("button", { name: "保存类型" }));
    expect(
      within(dialog)
        .getByRole("combobox", { name: "视图类型" })
        .closest(".octop-select"),
    ).toHaveTextContent("甘特");
    expect(props.onChangeType).toHaveBeenCalledTimes(1);
    await user.click(within(dialog).getByRole("button", { name: "保存类型" }));
    expect(props.onChangeType).toHaveBeenCalledTimes(2);
    expect(props.onChangeType).toHaveBeenLastCalledWith({
      baseline: {
        view: tableView(),
        collectionRevision: 7,
        catalogRevision: 5,
      },
      type: "gantt",
      definition: {
        ...tableView().definition,
        group_by: null,
        gantt: { zoom: "week" },
      },
    });
  });

  it("does not upgrade the independent collection when a single-view comparison is confirmed", async () => {
    const user = userEvent.setup();
    const comparison = readyComparison(4, 8, 5);
    const props = managerProps({
      onRefreshCompare: vi
        .fn()
        .mockResolvedValue({ state: "ready", comparison }),
      onConfirmCompare: vi.fn().mockReturnValue(true),
    });
    const { rerender } = render(managerTree(props));
    const dialog = await openManager(user);
    await user.click(
      within(dialog).getByRole("button", { name: "重命名 表格" }),
    );
    rerender(
      managerTree({
        ...props,
        revision: 8,
        views: [comparison.baseline.view, boardView()],
      }),
    );
    await user.click(
      within(dialog).getByRole("button", { name: "刷新后比较" }),
    );
    await user.click(
      await within(dialog).findByRole("button", {
        name: "确认采用当前值继续编辑",
      }),
    );
    await user.click(
      within(dialog).getByRole("button", { name: "取消这次编辑" }),
    );
    await user.click(within(dialog).getByRole("button", { name: "上移 看板" }));
    await user.click(within(dialog).getByRole("button", { name: "保存顺序" }));
    expect(props.onOrder).toHaveBeenCalledWith({
      baseline: { revision: 7, catalogRevision: 5 },
      viewIds: ["v-board", "v-table"],
    });
  });

  it("keeps a failed order and adopts a new collection only after current-list comparison confirmation", async () => {
    const user = userEvent.setup();
    const comparison = readyComparison(3, 8, 5);
    const props = managerProps({
      onOrder: vi
        .fn()
        .mockResolvedValueOnce({
          state: "stale",
          messageKey: "projects.planViews.stale",
        })
        .mockResolvedValue({
          state: "failed",
          messageKey: "projects.planViews.saveFailed",
        }),
      onRefreshCompare: vi
        .fn()
        .mockResolvedValue({ state: "ready", comparison }),
      onConfirmCompare: vi.fn().mockReturnValue(true),
    });
    const { rerender } = render(managerTree(props));
    const dialog = await openManager(user);
    await user.click(within(dialog).getByRole("button", { name: "上移 看板" }));
    await user.click(within(dialog).getByRole("button", { name: "保存顺序" }));
    rerender(managerTree({ ...props, revision: 8 }));
    expect(
      within(dialog).getByRole("button", { name: "保存顺序" }),
    ).toBeDisabled();
    await user.click(
      within(dialog).getByRole("button", { name: "刷新后比较" }),
    );
    expect(props.onConfirmCompare).not.toHaveBeenCalled();
    await user.click(
      await within(dialog).findByRole("button", {
        name: "确认采用当前值继续编辑",
      }),
    );
    await user.click(within(dialog).getByRole("button", { name: "保存顺序" }));
    expect(props.onOrder).toHaveBeenLastCalledWith({
      baseline: { revision: 8, catalogRevision: 5 },
      viewIds: ["v-board", "v-table"],
    });
  });

  it("does not confirm collection comparison before the accepted catalog prop arrives", async () => {
    const user = userEvent.setup();
    const comparison = readyComparison(3, 8, 6);
    const props = managerProps({
      revision: 8,
      catalogRevision: 5,
      onRefreshCompare: vi
        .fn()
        .mockResolvedValue({ state: "ready", comparison }),
      onConfirmCompare: vi.fn().mockReturnValue(true),
    });
    const { rerender } = render(managerTree(props));
    const dialog = await openManager(user);
    await user.click(
      within(dialog).getByRole("button", { name: "刷新后比较" }),
    );
    const confirm = await within(dialog).findByRole("button", {
      name: "确认采用当前值继续编辑",
    });
    expect(confirm).toBeDisabled();
    fireEvent.click(confirm);
    expect(props.onConfirmCompare).not.toHaveBeenCalled();
    rerender(managerTree({ ...props, catalogRevision: 6 }));
    expect(confirm).toBeEnabled();
  });

  it("rejects a comparison invalidated by another list change while preserving the order", async () => {
    const user = userEvent.setup();
    const comparison = readyComparison(3, 8, 5);
    const props = managerProps({
      revision: 8,
      onRefreshCompare: vi
        .fn()
        .mockResolvedValue({ state: "ready", comparison }),
      onConfirmCompare: vi.fn().mockReturnValue(true),
    });
    const { rerender } = render(managerTree(props));
    const dialog = await openManager(user);
    await user.click(within(dialog).getByRole("button", { name: "上移 看板" }));
    await user.click(
      within(dialog).getByRole("button", { name: "刷新后比较" }),
    );
    await within(dialog).findByRole("button", {
      name: "确认采用当前值继续编辑",
    });
    rerender(managerTree({ ...props, revision: 9 }));
    const confirm = within(dialog).getByRole("button", {
      name: "确认采用当前值继续编辑",
    });
    expect(confirm).toBeDisabled();
    fireEvent.click(confirm);
    expect(props.onConfirmCompare).not.toHaveBeenCalled();
    expect(within(dialog).getByText("顺序未保存")).toBeInTheDocument();
  });

  it("allows explicit comparison of a stale archived row before restoring its new baseline", async () => {
    const user = userEvent.setup();
    const archived = { ...boardView(), archived_at: 10 };
    const current = { ...archived, version: 4 };
    const comparison = {
      ...readyComparison(4, 8, 5),
      baseline: { view: current, collectionRevision: 8, catalogRevision: 5 },
      scope: { ...readyComparison().scope, viewId: current.view_id },
    };
    const props = managerProps({
      views: [tableView(), archived],
      onRefreshCompare: vi
        .fn()
        .mockResolvedValue({ state: "ready", comparison }),
      onConfirmCompare: vi.fn().mockReturnValue(true),
    });
    const { rerender } = render(managerTree(props));
    const dialog = await openManager(user);
    await user.click(within(dialog).getByRole("button", { name: "停用视图" }));
    rerender(
      managerTree({ ...props, revision: 8, views: [tableView(), current] }),
    );
    expect(
      within(dialog).getByRole("button", { name: "恢复 看板" }),
    ).toBeDisabled();
    await user.click(
      within(dialog).getByRole("button", { name: "刷新后比较 看板" }),
    );
    expect(props.onRefreshCompare).toHaveBeenCalledWith({
      view: archived,
      collectionRevision: 7,
      catalogRevision: 5,
    });
    await user.click(
      await within(dialog).findByRole("button", {
        name: "确认采用当前值继续编辑",
      }),
    );
    await user.click(within(dialog).getByRole("button", { name: "恢复 看板" }));
    expect(props.onRestore).toHaveBeenCalledWith(comparison.baseline);
  });

  it("protects the active limit on restore and keeps the archived data readable", async () => {
    const user = userEvent.setup();
    const archived = { ...boardView(), archived_at: 10 };
    const views = [
      ...Array.from({ length: 30 }, (_, index) => ({
        ...tableView(),
        view_id: "v-" + index,
        name: "有效" + index,
        position: index,
      })),
      archived,
    ];
    const props = managerProps({ views });
    render(managerTree(props));
    const dialog = await openManager(user);
    await user.click(within(dialog).getByRole("button", { name: "停用视图" }));
    const restore = within(dialog).getByRole("button", { name: "恢复 看板" });
    expect(restore).toBeDisabled();
    fireEvent.click(restore);
    expect(props.onRestore).not.toHaveBeenCalled();
    expect(
      within(dialog).getByText("看板", { selector: "strong" }),
    ).toBeInTheDocument();
  });

  it("preserves the complete create draft after Escape and continue editing", async () => {
    const user = userEvent.setup();
    render(managerTree(managerProps()));
    const dialog = await openManager(user);
    await user.click(within(dialog).getByRole("button", { name: "新建视图" }));
    const name = within(dialog).getByRole("textbox", { name: "视图名称" });
    fireEvent.change(name, { target: { value: "未创建" } });
    name.focus();
    // rc-dialog requires the native Escape keyCode, absent from userEvent/jsdom.
    fireEvent.keyDown(name, { key: "Escape", code: "Escape", keyCode: 27 });
    expect(
      screen.getByRole("dialog", { name: "保留或舍弃未应用的草稿" }),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "继续编辑" }));
    expect(name).toHaveValue("未创建");
  });

  it("counts Unicode name characters and rejects overlong names without dispatching", async () => {
    const user = userEvent.setup();
    const props = managerProps();
    render(managerTree(props));
    const dialog = await openManager(user);
    await user.click(within(dialog).getByRole("button", { name: "新建视图" }));
    const name = within(dialog).getByRole("textbox", { name: "视图名称" });
    fireEvent.change(name, { target: { value: "😀".repeat(41) } });
    expect(
      within(dialog).getByRole("button", { name: "创建视图" }),
    ).toBeDisabled();
    expect(props.onCreate).not.toHaveBeenCalled();
    fireEvent.change(name, { target: { value: "😀".repeat(40) } });
    await user.click(within(dialog).getByRole("button", { name: "创建视图" }));
    expect(props.onCreate).toHaveBeenCalledWith(
      expect.objectContaining({ name: "😀".repeat(40) }),
    );
  });
  it("returns global comparison to the independent collection after a single-view confirmation", async () => {
    const user = userEvent.setup();
    const comparison = readyComparison();
    const secondComparison = {
      ...comparison,
      scope: { ...comparison.scope, operationGeneration: 2 },
    };
    const props = managerProps({
      onRefreshCompare: vi
        .fn()
        .mockResolvedValueOnce({ state: "ready", comparison })
        .mockResolvedValue({ state: "ready", comparison: secondComparison }),
      onConfirmCompare: vi.fn().mockReturnValue(true),
    });
    const { rerender } = render(managerTree(props));
    const dialog = await openManager(user);
    await user.click(
      within(dialog).getByRole("button", { name: "重命名 表格" }),
    );
    rerender(
      managerTree({
        ...props,
        views: [comparison.baseline.view, boardView()],
        revision: 8,
        catalogRevision: 6,
      }),
    );
    await user.click(
      within(dialog).getByRole("button", { name: "刷新后比较" }),
    );
    await user.click(
      await within(dialog).findByRole("button", {
        name: "确认采用当前值继续编辑",
      }),
    );
    await user.click(
      within(dialog).getByRole("button", { name: "取消这次编辑" }),
    );
    expect(
      within(dialog).getByRole("button", { name: "新建视图" }),
    ).toBeDisabled();
    await user.click(
      within(dialog).getByRole("button", { name: "刷新后比较" }),
    );
    await user.click(
      await within(dialog).findByRole("button", {
        name: "确认采用当前值继续编辑",
      }),
    );
    const create = within(dialog).getByRole("button", { name: "新建视图" });
    expect(create).toBeEnabled();
    await user.click(create);
    fireEvent.change(
      within(dialog).getByRole("textbox", { name: "视图名称" }),
      { target: { value: "独立集合新建" } },
    );
    await user.click(within(dialog).getByRole("button", { name: "创建视图" }));
    expect(props.onCreate).toHaveBeenCalledWith(
      expect.objectContaining({
        baseline: { revision: 8, catalogRevision: 6 },
        name: "独立集合新建",
      }),
    );
  });
  it("presents a confirmed active-to-archived lifecycle change without rebinding collection actions", async () => {
    const user = userEvent.setup();
    const archived = { ...boardView(), version: 4, archived_at: 20 };
    const comparison = {
      baseline: { view: archived, collectionRevision: 8, catalogRevision: 5 },
      scope: {
        lifetime: 1,
        accountId: 2,
        projectId: "p1",
        viewId: archived.view_id,
        queryGeneration: 1,
        channel: "view-compare",
        operationGeneration: 1,
      },
    };
    const collectionComparison = readyComparison(3, 8, 5);
    const props = managerProps({
      onRefreshCompare: vi
        .fn()
        .mockResolvedValueOnce({ state: "ready", comparison })
        .mockResolvedValue({
          state: "ready",
          comparison: collectionComparison,
        }),
      onConfirmCompare: vi.fn().mockReturnValue(true),
      onRestore: vi.fn().mockResolvedValue({
        state: "failed",
        messageKey: "projects.planViews.saveFailed",
      }),
    });
    const { rerender } = render(managerTree(props));
    const dialog = await openManager(user);
    rerender(
      managerTree({ ...props, revision: 8, views: [tableView(), archived] }),
    );
    await user.click(
      within(dialog).getByRole("button", { name: "刷新后比较 看板" }),
    );
    await user.click(
      await within(dialog).findByRole("button", {
        name: "确认采用当前值继续编辑",
      }),
    );
    await user.click(within(dialog).getByRole("button", { name: "停用视图" }));
    const restore = within(dialog).getByRole("button", { name: "恢复 看板" });
    expect(restore).toBeEnabled();
    expect(
      within(dialog).queryByRole("button", { name: "停用 看板" }),
    ).not.toBeInTheDocument();
    expect(
      within(dialog).getByRole("button", { name: "新建视图" }),
    ).toBeDisabled();
    await user.click(
      within(dialog).getByRole("button", { name: "刷新后比较" }),
    );
    expect(props.onRefreshCompare).toHaveBeenLastCalledWith({
      view: tableView(),
      collectionRevision: 7,
      catalogRevision: 5,
    });
    await user.click(
      await within(dialog).findByRole("button", {
        name: "确认采用当前值继续编辑",
      }),
    );
    expect(
      within(dialog).getByRole("button", { name: "新建视图" }),
    ).toBeEnabled();
    await user.click(within(dialog).getByRole("button", { name: "恢复 看板" }));
    expect(props.onRestore).toHaveBeenCalledWith(comparison.baseline);
  });
});
