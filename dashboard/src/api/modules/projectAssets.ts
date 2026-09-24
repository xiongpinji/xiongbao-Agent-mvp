import { request, requestBlob, requestUpload } from "../request";
import type { UploadProgressHandler } from "../request";

/**
 * Project asset API (PS-06A / 023A) — private project asset library.
 *
 * Contract (`PROJECT_ASSET_CONTRACT.md`):
 *   GET  /projects/{project_id}/assets?parent_id=&q=&kind=&limit=&offset=
 *   GET  /projects/{project_id}/assets/usage
 *   POST /projects/{project_id}/assets/folders  { parent_id?, name }
 *   POST /projects/{project_id}/assets/upload   multipart file + parent_id?
 *   GET  /projects/{project_id}/assets/{node_id}/download
 *
 * `request()` prepends `/api`. Only safe node metadata crosses the wire —
 * never `object_key`, object paths or credentials. `parent_id` omitted means
 * the hidden per-project root. Non-members, unknown nodes and revoked members
 * receive 404; archived projects allow reads but reject writes with 403.
 *
 * The 023A upload response body is the safe node item plus a first-version
 * summary at `version` (`{version_id, size_bytes, sha256, media_type}`),
 * confirmed with the backend owner; `object_key` is never returned.
 */

export type ProjectAssetKind = "file" | "folder";

/** Safe node DTO; folders carry null `size_bytes` / `media_type`. */
export interface ProjectAssetNode {
  node_id: string;
  parent_node_id: string | null;
  kind: ProjectAssetKind;
  name: string;
  size_bytes: number | null;
  media_type: string | null;
  /** Unix epoch seconds, matching the backend's now_ts() convention. */
  created_at: number;
  updated_at: number;
}

export interface ProjectAssetListResponse {
  items: ProjectAssetNode[];
  total: number;
  limit: number;
  offset: number;
  has_more: boolean;
}

export interface ProjectAssetUsage {
  file_count: number;
  total_bytes: number;
}

export interface ProjectAssetVersionSummary {
  version_id: string;
  size_bytes: number;
  sha256: string;
  media_type: string | null;
}

export interface ProjectAssetUploadResponse extends ProjectAssetNode {
  version: ProjectAssetVersionSummary;
}

export interface ProjectAssetListParams {
  /** Omitted (or null) means the hidden project root. */
  parentId?: string | null;
  /** Blank values are omitted so a refresh always starts from page one. */
  q?: string;
  kind?: ProjectAssetKind;
  limit?: number;
  offset?: number;
}

export interface ProjectAssetFolderBody {
  /** Omitted (or null) creates the folder in the hidden project root. */
  parentId?: string | null;
  name: string;
}

export interface ProjectAssetUploadParams {
  parentId?: string | null;
  file: File;
}

/** Contract default page size; the server accepts limit 1–100. */
export const PROJECT_ASSETS_PAGE_SIZE = 50;

const assetsBase = (projectId: string) =>
  `/projects/${encodeURIComponent(projectId)}/assets`;

const nodeBase = (projectId: string, nodeId: string) =>
  `${assetsBase(projectId)}/${encodeURIComponent(nodeId)}`;

export const projectAssetsApi = {
  list: (projectId: string, params: ProjectAssetListParams = {}) => {
    const query = new URLSearchParams();
    if (params.parentId) query.set("parent_id", params.parentId);
    const q = params.q?.trim();
    if (q) query.set("q", q);
    if (params.kind) query.set("kind", params.kind);
    query.set("limit", String(params.limit ?? PROJECT_ASSETS_PAGE_SIZE));
    query.set("offset", String(params.offset ?? 0));
    return request<ProjectAssetListResponse>(
      `${assetsBase(projectId)}?${query.toString()}`,
    );
  },
  usage: (projectId: string) =>
    request<ProjectAssetUsage>(`${assetsBase(projectId)}/usage`),
  createFolder: (projectId: string, body: ProjectAssetFolderBody) =>
    request<ProjectAssetNode>(`${assetsBase(projectId)}/folders`, {
      method: "POST",
      body: JSON.stringify(
        body.parentId
          ? { parent_id: body.parentId, name: body.name }
          : { name: body.name },
      ),
    }),
  upload: (
    projectId: string,
    params: ProjectAssetUploadParams,
    options: RequestInit = {},
    onProgress?: UploadProgressHandler,
  ) => {
    const body = new FormData();
    body.append("file", params.file);
    if (params.parentId) body.append("parent_id", params.parentId);
    return requestUpload<ProjectAssetUploadResponse>(
      `${assetsBase(projectId)}/upload`,
      body,
      options,
      onProgress,
    );
  },
  download: (projectId: string, nodeId: string) =>
    requestBlob(`${nodeBase(projectId, nodeId)}/download`),
};
