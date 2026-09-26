import { request, requestBlob, requestUpload } from "../request";
import type { UploadProgressHandler } from "../request";

/**
 * Project asset API (PS-06A / 023A) — private project asset library.
 *
 * Contract (`PROJECT_ASSET_CONTRACT.md` + `PROJECT_ASSET_VERSIONS_CONTRACT.md`
 * + `PROJECT_ASSET_TRASH_042_DESIGN.md`):
 *   GET  /projects/{project_id}/assets?parent_id=&q=&kind=&limit=&offset=
 *   GET  /projects/{project_id}/assets/usage
 *   POST /projects/{project_id}/assets/folders  { parent_id?, name }
 *   POST /projects/{project_id}/assets/upload   multipart file + parent_id?
 *   GET  /projects/{project_id}/assets/{node_id}/download
 *   GET  /projects/{project_id}/assets/{node_id}/versions?limit=&offset=
 *   POST /projects/{project_id}/assets/{node_id}/versions   multipart file
 *   POST /projects/{project_id}/assets/{node_id}/versions/{version_id}/restore
 *   GET  /projects/{project_id}/assets/{node_id}/versions/{version_id}/download
 *   DELETE /projects/{project_id}/assets/{node_id}                       → 204
 *   GET  /projects/{project_id}/assets/trash?limit=&offset=
 *   POST /projects/{project_id}/assets/trash/{node_id}/restore
 *
 * `request()` prepends `/api`. Only safe node/version metadata crosses the
 * wire — never `object_key`, object paths or credentials. `parent_id` omitted
 * means the hidden per-project root. Non-members, unknown nodes/versions and
 * revoked members receive 404; archived projects allow reads but reject
 * writes with 403.
 *
 * The 023A upload response body is the safe node item plus a first-version
 * summary at `version` (`{version_id, size_bytes, sha256, media_type}`),
 * confirmed with the backend owner; `object_key` is never returned. Version
 * upload/restore reuse that shape; `version_id` — never the display-only
 * ordinal — selects and mutates a version.
 *
 * 042 recoverable trash: `DELETE …/assets/{node_id}` returns 204 and only
 * means "atomically moved to the trash" — never a permanent delete. The trash
 * list pages independent trash roots with safe metadata (no `object_key`, no
 * private paths); trashed items cannot be downloaded or previewed. Restore
 * returns the ordinary safe node; a same-name conflict is
 * `PROJECT_ASSET_NAME_CONFLICT` (409) and a trashed original parent is
 * `PROJECT_ASSET_PARENT_IN_TRASH` (409). Usage `file_count`/`total_bytes`
 * count **visible current versions only**; the same response carries
 * `trash_file_count`/`trash_total_bytes` for the trash's current versions.
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

/**
 * Usage totals. Both pairs count **current versions only** (042): the visible
 * files/folders total and, separately, what the trash still keeps. Historical
 * versions and any real physical quota are never part of these numbers.
 */
export interface ProjectAssetUsage {
  file_count: number;
  total_bytes: number;
  /** 042 trash totals; optional so a pre-042 server response still parses. */
  trash_file_count?: number;
  trash_total_bytes?: number;
}

/**
 * One trash root (042). Safe display metadata only — bytes, downloads and
 * previews of trashed content are never exposed. `deleted_by_name` is the
 * server-mapped member display name; null when unknown or deactivated.
 */
export interface ProjectAssetTrashItem {
  node_id: string;
  parent_node_id: string | null;
  kind: ProjectAssetKind;
  name: string;
  /** Human-readable original location captured at deletion time. */
  original_path: string;
  /** Unix epoch seconds, matching the backend's now_ts() convention. */
  deleted_at: number;
  deleted_by_name: string | null;
  /** Server-authoritative: only render an actionable restore when true. */
  can_restore: boolean;
}

export interface ProjectAssetTrashListResponse {
  items: ProjectAssetTrashItem[];
  total: number;
  limit: number;
  offset: number;
  has_more: boolean;
}

