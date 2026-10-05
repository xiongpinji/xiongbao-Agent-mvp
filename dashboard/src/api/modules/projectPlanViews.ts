import type { ProjectTodo, ProjectTodoStatus } from "./projectTodos";
import { request } from "../request";

export type PlanViewType = "list" | "table" | "board" | "gantt" | "calendar";
export type PlanVisibleField =
  | "title"
  | "status"
  | "assignee"
  | "priority"
  | "tags"
  | "start_date"
  | "due_date"
  | "created_at"
  | "updated_at"
  | "source";
export type PlanGroupBy =
  | "status"
  | "assignee"
  | "priority"
  | "tag"
  | "source"
  | null;
export type PlanFilterClause =
  | { field: "title"; op: "contains" | "not_contains"; value: string }
  | {
      field: "status";
      op: "in" | "not_in";
      values: readonly ProjectTodoStatus[];
    }
  | {
      field: "assignee";
      op: "in" | "not_in";
      values: readonly (number | null)[];
    }
  | {
      field: "priority";
      op: "in" | "not_in";
      values: readonly (string | null)[];
    }
  | { field: "tags"; op: "any" | "all" | "none_of"; values: readonly string[] }
  | { field: "tags" | "start_date" | "due_date"; op: "is_empty" | "not_empty" }
  | {
      field: "start_date" | "due_date";
      op: "on" | "before" | "after";
      value: string;
    }
  | {
      field: "start_date" | "due_date";
      op: "between";
      values: readonly [string, string];
    }
  | { field: "due_date"; op: "overdue"; value: boolean }
  | { field: "source"; op: "in" | "not_in"; values: readonly "manual"[] };
export interface PlanSortSpec {
  field: Exclude<PlanVisibleField, "tags" | "source">;
  direction: "asc" | "desc";
}
interface PlanDefinitionFields {
  schema_version: 1;
  fields: readonly PlanVisibleField[];
  group_by: PlanGroupBy;
  filters: readonly PlanFilterClause[];
  sort: readonly PlanSortSpec[];
}
export interface PlanDefinition extends PlanDefinitionFields {
  show_subtodos?: never;
  gantt?: never;
  calendar?: never;
}
export interface PlanTableDefinition extends PlanDefinitionFields {
  show_subtodos: boolean;
  gantt?: never;
  calendar?: never;
}
export interface PlanBoardDefinition extends PlanDefinition {
  group_by: Exclude<PlanGroupBy, null>;
}
export interface PlanGanttDefinition extends PlanDefinitionFields {
  show_subtodos?: never;
  group_by: null;
  gantt: { zoom: "day" | "week" | "month" };
  calendar?: never;
}
export interface PlanCalendarDefinition extends PlanDefinitionFields {
  show_subtodos?: never;
  group_by: null;
  calendar: { date_basis: "due_date" | "start_date"; mode: "month" | "week" };
  gantt?: never;
}
export type AnyPlanDefinition =
  | PlanDefinition
  | PlanTableDefinition
  | PlanBoardDefinition
  | PlanGanttDefinition
  | PlanCalendarDefinition;
type TypedPlanDefinition =
  | { type: "list"; definition: PlanDefinition }
  | { type: "table"; definition: PlanTableDefinition }
  | { type: "board"; definition: PlanBoardDefinition }
  | { type: "gantt"; definition: PlanGanttDefinition }
  | { type: "calendar"; definition: PlanCalendarDefinition };
