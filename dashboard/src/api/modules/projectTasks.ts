import { request } from "../request";

/**
 * Project task-links API (PS-05 first slice) — human project space.
 *
 * Contract (`docs/xiongbao/PROJECT_TASK_ACL_CONTRACT.md`):
 *   GET    /projects/{project_id}/tasks?q=&limit=&offset=
 *   GET    /projects/{project_id}/tasks/{thread_id}
 *   POST   /projects/{project_id}/tasks/links   { thread_id }
 *   DELETE /projects/{project_id}/tasks/{thread_id}
 *
 * The actor is always the authenticated user: the client never sends a user
 * id, role or source. Responses carry only the safe summary — never
 * `session_key`, artifacts, paths, messages or resumes. Non-members receive
 * 404, a task already linked to another project returns 409 +
 * `PROJECT_TASK_LINK_CONFLICT`, and deleting a link never deletes the
 * original conversation.
 */

/** Server-assigned link source; clients cannot choose it. */
export type ProjectTaskSource = "manual";

/** Safe summary returned by every project task route. */
export interface ProjectTask {
  project_id: string;
  thread_id: string;
  owner_user_id: number;
  agent_id: string;
  title: string | null;
  source: ProjectTaskSource;
  /** Unix epoch seconds, matching the backend's now_ts() convention. */
  last_active: number;
  created_at: number;
}

export interface ProjectTaskListResponse {
  items: ProjectTask[];
  limit: number;
  offset: number;
  has_more: boolean;
}

export interface ProjectTaskListParams {
  /** Title keyword; blank values are omitted. Wildcards are escaped server-side. */
  q?: string;
  /** Server cap is 100. */
  limit?: number;
  offset?: number;
}

/** Server-side page size cap is 100; keep a readable default. */
export const PROJECT_TASKS_PAGE_SIZE = 50;

const tasksBase = (projectId: string) =>
  `/projects/${encodeURIComponent(projectId)}/tasks`;

const taskPath = (projectId: string, threadId: string) =>
  `${tasksBase(projectId)}/${encodeURIComponent(threadId)}`;

export const projectTasksApi = {
  list: (projectId: string, params: ProjectTaskListParams = {}) => {
    const query = new URLSearchParams();
    const q = params.q?.trim();
    if (q) query.set("q", q);
    query.set("limit", String(params.limit ?? PROJECT_TASKS_PAGE_SIZE));
    query.set("offset", String(params.offset ?? 0));
    return request<ProjectTaskListResponse>(
      `${tasksBase(projectId)}?${query.toString()}`,
    );
  },
  get: (projectId: string, threadId: string) =>
    request<ProjectTask>(taskPath(projectId, threadId)),
  link: (projectId: string, threadId: string) =>
    request<ProjectTask>(`${tasksBase(projectId)}/links`, {
      method: "POST",
      body: JSON.stringify({ thread_id: threadId }),
    }),
  unlink: (projectId: string, threadId: string) =>
    request<void>(taskPath(projectId, threadId), { method: "DELETE" }),
};
