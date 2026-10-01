import { createRef } from "react";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type {
  PlanRendererProps,
  ProjectPlanViewsHandle,
  ProjectPlanViewsProps,
} from "./ProjectPlanViews";
import type { PlanViewManagerProps } from "./PlanViewManager";
import type { PlanViewSettingsProps } from "./PlanViewSettings";
const mocks = vi.hoisted(() => ({
  list: vi.fn(),
  get: vi.fn(),
  create: vi.fn(),
  update: vi.fn(),
  order: vi.fn(),
  setDefault: vi.fn(),
  archive: vi.fn(),
  restore: vi.fn(),
  query: vi.fn(),
  getProject: vi.fn(),
  getTodo: vi.fn(),
  oldList: vi.fn(),
  ui: {
    renderer: null as PlanRendererProps | null,
    manager: null as PlanViewManagerProps | null,
    settings: null as PlanViewSettingsProps | null,
  },
}));
const locale = vi.hoisted(() => ({ language: "zh" as "zh" | "en" }));
vi.mock("react-i18next", async () => {
  const en = (await import("../../../locales/en.json")).default;
  const zh = (await import("../../../locales/zh.json")).default;
  return {
    useTranslation: () => ({
      t: (
        key: string,
        fallback?: string | Record<string, unknown>,
        options?: Record<string, unknown>,
      ) => {
        const value = key
          .split(".")
          .reduce<unknown>(
            (node, part) =>
              node && typeof node === "object"
                ? (node as Record<string, unknown>)[part]
                : undefined,
            locale.language === "en" ? en : zh,
          );
        const interpolations =
          typeof fallback === "object" ? fallback : options;
        const template =
          typeof value === "string"
            ? value
            : typeof fallback === "string"
            ? fallback
            : String(interpolations?.defaultValue ?? key);
        return template.replace(/\{\{(\w+)\}\}/g, (match, name: string) =>
          interpolations && name in interpolations
            ? String(interpolations[name])
            : match,
        );
      },
      i18n: { language: locale.language },
    }),
  };
});
vi.mock("../../../api/modules/projectPlanViews", async (original) => ({
  ...(await original<typeof import("../../../api/modules/projectPlanViews")>()),
  projectPlanViewsApi: mocks,
}));
vi.mock("../../../api/modules/projects", () => ({
  projectsApi: { get: mocks.getProject },
}));
vi.mock("../../../api/modules/projectTodos", () => ({
  projectTodosApi: { get: mocks.getTodo, list: mocks.oldList },
}));
vi.mock("./PlanViewManager", () => ({
  default: (props: PlanViewManagerProps) => {
    mocks.ui.manager = props;
    return (
      <nav>
        {props.views
          .filter((view) => !view.archived_at)
          .map((view) => (
            <button
              key={view.view_id}
              onClick={() => props.onSelect(view.view_id)}
            >
              {view.name}
            </button>
          ))}
      </nav>
    );
  },
}));
vi.mock("./PlanViewSettings", () => ({
  default: (props: PlanViewSettingsProps) => {
    mocks.ui.settings = props;
    return (
      <div data-testid="settings">
        <button
          onClick={() =>
            props.onTemporaryChange({
              ...props.definition,
              fields: ["title", "status"],
            })
          }
        >
          Apply fields
        </button>
        <button
          onClick={() =>
            void props.onSave({
              baseline: props.baseline,
              definition: props.definition,
            })
          }
        >
          Save fields
        </button>
        <button onClick={() => void props.onRefreshCompare(props.baseline)}>
          Compare fields
        </button>
        <button
          onClick={() =>
            props.comparison && props.onConfirmCompare(props.comparison)
          }
        >
          Confirm fields
        </button>
        <span data-testid="settings-baseline">
          {props.baseline.view.version}:{props.baseline.catalogRevision}
        </span>
        <span data-testid="settings-locked">
          {String(props.conflictLocked)}
        </span>
      </div>
    );
  },
}));
function rendererFactory() {
  return {
    default: (props: PlanRendererProps) => {
      mocks.ui.renderer = props;
      return (
        <div data-testid="renderer">
          <span>{props.view.view_id}</span>
          {props.lanes.flatMap((lane) =>
            lane.items.map((todo) => (
              <span key={`${lane.laneId}:${todo.todo_id}`}>{todo.title}</span>
            )),
          )}
          <span data-testid="fields">{props.definition.fields.join(",")}</span>
          <span data-testid="metadata">{props.serverToday ?? "paused"}</span>
        </div>
      );
    },
  };
}
vi.mock("./PlanList", rendererFactory);
vi.mock("./PlanTable", rendererFactory);
vi.mock("./PlanBoard", rendererFactory);
vi.mock("./PlanGantt", rendererFactory);
vi.mock("./PlanCalendar", rendererFactory);
import ProjectPlanViews from "./ProjectPlanViews";
import {
  deferred,
  makePlanDefinition,
  makePlanQueryResponse,
  makePlanTodo,
  makePlanView,
  makePlanCatalog,
} from "./planView.testFixtures";
import type {
  PlanQueryRequest,
  PlanView,
  PlanViewMutationResponse,
} from "../../../api/modules/projectPlanViews";

const view1 = makePlanView({ view_id: "v1", name: "Table shared" });
const view2 = makePlanView({
  view_id: "v2",
  name: "Board shared",
  type: "board",
  position: 1,
});
const list = (
  items: PlanView[] = [view1, view2],
  revision = 1,
  default_view_id = items[0].view_id,
) => ({ project_id: "p1", revision, default_view_id, items });
const apiError = (
  status: number,
  reason?: string,
  condition_indices?: number[],
) =>
  new Error(
    `Request failed: ${status} - ${JSON.stringify({
      error: {
        code: status === 404 ? "NOT_FOUND" : "INVITE_INVALID",
        details: { reason, condition_indices },
      },
    })}`,
  );
