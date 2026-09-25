import { request } from "../request";

/**
 * Project-level expert list API (029 first slice).
 *
 * Contract (`docs/xiongbao/PROJECT_EXPERT_SELECTION_029_CONTRACT.md`):
 *   GET /projects/{id}/experts  → ProjectExpertsResponse
 *   PUT /projects/{id}/experts  → ProjectExpertsResponse
 *
 * GET is member-visible and always re-reads the current database state: an
 * expert that is no longer shared/enabled comes back as a
 * `status: "unavailable"` placeholder with `name` and `description` set to
 * `null`, so the dashboard must never render stale private details for it.
 * A hard-deleted expert simply disappears from `items`.
 *
 * PUT is owner/admin only. `expected_revision` is the revision the caller
 * read; a stale value returns 409 + `PROJECT_EXPERTS_CHANGED` without
 * changing the list. `agent_ids` is the exact ordered selection (max 20,
 * no duplicates) and the server re-validates every agent (single expert,
 * enabled, shared) inside one write transaction. The same ordered list
 * keeps the revision unchanged; a real change bumps it by one. The response
 * is always the server's canonical list.
 *
 * The list is a candidate gate for new project tasks only — it is not an
 * Agent authorization, and it never carries private Agent resources,
 * credentials, prompts, workspaces or history.
 */

export type ProjectExpertStatus = "available" | "unavailable";

/** One project expert row; unavailable rows are redacted placeholders. */
export interface ProjectExpert {
  agent_id: string;
  /** `null` when `status === "unavailable"` — never render a fallback name. */
  name: string | null;
  /** `null` when `status === "unavailable"` — never render a fallback text. */
  description: string | null;
  status: ProjectExpertStatus;
}

export interface ProjectExpertsResponse {
  /** Config revision written by the last successful PUT; 0 for legacy projects. */
  revision: number;
  /** Stable `sort_order` from the server; not necessarily alphabetical. */
  items: ProjectExpert[];
}

/** Server-side cap on the ordered project expert list. */
export const PROJECT_EXPERTS_LIMIT = 20;

const expertsPath = (projectId: string) =>
  `/projects/${encodeURIComponent(projectId)}/experts`;

export const projectExpertsApi = {
  list: (projectId: string) =>
    request<ProjectExpertsResponse>(expertsPath(projectId)),
  set: (projectId: string, expectedRevision: number, agentIds: string[]) =>
    request<ProjectExpertsResponse>(expertsPath(projectId), {
      method: "PUT",
      body: JSON.stringify({
        expected_revision: expectedRevision,
        agent_ids: agentIds,
      }),
    }),
};
