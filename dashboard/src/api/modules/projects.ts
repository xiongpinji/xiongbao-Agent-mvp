import { request } from "../request";

/**
 * Project space API — human collaboration domains (members, plans, assets).
 * Distinct from `teamsApi`, which manages expert Agent teams.
 *
 * Contract (PROJECT_SPACE_SPEC.md §数据与接口合同, batch 1):
 *   GET   /projects?q=&limit=&offset=  → ProjectListResponse
 *   POST  /projects                    → ProjectRecord
 *   GET   /projects/{id}               → ProjectRecord
 *   PATCH /projects/{id}               → ProjectRecord
 *   GET   /projects/{id}/members       → ProjectMember[]
 *
 * `request()` prepends `/api`. Non-members receive 404 on detail routes so
 * project existence is not leaked; logged-in members without edit rights
 * receive 403 on writes.
 */

/** Role of the calling user inside a project (server-provided). */
export type ProjectRole = "owner" | "admin" | "member";

export interface ProjectSummary {
  project_id: string;
  name: string;
  description: string;
  my_role: ProjectRole;
  member_count: number;
  /** Unix epoch seconds, matching the backend's now_ts() convention. */
  created_at: number;
  updated_at: number;
}

/** Only detail/create/update responses contain project instructions. */
export interface ProjectRecord extends ProjectSummary {
  instructions: string;
  /**
   * Server-computed SHA-256 of the current `instructions` UTF-8 bytes. Task
   * creation sends this previewed digest so a concurrent edit is rejected
   * instead of freezing unexpected instructions. Never contains the body.
   */
  instructions_sha256: string;
}

export interface ProjectListResponse {
  items: ProjectSummary[];
  limit: number;
  offset: number;
  has_more: boolean;
}

export interface ProjectMember {
  user_id: number;
  username: string;
  role: ProjectRole;
}

export interface ProjectCreateBody {
  name: string;
  description?: string;
  instructions?: string;
}

export interface ProjectUpdateBody {
  name?: string;
  description?: string;
  instructions?: string;
}

/** Server-side page size fixed by the batch-1 contract. */
export const PROJECTS_PAGE_SIZE = 20;

export interface ProjectListParams {
  /** Search keyword; blank values are omitted from the query string. */
  q?: string;
  limit?: number;
  offset?: number;
}

export const projectsApi = {
  list: (params: ProjectListParams = {}) => {
    const query = new URLSearchParams();
    const q = params.q?.trim();
    if (q) query.set("q", q);
    query.set("limit", String(params.limit ?? PROJECTS_PAGE_SIZE));
    query.set("offset", String(params.offset ?? 0));
    return request<ProjectListResponse>(`/projects?${query.toString()}`);
  },
  create: (body: ProjectCreateBody) =>
    request<ProjectRecord>("/projects", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  get: (projectId: string) =>
    request<ProjectRecord>(`/projects/${encodeURIComponent(projectId)}`),
  update: (projectId: string, body: ProjectUpdateBody) =>
    request<ProjectRecord>(`/projects/${encodeURIComponent(projectId)}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  members: (projectId: string) =>
    request<ProjectMember[]>(
      `/projects/${encodeURIComponent(projectId)}/members`,
    ),
};