export interface ProjectAssetTrashListParams {
  limit?: number;
  offset?: number;
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

/**
 * One historical version of a file node (PS-06B-1). `uploaded_by` is an
 * opaque nullable user id — render a placeholder when it is null.
 */
export interface ProjectAssetVersion {
  version_id: string;
  size_bytes: number;
  sha256: string;
  media_type: string | null;
  uploaded_by: number | null;
  /** Unix epoch seconds, matching the backend's now_ts() convention. */
  created_at: number;
  is_current: boolean;
}

export interface ProjectAssetVersionListResponse {
  items: ProjectAssetVersion[];
  total: number;
  limit: number;
  offset: number;
  has_more: boolean;
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

export interface ProjectAssetVersionListParams {
  limit?: number;
  offset?: number;
}

export interface ProjectAssetVersionUploadParams {
  file: File;
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

/** Version list default page size (contract: limit 1–100, offset ≥ 0). */
export const PROJECT_ASSET_VERSIONS_PAGE_SIZE = 50;

const assetsBase = (projectId: string) =>
  `/projects/${encodeURIComponent(projectId)}/assets`;

const nodeBase = (projectId: string, nodeId: string) =>
  `${assetsBase(projectId)}/${encodeURIComponent(nodeId)}`;

const versionsBase = (projectId: string, nodeId: string) =>
  `${nodeBase(projectId, nodeId)}/versions`;

const versionBase = (projectId: string, nodeId: string, versionId: string) =>
  `${versionsBase(projectId, nodeId)}/${encodeURIComponent(versionId)}`;

const trashBase = (projectId: string) => `${assetsBase(projectId)}/trash`;

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
  listVersions: (
    projectId: string,
    nodeId: string,
    params: ProjectAssetVersionListParams = {},
  ) => {
    const query = new URLSearchParams();
    query.set(
      "limit",
      String(params.limit ?? PROJECT_ASSET_VERSIONS_PAGE_SIZE),
    );
    query.set("offset", String(params.offset ?? 0));
    return request<ProjectAssetVersionListResponse>(
      `${versionsBase(projectId, nodeId)}?${query.toString()}`,
    );
  },
  uploadVersion: (
    projectId: string,
    nodeId: string,
    params: ProjectAssetVersionUploadParams,
    options: RequestInit = {},
    onProgress?: UploadProgressHandler,
  ) => {
    const body = new FormData();
    // The multipart filename never renames the node; the node id in the path
    // selects the target and the server keeps its existing name.
    body.append("file", params.file);
    return requestUpload<ProjectAssetUploadResponse>(
      versionsBase(projectId, nodeId),
      body,
      options,
      onProgress,
    );
  },
  restoreVersion: (projectId: string, nodeId: string, versionId: string) =>
    request<ProjectAssetUploadResponse>(
      `${versionBase(projectId, nodeId, versionId)}/restore`,
      { method: "POST" },
    ),
  /**
   * Historical version bytes via the member-gated download route. The
   * optional `RequestInit` (e.g. an `AbortSignal` for the 027 PDF preview)
   * and `(loaded, total)` progress callback pass straight through to the
   * authenticated `requestBlob`; the three-argument legacy call is unchanged.
   */
  downloadVersion: (
    projectId: string,
    nodeId: string,
    versionId: string,
    options: RequestInit = {},
    onProgress?: (loaded: number, total: number) => void,
  ) =>
    requestBlob(
      `${versionBase(projectId, nodeId, versionId)}/download`,
      options,
      onProgress,
    ),
  /**
   * 042: atomically move a file/folder (with its visible subtree) to the
   * trash. 204 means "in the trash, recoverable" — never permanently deleted.
   */
  deleteToTrash: (projectId: string, nodeId: string) =>
    request<void>(nodeBase(projectId, nodeId), { method: "DELETE" }),
  /** 042: page the independent trash roots (safe metadata only). */
  listTrash: (projectId: string, params: ProjectAssetTrashListParams = {}) => {
    const query = new URLSearchParams();
    query.set("limit", String(params.limit ?? PROJECT_ASSETS_PAGE_SIZE));
    query.set("offset", String(params.offset ?? 0));
    return request<ProjectAssetTrashListResponse>(
      `${trashBase(projectId)}?${query.toString()}`,
    );
  },
  /**
   * 042: restore one trash root to its original parent. Returns the ordinary
   * safe node; 409 means a same-name conflict or a parent still in the trash,
   * and the whole subtree stays trashed.
   */
  restoreTrashed: (projectId: string, nodeId: string) =>
    request<ProjectAssetNode>(
      `${trashBase(projectId)}/${encodeURIComponent(nodeId)}/restore`,
      { method: "POST" },
    ),
};
