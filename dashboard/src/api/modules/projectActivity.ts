import { request } from "../request";

/**
 * Project activity API (PS-03A) — safe project timeline + plain-text messages.
 *
 * Contract (`PROJECT_ACTIVITY_CONTRACT.md`):
 *   GET  /projects/{project_id}/activity?scope=related|members&limit=&cursor=
 *   POST /projects/{project_id}/messages  { body }
 *
 * `request()` prepends `/api`. The actor always comes from server auth, so
 * callers must never send a client-supplied user id, role, target or
 * timestamp. `cursor` is an opaque server token that is passed back verbatim;
 * it is a sort position, not an authorization credential. Non-members and
 * unknown projects both receive 404; an unparsable cursor receives 422 with
 * `PROJECT_ACTIVITY_CURSOR_INVALID`.
 */

export type ProjectActivityScope = "related" | "members";

export type ProjectActivityObjectKind =
  | "project"
  | "member"
  | "todo"
  | "message";

/**
 * Fixed safe DTO. The server never returns `payload_json`, task ids/titles,
 * invite ids/tokens, instructions, attachment paths or credentials; member
 * event targets intentionally omit `object_id`.
 */
export interface ProjectActivityItem {
  event_id: number;
  event_type: string;
  actor_user_id: number | null;
  actor_name: string | null;
  object_kind: ProjectActivityObjectKind;
  object_id: string | null;
  message_body: string | null;
  /** Unix epoch seconds, matching the backend's now_ts() convention. */
  created_at: number;
}

export interface ProjectActivityListResponse {
  items: ProjectActivityItem[];
  /** Opaque `created_at:id` token for the next page; null when exhausted. */
  next_cursor: string | null;
}

export interface ProjectActivityListParams {
  scope: ProjectActivityScope;
  limit?: number;
  /** Blank values are omitted so a refresh always starts from page one. */
  cursor?: string;
}

/** Contract default page size; server accepts 1–50. */
export const PROJECT_ACTIVITY_PAGE_SIZE = 20;
/** Server-side `<project_messages>` body cap (trimmed, 1–4000 chars). */
export const PROJECT_MESSAGE_MAX_LENGTH = 4000;

const activityBase = (projectId: string) =>
  `/projects/${encodeURIComponent(projectId)}/activity`;

const messagesBase = (projectId: string) =>
  `/projects/${encodeURIComponent(projectId)}/messages`;

export const projectActivityApi = {
  list: (projectId: string, params: ProjectActivityListParams) => {
    const query = new URLSearchParams();
    query.set("scope", params.scope);
    query.set("limit", String(params.limit ?? PROJECT_ACTIVITY_PAGE_SIZE));
    const cursor = params.cursor?.trim();
    if (cursor) query.set("cursor", cursor);
    return request<ProjectActivityListResponse>(
      `${activityBase(projectId)}?${query.toString()}`,
    );
  },
  postMessage: (projectId: string, body: string) =>
    request<ProjectActivityItem>(messagesBase(projectId), {
      method: "POST",
      body: JSON.stringify({ body }),
    }),
};