const props = (
  extra: Partial<ProjectPlanViewsProps> = {},
): ProjectPlanViewsProps => ({
  accountId: 1,
  projectId: "p1",
  role: "owner",
  members: [{ user_id: 1, username: "owner", role: "owner" }],
  catalog: makePlanCatalog(),
  catalogLoading: false,
  catalogError: null,
  onCatalogRetry: vi.fn(async () => false),
  canEdit: vi.fn(() => true),
  canDelete: vi.fn(() => true),
  onCreateTodo: vi.fn(),
  onEditTodo: vi.fn(),
  onDeleteTodo: vi.fn(),
  onOpenTodo: vi.fn(),
  onProposeTodoPatch: vi.fn(async () => ({
    status: "failed",
    messageKey: "projects.planViews.failed",
  })),
  onBulkTodo: vi.fn(async () => {}),
  onLoadedTodosChanged: vi.fn(),
  onProjectAccessLost: vi.fn(),
  ...extra,
});
beforeEach(() => {
  locale.language = "zh";
  const getStyle = window.getComputedStyle.bind(window);
  vi.spyOn(window, "getComputedStyle").mockImplementation((element) =>
    getStyle(element),
  );
  localStorage.clear();
  Object.values(mocks).forEach((value) => {
    if (typeof value === "function" && "mockReset" in value) value.mockReset();
  });
  mocks.ui.renderer = null;
  mocks.ui.manager = null;
  mocks.ui.settings = null;
  mocks.list.mockResolvedValue(list());
  mocks.get.mockResolvedValue(view1);
  mocks.getProject.mockResolvedValue({ project_id: "p1" });
  mocks.getTodo.mockResolvedValue(makePlanTodo());
  mocks.query.mockImplementation((_project: string, body: PlanQueryRequest) =>
    Promise.resolve(
      makePlanQueryResponse({
        view_id: body.view_id,
        view_version: body.expected_view_version,
        catalog_revision: body.expected_catalog_revision,
        items: [
          makePlanTodo({
            title: "Actual record",
            catalog_revision: body.expected_catalog_revision,
          }),
        ],
        groups:
          body.view_id === "v2"
            ? [{ key: { kind: "status", id: "todo" }, count: 1 }]
            : [],
      }),
    ),
  );
  mocks.update.mockImplementation(
    (
      _project: string,
      id: string,
      body: { expected_version: number; definition?: unknown; name?: string },
    ) =>
      Promise.resolve({
        revision: 2,
        default_view_id: "v1",
        item: {
          ...view1,
          view_id: id,
          version: body.expected_version + 1,
          ...(body.definition ? { definition: body.definition } : {}),
          ...(body.name ? { name: body.name } : {}),
        },
      }),
  );
  mocks.setDefault.mockResolvedValue({ revision: 2, default_view_id: "v2" });
});
afterEach(() => vi.restoreAllMocks());
const ready = async () =>
  waitFor(() => expect(screen.getByTestId("renderer")).toBeInTheDocument());