export type PlanView = TypedPlanDefinition & {
  view_id: string;
  project_id: string;
  name: string;
  version: number;
  position: number;
  archived_at: number | null;
  created_at: number;
  updated_at: number;
};
export interface PlanViewListResponse {
  project_id: string;
  revision: number;
  default_view_id: string;
  items: PlanView[];
}
export interface PlanViewMutationResponse {
  revision: number;
  default_view_id: string;
  item: PlanView;
}
export interface PlanViewOrderResponse {
  revision: number;
  default_view_id: string;
  items: PlanView[];
}
export interface PlanViewDefaultResponse {
  revision: number;
  default_view_id: string;
}
export type PlanViewCreateBody = TypedPlanDefinition & {
  expected_revision: number;
  expected_catalog_revision: number;
  name: string;
};
export interface PlanViewUpdateBody {
  expected_version: number;
  name?: string;
  type?: PlanViewType;
  definition?: AnyPlanDefinition;
  expected_catalog_revision?: number;
}
export interface PlanViewOrderBody {
  expected_revision: number;
  view_ids: readonly string[];
}
export interface PlanViewDefaultBody {
  expected_revision: number;
  view_id: string;
}
export interface PlanViewVersionBody {
  expected_version: number;
}
export interface PlanQueryGroupKey {
  kind: Exclude<PlanGroupBy, null>;
  id: string | null;
}
export interface PlanQueryWindow {
  start_date: string;
  end_date: string;
}
export interface PlanQueryRequest {
  view_id: string;
  expected_view_version: number;
  expected_catalog_revision: number;
  override_definition?: AnyPlanDefinition;
  group_key?: PlanQueryGroupKey;
  window?: PlanQueryWindow;
  bucket?: "scheduled" | "unscheduled";
  limit?: number;
  cursor?: string;
}
export interface PlanQueryTodo extends ProjectTodo {
  parent_title: string | null;
}
export interface PlanQueryResponse {
  items: PlanQueryTodo[];
  next_cursor: string | null;
  total: number;
  matched_total: number;
  unscheduled_total: number | null;
  groups: { key: PlanQueryGroupKey; count: number }[];
  view_id: string;
  view_version: number;
  catalog_revision: number;
  server_today: string;
  server_timezone: string;
  query_fingerprint: string;
}

// Older saved tables omit the flag. New definitions always carry an explicit bool.
export function normalizePlanDefinition(
  type: PlanViewType,
  definition: AnyPlanDefinition,
): AnyPlanDefinition {
  return type === "table" &&
    !definition.gantt &&
    !definition.calendar &&
    !("show_subtodos" in definition)
    ? { ...definition, show_subtodos: false }
    : definition;
}

const base = (projectId: string) =>
  `/projects/${encodeURIComponent(projectId)}/plan`;
const viewPath = (projectId: string, viewId: string) =>
  `${base(projectId)}/views/${encodeURIComponent(viewId)}`;
const read = <T>(path: string, options: RequestInit = {}) =>
  request<T>(path, { ...options, method: "GET", body: undefined });
const write = <T>(
  path: string,
  method: "POST" | "PATCH" | "PUT",
  body: object,
  options: RequestInit = {},
) => request<T>(path, { ...options, method, body: JSON.stringify(body) });
export const projectPlanViewsApi = {
  list: (projectId: string, options?: RequestInit) =>
    read<PlanViewListResponse>(`${base(projectId)}/views`, options),
  get: (projectId: string, viewId: string, options?: RequestInit) =>
    read<PlanView>(viewPath(projectId, viewId), options),
  create: (
    projectId: string,
    body: PlanViewCreateBody,
    options?: RequestInit,
  ) =>
    write<PlanViewMutationResponse>(
      `${base(projectId)}/views`,
      "POST",
      body,
      options,
    ),
  update: (
    projectId: string,
    viewId: string,
    body: PlanViewUpdateBody,
    options?: RequestInit,
  ) =>
    write<PlanViewMutationResponse>(
      viewPath(projectId, viewId),
      "PATCH",
      body,
      options,
    ),
  order: (projectId: string, body: PlanViewOrderBody, options?: RequestInit) =>
    write<PlanViewOrderResponse>(
      `${base(projectId)}/views/order`,
      "PUT",
      body,
      options,
    ),
  setDefault: (
    projectId: string,
    body: PlanViewDefaultBody,
    options?: RequestInit,
  ) =>
    write<PlanViewDefaultResponse>(
      `${base(projectId)}/views/default`,
      "PUT",
      body,
      options,
    ),
  archive: (
    projectId: string,
    viewId: string,
    body: PlanViewVersionBody,
    options?: RequestInit,
  ) =>
    write<PlanViewMutationResponse>(
      `${viewPath(projectId, viewId)}/archive`,
      "POST",
      body,
      options,
    ),
  restore: (
    projectId: string,
    viewId: string,
    body: PlanViewVersionBody,
    options?: RequestInit,
  ) =>
    write<PlanViewMutationResponse>(
      `${viewPath(projectId, viewId)}/restore`,
      "POST",
      body,
      options,
    ),
  query: (projectId: string, body: PlanQueryRequest, options?: RequestInit) =>
    write<PlanQueryResponse>(`${base(projectId)}/query`, "POST", body, options),
};
