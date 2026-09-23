import { request } from "../request";

/**
 * Project plan todos API (PS-04) — human project space, not Agent expert teams.
 *
 * Contract (`PROJECT_TODO_CONTRACT.md`):
 *   GET    /projects/{project_id}/todos?q=&status=&assignee_user_id=&limit=&offset=
 *   POST   /projects/{project_id}/todos
 *   GET    /projects/{project_id}/todos/{todo_id}
 *   PATCH  /projects/{project_id}/todos/{todo_id}
 *   DELETE /projects/{project_id}/todos/{todo_id}?expected_version=
 *   POST   /projects/{project_id}/todos/bulk
 *
 * `request()` prepends `/api`. The actor always comes from server auth —
 * callers must never send a client-supplied user id in a body. Non-members
 * receive 404; members without write rights receive 403.
 */

export type ProjectTodoStatus = "todo" | "in_progress" | "done";

export interface ProjectTodo {
  todo_id: string;
  project_id: string;
  title: string;
  description: string;
  status: ProjectTodoStatus;
  creator_user_id: number;
  assignee_user_id: number | null;
  version: number;
  /** Unix epoch seconds, matching the backend's now_ts() convention. */
  created_at: number;
  updated_at: number;
}

export interface ProjectTodoListResponse {
  items: ProjectTodo[];
  limit: number;
  offset: number;
  has_more: boolean;
}

export interface ProjectTodoListParams {
  /** Title keyword; blank values are omitted. Wildcards are escaped server-side. */
  q?: string;
  status?: ProjectTodoStatus;
  assigneeUserId?: number;
  limit?: number;
  offset?: number;
}

export interface ProjectTodoCreateBody {
  title: string;
  description?: string;
  assignee_user_id?: number;
}

export interface ProjectTodoUpdateBody {
  /** Optimistic concurrency token from the last read of this record. */
  expected_version: number;
  title?: string;
  description?: string;
  status?: ProjectTodoStatus;
  /** `null` clears the assignee (owner/admin only). */
  assignee_user_id?: number | null;
}

export interface ProjectTodoBulkItem {
  todo_id: string;
  expected_version: number;
}

export interface ProjectTodoBulkBody {
  items: ProjectTodoBulkItem[];
  status?: ProjectTodoStatus;
  assignee_user_id?: number | null;
}

/**
 * `POST /bulk` response envelope is not pinned by the contract beyond "the
 * updated records"; accept the list-style `{items}` envelope and a bare
 * array so the UI can recover by reloading when neither is usable.
 */
export type ProjectTodoBulkResponse = { items: ProjectTodo[] } | ProjectTodo[];

export function normalizeTodoBulkResponse(response: unknown): ProjectTodo[] {
  if (Array.isArray(response)) return response as ProjectTodo[];
  if (response && typeof response === "object") {
    const items = (response as { items?: unknown }).items;
    if (Array.isArray(items)) return items as ProjectTodo[];
  }
  return [];
}

/** Server-side page size cap is 100; keep a readable default. */
export const PROJECT_TODOS_PAGE_SIZE = 50;
/** Atomic `/bulk` accepts 1–50 records per request. */
export const PROJECT_TODOS_BULK_MAX = 50;

const todosBase = (projectId: string) =>
  `/projects/${encodeURIComponent(projectId)}/todos`;

const todoPath = (projectId: string, todoId: string) =>
  `${todosBase(projectId)}/${encodeURIComponent(todoId)}`;

export const projectTodosApi = {
  list: (projectId: string, params: ProjectTodoListParams = {}) => {
    const query = new URLSearchParams();
    const q = params.q?.trim();
    if (q) query.set("q", q);
    if (params.status) query.set("status", params.status);
    if (params.assigneeUserId != null) {
      query.set("assignee_user_id", String(params.assigneeUserId));
    }
    query.set("limit", String(params.limit ?? PROJECT_TODOS_PAGE_SIZE));
    query.set("offset", String(params.offset ?? 0));
    return request<ProjectTodoListResponse>(
      `${todosBase(projectId)}?${query.toString()}`,
    );
  },
  create: (projectId: string, body: ProjectTodoCreateBody) =>
    request<ProjectTodo>(todosBase(projectId), {
      method: "POST",
      body: JSON.stringify(body),
    }),
  get: (projectId: string, todoId: string) =>
    request<ProjectTodo>(todoPath(projectId, todoId)),
  update: (projectId: string, todoId: string, body: ProjectTodoUpdateBody) =>
    request<ProjectTodo>(todoPath(projectId, todoId), {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  remove: (projectId: string, todoId: string, expectedVersion: number) =>
    request<void>(
      `${todoPath(projectId, todoId)}?expected_version=${expectedVersion}`,
      { method: "DELETE" },
    ),
  bulk: (projectId: string, body: ProjectTodoBulkBody) =>
    request<ProjectTodoBulkResponse>(`${todosBase(projectId)}/bulk`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
};