describe("shared shell query configuration and C1 bridges", () => {
  it("loads server views and the POST query source without legacy GET pages", async () => {
    render(<ProjectPlanViews {...props()} />);
    await ready();
    await screen.findByText("Actual record");
    expect(mocks.query.mock.calls[0][1]).toMatchObject({
      view_id: "v1",
      expected_view_version: 1,
      expected_catalog_revision: 1,
    });
    expect(mocks.oldList).not.toHaveBeenCalled();
    expect(screen.getByText(/待办总数/)).toBeInTheDocument();
  });
  it("member full temporary settings query without PATCH and dirty switch Continue/Discard are real", async () => {
    render(<ProjectPlanViews {...props({ role: "member" })} />);
    await ready();
    fireEvent.click(screen.getByRole("button", { name: "视图设置" }));
    fireEvent.click(screen.getByText("Apply fields"));
    await waitFor(() =>
      expect(
        mocks.query.mock.calls.at(-1)![1].override_definition?.fields,
      ).toEqual(["title", "status"]),
    );
    fireEvent.click(screen.getByText("Save fields"));
    expect(mocks.update).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText("Board shared"));
    fireEvent.click(await screen.findByRole("button", { name: "继续编辑" }));
    expect(screen.getByTestId("renderer")).toHaveTextContent("v1");
    fireEvent.click(screen.getByText("Board shared"));
    fireEvent.click(await screen.findByRole("button", { name: "舍弃并切换" }));
    await waitFor(() =>
      expect(screen.getByTestId("renderer")).toHaveTextContent("v2"),
    );
    expect(mocks.query.mock.calls.at(-1)![1]).not.toHaveProperty(
      "override_definition",
    );
  });
  it("failed Save then switch keeps the full original draft and view", async () => {
    mocks.update.mockRejectedValue(apiError(409, "view_version_conflict"));
    render(<ProjectPlanViews {...props()} />);
    await ready();
    fireEvent.click(screen.getByRole("button", { name: "视图设置" }));
    fireEvent.click(screen.getByText("Apply fields"));
    fireEvent.click(screen.getByText("Board shared"));
    fireEvent.click(await screen.findByRole("button", { name: "保存后切换" }));
    await waitFor(() => expect(mocks.update).toHaveBeenCalledTimes(1));
    expect(mocks.update.mock.calls[0][2]).toMatchObject({
      expected_version: 1,
      expected_catalog_revision: 1,
      definition: { fields: ["title", "status"] },
    });
    expect(screen.getByTestId("renderer")).toHaveTextContent("v1");
    expect(screen.getByTestId("fields")).toHaveTextContent("title,status");
    expect(screen.getByTestId("settings-locked")).toHaveTextContent("true");
  });
  it("preserves search/status/assignee shortcuts as a complete real override definition", async () => {
    render(<ProjectPlanViews {...props()} />);
    await ready();
    fireEvent.change(screen.getByRole("textbox", { name: "搜索待办" }), {
      target: { value: "literal_%" },
    });
    await waitFor(() =>
      expect(
        mocks.query.mock.calls.at(-1)![1].override_definition.filters,
      ).toContainEqual({ field: "title", op: "contains", value: "literal_%" }),
    );
    expect(
      mocks.query.mock.calls.at(-1)![1].override_definition,
    ).toHaveProperty("schema_version", 1);
    expect(
      mocks.query.mock.calls.at(-1)![1].override_definition,
    ).toHaveProperty("sort");
    expect(
      screen.getByRole("combobox", { name: "筛选状态" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("combobox", { name: "筛选处理人" }),
    ).toBeInTheDocument();
    expect(mocks.oldList).not.toHaveBeenCalled();
  });
  it("waits for retry true and the new accepted catalog prop before using current view V/R", async () => {
    const retry = deferred<boolean>();
    const ref = createRef<ProjectPlanViewsHandle>();
    const initial = props({ onCatalogRetry: () => retry.promise });
    const { rerender } = render(<ProjectPlanViews {...initial} ref={ref} />);
    await ready();
    await screen.findByText("Actual record");
    mocks.get.mockResolvedValue({ ...view1, version: 2 });
    let refresh!: Promise<boolean>;
    act(() => {
      refresh = ref.current!.refreshCurrent();
    });
    await act(async () => {
      retry.resolve(true);
      await retry.promise;
    });
    expect(mocks.get).not.toHaveBeenCalled();
    const callsBefore = mocks.query.mock.calls.length;
    await act(async () => {
      rerender(
        <ProjectPlanViews
          {...initial}
          catalog={makePlanCatalog({ revision: 2 })}
          ref={ref}
        />,
      );
    });
    await act(async () => {
      expect(await refresh).toBe(true);
    });
    expect(
      mocks.query.mock.calls
        .slice(callsBefore)
        .every(
          (call) =>
            call[1].expected_view_version === 2 &&
            call[1].expected_catalog_revision === 2,
        ),
    ).toBe(true);
  });
  it("retry false stops requery and preserves a paused query draft", async () => {
    const ref = createRef<ProjectPlanViewsHandle>();
    render(<ProjectPlanViews {...props()} ref={ref} />);
    await ready();
    await screen.findByText("Actual record");
    mocks.query.mockRejectedValueOnce(apiError(409, "query_changed"));
    await act(async () => {
      await mocks.ui.renderer!.onLoadFirst("all");
    });
    expect(screen.getByTestId("metadata")).toHaveTextContent("paused");
    const before = mocks.query.mock.calls.length;
    await act(async () => {
      expect(await ref.current!.refreshCurrent()).toBe(false);
    });
    expect(mocks.query).toHaveBeenCalledTimes(before);
    expect(screen.getByText("Actual record")).toBeInTheDocument();
  });
  it("returns false/stale across account/project ABA during refresh and original renderer callbacks", async () => {
    const retry = deferred<boolean>();
    const ref = createRef<ProjectPlanViewsHandle>();
    const initial = props({ onCatalogRetry: () => retry.promise });
    const { rerender } = render(<ProjectPlanViews {...initial} ref={ref} />);
    await ready();
    await screen.findByText("Actual record");
    const oldRenderer = mocks.ui.renderer!;
    let refresh!: Promise<boolean>;
    act(() => {
      refresh = ref.current!.refreshCurrent();
    });
    rerender(<ProjectPlanViews {...initial} accountId={2} ref={ref} />);
    rerender(<ProjectPlanViews {...initial} accountId={1} ref={ref} />);
    await ready();
    await act(async () => {
      retry.resolve(true);
      expect(await refresh).toBe(false);
    });
    expect(
      await oldRenderer.onProposeTodoPatch(makePlanTodo(), {
        changes: { status: "done" },
        baseVersion: 1,
      }),
    ).toMatchObject({ status: "stale" });
    expect(initial.onProposeTodoPatch).not.toHaveBeenCalled();
  });
  it.each(
    [
      { context: "project", projectId: "p2", accountId: 1 },
      { context: "account", projectId: "p1", accountId: 2 },
    ].flatMap((context) =>
      [false, true].flatMap((returnToA) =>
        [200, 409, 404].map((status) => ({ ...context, returnToA, status })),
      ),
    ),
  )(
    "isolates pending shared definition PATCH $status across $context change (return to A=$returnToA)",
    async ({ projectId, accountId, returnToA, status }) => {
      const pending = deferred<PlanViewMutationResponse>();
      mocks.update.mockReturnValueOnce(pending.promise);
      let recordLabel = "A";
      mocks.query.mockImplementation(
        (project: string, body: PlanQueryRequest) =>
          Promise.resolve(
            makePlanQueryResponse({
              view_id: body.view_id,
              view_version: body.expected_view_version,
              catalog_revision: body.expected_catalog_revision,
              items: [
                makePlanTodo({
                  project_id: project,
                  title: `${recordLabel} record`,
                  catalog_revision: body.expected_catalog_revision,
                }),
              ],
            }),
          ),
      );
      const initial = props();
      const { rerender } = render(<ProjectPlanViews {...initial} />);
      await screen.findByText("A record");
      fireEvent.click(screen.getByRole("button", { name: "视图设置" }));
      fireEvent.click(screen.getByText("Apply fields"));
      await waitFor(() =>
        expect(
          mocks.query.mock.calls.at(-1)![1].override_definition?.fields,
        ).toEqual(["title", "status"]),
      );
      const originalSettings = mocks.ui.settings!;
      let save!: ReturnType<PlanViewSettingsProps["onSave"]>;
      act(() => {
        save = originalSettings.onSave({
          baseline: originalSettings.baseline,
          definition: originalSettings.definition,
        });
      });
      expect(mocks.update.mock.calls[0].slice(0, 3)).toEqual([
        "p1",
        "v1",
        {
          expected_version: 1,
          expected_catalog_revision: 1,
          definition: originalSettings.definition,
        },
      ]);

      const loadContext = async (
        nextProps: ProjectPlanViewsProps,
        label: string,
        version: number,
      ) => {
        recordLabel = label;
        const selected = makePlanView({
          project_id: nextProps.projectId,
          view_id: "v1",
          name: `${label} shared view`,
          version,
          definition: {
            ...makePlanDefinition(),
            fields: ["title", "priority"],
          },
        });
        const alternate = makePlanView({
          ...selected,
          view_id: "v2",
          name: `${label} alternate view`,
          position: 1,
        });
        const views = [selected, alternate];
        mocks.list.mockResolvedValue({
          ...list(views, version),
          project_id: nextProps.projectId,
        });
        rerender(<ProjectPlanViews {...nextProps} />);
        await screen.findByText(`${label} record`);
        expect(screen.queryByText("A record")).not.toBeInTheDocument();
        fireEvent.click(screen.getByRole("button", { name: "视图设置" }));
        const draft = {
          ...selected.definition,
          fields: ["title", "tags", "due_date"] as const,
        };
        act(() => mocks.ui.settings!.onTemporaryChange(draft));
        await waitFor(() =>
          expect(mocks.query.mock.calls.at(-1)!.slice(0, 2)).toEqual([
            nextProps.projectId,
            expect.objectContaining({
              view_id: selected.view_id,
              expected_view_version: version,
              expected_catalog_revision: nextProps.catalog!.revision,
              override_definition: draft,
            }),
          ]),
        );
        return { views, draft, selected, nextProps, label };
      };
      const nextProps = props({
        projectId,
        accountId,
        members: [{ user_id: accountId, username: "B owner", role: "owner" }],
        catalog: makePlanCatalog({ project_id: projectId, revision: 3 }),
      });
      let currentContext = await loadContext(nextProps, "B", 3);
      if (returnToA) {
        currentContext = await loadContext(
          { ...initial, catalog: makePlanCatalog({ revision: 4 }) },
          "Returned A",
          4,
        );
        expect(screen.queryByText("B record")).not.toBeInTheDocument();
      }
      const queryCount = mocks.query.mock.calls.length;
      const listCount = mocks.list.mock.calls.length;
      const currentBaseline = mocks.ui.settings!.baseline;
      await act(async () => {
        if (status === 200) {
          pending.resolve({
            item: {
              ...view1,
              name: "Late A saved view",
              version: 50,
              definition: originalSettings.definition,
            },
            revision: 50,
            default_view_id: "v1",
          });
        } else {
          pending.reject(
            apiError(
              status,
              status === 409 ? "view_version_conflict" : undefined,
            ),
          );
        }
        expect(await save).toMatchObject({ state: "stale" });
      });

      expect(
        screen.getByText(`${currentContext.label} record`),
      ).toBeInTheDocument();
      expect(screen.getByTestId("fields")).toHaveTextContent(
        "title,tags,due_date",
      );
      expect(
        screen.getByRole("button", {
          name: `${currentContext.label} shared view`,
        }),
      ).toBeInTheDocument();
      expect(
        screen.getByRole("button", {
          name: `${currentContext.label} alternate view`,
        }),
      ).toBeInTheDocument();
      expect(screen.queryByText("Late A saved view")).not.toBeInTheDocument();
      expect(mocks.ui.renderer!.view).toEqual(currentContext.selected);
      expect(mocks.ui.renderer!.definition).toEqual(currentContext.draft);
      expect(mocks.ui.manager!.views).toEqual(currentContext.views);
      expect(mocks.ui.settings!.baseline).toEqual(currentBaseline);
      expect(mocks.ui.settings!.conflictLocked).toBe(false);
      expect(mocks.ui.settings!.busy).toBe(false);
      expect(mocks.ui.settings!.errorKey).toBeNull();
      expect(mocks.ui.manager!.errorKey).toBeNull();
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
      expect(mocks.query).toHaveBeenCalledTimes(queryCount);
      expect(mocks.list).toHaveBeenCalledTimes(listCount);
      expect(mocks.update).toHaveBeenCalledTimes(1);
      expect(mocks.getProject).not.toHaveBeenCalled();
      expect(initial.onProjectAccessLost).not.toHaveBeenCalled();
      expect(nextProps.onProjectAccessLost).not.toHaveBeenCalled();
    },
  );
  it("same-view advance locks immutable dirty baseline; explicit compare confirmation preserves draft and upgrades V/R", async () => {
    const ref = createRef<ProjectPlanViewsHandle>();
    let acceptedProps = props();
    const retry = vi.fn(async () => true);
    acceptedProps = { ...acceptedProps, onCatalogRetry: retry };
    const { rerender } = render(
      <ProjectPlanViews {...acceptedProps} ref={ref} />,
    );
    await ready();
    await screen.findByText("Actual record");
    fireEvent.click(screen.getByRole("button", { name: "视图设置" }));
    fireEvent.click(screen.getByText("Apply fields"));
    mocks.get.mockResolvedValue({ ...view1, version: 2 });
    let refresh!: Promise<boolean>;
    act(() => {
      refresh = ref.current!.refreshCurrent();
    });
    await act(async () => {
      rerender(
        <ProjectPlanViews
          {...acceptedProps}
          catalog={makePlanCatalog({ revision: 2 })}
          ref={ref}
        />,
      );
    });
    await act(async () => {
      expect(await refresh).toBe(true);
    });
    expect(screen.getByTestId("settings-baseline")).toHaveTextContent("1:1");
    expect(screen.getByTestId("settings-locked")).toHaveTextContent("true");
    let comparison!: ReturnType<PlanViewSettingsProps["onRefreshCompare"]>;
    act(() => {
      comparison = mocks.ui.settings!.onRefreshCompare(
        mocks.ui.settings!.baseline,
      );
    });
    await act(async () => {
      rerender(
        <ProjectPlanViews
          {...acceptedProps}
          catalog={makePlanCatalog({ revision: 2 })}
          ref={ref}
        />,
      );
    });
    await act(async () => {
      await comparison;
    });
    fireEvent.click(screen.getByText("Confirm fields"));
    expect(screen.getByTestId("settings-baseline")).toHaveTextContent("2:2");
    expect(screen.getByTestId("fields")).toHaveTextContent("title,status");
    fireEvent.click(screen.getByText("Save fields"));
    await waitFor(() =>
      expect(mocks.update.mock.calls.at(-1)![2]).toMatchObject({
        expected_version: 2,
        expected_catalog_revision: 2,
      }),
    );
  });
  it("another default/collection revision does not falsely block a single-view version action", async () => {
    render(<ProjectPlanViews {...props()} />);
    await ready();
    await screen.findByText("Actual record");
    const original = { view: view1, collectionRevision: 1, catalogRevision: 1 };
    await act(async () => {
      expect(
        await mocks.ui.manager!.onDefault({
          baseline: { revision: 1, catalogRevision: 1 },
          viewId: "v2",
        }),
      ).toMatchObject({ state: "saved" });
    });
    await act(async () => {
      expect(
        await mocks.ui.manager!.onRename({
          baseline: original,
          name: "Renamed",
        }),
      ).toMatchObject({ state: "saved" });
    });
    expect(mocks.update.mock.calls[0][2]).toEqual({
      expected_version: 1,
      name: "Renamed",
    });
  });
  it("lost-assignee safe indices require explicit whole-condition temporary repair", async () => {
    const bad = {
      ...view1,
      definition: {
        ...makePlanDefinition(),
        filters: [
          { field: "assignee" as const, op: "in" as const, values: [999] },
          { field: "title" as const, op: "contains" as const, value: "keep" },
        ],
      },
    } as PlanView;
    mocks.list.mockResolvedValue(list([bad]));
    mocks.query.mockRejectedValueOnce(
      apiError(409, "filter_reference_unavailable", [0]),
    );
    render(<ProjectPlanViews {...props({ role: "member" })} />);
    fireEvent.click(
      await screen.findByRole("button", { name: "移除失效条件 1" }),
    );
    await waitFor(() =>
      expect(
        mocks.query.mock.calls.at(-1)![1].override_definition.filters,
      ).toEqual([{ field: "title", op: "contains", value: "keep" }]),
    );
    expect(mocks.update).not.toHaveBeenCalled();
  });
  it("view 404 rechecks project read and chooses a validated effective view instead of clearing the project", async () => {
    mocks.query.mockRejectedValueOnce(apiError(404));
    mocks.list
      .mockResolvedValueOnce(list())
      .mockResolvedValue(list([view2], 2, "v2"));
    const current = props();
    render(<ProjectPlanViews {...current} />);
    await waitFor(() =>
      expect(mocks.query.mock.calls.at(-1)![1].view_id).toBe("v2"),
    );
    expect(mocks.getProject).toHaveBeenCalledWith("p1");
    expect(current.onProjectAccessLost).not.toHaveBeenCalled();
  });
  it("project 404 clears private rows/draft; 403/422 retain legal rows", async () => {
    const current = props();
    const ref = createRef<ProjectPlanViewsHandle>();
    render(<ProjectPlanViews {...current} ref={ref} />);
    await ready();
    await screen.findByText("Actual record");
    mocks.query.mockRejectedValueOnce(apiError(422, "invalid_query_request"));
    await act(async () => {
      await mocks.ui.renderer!.onLoadFirst("all");
    });
    expect(screen.getByText("Actual record")).toBeInTheDocument();
    mocks.query.mockRejectedValueOnce(apiError(403));
    await act(async () => {
      await mocks.ui.renderer!.onLoadFirst("all");
    });
    expect(screen.getByText("Actual record")).toBeInTheDocument();
    mocks.query.mockRejectedValueOnce(apiError(404));
    mocks.getProject.mockRejectedValueOnce(apiError(404));
    await act(async () => {
      await mocks.ui.renderer!.onLoadFirst("all");
    });
    expect(current.onProjectAccessLost).toHaveBeenCalledTimes(1);
    expect(screen.queryByText("Actual record")).not.toBeInTheDocument();
  });
  it("bridges only highest-version unique loaded todos without a parent callback render loop", async () => {
    const current = props();
    const { rerender } = render(<ProjectPlanViews {...current} />);
    await ready();
    await screen.findByText("Actual record");
    const loaded = current.onLoadedTodosChanged as ReturnType<typeof vi.fn>;
    await waitFor(() => expect(loaded.mock.calls.at(-1)![0]).toHaveLength(1));
    const scope = loaded.mock.calls.at(-1)![1];
    expect(scope).toMatchObject({
      accountId: 1,
      projectId: "p1",
      viewId: "v1",
      channel: "loaded-todos",
    });
    const calls = loaded.mock.calls.length,
      queryCalls = mocks.query.mock.calls.length;
    rerender(<ProjectPlanViews {...current} onLoadedTodosChanged={vi.fn()} />);
    expect(loaded).toHaveBeenCalledTimes(calls);
    expect(mocks.query).toHaveBeenCalledTimes(queryCalls);
  });
  it("passes original scopes for C1 create/edit/detail/delete and rejects unsuccessful bulk", async () => {
    const current = props({
      canEdit: () => true,
      canDelete: () => false,
      onBulkTodo: vi.fn(async () => {
        throw apiError(409);
      }),
    });
    render(<ProjectPlanViews {...current} />);
    await ready();
    await screen.findByText("Actual record");
    fireEvent.click(screen.getByRole("button", { name: "新建待办" }));
    const todo = makePlanTodo();
    const originalRenderer = mocks.ui.renderer!;
    act(() => {
      originalRenderer.onEditTodo(todo);
      originalRenderer.onOpenTodo(todo, null);
      originalRenderer.onDeleteTodo(todo);
    });
    expect(current.onCreateTodo).toHaveBeenCalledWith(
      expect.objectContaining({ projectId: "p1", viewId: "v1" }),
    );
    expect(current.onEditTodo).toHaveBeenCalledWith(
      todo,
      expect.objectContaining({ projectId: "p1" }),
    );
    expect(current.onOpenTodo).toHaveBeenCalledWith(
      todo,
      null,
      expect.objectContaining({ projectId: "p1" }),
    );
    expect(current.onDeleteTodo).not.toHaveBeenCalled();
    await act(async () => {
      await expect(
        originalRenderer.onBulkTodo(
          [{ todo_id: todo.todo_id, expected_version: todo.version }],
          { status: "done" },
        ),
      ).rejects.toThrow();
    });
  });
  it("adds quick search to all shared predicates and replaces only its own temporary predicate", async () => {
    const shared = {
      ...view1,
      definition: {
        ...makePlanDefinition(),
        filters: [
          {
            field: "title" as const,
            op: "not_contains" as const,
            value: "private",
          },
          {
            field: "status" as const,
            op: "not_in" as const,
            values: ["done" as const],
          },
          { field: "assignee" as const, op: "in" as const, values: [1] },
        ],
      },
    } as PlanView;
    mocks.list.mockResolvedValue(list([shared]));
    render(<ProjectPlanViews {...props()} />);
    await ready();
    await screen.findByText("Actual record");
    fireEvent.change(screen.getByRole("textbox", { name: "搜索待办" }), {
      target: { value: "first" },
    });
    await waitFor(() =>
      expect(
        mocks.query.mock.calls.at(-1)![1].override_definition.filters,
      ).toEqual([
        ...shared.definition.filters,
        { field: "title", op: "contains", value: "first" },
      ]),
    );
    fireEvent.change(screen.getByRole("textbox", { name: "搜索待办" }), {
      target: { value: "second" },
    });
    await waitFor(() =>
      expect(
        mocks.query.mock.calls.at(-1)![1].override_definition.filters,
      ).toEqual([
        ...shared.definition.filters,
        { field: "title", op: "contains", value: "second" },
      ]),
    );
    fireEvent.change(screen.getByRole("textbox", { name: "搜索待办" }), {
      target: { value: "" },
    });
    await waitFor(() =>
      expect(
        mocks.query.mock.calls.at(-1)![1].override_definition.filters,
      ).toEqual(shared.definition.filters),
    );
    expect(mocks.update).not.toHaveBeenCalled();
  });
  it("same-view type replacement cannot discard an immutable dirty settings draft", async () => {
    render(<ProjectPlanViews {...props()} />);
    await ready();
    await screen.findByText("Actual record");
    fireEvent.click(screen.getByRole("button", { name: "视图设置" }));
    fireEvent.click(screen.getByText("Apply fields"));
    await screen.findByText("Actual record");
    await act(async () => {
      expect(
        await mocks.ui.manager!.onChangeType({
          baseline: { view: view1, collectionRevision: 1, catalogRevision: 1 },
          type: "board",
          definition: makePlanDefinition("board"),
        }),
      ).toMatchObject({ state: "invalid" });
    });
    expect(mocks.update).not.toHaveBeenCalled();
    expect(screen.getByTestId("fields")).toHaveTextContent("title,status");
    expect(screen.getByTestId("settings-baseline")).toHaveTextContent("1:1");
  });
  it("another view's version conflict leaves the selected dirty definition unlocked", async () => {
    mocks.update.mockRejectedValue(apiError(409, "view_version_conflict"));
    render(<ProjectPlanViews {...props()} />);
    await ready();
    await screen.findByText("Actual record");
    fireEvent.click(screen.getByRole("button", { name: "视图设置" }));
    fireEvent.click(screen.getByText("Apply fields"));
    await screen.findByText("Actual record");
    await act(async () => {
      expect(
        await mocks.ui.manager!.onRename({
          baseline: { view: view2, collectionRevision: 1, catalogRevision: 1 },
          name: "Other conflict",
        }),
      ).toMatchObject({ state: "conflict" });
    });
    expect(screen.getByTestId("settings-locked")).toHaveTextContent("false");
    expect(screen.getByTestId("fields")).toHaveTextContent("title,status");
  });
  it("view ABA during manual refresh returns false and the current view remains queryable", async () => {
    const retry = deferred<boolean>();
    const ref = createRef<ProjectPlanViewsHandle>();
    render(
      <ProjectPlanViews
        {...props({ onCatalogRetry: () => retry.promise })}
        ref={ref}
      />,
    );
    await ready();
    await screen.findByText("Actual record");
    const originalScope = ref.current!.captureOperation("editor")!;
    let refresh!: Promise<boolean>;
    act(() => {
      refresh = ref.current!.refreshCurrent();
    });
    fireEvent.click(screen.getByText("Board shared"));
    await waitFor(() =>
      expect(screen.getByTestId("renderer")).toHaveTextContent("v2"),
    );
    fireEvent.click(screen.getByText("Table shared"));
    await waitFor(() =>
      expect(screen.getByTestId("renderer")).toHaveTextContent("v1"),
    );
    await act(async () => {
      retry.resolve(true);
      expect(await refresh).toBe(false);
    });
    await waitFor(() =>
      expect(screen.getByText("Actual record")).toBeInTheDocument(),
    );
    expect(ref.current!.isCurrentOperation(originalScope)).toBe(false);
    expect(
      mocks.query.mock.calls.some((call) => call[1].view_id === "v2"),
    ).toBe(true);
    expect(
      mocks.query.mock.calls.filter((call) => call[1].view_id === "v1").length,
    ).toBeGreaterThan(1);
  });
  it("old renderer callbacks after a same-view query refresh cannot acquire new operation scopes", async () => {
    const ref = createRef<ProjectPlanViewsHandle>();
    const current = props();
    render(<ProjectPlanViews {...current} ref={ref} />);
    await ready();
    await screen.findByText("Actual record");
    const original = mocks.ui.renderer!,
      scope = ref.current!.captureOperation("probe")!;
    fireEvent.change(screen.getByRole("textbox", { name: "搜索待办" }), {
      target: { value: "next frame" },
    });
    await waitFor(() =>
      expect(
        mocks.query.mock.calls.at(-1)![1].override_definition.filters,
      ).toContainEqual({ field: "title", op: "contains", value: "next frame" }),
    );
    const todo = makePlanTodo();
    act(() => {
      original.onEditTodo(todo);
      original.onOpenTodo(todo, null);
      original.onDeleteTodo(todo);
    });
    await expect(
      original.onProposeTodoPatch(todo, {
        changes: { status: "done" },
        baseVersion: 1,
      }),
    ).resolves.toMatchObject({ status: "stale" });
    await expect(
      original.onBulkTodo([{ todo_id: todo.todo_id, expected_version: 1 }], {
        status: "done",
      }),
    ).rejects.toThrow();
    expect(current.onEditTodo).not.toHaveBeenCalled();
    expect(current.onOpenTodo).not.toHaveBeenCalled();
    expect(current.onDeleteTodo).not.toHaveBeenCalled();
    expect(current.onProposeTodoPatch).not.toHaveBeenCalled();
    expect(current.onBulkTodo).not.toHaveBeenCalled();
    expect(ref.current!.isCurrentOperation(scope)).toBe(false);
  });
  it("todo compare reads a filtered-out complete DTO only after accepting catalog/date metadata", async () => {
    const ref = createRef<ProjectPlanViewsHandle>();
    const initial = props({ onCatalogRetry: async () => true });
    const { rerender } = render(<ProjectPlanViews {...initial} ref={ref} />);
    await ready();
    await screen.findByText("Actual record");
    mocks.query.mockImplementation((_project: string, body: PlanQueryRequest) =>
      Promise.resolve(
        makePlanQueryResponse({
          view_id: body.view_id,
          view_version: body.expected_view_version,
          items: [],
          catalog_revision: body.expected_catalog_revision,
          server_today: "2026-09-30",
          server_timezone: "Asia/Shanghai",
        }),
      ),
    );
    mocks.getTodo.mockResolvedValue(
      makePlanTodo({
        version: 4,
        catalog_revision: 2,
        title: "Outside filter",
      }),
    );
    let comparison!: Promise<
      import("./ProjectPlanViews").PlanTodoCompareResult
    >;
    act(() => {
      comparison = mocks.ui.renderer!.onRefreshTodoCompare(makePlanTodo());
    });
    expect(mocks.getTodo).not.toHaveBeenCalled();
    await act(async () => {
      rerender(
        <ProjectPlanViews
          {...initial}
          catalog={makePlanCatalog({ revision: 2, server_today: "2026-09-30" })}
          ref={ref}
        />,
      );
    });
    await act(async () => {
      expect(await comparison).toMatchObject({
        state: "ready",
        todo: { title: "Outside filter", version: 4, catalog_revision: 2 },
        catalogRevision: 2,
        serverToday: "2026-09-30",
        serverTimezone: "Asia/Shanghai",
      });
    });
    expect(mocks.getTodo).toHaveBeenCalledWith("p1", "todo1");
  });
  it("todo compare cannot downgrade an accepted PATCH high water or accept a mismatched catalog DTO", async () => {
    const ref = createRef<ProjectPlanViewsHandle>();
    const initial = props({ onCatalogRetry: async () => true });
    const { rerender } = render(<ProjectPlanViews {...initial} ref={ref} />);
    await ready();
    await screen.findByText("Actual record");
    act(() => {
      const scope = ref.current!.captureOperation("patch")!;
      ref.current!.acceptTodo(
        scope,
        makePlanTodo({
          version: 5,
          catalog_revision: 2,
          title: "Accepted patch",
        }),
      );
    });
    mocks.getTodo.mockResolvedValue(
      makePlanTodo({ version: 4, catalog_revision: 2 }),
    );
    let comparison!: Promise<
      import("./ProjectPlanViews").PlanTodoCompareResult
    >;
    act(() => {
      comparison = mocks.ui.renderer!.onRefreshTodoCompare(makePlanTodo());
    });
    await act(async () => {
      rerender(
        <ProjectPlanViews
          {...initial}
          catalog={makePlanCatalog({ revision: 2 })}
          ref={ref}
        />,
      );
    });
    await act(async () => {
      expect(await comparison).toMatchObject({ state: "failed" });
    });
    expect(screen.getByText("Accepted patch")).toBeInTheDocument();
    mocks.getTodo.mockResolvedValue(
      makePlanTodo({ version: 6, catalog_revision: 1 }),
    );
    act(() => {
      comparison = mocks.ui.renderer!.onRefreshTodoCompare(makePlanTodo());
    });
    await act(async () => {
      rerender(
        <ProjectPlanViews
          {...initial}
          catalog={makePlanCatalog({ revision: 2 })}
          ref={ref}
        />,
      );
    });
    await act(async () => {
      expect(await comparison).toMatchObject({ state: "failed" });
    });
  });
  it("returns saved after ROOT accepted PATCH and its own refresh advanced query generation", async () => {
    const ref = createRef<ProjectPlanViewsHandle>();
    const saved = makePlanTodo({ version: 2, title: "Saved patch" });
    let refreshResult = false;
    const initial = props({
      onCatalogRetry: async () => true,
      onProposeTodoPatch: vi.fn(async (_todo, _proposal, scope) => {
        ref.current!.acceptTodo(scope, saved);
        refreshResult = await ref.current!.refreshCurrent();
        return { status: "saved" as const, todo: saved };
      }),
    });
    const { rerender } = render(<ProjectPlanViews {...initial} ref={ref} />);
    await ready();
    await screen.findByText("Actual record");
    let patch!: Promise<import("./ProjectPlanViews").PlanTodoMutationResult>;
    act(() => {
      patch = mocks.ui.renderer!.onProposeTodoPatch(makePlanTodo(), {
        changes: { status: "done" },
        baseVersion: 1,
      });
    });
    await act(async () => {
      rerender(
        <ProjectPlanViews {...initial} catalog={makePlanCatalog()} ref={ref} />,
      );
    });
    await act(async () => {
      expect(await patch).toMatchObject({ status: "saved", todo: saved });
    });
    expect(refreshResult).toBe(true);
    expect(screen.getByText("Saved patch")).toBeInTheDocument();
  });
  it("an accepted PATCH receipt never upgrades a delayed completion after a user query edit", async () => {
    const ref = createRef<ProjectPlanViewsHandle>();
    const completion =
      deferred<import("./ProjectPlanViews").PlanTodoMutationResult>();
    const saved = makePlanTodo({ version: 2, title: "Saved before user edit" });
    const initial = props({
      onProposeTodoPatch: vi.fn(async (_todo, _proposal, scope) => {
        ref.current!.acceptTodo(scope, saved);
        return completion.promise;
      }),
    });
    render(<ProjectPlanViews {...initial} ref={ref} />);
    await ready();
    await screen.findByText("Actual record");
    let patch!: Promise<import("./ProjectPlanViews").PlanTodoMutationResult>;
    act(() => {
      patch = mocks.ui.renderer!.onProposeTodoPatch(makePlanTodo(), {
        changes: { status: "done" },
        baseVersion: 1,
      });
    });
    fireEvent.change(screen.getByRole("textbox", { name: "搜索待办" }), {
      target: { value: "new user query" },
    });
    await waitFor(() =>
      expect(
        mocks.query.mock.calls.at(-1)![1].override_definition.filters,
      ).toContainEqual({
        field: "title",
        op: "contains",
        value: "new user query",
      }),
    );
    await act(async () => {
      completion.resolve({ status: "saved", todo: saved });
      expect(await patch).toMatchObject({ status: "stale" });
    });
  });
  it("manual metadata refresh pauses cursor loading and writes while retaining legal rows", async () => {
    const ref = createRef<ProjectPlanViewsHandle>();
    const retry = deferred<boolean>();
    const initial = props({ onCatalogRetry: () => retry.promise });
    mocks.query.mockImplementation((_project: string, body: PlanQueryRequest) =>
      Promise.resolve(
        makePlanQueryResponse({
          view_id: body.view_id,
          items: [makePlanTodo({ title: "Actual record" })],
          next_cursor: "old-cursor",
        }),
      ),
    );
    render(<ProjectPlanViews {...initial} ref={ref} />);
    await ready();
    await screen.findByText("Actual record");
    let refresh!: Promise<boolean>;
    act(() => {
      refresh = ref.current!.refreshCurrent();
    });
    const refreshing = mocks.ui.renderer!,
      before = mocks.query.mock.calls.length;
    await act(async () => {
      expect(await refreshing.onLoadMore("all")).toBe(false);
      expect(await refreshing.onLoadFirst("all")).toBe(false);
      expect(
        await refreshing.onProposeTodoPatch(makePlanTodo(), {
          changes: { status: "done" },
          baseVersion: 1,
        }),
      ).toMatchObject({ status: "stale" });
    });
    await expect(
      refreshing.onBulkTodo([{ todo_id: "todo1", expected_version: 1 }], {
        status: "done",
      }),
    ).rejects.toThrow();
    expect(mocks.query).toHaveBeenCalledTimes(before);
    expect(initial.onProposeTodoPatch).not.toHaveBeenCalled();
    expect(initial.onBulkTodo).not.toHaveBeenCalled();
    expect(screen.getByText("Actual record")).toBeInTheDocument();
    await act(async () => {
      retry.resolve(false);
      expect(await refresh).toBe(false);
    });
  });
  it.each(["members", "role", "member-role"] as const)(
    "rechecks DTO/ACL and invalidates old scopes when %s change without upgrading business V/R",
    async (kind) => {
      const ref = createRef<ProjectPlanViewsHandle>();
      const initial = props({
        members: [
          { user_id: 1, username: "owner", role: "owner" },
          { user_id: 2, username: "bob", role: "member" },
        ],
      });
      const { rerender } = render(<ProjectPlanViews {...initial} ref={ref} />);
      await ready();
      await screen.findByText("Actual record");
      const origin = mocks.ui.renderer!,
        originalScope = ref.current!.captureOperation("editor")!,
        calls = mocks.query.mock.calls.length;
      const changed =
        kind === "members"
          ? { ...initial, members: initial.members.slice(0, 1) }
          : kind === "role"
          ? { ...initial, role: "member" as const }
          : {
              ...initial,
              members: initial.members.map((member) =>
                member.user_id === 2
                  ? { ...member, role: "admin" as const }
                  : member,
              ),
            };
      await act(async () => {
        rerender(<ProjectPlanViews {...changed} ref={ref} />);
      });
      await waitFor(() =>
        expect(mocks.query.mock.calls.length).toBeGreaterThan(calls),
      );
      expect(mocks.query.mock.calls.at(-1)![1]).toMatchObject({
        expected_view_version: 1,
        expected_catalog_revision: 1,
      });
      expect(ref.current!.isCurrentOperation(originalScope)).toBe(false);
      const fresh = ref.current!.captureOperation("editor")!;
      expect(fresh.lifetime).toBe(originalScope.lifetime);
      expect(fresh.queryGeneration).toBeGreaterThan(
        originalScope.queryGeneration,
      );
      await expect(
        origin.onProposeTodoPatch(makePlanTodo(), {
          changes: { status: "done" },
          baseVersion: 1,
        }),
      ).resolves.toMatchObject({ status: "stale" });
      await expect(
        origin.onBulkTodo([{ todo_id: "todo1", expected_version: 1 }], {
          status: "done",
        }),
      ).rejects.toThrow();
      expect(initial.onProposeTodoPatch).not.toHaveBeenCalled();
      expect(initial.onBulkTodo).not.toHaveBeenCalled();
      expect(mocks.oldList).not.toHaveBeenCalled();
    },
  );
  it.each(["zh", "en"] as const)(
    "renders toolbar, counts and failure text from the actual %s resource",
    async (language) => {
      locale.language = language;
      const resource = (
        language === "en"
          ? await import("../../../locales/en.json")
          : await import("../../../locales/zh.json")
      ).default.projects;
      mocks.query.mockRejectedValue(apiError(403));
      const { container } = render(<ProjectPlanViews {...props()} />);
      await ready();
      expect(
        screen.getByRole("textbox", { name: resource.planViews.search }),
      ).toBeInTheDocument();
      expect(
        screen.getByRole("combobox", { name: resource.planViews.statusFilter }),
      ).toBeInTheDocument();
      expect(
        screen.getByRole("combobox", {
          name: resource.planViews.assigneeFilter,
        }),
      ).toBeInTheDocument();
      expect(
        screen.getByText(
          resource.planViews.totalCount.replace("{{count}}", "0"),
        ),
      ).toBeInTheDocument();
      expect(
        await screen.findByText(resource.planViews.forbidden),
      ).toBeInTheDocument();
      expect(container.textContent).not.toContain("projects.planViews.");
    },
  );
  it("discards an already pending old query when an explicit metadata refresh begins", async () => {
    const ref = createRef<ProjectPlanViewsHandle>(),
      retry = deferred<boolean>(),
      pending =
        deferred<
          import("../../../api/modules/projectPlanViews").PlanQueryResponse
        >();
    render(
      <ProjectPlanViews
        {...props({ onCatalogRetry: () => retry.promise })}
        ref={ref}
      />,
    );
    await ready();
    await screen.findByText("Actual record");
    mocks.query.mockReturnValueOnce(pending.promise);
    let olderQuery!: Promise<boolean>, refresh!: Promise<boolean>;
    act(() => {
      olderQuery = mocks.ui.renderer!.onLoadFirst("all");
    });
    const oldSignal = mocks.query.mock.calls.at(-1)![2].signal;
    act(() => {
      refresh = ref.current!.refreshCurrent();
    });
    await act(async () => {
      pending.resolve(
        makePlanQueryResponse({
          view_id: "v1",
          items: [makePlanTodo({ title: "Late prior query", version: 2 })],
        }),
      );
      expect(await olderQuery).toBe(false);
    });
    expect(oldSignal.aborted).toBe(true);
    expect(screen.getByText("Actual record")).toBeInTheDocument();
    expect(screen.queryByText("Late prior query")).not.toBeInTheDocument();
    await act(async () => {
      retry.resolve(false);
      expect(await refresh).toBe(false);
    });
  });
  it("a dirty selected definition cannot be silently discarded by archiving its view", async () => {
    mocks.archive.mockResolvedValue({
      revision: 2,
      default_view_id: "v2",
      item: { ...view1, version: 2, archived_at: 2 },
    });
    render(<ProjectPlanViews {...props()} />);
    await ready();
    await screen.findByText("Actual record");
    fireEvent.click(screen.getByRole("button", { name: "视图设置" }));
    fireEvent.click(screen.getByText("Apply fields"));
    await act(async () => {
      expect(
        await mocks.ui.manager!.onArchive({
          view: view1,
          collectionRevision: 1,
          catalogRevision: 1,
        }),
      ).toMatchObject({ state: "invalid" });
    });
    expect(mocks.archive).not.toHaveBeenCalled();
    expect(screen.getByTestId("fields")).toHaveTextContent("title,status");
  });
  it("a late default response cannot lower the accepted collection revision from another view mutation", async () => {
    const late = deferred<{ revision: number; default_view_id: string }>();
    mocks.setDefault.mockReturnValueOnce(late.promise);
    mocks.update.mockResolvedValue({
      revision: 3,
      default_view_id: "v2",
      item: { ...view2, name: "Other renamed", version: 2 },
    });
    render(<ProjectPlanViews {...props()} />);
    await ready();
    await screen.findByText("Actual record");
    let defaultAction!: Promise<
      import("./ProjectPlanViews").PlanViewActionResult
    >;
    act(() => {
      defaultAction = mocks.ui.manager!.onDefault({
        baseline: { revision: 1, catalogRevision: 1 },
        viewId: "v2",
      });
    });
    await act(async () => {
      expect(
        await mocks.ui.manager!.onRename({
          baseline: { view: view2, collectionRevision: 1, catalogRevision: 1 },
          name: "Other renamed",
        }),
      ).toMatchObject({ state: "saved" });
    });
    expect(mocks.ui.manager!.revision).toBe(3);
    await act(async () => {
      late.resolve({ revision: 2, default_view_id: "v2" });
      expect(await defaultAction).toMatchObject({ state: "saved" });
    });
    expect(mocks.ui.manager!.revision).toBe(3);
    expect(mocks.ui.manager!.defaultViewId).toBe("v2");
  });
  it("an old same-lifetime renderer draft may explicitly compare after refresh while writes and view ABA remain stale", async () => {
    const ref = createRef<ProjectPlanViewsHandle>(),
      initial = props({ onCatalogRetry: async () => true });
    const { rerender } = render(<ProjectPlanViews {...initial} ref={ref} />);
    await ready();
    await screen.findByText("Actual record");
    const original = mocks.ui.renderer!;
    let refresh!: Promise<boolean>;
    act(() => {
      refresh = ref.current!.refreshCurrent();
    });
    await act(async () => {
      rerender(
        <ProjectPlanViews
          {...initial}
          catalog={makePlanCatalog({ revision: 2 })}
          ref={ref}
        />,
      );
    });
    await act(async () => {
      expect(await refresh).toBe(true);
    });
    mocks.getTodo.mockResolvedValue(
      makePlanTodo({ version: 3, catalog_revision: 2 }),
    );
    let comparison!: Promise<
      import("./ProjectPlanViews").PlanTodoCompareResult
    >;
    act(() => {
      comparison = original.onRefreshTodoCompare(makePlanTodo());
    });
    await act(async () => {
      rerender(
        <ProjectPlanViews
          {...initial}
          catalog={makePlanCatalog({ revision: 2 })}
          ref={ref}
        />,
      );
    });
    await act(async () => {
      expect(await comparison).toMatchObject({
        state: "ready",
        todo: { version: 3, catalog_revision: 2 },
        catalogRevision: 2,
      });
    });
    await expect(
      original.onProposeTodoPatch(makePlanTodo(), {
        changes: { status: "done" },
        baseVersion: 1,
      }),
    ).resolves.toMatchObject({ status: "stale" });
    expect(initial.onProposeTodoPatch).not.toHaveBeenCalled();
    const beforeABA = mocks.ui.renderer!,
      reads = mocks.getTodo.mock.calls.length;
    fireEvent.click(screen.getByText("Board shared"));
    await waitFor(() =>
      expect(screen.getByTestId("renderer")).toHaveTextContent("v2"),
    );
    fireEvent.click(screen.getByText("Table shared"));
    await waitFor(() =>
      expect(screen.getByTestId("renderer")).toHaveTextContent("v1"),
    );
    await expect(
      beforeABA.onRefreshTodoCompare(makePlanTodo()),
    ).resolves.toMatchObject({ state: "stale" });
    expect(mocks.getTodo).toHaveBeenCalledTimes(reads);
  });
  it.each([false, true])(
    "todo compare 404 rechecks project read and clears private state only for project loss=%s",
    async (projectLost) => {
      const ref = createRef<ProjectPlanViewsHandle>(),
        initial = props({ onCatalogRetry: async () => true });
      const { rerender } = render(<ProjectPlanViews {...initial} ref={ref} />);
      await ready();
      await screen.findByText("Actual record");
      mocks.getTodo.mockRejectedValue(apiError(404));
      if (projectLost) mocks.getProject.mockRejectedValue(apiError(404));
      let comparison!: Promise<
        import("./ProjectPlanViews").PlanTodoCompareResult
      >;
      act(() => {
        comparison = mocks.ui.renderer!.onRefreshTodoCompare(makePlanTodo());
      });
      await act(async () => {
        rerender(
          <ProjectPlanViews
            {...initial}
            catalog={makePlanCatalog()}
            ref={ref}
          />,
        );
      });
      await act(async () => {
        expect(await comparison).toMatchObject({
          state: projectLost ? "stale" : "failed",
        });
      });
      expect(mocks.getProject).toHaveBeenCalledWith("p1");
      if (projectLost) {
        expect(initial.onProjectAccessLost).toHaveBeenCalledTimes(1);
        expect(screen.queryByText("Actual record")).not.toBeInTheDocument();
      } else {
        expect(initial.onProjectAccessLost).not.toHaveBeenCalled();
        expect(screen.getByText("Actual record")).toBeInTheDocument();
      }
    },
  );
});
