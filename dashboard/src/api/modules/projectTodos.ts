import {
  request,
  requestBlob,
  requestUpload,
  type UploadProgressHandler,
} from "../request";

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
export type ProjectTodoDescriptionFormat = "plain" | "markdown";

export interface ProjectTodo {
  todo_id: string;
  project_id: string;
  title: string;
  description: string;
  description_format: ProjectTodoDescriptionFormat;
  status: ProjectTodoStatus;
  start_date: string | null;
  due_date: string | null;
  priority_id: string | null;
  tag_ids: string[];
  catalog_revision: number;
  display_revision: number;
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

export interface ProjectTodoCommentImage {
  image_id: string;
  media_type: string;
  size_bytes: number;
  position: number;
}

export interface ProjectTodoComment {
  comment_id: string;
  todo_id: string;
  author_user_id: number | null;
  author_name: string;
  body: string;
  /** Empty in B1; B2 adds private images with separate authorized reads. */
  images: ProjectTodoCommentImage[];
  created_at: number;
}

export interface ProjectTodoCommentPage {
  items: ProjectTodoComment[];
  next_cursor: string | null;
}

export interface ProjectTodoCommentListParams {
  limit?: number;
  cursor?: string;
}

export interface ProjectTodoCommentCreateBody {
  body: string;
  client_request_id: string;
  images?: File[];
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
  description_format?: ProjectTodoDescriptionFormat;
  assignee_user_id?: number;
  status?: ProjectTodoStatus;
  start_date?: string | null;
  due_date?: string | null;
  priority_id?: string | null;
  tag_ids?: string[];
  expected_catalog_revision?: number;
}

export interface ProjectTodoUpdateBody {
  /** Optimistic concurrency token from the last read of this record. */
  expected_version: number;
  title?: string;
  description?: string;
  description_format?: ProjectTodoDescriptionFormat;
  status?: ProjectTodoStatus;
  /** `null` clears the assignee (owner/admin only). */
  assignee_user_id?: number | null;
  start_date?: string | null;
  due_date?: string | null;
  priority_id?: string | null;
  /** Full replacement set; [] clears all tags. */
  tag_ids?: string[];
  expected_catalog_revision?: number;
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
export const PROJECT_TODO_COMMENTS_PAGE_SIZE = 20;

const todosBase = (projectId: string) =>
  `/projects/${encodeURIComponent(projectId)}/todos`;

const todoPath = (projectId: string, todoId: string) =>
  `${todosBase(projectId)}/${encodeURIComponent(todoId)}`;

const commentsPath = (projectId: string, todoId: string) =>
  `${todoPath(projectId, todoId)}/comments`;

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
  get: (projectId: string, todoId: string, options?: RequestInit) =>
    options
      ? request<ProjectTodo>(todoPath(projectId, todoId), {
          ...options,
          method: "GET",
          body: undefined,
        })
      : request<ProjectTodo>(todoPath(projectId, todoId)),
  listComments: (
    projectId: string,
    todoId: string,
    params: ProjectTodoCommentListParams = {},
  ) => {
    const query = new URLSearchParams();
    query.set("limit", String(params.limit ?? PROJECT_TODO_COMMENTS_PAGE_SIZE));
    if (params.cursor) query.set("cursor", params.cursor);
    return request<ProjectTodoCommentPage>(
      `${commentsPath(projectId, todoId)}?${query.toString()}`,
    );
  },
  createComment: (
    projectId: string,
    todoId: string,
    body: ProjectTodoCommentCreateBody,
    options: RequestInit = {},
    onProgress?: UploadProgressHandler,
  ) => {
    const { images, ...json } = body;
    if (!images?.length) {
      return request<ProjectTodoComment>(commentsPath(projectId, todoId), {
        ...options,
        method: "POST",
        body: JSON.stringify(json),
      });
    }
    const form = new FormData();
    form.append("client_request_id", body.client_request_id);
    form.append("body", body.body);
    for (const image of images) form.append("images", image);
    return requestUpload<ProjectTodoComment>(
      commentsPath(projectId, todoId),
      form,
      options,
      onProgress,
    );
  },
  readCommentImage: (
    projectId: string,
    todoId: string,
    commentId: string,
    imageId: string,
    options: RequestInit = {},
  ) =>
    requestBlob(
      `${commentsPath(projectId, todoId)}/${encodeURIComponent(
        commentId,
      )}/images/${encodeURIComponent(imageId)}`,
      options,
    ),
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
