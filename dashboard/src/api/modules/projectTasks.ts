import { request } from "../request";

/**
 * Project task API (PS-05 first slice + PS-05B-1 card sharing + PS-05B-2A
 * opt-in text reading) — human project space.
 *
 * Contract (`docs/xiongbao/PROJECT_TASK_ACL_CONTRACT.md`,
 * `docs/xiongbao/PROJECT_TASK_SHARE_CONTRACT.md` and
 * `docs/xiongbao/PROJECT_TASK_CONTENT_READ_CONTRACT.md`):
 *   GET    /projects/{project_id}/tasks?scope=own|shared|all&q=&limit=&offset=
 *   GET    /projects/{project_id}/tasks/{thread_id}
 *   POST   /projects/{project_id}/tasks/links            { thread_id }
 *   DELETE /projects/{project_id}/tasks/{thread_id}
 *   GET    /projects/{project_id}/tasks/{thread_id}/shares
 *   POST   /projects/{project_id}/tasks/{thread_id}/shares   { user_id }
 *   DELETE /projects/{project_id}/tasks/{thread_id}/shares/{user_id}
 *   POST   /projects/{project_id}/tasks/{thread_id}/shares/{user_id}/text
 *   DELETE /projects/{project_id}/tasks/{thread_id}/shares/{user_id}/text
 *   GET    /projects/{project_id}/tasks/{thread_id}/messages?limit=&before_seq=
 *
 * Visibility is always server-filtered: `scope` is sent on every list call
 * (default `own`), so the client never decides which cards a member may see.
 * The actor is always the authenticated user: the client never sends a user
 * id, role or source to task routes, and the text grant/revoke body is empty
 * (the server assigns grantee from the path and grantor/time itself). Task
 * card sharing alone never grants text: `can_read_text` is a separate boolean
 * the server computes per caller, and only a `ready` messages page carries
 * projected plain text (never raw history, `message_json`, tools, thinking,
 * paths or artifacts). Responses carry only the safe summary — never
 * `session_key`, artifacts, paths, messages or resumes. The share DTO is
 * separate from the task summary and never carries credentials, body, paths
 * or artifacts. Non-members receive 404, a task already linked to another
 * project returns 409 + `PROJECT_TASK_LINK_CONFLICT`, and deleting a link
 * never deletes the original conversation.
 */

/** Server-assigned link source; clients cannot choose it. */
export type ProjectTaskSource = "manual";

/** Server-side list scope. `all` is own + explicitly shared cards only. */
export type ProjectTaskScope = "own" | "shared" | "all";

/** Who the caller is relative to this card; drives read-only rendering. */
export type ProjectTaskAccess = "owner" | "reader";

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
  /** `reader` cards must never expose a `/chat` link or write actions. */
  access: ProjectTaskAccess;
  /**
   * Owner: always true. Reader: true only with an active card share *and* a
   * separate 025 text grant. Card-only readers must never request text.
   */
  can_read_text: boolean;
}

export interface ProjectTaskListResponse {
  items: ProjectTask[];
  limit: number;
  offset: number;
  has_more: boolean;
}

export interface ProjectTaskListParams {
  /** Defaults to `own` and is always sent, so filtering stays server-side. */
  scope?: ProjectTaskScope;
  /** Title keyword; blank values are omitted. Wildcards are escaped server-side. */
  q?: string;
  /** Server cap is 100. */
  limit?: number;
  offset?: number;
}

/** Fixed share role for this slice; the server assigns it. */
export type ProjectTaskShareRole = "reader";

/** One active reader grant, without any grantee profile or credentials. */
export interface ProjectTaskShare {
  user_id: number;
  role: ProjectTaskShareRole;
  /** Unix epoch seconds. */
  granted_at: number;
  /** Whether this reader additionally holds the separate 025 text grant. */
  can_read_text: boolean;
}

export interface ProjectTaskSharesResponse {
  items: ProjectTaskShare[];
}

/** `{user_id, granted_at}` returned by the separate text grant. */
export interface ProjectTaskTextGrant {
  user_id: number;
  /** Unix epoch seconds; re-granting an active row keeps the first time. */
  granted_at: number;
}

