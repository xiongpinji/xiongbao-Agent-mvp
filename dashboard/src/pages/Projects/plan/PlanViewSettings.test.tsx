import { ConfigProvider } from "antd";
import { useId } from "react";
import { createRequire } from "node:module";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
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
import type { ProjectTodoCatalog } from "../../../api/modules/projectTodoCatalog";
import type { PlanViewComparison } from "./ProjectPlanViews";
import PlanViewSettings, {
  type PlanViewSettingsProps,
  planDefinitionForType,
} from "./PlanViewSettings";

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

function viewFixture(): PlanView & { type: "table" } {
  return {
    view_id: "v1",
    project_id: "p1",
    name: "项目计划",
    type: "table",
    definition: {
      show_subtodos: false,
      schema_version: 1,
      fields: ["title", "status", "assignee", "priority", "tags"],
      group_by: null,
      filters: [
        { field: "assignee", op: "not_in", values: [null, 2] },
        { field: "priority", op: "in", values: [null, "pr-old"] },
        { field: "tags", op: "all", values: ["tag-a", "tag-old"] },
        {
          field: "due_date",
          op: "between",
          values: ["2026-09-01", "2026-10-01"],
        },
        { field: "source", op: "in", values: ["manual"] },
      ],
      sort: [
        { field: "updated_at", direction: "desc" },
        { field: "title", direction: "asc" },
      ],
    },
    version: 3,
    position: 0,
    archived_at: null,
    created_at: 1,
    updated_at: 2,
  };
}

function catalogFixture(): ProjectTodoCatalog {
  return {
    project_id: "p1",
    revision: 5,
    server_today: "2026-09-29",
    server_timezone: "Asia/Shanghai",
    priorities: [
      {
        priority_id: "pr-old",
        name: "原优先级",
        color: "gray",
        position: 0,
        archived_at: 10,
        created_at: 1,
        updated_at: 2,
      },
    ],
    tags: [
      {
        tag_id: "tag-a",
        name: "当前标签",
        color: "blue",
        archived_at: null,
        created_at: 1,
        updated_at: 2,
      },
      {
        tag_id: "tag-old",
        name: "停用标签",
        color: "gray",
        archived_at: 10,
        created_at: 1,
        updated_at: 2,
      },
    ],
  };
}

function settingsProps(
  overrides: Partial<PlanViewSettingsProps> = {},
): PlanViewSettingsProps {
  const view = overrides.view ?? viewFixture();
  return {
    view,
    definition: view.definition,
    baseline: { view, collectionRevision: 7, catalogRevision: 5 },
    catalog: catalogFixture(),
    members: [{ user_id: 2, username: "成员二", role: "member" }],
    isManager: true,
    conflictLocked: false,
    busy: false,
    comparison: null,
    onTemporaryChange: vi.fn(),
    onSave: vi.fn().mockResolvedValue({ state: "saved" }),
    onRefreshCompare: vi.fn().mockResolvedValue({
      state: "failed",
      messageKey: "projects.planViews.refreshFailed",
    }),
    onConfirmCompare: vi.fn().mockReturnValue(false),
    onCancel: vi.fn(),
    ...overrides,
  };
}

function settingsTree(props: PlanViewSettingsProps) {
  return (
    <ConfigProvider prefixCls="octop" theme={{ token: { motion: false } }}>
      <PlanViewSettings {...props} />
    </ConfigProvider>
  );
}

async function choose(
  user: ReturnType<typeof userEvent.setup>,
  name: string,
  option: string,
) {
  await user.click(screen.getByRole("combobox", { name }));
  await user.click(screen.getByRole("option", { name: option }));
}

