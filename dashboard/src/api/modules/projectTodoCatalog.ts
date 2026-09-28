import { request } from "../request";
export type TodoCatalogColor =
  | "red"
  | "orange"
  | "yellow"
  | "green"
  | "blue"
  | "purple"
  | "gray";
export interface TodoCatalogItem {
  name: string;
  color: TodoCatalogColor;
  archived_at: number | null;
  created_at: number;
  updated_at: number;
}
export interface TodoPriority extends TodoCatalogItem {
  priority_id: string;
  position: number;
}
export interface TodoTag extends TodoCatalogItem {
  tag_id: string;
}
export interface ProjectTodoCatalog {
  project_id: string;
  revision: number;
  server_today: string;
  server_timezone: string;
  priorities: TodoPriority[];
  tags: TodoTag[];
}
export interface CatalogCreateBody {
  expected_revision: number;
  name: string;
  color: TodoCatalogColor;
}
export interface CatalogUpdateBody {
  expected_revision: number;
  name?: string;
  color?: TodoCatalogColor;
}
export interface CatalogRevisionBody {
  expected_revision: number;
}
export interface CatalogItemResponse<T> {
  revision: number;
  item: T;
}
export interface CatalogOrderResponse {
  revision: number;
  priorities: TodoPriority[];
}
const base = (projectId: string) =>
  `/projects/${encodeURIComponent(projectId)}/plan`;
const optionPath = (
  projectId: string,
  kind: "priorities" | "tags",
  id: string,
) => `${base(projectId)}/${kind}/${encodeURIComponent(id)}`;
const write = <T>(path: string, method: string, body: object) =>
  request<T>(path, { method, body: JSON.stringify(body) });
export const projectTodoCatalogApi = {
  get: (p: string, options?: RequestInit) =>
    options
      ? request<ProjectTodoCatalog>(`${base(p)}/catalog`, options)
      : request<ProjectTodoCatalog>(`${base(p)}/catalog`),
  createPriority: (p: string, body: CatalogCreateBody) =>
    write<CatalogItemResponse<TodoPriority>>(
      `${base(p)}/priorities`,
      "POST",
      body,
    ),
  updatePriority: (p: string, id: string, body: CatalogUpdateBody) =>
    write<CatalogItemResponse<TodoPriority>>(
      optionPath(p, "priorities", id),
      "PATCH",
      body,
    ),
  orderPriorities: (
    p: string,
    body: CatalogRevisionBody & { priority_ids: string[] },
  ) => write<CatalogOrderResponse>(`${base(p)}/priorities/order`, "PUT", body),
  archivePriority: (p: string, id: string, body: CatalogRevisionBody) =>
    write<CatalogItemResponse<TodoPriority>>(
      `${optionPath(p, "priorities", id)}/archive`,
      "POST",
      body,
    ),
  restorePriority: (p: string, id: string, body: CatalogRevisionBody) =>
    write<CatalogItemResponse<TodoPriority>>(
      `${optionPath(p, "priorities", id)}/restore`,
      "POST",
      body,
    ),
  createTag: (p: string, body: CatalogCreateBody) =>
    write<CatalogItemResponse<TodoTag>>(`${base(p)}/tags`, "POST", body),
  updateTag: (p: string, id: string, body: CatalogUpdateBody) =>
    write<CatalogItemResponse<TodoTag>>(
      optionPath(p, "tags", id),
      "PATCH",
      body,
    ),
  archiveTag: (p: string, id: string, body: CatalogRevisionBody) =>
    write<CatalogItemResponse<TodoTag>>(
      `${optionPath(p, "tags", id)}/archive`,
      "POST",
      body,
    ),
  restoreTag: (p: string, id: string, body: CatalogRevisionBody) =>
    write<CatalogItemResponse<TodoTag>>(
      `${optionPath(p, "tags", id)}/restore`,
      "POST",
      body,
    ),
};