/** Plain-text role in the projected conversation. */
export type ProjectTaskMessageRole = "user" | "assistant";

/**
 * Projection readiness. `pending` means the text is not synced yet or uses
 * versioned history storage this slice does not support; it never carries
 * items and must not be shown as an empty conversation.
 */
export type ProjectTaskMessagesStatus = "ready" | "pending";

/** One displayable plain-text message from the server-side projection. */
export interface ProjectTaskMessage {
  /** Monotonic conversation row sequence; pages are ordered by it. */
  seq: number;
  role: ProjectTaskMessageRole;
  /** Escaped plain text only — never HTML, Markdown or tool metadata. */
  text: string;
  /** Unix epoch seconds. */
  created_at: number;
  /** True when the server capped this text at its character boundary. */
  truncated: boolean;
}

/** One cursor page of projected plain text (`seq` descending). */
export interface ProjectTaskMessagesResponse {
  status: ProjectTaskMessagesStatus;
  items: ProjectTaskMessage[];
  has_more: boolean;
  /** Cursor for the next older page; null when there is none. */
  next_before_seq: number | null;
}

export interface ProjectTaskMessagesParams {
  /** Server cap is 100. */
  limit?: number;
  /** Positive row `seq`; omit for the newest page. */
  beforeSeq?: number;
}

/** Server-side page size cap is 100; keep a readable default. */
export const PROJECT_TASKS_PAGE_SIZE = 50;

/** Contract default for the text page (`limit` 1–100, default 50). */
export const PROJECT_TASK_MESSAGE_PAGE_SIZE = 50;

const tasksBase = (projectId: string) =>
  `/projects/${encodeURIComponent(projectId)}/tasks`;

const taskPath = (projectId: string, threadId: string) =>
  `${tasksBase(projectId)}/${encodeURIComponent(threadId)}`;

const sharesPath = (projectId: string, threadId: string) =>
  `${taskPath(projectId, threadId)}/shares`;

const textPath = (projectId: string, threadId: string, userId: number) =>
  `${sharesPath(projectId, threadId)}/${encodeURIComponent(
    String(userId),
  )}/text`;

const messagesPath = (projectId: string, threadId: string) =>
  `${taskPath(projectId, threadId)}/messages`;

export const projectTasksApi = {
  list: (projectId: string, params: ProjectTaskListParams = {}) => {
    const query = new URLSearchParams();
    query.set("scope", params.scope ?? "own");
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
  shares: (projectId: string, threadId: string) =>
    request<ProjectTaskSharesResponse>(sharesPath(projectId, threadId)),
  share: (projectId: string, threadId: string, userId: number) =>
    request<ProjectTaskShare>(sharesPath(projectId, threadId), {
      method: "POST",
      body: JSON.stringify({ user_id: userId }),
    }),
  revoke: (projectId: string, threadId: string, userId: number) =>
    request<void>(
      `${sharesPath(projectId, threadId)}/${encodeURIComponent(
        String(userId),
      )}`,
      { method: "DELETE" },
    ),
  /** Separate opt-in text grant for an already card-shared member. */
  grantText: (projectId: string, threadId: string, userId: number) =>
    request<ProjectTaskTextGrant>(textPath(projectId, threadId, userId), {
      method: "POST",
    }),
  /** Revokes text only; the card share stays active. */
  revokeText: (projectId: string, threadId: string, userId: number) =>
    request<void>(textPath(projectId, threadId, userId), {
      method: "DELETE",
    }),
  /** Newest page first; pass `beforeSeq` from `next_before_seq` for older. */
  messages: (
    projectId: string,
    threadId: string,
    params: ProjectTaskMessagesParams = {},
  ) => {
    const query = new URLSearchParams();
    query.set("limit", String(params.limit ?? PROJECT_TASK_MESSAGE_PAGE_SIZE));
    if (params.beforeSeq != null) {
      query.set("before_seq", String(params.beforeSeq));
    }
    return request<ProjectTaskMessagesResponse>(
      `${messagesPath(projectId, threadId)}?${query.toString()}`,
    );
  },
};