describe("C2 complete definition settings behavior", () => {
  it("normalizes a legacy table off and applies explicit true then false for a member", () => {
    const props = settingsProps({ isManager: false });
    const { show_subtodos: _flag, ...legacy } = props.definition;
    props.definition = legacy;
    render(settingsTree(props));
    const toggle = screen.getByRole("checkbox", { name: "显示子待办" });
    expect(toggle).not.toBeChecked();
    fireEvent.click(toggle);
    fireEvent.click(screen.getByRole("button", { name: "应用临时调整" }));
    expect(props.onTemporaryChange).toHaveBeenLastCalledWith({
      ...legacy,
      show_subtodos: true,
    });
    fireEvent.click(toggle);
    fireEvent.click(screen.getByRole("button", { name: "应用临时调整" }));
    expect(props.onTemporaryChange).toHaveBeenLastCalledWith({
      ...legacy,
      show_subtodos: false,
    });
    expect(props.onSave).not.toHaveBeenCalled();
  });

  it("saves the explicit flag with the original shared baseline", async () => {
    const props = settingsProps();
    render(settingsTree(props));
    fireEvent.click(screen.getByRole("checkbox", { name: "显示子待办" }));
    fireEvent.click(screen.getByRole("button", { name: "保存到共享视图" }));
    await waitFor(() =>
      expect(props.onSave).toHaveBeenCalledWith({
        baseline: props.baseline,
        definition: { ...props.definition, show_subtodos: true },
      }),
    );
  });

  it.each(["list", "board", "gantt", "calendar"] as const)(
    "omits the table flag when switching to %s and rejects a forged flag",
    (type) => {
      const definition = planDefinitionForType(type, {
        ...viewFixture().definition,
        show_subtodos: true,
      });
      expect(definition).not.toHaveProperty("show_subtodos");
      const view = { ...viewFixture(), type, definition } as PlanView;
      const props = settingsProps({
        view,
        definition: { ...definition, show_subtodos: true },
      });
      render(settingsTree(props));
      expect(screen.queryByRole("checkbox", { name: "显示子待办" })).toBeNull();
      expect(
        screen.getByRole("button", { name: "应用临时调整" }),
      ).toBeDisabled();
      expect(
        screen.getByRole("button", { name: "保存到共享视图" }),
      ).toBeDisabled();
    },
  );

  it("collects the real accessible settings with separate temporary and shared actions", () => {
    render(settingsTree(settingsProps()));
    expect(
      screen.getByRole("dialog", { name: "视图设置" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "应用临时调整" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "保存到共享视图" }),
    ).toBeInTheDocument();
  });

  it("allows member temporary settings but disables every shared save", async () => {
    const user = userEvent.setup();
    const props = settingsProps({ isManager: false });
    render(settingsTree(props));
    expect(screen.getByRole("button", { name: "应用临时调整" })).toBeEnabled();
    const save = screen.getByRole("button", { name: "保存到共享视图" });
    expect(save).toBeDisabled();
    fireEvent.click(save);
    expect(props.onSave).not.toHaveBeenCalled();
    expect(
      screen.getByText("只有项目所有者或管理员可以保存共享设置。"),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("checkbox", { name: "优先级" }));
    await user.click(screen.getByRole("button", { name: "应用临时调整" }));
    expect(props.onTemporaryChange).toHaveBeenCalledWith({
      ...props.definition,
      fields: ["title", "status", "assignee", "tags"],
    });
  });

  it("fixes title as the first visible field and prevents hiding it", () => {
    render(settingsTree(settingsProps()));
    const title = screen.getByRole("checkbox", { name: "标题" });
    expect(title).toBeChecked();
    expect(title).toBeDisabled();
  });

  it("applies a complete definition preserving ordered fields, nullable filters and every sort", async () => {
    const user = userEvent.setup();
    const props = settingsProps();
    render(settingsTree(props));
    await user.click(screen.getByRole("checkbox", { name: "优先级" }));
    await user.click(screen.getByRole("button", { name: "应用临时调整" }));
    expect(props.onTemporaryChange).toHaveBeenCalledWith({
      ...props.definition,
      fields: ["title", "status", "assignee", "tags"],
    });
    expect(props.onSave).not.toHaveBeenCalled();
  });

  it("keeps its original complete baseline when only the collection revision prop advances", async () => {
    const user = userEvent.setup();
    const props = settingsProps();
    const original = structuredClone(props.baseline);
    const { rerender } = render(settingsTree(props));
    await user.click(screen.getByRole("checkbox", { name: "优先级" }));
    rerender(
      settingsTree({
        ...props,
        baseline: { ...props.baseline, collectionRevision: 99 },
      }),
    );
    await user.click(screen.getByRole("button", { name: "保存到共享视图" }));
    expect(props.onSave).toHaveBeenCalledWith({
      baseline: original,
      definition: {
        ...props.definition,
        fields: ["title", "status", "assignee", "tags"],
      },
    });
  });

  it("locks a newer same-view version without discarding the field draft or auto-comparing", async () => {
    const user = userEvent.setup();
    const props = settingsProps();
    const { rerender } = render(settingsTree(props));
    await user.click(screen.getByRole("checkbox", { name: "优先级" }));
    rerender(
      settingsTree({
        ...props,
        view: { ...viewFixture(), version: 4, name: "他人名称" },
        conflictLocked: true,
      }),
    );
    expect(screen.getByRole("checkbox", { name: "优先级" })).not.toBeChecked();
    expect(
      screen.getByRole("button", { name: "保存到共享视图" }),
    ).toBeDisabled();
    expect(props.onRefreshCompare).not.toHaveBeenCalled();
    expect(props.onConfirmCompare).not.toHaveBeenCalled();
  });

  it("requires a separate explicit comparison confirmation before replacing the save baseline", async () => {
    const user = userEvent.setup();
    const props = settingsProps({ conflictLocked: true });
    const comparison: PlanViewComparison = {
      baseline: {
        view: { ...viewFixture(), version: 4 },
        collectionRevision: 8,
        catalogRevision: 6,
      },
      scope: {
        lifetime: 1,
        accountId: 2,
        projectId: "p1",
        viewId: "v1",
        queryGeneration: 1,
        channel: "view-compare",
        operationGeneration: 1,
      },
    };
    props.onRefreshCompare = vi
      .fn()
      .mockResolvedValue({ state: "ready", comparison });
    props.onConfirmCompare = vi.fn().mockReturnValue(true);
    render(settingsTree(props));
    await user.click(screen.getByRole("checkbox", { name: "优先级" }));
    await user.click(screen.getByRole("button", { name: "刷新后比较" }));
    expect(props.onRefreshCompare).toHaveBeenCalledWith(props.baseline);
    expect(props.onConfirmCompare).not.toHaveBeenCalled();
    await user.click(
      await screen.findByRole("button", { name: "确认采用当前值继续编辑" }),
    );
    expect(props.onConfirmCompare).toHaveBeenCalledWith(comparison);
    await user.click(screen.getByRole("button", { name: "保存到共享视图" }));
    expect(props.onSave).toHaveBeenCalledWith({
      baseline: comparison.baseline,
      definition: {
        ...props.definition,
        fields: ["title", "status", "assignee", "tags"],
      },
    });
  });

  it("applies calendar basis and mode together through a full same-type definition", async () => {
    const user = userEvent.setup();
    const view: PlanView = {
      ...viewFixture(),
      type: "calendar",
      definition: {
        ...planDefinitionForType("calendar", viewFixture().definition),
        group_by: null,
        calendar: { date_basis: "due_date", mode: "month" },
      },
    };
    const props = settingsProps({ view });
    render(settingsTree(props));
    await choose(user, "日历日期依据", "开始日期");
    await choose(user, "日历模式", "周");
    await user.click(screen.getByRole("button", { name: "应用临时调整" }));
    expect(props.onTemporaryChange).toHaveBeenCalledWith({
      ...view.definition,
      calendar: { date_basis: "start_date", mode: "week" },
    });
  });

  it("applies gantt zoom through a full same-type definition", async () => {
    const user = userEvent.setup();
    const view: PlanView = {
      ...viewFixture(),
      type: "gantt",
      definition: {
        ...planDefinitionForType("gantt", viewFixture().definition),
        group_by: null,
        gantt: { zoom: "week" },
      },
    };
    const props = settingsProps({ view });
    render(settingsTree(props));
    await choose(user, "甘特缩放", "月");
    await user.click(screen.getByRole("button", { name: "应用临时调整" }));
    expect(props.onTemporaryChange).toHaveBeenCalledWith({
      ...view.definition,
      gantt: { zoom: "month" },
    });
  });

  it("does not offer the ungrouped choice for board", async () => {
    const user = userEvent.setup();
    const view: PlanView = {
      ...viewFixture(),
      type: "board",
      definition: planDefinitionForType("board", viewFixture().definition),
    };
    render(settingsTree(settingsProps({ view })));
    await user.click(screen.getByRole("combobox", { name: "分组方式" }));
    expect(
      within(screen.getByRole("listbox")).queryByRole("option", {
        name: "不分组",
      }),
    ).not.toBeInTheDocument();
  });

  it("changes field order without moving or hiding title", async () => {
    const user = userEvent.setup();
    const props = settingsProps();
    render(settingsTree(props));
    await user.click(screen.getByRole("button", { name: "上移显示字段 标签" }));
    await user.click(screen.getByRole("button", { name: "应用临时调整" }));
    expect(props.onTemporaryChange).toHaveBeenCalledWith({
      ...props.definition,
      fields: ["title", "status", "assignee", "tags", "priority"],
    });
    expect(
      screen.getByRole("button", { name: "上移显示字段 状态" }),
    ).toBeDisabled();
  });

  it.each(["status", "assignee", "priority", "tag", "source"] as const)(
    "applies %s grouping as a full definition",
    async (group) => {
      const user = userEvent.setup();
      const props = settingsProps();
      render(settingsTree(props));
      const labels = {
        status: "状态",
        assignee: "处理人",
        priority: "优先级",
        tag: "标签",
        source: "来源",
      };
      await choose(user, "分组方式", labels[group]);
      await user.click(screen.getByRole("button", { name: "应用临时调整" }));
      expect(props.onTemporaryChange).toHaveBeenCalledWith({
        ...props.definition,
        group_by: group,
      });
    },
  );

  it("adds an AND filter and retains every existing nullable, historical and date condition", async () => {
    const user = userEvent.setup();
    const props = settingsProps();
    render(settingsTree(props));
    await user.click(screen.getByRole("button", { name: "添加筛选条件" }));
    fireEvent.change(screen.getByRole("textbox", { name: "筛选值 6" }), {
      target: { value: "%_\\字面搜索" },
    });
    await user.click(screen.getByRole("button", { name: "应用临时调整" }));
    expect(props.onTemporaryChange).toHaveBeenCalledWith({
      ...props.definition,
      filters: [
        ...props.definition.filters,
        { field: "title", op: "contains", value: "%_\\字面搜索" },
      ],
    });
    expect(screen.getByText("满足所有条件（AND）")).toBeInTheDocument();
  });

  it("limits filters to twelve and requires an explicit whole-condition removal", async () => {
    const user = userEvent.setup();
    const view = viewFixture();
    view.definition = {
      ...view.definition,
      filters: Array.from({ length: 12 }, (_, index) => ({
        field: "title" as const,
        op: "contains" as const,
        value: `条件${index}`,
      })),
    };
    const props = settingsProps({ view });
    render(settingsTree(props));
    expect(screen.getByRole("button", { name: "添加筛选条件" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "移除筛选条件 2" }));
    await user.click(screen.getByRole("button", { name: "应用临时调整" }));
    expect(props.onTemporaryChange).toHaveBeenCalledWith({
      ...view.definition,
      filters: view.definition.filters.filter((_, index) => index !== 1),
    });
  });

  it.each([
    {
      clause: { field: "tags", op: "all", values: ["tag-a"] },
      option: "为空",
      expected: { field: "tags", op: "is_empty" },
    },
    {
      clause: { field: "tags", op: "any", values: ["tag-old"] },
      option: "不含这些标签",
      expected: { field: "tags", op: "none_of", values: ["tag-old"] },
    },
    {
      clause: { field: "start_date", op: "on", value: "2026-09-29" },
      option: "不为空",
      expected: { field: "start_date", op: "not_empty" },
    },
    {
      clause: { field: "due_date", op: "on", value: "2026-09-29" },
      option: "是否逾期",
      expected: { field: "due_date", op: "overdue", value: true },
    },
  ] as const)(
    "changes $clause.field/$clause.op into an exact field/op union without leftover values",
    async ({ clause, option, expected }) => {
      const user = userEvent.setup();
      const view = viewFixture();
      view.definition = { ...view.definition, filters: [clause] };
      const props = settingsProps({ view });
      render(settingsTree(props));
      await choose(user, "筛选运算 1", option);
      await user.click(screen.getByRole("button", { name: "应用临时调整" }));
      expect(props.onTemporaryChange).toHaveBeenCalledWith({
        ...view.definition,
        filters: [expected],
      });
    },
  );

  it("changes nullable member and priority values while keeping IDs in their real wire types", async () => {
    const user = userEvent.setup();
    const view = viewFixture();
    view.definition = {
      ...view.definition,
      filters: [
        { field: "assignee", op: "in", values: [null] },
        { field: "priority", op: "not_in", values: [null] },
      ],
    };
    const props = settingsProps({ view });
    render(settingsTree(props));
    await choose(user, "筛选值 1", "成员二");
    await choose(user, "筛选值 2", "原优先级（已停用）");
    await user.click(screen.getByRole("button", { name: "应用临时调整" }));
    expect(props.onTemporaryChange).toHaveBeenCalledWith({
      ...view.definition,
      filters: [
        { field: "assignee", op: "in", values: [null, 2] },
        { field: "priority", op: "not_in", values: [null, "pr-old"] },
      ],
    });
  });

  it("retains a departed-assignee condition until the user explicitly removes it", async () => {
    const user = userEvent.setup();
    const props = settingsProps({ isManager: false, members: [] });
    render(settingsTree(props));
    expect(screen.getByRole("button", { name: "应用临时调整" })).toBeDisabled();
    expect(props.onTemporaryChange).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "移除筛选条件 1" }));
    await user.click(screen.getByRole("button", { name: "应用临时调整" }));
    expect(props.onTemporaryChange).toHaveBeenCalledWith({
      ...props.definition,
      filters: props.definition.filters.slice(1),
    });
    expect(props.onSave).not.toHaveBeenCalled();
  });

  it("rejects a reversed or invalid date range without losing the inputs", async () => {
    const user = userEvent.setup();
    const view = viewFixture();
    view.definition = {
      ...view.definition,
      filters: [
        {
          field: "due_date",
          op: "between",
          values: ["2026-09-01", "2026-10-01"],
        },
      ],
    };
    const props = settingsProps({ view });
    render(settingsTree(props));
    fireEvent.change(screen.getByLabelText("筛选起始日期 1"), {
      target: { value: "2026-10-02" },
    });
    expect(screen.getByRole("button", { name: "应用临时调整" })).toBeDisabled();
    expect(screen.getByLabelText("筛选起始日期 1")).toHaveValue("2026-10-02");
    fireEvent.change(screen.getByLabelText("筛选结束日期 1"), {
      target: { value: "2026-10-03" },
    });
    await user.click(screen.getByRole("button", { name: "应用临时调整" }));
    expect(props.onTemporaryChange).toHaveBeenCalledWith({
      ...view.definition,
      filters: [
        {
          field: "due_date",
          op: "between",
          values: ["2026-10-02", "2026-10-03"],
        },
      ],
    });
  });

  it("limits sorting to three unique fields and applies explicit order and direction", async () => {
    const user = userEvent.setup();
    const props = settingsProps();
    render(settingsTree(props));
    await user.click(screen.getByRole("button", { name: "添加排序" }));
    expect(screen.getByRole("button", { name: "添加排序" })).toBeDisabled();
    await choose(user, "排序字段 3", "截止日期");
    await choose(user, "排序方向 3", "降序");
    await user.click(screen.getByRole("button", { name: "上移排序 3" }));
    await user.click(screen.getByRole("button", { name: "应用临时调整" }));
    expect(props.onTemporaryChange).toHaveBeenCalledWith({
      ...props.definition,
      sort: [
        { field: "updated_at", direction: "desc" },
        { field: "due_date", direction: "desc" },
        { field: "title", direction: "asc" },
      ],
    });
  });

  it("keeps a failed shared-save full draft and locks catalog advancement instead of silently rebinding", async () => {
    const user = userEvent.setup();
    const props = settingsProps({
      onSave: vi.fn().mockResolvedValue({
        state: "failed",
        messageKey: "projects.planViews.saveFailed",
      }),
    });
    const { rerender } = render(settingsTree(props));
    await user.click(screen.getByRole("checkbox", { name: "优先级" }));
    await user.click(screen.getByRole("button", { name: "保存到共享视图" }));
    expect(screen.getByRole("checkbox", { name: "优先级" })).not.toBeChecked();
    expect(
      screen.getByText("保存失败，草稿已保留，请重试。"),
    ).toBeInTheDocument();
    rerender(
      settingsTree({
        ...props,
        catalog: { ...catalogFixture(), revision: 6 },
        baseline: { ...props.baseline, catalogRevision: 6 },
      }),
    );
    expect(
      screen.getByRole("button", { name: "保存到共享视图" }),
    ).toBeDisabled();
    expect(props.onSave).toHaveBeenCalledTimes(1);
  });

  it("keeps an unapplied dirty draft when cancel is followed by continue editing", async () => {
    const user = userEvent.setup();
    const props = settingsProps();
    render(settingsTree(props));
    await user.click(screen.getByRole("checkbox", { name: "优先级" }));
    await user.click(screen.getByRole("button", { name: "取消" }));
    expect(props.onCancel).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "继续编辑" }));
    expect(screen.getByRole("checkbox", { name: "优先级" })).not.toBeChecked();
    expect(props.onCancel).not.toHaveBeenCalled();
  });

  it("renders English labels and the table subtodo toggle without attachment controls", () => {
    localeState.language = "en";
    render(settingsTree(settingsProps()));
    expect(
      screen.getByRole("dialog", { name: "View settings" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("checkbox", { name: "Created" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("checkbox", { name: "Updated" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("checkbox", { name: /attachment/i }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("checkbox", { name: "Show subtodos" }),
    ).not.toBeChecked();
  });
});
describe("C2 settings draft and strict-definition boundaries", () => {
  const comparisonFor = (
    view = { ...viewFixture(), version: 4 },
  ): PlanViewComparison => ({
    baseline: { view, collectionRevision: 8, catalogRevision: 5 },
    scope: {
      lifetime: 1,
      accountId: 2,
      projectId: "p1",
      viewId: view.view_id,
      queryGeneration: 1,
      channel: "view-compare",
      operationGeneration: 1,
    },
  });

  it("rejects leftover tag values on an empty predicate rather than sending an invalid union", async () => {
    const user = userEvent.setup();
    const view = viewFixture();
    // Structural extra keys model a malformed wire payload; they must not reach a query.
    const invalidClause = {
      field: "tags" as const,
      op: "is_empty" as const,
      values: ["tag-a"],
    };
    view.definition = { ...view.definition, filters: [invalidClause] };
    const props = settingsProps({ view });
    render(settingsTree(props));
    const apply = screen.getByRole("button", { name: "应用临时调整" });
    expect(apply).toBeDisabled();
    fireEvent.click(apply);
    expect(props.onTemporaryChange).not.toHaveBeenCalled();
    expect(
      screen.getByText("筛选条件 1 无效，请检查运算和值。"),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "移除筛选条件 1" }));
    await user.click(screen.getByRole("button", { name: "应用临时调整" }));
    expect(props.onTemporaryChange).toHaveBeenCalledWith({
      ...view.definition,
      filters: [],
    });
  });

  it("rejects a date-between payload containing a third endpoint", () => {
    const view = viewFixture();
    const clause = {
      field: "due_date" as const,
      op: "between" as const,
      values: ["2026-09-01", "2026-10-01"] as const,
    };
    Object.assign(clause, {
      values: ["2026-09-01", "2026-10-01", "2026-11-01"],
    });
    view.definition = { ...view.definition, filters: [clause] };
    const props = settingsProps({ view });
    render(settingsTree(props));
    expect(screen.getByRole("button", { name: "应用临时调整" })).toBeDisabled();
    expect(props.onTemporaryChange).not.toHaveBeenCalled();
  });

  it("keeps a valid full draft when permission is revoked and still allows a temporary apply", async () => {
    const user = userEvent.setup();
    const props = settingsProps();
    const { rerender } = render(settingsTree(props));
    await user.click(screen.getByRole("checkbox", { name: "优先级" }));
    rerender(settingsTree({ ...props, isManager: false }));
    const save = screen.getByRole("button", { name: "保存到共享视图" });
    expect(save).toBeDisabled();
    fireEvent.click(save);
    expect(props.onSave).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "应用临时调整" }));
    expect(props.onTemporaryChange).toHaveBeenCalledWith({
      ...props.definition,
      fields: ["title", "status", "assignee", "tags"],
    });
  });

  it("does not accept an archived comparison as a new settings-save baseline", async () => {
    const user = userEvent.setup();
    const comparison = comparisonFor({
      ...viewFixture(),
      version: 4,
      archived_at: 20,
    });
    const props = settingsProps({
      conflictLocked: true,
      onRefreshCompare: vi
        .fn()
        .mockResolvedValue({ state: "ready", comparison }),
      onConfirmCompare: vi.fn().mockReturnValue(true),
    });
    render(settingsTree(props));
    await user.click(screen.getByRole("checkbox", { name: "优先级" }));
    await user.click(screen.getByRole("button", { name: "刷新后比较" }));
    const confirm = await screen.findByRole("button", {
      name: "确认采用当前值继续编辑",
    });
    expect(confirm).toBeDisabled();
    fireEvent.click(confirm);
    expect(props.onConfirmCompare).not.toHaveBeenCalled();
    expect(
      screen.getByRole("button", { name: "保存到共享视图" }),
    ).toBeDisabled();
    expect(screen.getByRole("checkbox", { name: "优先级" })).not.toBeChecked();
  });

  it("keeps the entire draft locked if the comparison scope confirmation is rejected", async () => {
    const user = userEvent.setup();
    const comparison = comparisonFor();
    const props = settingsProps({
      conflictLocked: true,
      onRefreshCompare: vi
        .fn()
        .mockResolvedValue({ state: "ready", comparison }),
      onConfirmCompare: vi.fn().mockReturnValue(false),
    });
    render(settingsTree(props));
    await user.click(screen.getByRole("checkbox", { name: "优先级" }));
    await user.click(screen.getByRole("button", { name: "刷新后比较" }));
    await user.click(
      await screen.findByRole("button", { name: "确认采用当前值继续编辑" }),
    );
    expect(
      screen.getByRole("button", { name: "保存到共享视图" }),
    ).toBeDisabled();
    expect(screen.getByRole("checkbox", { name: "优先级" })).not.toBeChecked();
    expect(props.onSave).not.toHaveBeenCalled();
    expect(
      screen.getByText("比较结果已失效，请重新刷新比较。"),
    ).toBeInTheDocument();
  });

  it("ignores a delayed comparison when the settings view lifetime changes", async () => {
    const user = userEvent.setup();
    let finish: (value: {
      state: "ready";
      comparison: PlanViewComparison;
    }) => void = () => {};
    const response = new Promise<{
      state: "ready";
      comparison: PlanViewComparison;
    }>((resolve) => {
      finish = resolve;
    });
    const props = settingsProps({
      conflictLocked: true,
      onRefreshCompare: vi.fn().mockReturnValue(response),
    });
    const { rerender } = render(settingsTree(props));
    await user.click(screen.getByRole("button", { name: "刷新后比较" }));
    const nextView = { ...viewFixture(), view_id: "v2", name: "另一视图" };
    const nextProps = settingsProps({ view: nextView });
    rerender(settingsTree(nextProps));
    await act(async () => {
      finish({ state: "ready", comparison: comparisonFor() });
      await response;
    });
    expect(
      screen.queryByRole("button", { name: "确认采用当前值继续编辑" }),
    ).not.toBeInTheDocument();
    await user.click(screen.getByRole("checkbox", { name: "优先级" }));
    await user.click(screen.getByRole("button", { name: "保存到共享视图" }));
    expect(nextProps.onSave).toHaveBeenCalledWith({
      baseline: nextProps.baseline,
      definition: {
        ...nextView.definition,
        fields: ["title", "status", "assignee", "tags"],
      },
    });
    expect(props.onConfirmCompare).not.toHaveBeenCalled();
  });

  it("locks a background type change while retaining every local field and condition", async () => {
    const user = userEvent.setup();
    const props = settingsProps();
    const { rerender } = render(settingsTree(props));
    await user.click(screen.getByRole("checkbox", { name: "优先级" }));
    const board: PlanView = {
      ...viewFixture(),
      type: "board",
      version: 4,
      definition: planDefinitionForType("board", viewFixture().definition),
    };
    rerender(settingsTree({ ...props, view: board, conflictLocked: true }));
    expect(screen.getByRole("checkbox", { name: "优先级" })).not.toBeChecked();
    expect(screen.getByRole("button", { name: "应用临时调整" })).toBeDisabled();
    expect(
      screen.getByRole("button", { name: "保存到共享视图" }),
    ).toBeDisabled();
    expect(
      screen.getAllByRole("button", { name: /移除筛选条件/ }),
    ).toHaveLength(5);
    expect(props.onTemporaryChange).not.toHaveBeenCalled();
  });

  it("offers the actual manual source as the only source-filter option", async () => {
    const user = userEvent.setup();
    const view = viewFixture();
    view.definition = {
      ...view.definition,
      filters: [{ field: "source", op: "not_in", values: ["manual"] }],
    };
    const props = settingsProps({ view });
    render(settingsTree(props));
    await user.click(screen.getByRole("combobox", { name: "筛选值 1" }));
    const options = within(screen.getByRole("listbox")).getAllByRole("option");
    expect(options).toHaveLength(1);
    expect(options[0]).toHaveAccessibleName("手动创建");
  });

  it("changes overdue to a real false boolean through the complete definition", async () => {
    const user = userEvent.setup();
    const view = viewFixture();
    view.definition = {
      ...view.definition,
      filters: [{ field: "due_date", op: "overdue", value: true }],
    };
    const props = settingsProps({ view });
    render(settingsTree(props));
    await choose(user, "筛选值 1", "未逾期");
    await user.click(screen.getByRole("button", { name: "应用临时调整" }));
    expect(props.onTemporaryChange).toHaveBeenCalledWith({
      ...view.definition,
      filters: [{ field: "due_date", op: "overdue", value: false }],
    });
  });

  it("changes a date filter to nullable assignee without retaining date-only values", async () => {
    const user = userEvent.setup();
    const view = viewFixture();
    view.definition = {
      ...view.definition,
      filters: [
        {
          field: "due_date",
          op: "between",
          values: ["2026-09-01", "2026-10-01"],
        },
      ],
    };
    const props = settingsProps({ view });
    render(settingsTree(props));
    await choose(user, "筛选字段 1", "处理人");
    await user.click(screen.getByRole("button", { name: "应用临时调整" }));
    expect(props.onTemporaryChange).toHaveBeenCalledWith({
      ...view.definition,
      filters: [{ field: "assignee", op: "in", values: [null] }],
    });
  });

  it("appends newly visible audit fields and explicitly removes a sort in the full proposal", async () => {
    const user = userEvent.setup();
    const props = settingsProps();
    render(settingsTree(props));
    await user.click(screen.getByRole("checkbox", { name: "创建时间" }));
    await user.click(screen.getByRole("checkbox", { name: "更新时间" }));
    await user.click(screen.getByRole("button", { name: "移除排序 1" }));
    await user.click(screen.getByRole("button", { name: "应用临时调整" }));
    expect(props.onTemporaryChange).toHaveBeenCalledWith({
      ...props.definition,
      fields: [...props.definition.fields, "created_at", "updated_at"],
      sort: [{ field: "title", direction: "asc" }],
    });
  });

  it("checks the 200-character filter limit as Unicode characters and preserves invalid input", async () => {
    const user = userEvent.setup();
    const view = viewFixture();
    view.definition = {
      ...view.definition,
      filters: [{ field: "title", op: "not_contains", value: "原文" }],
    };
    const props = settingsProps({ view });
    render(settingsTree(props));
    const value = screen.getByRole("textbox", { name: "筛选值 1" });
    fireEvent.change(value, { target: { value: "😀".repeat(201) } });
    expect(value).toHaveValue("😀".repeat(201));
    expect(screen.getByRole("button", { name: "应用临时调整" })).toBeDisabled();
    expect(props.onTemporaryChange).not.toHaveBeenCalled();
    fireEvent.change(value, { target: { value: "😀".repeat(200) } });
    await user.click(screen.getByRole("button", { name: "应用临时调整" }));
    expect(props.onTemporaryChange).toHaveBeenCalledWith({
      ...view.definition,
      filters: [
        { field: "title", op: "not_contains", value: "😀".repeat(200) },
      ],
    });
  });

  it.each(["gantt", "calendar"] as const)(
    "keeps %s ungrouped and prevents an invalid group choice",
    (type) => {
      const view: PlanView =
        type === "gantt"
          ? {
              ...viewFixture(),
              type,
              definition: {
                ...viewFixture().definition,
                group_by: null,
                gantt: { zoom: "week" },
              },
            }
          : {
              ...viewFixture(),
              type,
              definition: {
                ...viewFixture().definition,
                group_by: null,
                calendar: { date_basis: "due_date", mode: "month" },
              },
            };
      render(settingsTree(settingsProps({ view })));
      expect(screen.getByRole("combobox", { name: "分组方式" })).toBeDisabled();
    },
  );

  it("preserves a keyboard-cancelled draft and returns focus to the original field", async () => {
    const user = userEvent.setup();
    const props = settingsProps();
    render(settingsTree(props));
    const priority = screen.getByRole("checkbox", { name: "优先级" });
    await user.click(priority);
    // rc-dialog reads the native legacy keyCode; userEvent's jsdom keyCode is 0.
    fireEvent.keyDown(priority, { key: "Escape", code: "Escape", keyCode: 27 });
    expect(
      screen.getByRole("dialog", { name: "保留或舍弃未应用的草稿" }),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "继续编辑" }));
    expect(priority).not.toBeChecked();
    await waitFor(() => expect(priority).toHaveFocus());
    expect(props.onCancel).not.toHaveBeenCalled();
  });

  it("closes an unchanged settings modal exactly once after Escape", async () => {
    const props = settingsProps();
    render(settingsTree(props));
    const priority = screen.getByRole("checkbox", { name: "优先级" });
    priority.focus();
    fireEvent.keyDown(priority, { key: "Escape", code: "Escape", keyCode: 27 });
    await waitFor(() => expect(props.onCancel).toHaveBeenCalledTimes(1));
    expect(props.onTemporaryChange).not.toHaveBeenCalled();
    expect(props.onSave).not.toHaveBeenCalled();
  });
});
