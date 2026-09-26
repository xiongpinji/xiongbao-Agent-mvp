import { request } from "../request";

/**
 * Project task API (PS-05 first slice + PS-05B-1 card sharing + PS-05B-2A
 * opt-in text reading) — human project space.
 *
 * Contract (`docs/xiongbao/PROJECT_TASK_ACL_CONTRACT.md`,
 * `docs/xiongbao/PROJECT_TASK_SHARE_CONTRACT.md` and
 * `docs/xiongbao/PROJECT_TASK_CONTENT_READ_CONTRACT.md`):
 *   GET    /projects/task-capabilities
 *   GET    /projects/{project_id}/tasks?scope=own|shared|all&q=&limit=&offset=
 *   GET    /projects/{project_id}/tasks/{thread_id}
 *   POST   /projects/{project_id}/tasks                  { agent_id, mode?, expected_instructions_sha256, expected_experts_revision? }
 *   POST   /projects/{project_id}/tasks/links            { thread_id }
 *   DELETE /projects/{project_id}/tasks/{thread_id}       (unlink only)
 *   DELETE /project-task-files/{thread_id}                (full private file-task delete)
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
 *
 * `create` freezes the project instructions the caller previewed: the body
 * carries only the chosen `agent_id`, the optional `mode` and that preview's
 * `expected_instructions_sha256` (never the instructions body, user id, role
 * or source). A changed digest returns 409 + `PROJECT_INSTRUCTIONS_CHANGED`
 * before any row exists, so the caller must refresh and confirm again. The
 * 201 response is just the existing safe task summary — creating a task is
 * never a sent first turn and the response must not be rendered as one.
 *
 * 030A controlled file tasks: `mode` defaults to `chat`, and old callers keep
 * sending the chat shape unchanged. `GET /projects/task-capabilities` is only
 * a UI hint (`{files:{available, reason}}`); the server re-checks it on create
 * and can reject with 422 + `PROJECT_TASK_FILES_UNSUPPORTED`, 409 +
 * `PROJECT_TASK_FILES_QUOTA` or 503 + `PROJECT_TASK_FILES_UNAVAILABLE`. A
 * `files` task's public card keeps `agent_id` as the selected source expert,
 * while `chat_agent_id` (owner-only) is the private runtime used for chat
 * navigation. Readers never receive the runtime id. The project-scoped DELETE
 * only unlinks; the private file task itself is deleted through
 * `DELETE /project-task-files/{thread_id}`, which is owner-only and
 * irreversible.
 */

/** Server-assigned link source; clients cannot choose it. */
export type ProjectTaskSource = "manual" | "project";

/**
 * Task workspace mode. Missing means `chat` for responses from older servers
 * and for every pre-030A linked row.
 */
export type ProjectTaskMode = "chat" | "files";

/**
 * Server-checked availability of the controlled file task (`mode: "files"`).
 * This is only a UI hint; creation re-checks it server-side.
 */
export interface ProjectTaskFilesCapability {
  available: boolean;
  /** Human-readable reason when `available` is false; may be null. */
  reason: string | null;
}

export interface ProjectTaskCapabilities {
  files: ProjectTaskFilesCapability;
}

/** Server-side list scope. `all` is own + explicitly shared cards only. */
export type ProjectTaskScope = "own" | "shared" | "all";

/** Who the caller is relative to this card; drives read-only rendering. */
export type ProjectTaskAccess = "owner" | "reader";

/** Safe summary returned by every project task route. */
export interface ProjectTask {
  project_id: string;
  thread_id: string;
  owner_user_id: number;
  /** Public selected source expert id; always safe to display. */
  agent_id: string;
  /** Task workspace mode; absent on old responses and means `chat`. */
  mode?: ProjectTaskMode;
  /** Source expert for a `files` task; owner-only detail. */
  source_expert_id?: string | null;
  /**
   * Owner-only private runtime agent id used for `/chat/{id}/{thread}` on a
   * `files` task. Never present for readers and never rendered as text.
   */
  chat_agent_id?: string | null;
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
  /**
   * Server-wide controlled file task capability. Tolerate an absent route
   * (404) as "unavailable" instead of inventing a create action.
   */
  taskCapabilities: () =>
    request<ProjectTaskCapabilities>("/projects/task-capabilities"),
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
  /**
   * Creates a private project-owned task. `expectedInstructionsSha256` is the
   * digest of the instructions the member previewed. New clients also send
   * `expectedExpertsRevision` from the current project expert selection.
   * The server rejects either stale preview before creating a task.
   *
   * `mode` is only sent when the caller explicitly picked `files`; existing
   * chat callers keep the exact old body so the server default stays `chat`.
   */
  create: (
    projectId: string,
    agentId: string,
    expectedInstructionsSha256: string,
    expectedExpertsRevision?: number,
    mode?: ProjectTaskMode,
  ) =>
    request<ProjectTask>(tasksBase(projectId), {
      method: "POST",
      body: JSON.stringify({
        agent_id: agentId,
        expected_instructions_sha256: expectedInstructionsSha256,
        ...(expectedExpertsRevision == null
          ? {}
          : { expected_experts_revision: expectedExpertsRevision }),
        ...(mode == null ? {} : { mode }),
      }),
    }),
  link: (projectId: string, threadId: string) =>
    request<ProjectTask>(`${tasksBase(projectId)}/links`, {
      method: "POST",
      body: JSON.stringify({ thread_id: threadId }),
    }),
  /** Detaches the project link only; the conversation and files are kept. */
  unlink: (projectId: string, threadId: string) =>
    request<void>(taskPath(projectId, threadId), { method: "DELETE" }),
  /**
   * Irreversible full delete of the caller's private file task (runtime,
   * thread metadata and managed workspace). Owner-only and independent of the
   * project link. Repeated deletes return 404 after the first success.
   */
  deleteFileTask: (threadId: string) =>
    request<void>(`/project-task-files/${encodeURIComponent(threadId)}`, {
      method: "DELETE",
    }),
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
