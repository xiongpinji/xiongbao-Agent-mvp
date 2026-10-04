import type { ProjectTodo } from "../../../api/modules/projectTodos";
import type { ProjectTodoCatalog } from "../../../api/modules/projectTodoCatalog";
import type {
  AnyPlanDefinition,
  PlanQueryResponse,
  PlanView,
  PlanViewType,
} from "../../../api/modules/projectPlanViews";
import { catalogFixture } from "../todoCatalog.testFixtures";
import type { PlanRendererProps } from "./ProjectPlanViews";

export const makePlanTodo = (
  overrides: Partial<ProjectTodo> = {},
): ProjectTodo => ({
  todo_id: "todo1",
  project_id: "p1",
  title: "Synthetic todo",
  description: "",
  description_format: "plain",
  status: "todo",
  start_date: null,
  due_date: null,
  priority_id: null,
  tag_ids: [],
  catalog_revision: 1,
  display_revision: overrides.version ?? 1,
  creator_user_id: 1,
  assignee_user_id: null,
  version: 1,
  created_at: 1,
  updated_at: 1,
  ...overrides,
});
export function makePlanDefinition(
  type: PlanViewType = "table",
): AnyPlanDefinition {
  const definition = {
    schema_version: 1 as const,
    fields: ["title", "status", "assignee", "priority"] as const,
    group_by: type === "board" ? ("status" as const) : null,
    filters: [],
    sort: [{ field: "updated_at" as const, direction: "desc" as const }],
  };
  if (type === "gantt")
    return { ...definition, group_by: null, gantt: { zoom: "week" } };
  if (type === "calendar")
    return {
      ...definition,
      group_by: null,
      calendar: { date_basis: "due_date", mode: "month" },
    };
  return {
    ...definition,
    fields:
      type === "board"
        ? ["title", "status", "assignee", "priority", "tags"]
        : [
            "title",
            "status",
            "assignee",
            "priority",
            "tags",
            "start_date",
            "due_date",
          ],
  };
}
export function makePlanView(overrides: Partial<PlanView> = {}): PlanView {
  const type = overrides.type ?? "table";
  return {
    view_id: "view1",
    project_id: "p1",
    name: "Synthetic shared view",
    type,
    definition: makePlanDefinition(type),
    version: 1,
    position: 0,
    archived_at: null,
    created_at: 1,
    updated_at: 1,
    ...overrides,
  } as PlanView;
}
export const makePlanQueryResponse = (
  overrides: Partial<PlanQueryResponse> = {},
): PlanQueryResponse => ({
  items: [makePlanTodo()],
  next_cursor: null,
  total: 1,
  matched_total: 1,
  unscheduled_total: null,
  groups: [],
  view_id: "view1",
  view_version: 1,
  catalog_revision: 1,
  server_today: "2026-09-29",
  server_timezone: "Asia/Shanghai",
  query_fingerprint: "synthetic-fingerprint",
  ...overrides,
});
export const makePlanCatalog = (
  overrides: Partial<ProjectTodoCatalog> = {},
): ProjectTodoCatalog => ({
  ...catalogFixture,
  priorities: catalogFixture.priorities.map((item) => ({ ...item })),
  tags: catalogFixture.tags.map((item) => ({ ...item })),
  server_today: "2026-09-29",
  ...overrides,
});
export function deferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}
export function makePlanRendererProps(
  overrides: Partial<PlanRendererProps> = {},
): PlanRendererProps {
  const view = overrides.view ?? makePlanView();
  const todo = makePlanTodo();
  return {
    accountId: 1,
    projectId: "p1",
    view,
    definition: view.definition,
    lanes: [
      {
        laneId: "all",
        groupKey: null,
        bucket: null,
        items: [todo],
        serverCount: 1,
        nextCursor: null,
        queryFingerprint: "synthetic-fingerprint",
        loadingFirst: false,
        loadingMore: false,
        errorKey: null,
        paused: false,
      },
    ],
    total: 1,
    matchedTotal: 1,
    unscheduledTotal: null,
    catalog: makePlanCatalog(),
    members: [{ user_id: 1, username: "Synthetic member", role: "owner" }],
    serverToday: "2026-09-29",
    serverTimezone: "Asia/Shanghai",
    window: null,
    isManager: true,
    canEdit: () => true,
    canDelete: () => true,
    onOpenTodo: () => {},
    onEditTodo: () => {},
    onDeleteTodo: () => {},
    onProposeTodoPatch: async () => ({
      status: "failed",
      messageKey: "projects.planViews.failed",
    }),
    onRefreshTodoCompare: async () => ({
      state: "failed",
      messageKey: "projects.planViews.failed",
    }),
    onBulkTodo: async () => {},
    onLoadFirst: async () => false,
    onLoadMore: async () => false,
    onWindowChange: () => {},
    onTemporaryDefinitionChange: () => {},
    ...overrides,
  };
}
