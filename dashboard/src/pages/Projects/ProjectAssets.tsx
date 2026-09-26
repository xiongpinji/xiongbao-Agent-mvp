/**
 * ProjectAssets — 项目详情“资产”页签 (PS-06A / 023A + 042 trash).
 *
 * Real private project asset library backed only by the 023A/042 API:
 * - hidden root by default; the folder navigation stack builds the breadcrumb
 *   (no URL deep links, a refresh returns to the root)
 * - name search, file/folder filter and `limit/offset` paging are all
 *   server-side; rows are deduped by `node_id` while paging
 * - usage (`file_count` / `total_bytes`) counts visible **current versions
 *   only**; 042 adds `trash_file_count` / `trash_total_bytes` shown separately
 *   as “回收站保留” — never a made-up quota, remaining space or disk-freed
 *   claim (moving to the trash does not free disk)
 * - files download through `requestBlob` into a one-shot `<a download>`
 *   anchor (the JWT header cannot ride a plain anchor), revoked afterwards
 * - request state is keyed by `projectId\0parentId\0query\0kind`, so switching
 *   project, folder or filter never flashes stale rows and late list/upload
 *   responses are dropped; the trash list carries its own project key and
 *   monotonic sequence with the same guarantees
 * - a 404 clears rows, usage **and** trash rows, closes the delete/version/
 *   trash modals and shows the no-access state; 409/413/403 surface
 *   recoverable messages; filenames and MIME types are rendered as inert text
 *   from server metadata
 * - a file row’s “版本管理” opens the wide PS-06B-1 version modal; folder rows
 *   never expose version actions
 * - 042 recoverable trash: file and folder rows offer “移入回收站” behind an
 *   accessible confirm modal (cancel mutates nothing, failure keeps the row);
 *   the “回收站” view lists trash roots with name/type/original path/deletion
 *   time+actor and a restore action only when the server says `can_restore`.
 *   Trash rows are inert — no open, download or preview. Restore 409s keep
 *   the row and explain either the same-name conflict or “先恢复父级”.
 *   There is deliberately no permanent-delete or empty-trash control.
 *
 * “Add to task” has no safe backend yet (PS-05B), so it stays visibly
 * unavailable.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  Alert,
  Breadcrumb,
  Button,
  Dropdown,
  Input,
  Modal,
  Segmented,
  Spin,
  Tooltip,
  Typography,
  Upload,
} from "antd";
import {
  ChevronRight,
  Download,
  FileText,
  Folder,
  FolderPlus,
  History,
  ListPlus,
  MoreHorizontal,
  RefreshCw,
  RotateCcw,
  Trash2,
  Upload as UploadIcon,
} from "lucide-react";
import { EmptyState } from "../../components/EmptyState";
import ProjectAssetVersions from "./ProjectAssetVersions";
import {
  PROJECT_ASSETS_PAGE_SIZE,
  projectAssetsApi,
  type ProjectAssetKind,
  type ProjectAssetNode,
  type ProjectAssetTrashItem,
  type ProjectAssetUsage,
} from "../../api/modules/projectAssets";
import { useServerTimezone } from "../../hooks/useServerTimezone";
import { formatServerDateTime } from "../../utils/formatMessageTime";
import {
  apiErrorMessage,
  isNotFoundApiError,
  parseApiError,
} from "../../utils/apiError";
import { message } from "../../utils/antdMessage";

const { Text } = Typography;

/** Server-side `<name>` cap from the frozen contract. */
const MAX_FOLDER_NAME_LENGTH = 120;

type KindFilter = ProjectAssetKind | "all";

interface Props {
  projectId: string;
}

interface FolderCrumb {
  node_id: string;
  name: string;
}

/** Project-scoped UI state; keyed so a project switch cannot leak it. */
interface AssetsUiState {
  key: string;
  stack: FolderCrumb[];
  searchInput: string;
  query: string;
  kind: KindFilter;
}

interface AssetsListState {
  /** `${projectId}\u0000${parentId}\u0000${query}\u0000${kind}` this page belongs to. */
  key: string;
  items: ProjectAssetNode[];
  total: number;
  hasMore: boolean;
  nextOffset: number;
  loading: boolean;
  loadingMore: boolean;
  error: unknown;
  appendError: unknown;
}

interface UsageState {
  key: string;
  value: ProjectAssetUsage | null;
}

/** 042 trash list state; keyed by project so a switch cannot leak rows. */
interface TrashState {
  key: string;
  items: ProjectAssetTrashItem[];
  total: number;
  hasMore: boolean;
  nextOffset: number;
  loading: boolean;
  loadingMore: boolean;
  error: unknown;
  appendError: unknown;
}

const secondaryStyle: React.CSSProperties = {
  fontSize: 12,
  color: "var(--fn-text-tertiary, rgba(0,0,0,0.45))",
};

const rowStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 8,
  width: "100%",
  minWidth: 0,
  padding: "8px 10px",
  background: "var(--fn-bg-elevated, #fff)",
  border: "1px solid var(--fn-border-color-split, rgba(0,0,0,0.06))",
  borderRadius: 8,
  textAlign: "left",
};

function freshUiState(projectId: string): AssetsUiState {
  return { key: projectId, stack: [], searchInput: "", query: "", kind: "all" };
}

function freshListState(key: string): AssetsListState {
  return {
    key,
    items: [],
    total: 0,
    hasMore: false,
    nextOffset: 0,
    loading: true,
    loadingMore: false,
    error: null,
    appendError: null,
  };
}

function mergeUniqueNodes(
  previous: ProjectAssetNode[],
  incoming: ProjectAssetNode[],
): ProjectAssetNode[] {
  const seen = new Set(previous.map((node) => node.node_id));
  const merged = [...previous];
  for (const node of incoming) {
    if (seen.has(node.node_id)) continue;
    seen.add(node.node_id);
    merged.push(node);
  }
  return merged;
}

function freshTrashState(key: string): TrashState {
  return {
    key,
    items: [],
    total: 0,
    hasMore: false,
    nextOffset: 0,
    loading: true,
    loadingMore: false,
    error: null,
    appendError: null,
  };
}

function mergeUniqueTrash(
  previous: ProjectAssetTrashItem[],
  incoming: ProjectAssetTrashItem[],
): ProjectAssetTrashItem[] {
  const seen = new Set(previous.map((item) => item.node_id));
  const merged = [...previous];
  for (const item of incoming) {
    if (seen.has(item.node_id)) continue;
    seen.add(item.node_id);
    merged.push(item);
  }
  return merged;
}

/** Readable size without inventing precision; null (folders) shows a dash. */
function formatBytes(bytes: number | null | undefined): string {
  if (bytes == null || !Number.isFinite(bytes) || bytes < 0) return "—";
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let value = bytes / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value.toFixed(value >= 10 ? 0 : 1)} ${units[unit]}`;
}

function httpStatus(error: unknown): number | null {
  const raw = error instanceof Error ? error.message : String(error ?? "");
  const match = raw.match(/\b(4\d{2}|5\d{2})\b/);
  return match ? Number(match[1]) : null;
}

export default function ProjectAssets({ projectId }: Props) {
  const { t } = useTranslation();
  const timezone = useServerTimezone();

  const [uiState, setUiState] = useState<AssetsUiState>(() =>
    freshUiState(projectId),
  );
  const [listState, setListState] = useState<AssetsListState>(() =>
    freshListState(`${projectId}\u0000\u0000\u0000all`),
  );
  const [usageState, setUsageState] = useState<UsageState | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [usageTick, setUsageTick] = useState(0);

  const [folderOpen, setFolderOpen] = useState(false);
  const [folderName, setFolderName] = useState("");
  const [folderError, setFolderError] = useState<string | null>(null);
  const [folderSubmitting, setFolderSubmitting] = useState(false);

  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [downloadingId, setDownloadingId] = useState<string | null>(null);
  const [versionNode, setVersionNode] = useState<{
    projectId: string;
    node: ProjectAssetNode;
  } | null>(null);

  /** 042: node pending the “移入回收站” confirmation; cancel mutates nothing. */
  const [deleteTarget, setDeleteTarget] = useState<{
    projectId: string;
    node: ProjectAssetNode;
  } | null>(null);
  const [deleteSubmitting, setDeleteSubmitting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  /** 042: trash view state; `key` is the projectId the rows belong to. */
  const [trashOpen, setTrashOpen] = useState(false);
  const [trashState, setTrashState] = useState<TrashState | null>(null);
  const [trashTick, setTrashTick] = useState(0);
  const [trashActionError, setTrashActionError] = useState<string | null>(null);
  const [restoringId, setRestoringId] = useState<string | null>(null);

  /** Monotonic guards: late responses never overwrite fresher state. */
  const fetchSeq = useRef(0);
  const usageSeq = useRef(0);
  const mutationSeq = useRef(0);
  const downloadSeq = useRef(0);
  const deleteSeq = useRef(0);
  const trashSeq = useRef(0);
  const restoreSeq = useRef(0);
  const uploadAbort = useRef<AbortController | null>(null);
  const currentProjectId = useRef(projectId);
  currentProjectId.current = projectId;

  /**
   * 042: drop every trash/delete trace and invalidate their in-flight calls.
   * Used on project switch and on any 404 revocation so a late trash or
   * delete response can never repopulate the new project or a cleared page.
   */
  const clearTrashAndDeleteState = useCallback(() => {
    deleteSeq.current += 1;
    trashSeq.current += 1;
    restoreSeq.current += 1;
    setDeleteTarget(null);
    setDeleteSubmitting(false);
    setDeleteError(null);
    setTrashOpen(false);
    setTrashState(null);
    setTrashActionError(null);
    setRestoringId(null);
  }, []);

  // Reset everything that belongs to the previous project before any fetch.
  useEffect(() => {
    setUiState((previous) =>
      previous.key === projectId ? previous : freshUiState(projectId),
    );
    setFolderOpen(false);
    setFolderName("");
    setFolderError(null);
    setFolderSubmitting(false);
    setUploading(false);
    setUploadError(null);
    setActionError(null);
    setDownloadingId(null);
    setVersionNode(null);
    clearTrashAndDeleteState();
    fetchSeq.current += 1;
    usageSeq.current += 1;
    mutationSeq.current += 1;
    downloadSeq.current += 1;
    uploadAbort.current?.abort();
    uploadAbort.current = null;
  }, [projectId, clearTrashAndDeleteState]);

  const ui = uiState.key === projectId ? uiState : freshUiState(projectId);
  const parentId =
    ui.stack.length > 0 ? ui.stack[ui.stack.length - 1].node_id : null;
  const kindParam: ProjectAssetKind | undefined =
    ui.kind === "all" ? undefined : ui.kind;
  const stateKey = `${projectId}\u0000${parentId ?? ""}\u0000${ui.query}\u0000${
    ui.kind
  }`;
  /** Latest list key for callbacks/effects that must not re-run on filters. */
  const stateKeyRef = useRef(stateKey);
  stateKeyRef.current = stateKey;

  const updateUi = useCallback(
    (patch: Partial<Omit<AssetsUiState, "key">>) => {
      setUiState((previous) => ({
        ...(previous.key === projectId ? previous : freshUiState(projectId)),
        ...patch,
      }));
    },
    [projectId],
  );

  const reload = useCallback(() => {
    setReloadKey((key) => key + 1);
    setUsageTick((tick) => tick + 1);
  }, []);

  useEffect(() => {
    const seq = ++fetchSeq.current;
    const key = stateKey;
    setListState(freshListState(key));
    projectAssetsApi
      .list(projectId, {
        parentId,
        q: ui.query,
        kind: kindParam,
        limit: PROJECT_ASSETS_PAGE_SIZE,
        offset: 0,
      })
      .then((data) => {
        if (seq !== fetchSeq.current) return;
        const items = Array.isArray(data?.items) ? data.items : [];
        setListState({
          key,
          items,
          total: data?.total ?? items.length,
          hasMore: Boolean(data?.has_more),
          nextOffset: items.length,
          loading: false,
          loadingMore: false,
          error: null,
          appendError: null,
        });
      })
      .catch((err: unknown) => {
        if (seq !== fetchSeq.current) return;
        if (isNotFoundApiError(err)) {
          // Revocation: drop the usage and trash in flight and on screen.
          usageSeq.current += 1;
          setUsageState(null);
          clearTrashAndDeleteState();
        }
        setListState({
          key,
          items: [],
          total: 0,
          hasMore: false,
          nextOffset: 0,
          loading: false,
          loadingMore: false,
          error: err,
          appendError: null,
        });
      });
  }, [
    projectId,
    parentId,
    kindParam,
    ui.query,
    reloadKey,
    stateKey,
    clearTrashAndDeleteState,
  ]);

  useEffect(() => {
    const seq = ++usageSeq.current;
    projectAssetsApi
      .usage(projectId)
      .then((data) => {
        if (seq !== usageSeq.current) return;
        setUsageState({
          key: projectId,
          value: {
            file_count: data?.file_count ?? 0,
            total_bytes: data?.total_bytes ?? 0,
            trash_file_count: data?.trash_file_count,
            trash_total_bytes: data?.trash_total_bytes,
          },
        });
      })
      .catch(() => {
        if (seq !== usageSeq.current) return;
        setUsageState({ key: projectId, value: null });
      });
  }, [projectId, usageTick]);

  const applyNotFound = useCallback(
    (key: string, err: unknown) => {
      fetchSeq.current += 1;
      usageSeq.current += 1;
      uploadAbort.current?.abort();
      uploadAbort.current = null;
      setUsageState(null);
      setUploading(false);
      setUploadError(null);
      setFolderOpen(false);
      setFolderSubmitting(false);
      setDownloadingId(null);
      setVersionNode(null);
      // 042: a revocation also clears the trash rows and closes the
      // delete/trash modals; their late responses stay invalidated.
      clearTrashAndDeleteState();
      setListState((previous) =>
        previous.key !== key
          ? previous
          : {
              ...previous,
              items: [],
              total: 0,
              hasMore: false,
              nextOffset: 0,
              loading: false,
              loadingMore: false,
              error: err,
              appendError: null,
            },
      );
    },
    [clearTrashAndDeleteState],
  );

  // 042: fetch the trash roots whenever the trash view is (re)opened or
  // refreshed. Responses are tied to this projectId and a monotonic
  // sequence, so a project switch or revocation drops late payloads.
  useEffect(() => {
    if (!trashOpen) return;
    const seq = ++trashSeq.current;
    const key = projectId;
    setTrashState(freshTrashState(key));
    projectAssetsApi
      .listTrash(projectId, {
        limit: PROJECT_ASSETS_PAGE_SIZE,
        offset: 0,
      })
      .then((data) => {
        if (seq !== trashSeq.current || key !== currentProjectId.current)
          return;
        const items = Array.isArray(data?.items) ? data.items : [];
        setTrashState({
          key,
          items,
          total: data?.total ?? items.length,
          hasMore: Boolean(data?.has_more),
          nextOffset: items.length,
          loading: false,
          loadingMore: false,
          error: null,
          appendError: null,
        });
      })
      .catch((err: unknown) => {
        if (seq !== trashSeq.current || key !== currentProjectId.current)
          return;
        if (isNotFoundApiError(err)) {
          // Revocation: clear the directory, usage and trash at once.
          applyNotFound(stateKeyRef.current, err);
          return;
        }
        setTrashState({ ...freshTrashState(key), loading: false, error: err });
      });
  }, [trashOpen, projectId, trashTick, applyNotFound]);

  const openVersionModal = useCallback(
    (node: ProjectAssetNode) => setVersionNode({ projectId, node }),
    [projectId],
  );
  const closeVersionModal = useCallback(() => setVersionNode(null), []);
  const handleVersionChanged = useCallback(
    (originProjectId: string) => {
      if (originProjectId === currentProjectId.current) reload();
    },
    [reload],
  );
  const handleVersionAccessLost = useCallback(
    (err: unknown, originProjectId: string) => {
      if (originProjectId !== currentProjectId.current) return;
      applyNotFound(stateKey, err);
      // A 404 may be one missing historical object. Recheck membership via
      // the normal asset list before declaring the whole project inaccessible.
      setListState(freshListState(stateKey));
      reload();
    },
    [applyNotFound, reload, stateKey],
  );

  const current = listState.key === stateKey ? listState : null;
  const items = current?.items ?? [];
  const loading = current?.loading ?? true;
  const loadingMore = current?.loadingMore ?? false;
  const total = current?.total ?? 0;
  const hasMore = current?.hasMore ?? false;
  const error = current?.error ?? null;
  const appendError = current?.appendError ?? null;
  const notFound = error != null && isNotFoundApiError(error);

  const usageValue =
    usageState != null && usageState.key === projectId
      ? usageState.value
      : null;

  const loadMore = async () => {
    if (current == null || !current.hasMore || loading || loadingMore) return;
    const seq = ++fetchSeq.current;
    const key = stateKey;
    const offset = current.nextOffset;
    setListState((previous) =>
      previous.key === key
        ? { ...previous, loadingMore: true, appendError: null }
        : previous,
    );
    try {
      const data = await projectAssetsApi.list(projectId, {
        parentId,
        q: ui.query,
        kind: kindParam,
        limit: PROJECT_ASSETS_PAGE_SIZE,
        offset,
      });
      if (seq !== fetchSeq.current) return;
      const items = Array.isArray(data?.items) ? data.items : [];
      setListState((previous) =>
        previous.key !== key
          ? previous
          : {
              ...previous,
              items: mergeUniqueNodes(previous.items, items),
              total: data?.total ?? previous.total,
              hasMore: Boolean(data?.has_more),
              nextOffset: offset + items.length,
              appendError: null,
              loadingMore: false,
            },
      );
    } catch (err: unknown) {
      if (seq !== fetchSeq.current) return;
      if (isNotFoundApiError(err)) {
        applyNotFound(key, err);
        return;
      }
      setListState((previous) =>
        previous.key !== key
          ? previous
          : { ...previous, appendError: err, loadingMore: false },
      );
    }
  };

  const loadMoreTrash = async () => {
    const trash = trashState;
    if (
      trash == null ||
      trash.key !== projectId ||
      !trash.hasMore ||
      trash.loading ||
      trash.loadingMore
    )
      return;
    const seq = ++trashSeq.current;
    const key = projectId;
    const offset = trash.nextOffset;
    setTrashState((previous) =>
      previous != null && previous.key === key
        ? { ...previous, loadingMore: true, appendError: null }
        : previous,
    );
    try {
      const data = await projectAssetsApi.listTrash(projectId, {
        limit: PROJECT_ASSETS_PAGE_SIZE,
        offset,
      });
      if (seq !== trashSeq.current || key !== currentProjectId.current) return;
      const items = Array.isArray(data?.items) ? data.items : [];
      setTrashState((previous) =>
        previous == null || previous.key !== key
          ? previous
          : {
              ...previous,
              items: mergeUniqueTrash(previous.items, items),
              total: data?.total ?? previous.total,
              hasMore: Boolean(data?.has_more),
              nextOffset: offset + items.length,
              loadingMore: false,
              appendError: null,
            },
      );
    } catch (err: unknown) {
      if (seq !== trashSeq.current || key !== currentProjectId.current) return;
      if (isNotFoundApiError(err)) {
        applyNotFound(stateKeyRef.current, err);
        return;
      }
      setTrashState((previous) =>
        previous == null || previous.key !== key
          ? previous
          : { ...previous, appendError: err, loadingMore: false },
      );
    }
  };

  const openTrashModal = () => {
    setTrashActionError(null);
    setTrashOpen(true);
  };

  const closeTrashModal = () => {
    // Invalidate an in-flight trash page so it cannot paint a closed view.
    trashSeq.current += 1;
    setTrashOpen(false);
    setTrashActionError(null);
    setRestoringId(null);
  };

  /** 042 trash mutation errors: the two 409 codes get dedicated guidance. */
  const trashMutationErrorText = (err: unknown, fallback: string): string => {
    const parsed = parseApiError(err);
    if (parsed?.code === "PROJECT_ASSET_NAME_CONFLICT") {
      return t(
        "projects.assets.trashRestoreNameConflict",
        "原位置已有同名文件或文件夹；当前暂不支持改名或移动，请先处理同名项，此项保留在回收站。",
      );
    }
    if (parsed?.code === "PROJECT_ASSET_PARENT_IN_TRASH") {
      return t(
        "projects.assets.trashRestoreParentInTrash",
        "原文件夹仍在回收站：请先恢复父级，再恢复此项。",
      );
    }
    if (parsed?.code === "FORBIDDEN" || httpStatus(err) === 403) {
      return t(
        "projects.assets.trashForbidden",
        "项目已归档，或你没有管理该资产的权限。",
      );
    }
    return apiErrorMessage(err, fallback, t);
  };

  const openDeleteModal = (node: ProjectAssetNode) => {
    setDeleteError(null);
    setDeleteTarget({ projectId, node });
  };

  const closeDeleteModal = () => {
    // Cancel makes zero mutation; block closing mid-request only.
    if (deleteSubmitting) return;
    setDeleteTarget(null);
    setDeleteError(null);
  };

  const confirmDelete = async () => {
    const target = deleteTarget;
    if (target == null || target.projectId !== projectId) return;
    const seq = ++deleteSeq.current;
    const submittedProjectId = projectId;
    setDeleteSubmitting(true);
    setDeleteError(null);
    try {
      await projectAssetsApi.deleteToTrash(projectId, target.node.node_id);
      if (
        seq !== deleteSeq.current ||
        submittedProjectId !== currentProjectId.current
      )
        return;
      setDeleteTarget(null);
      // The trashed node must not keep an open version modal alive.
      setVersionNode((previous) =>
        previous != null &&
        previous.projectId === submittedProjectId &&
        previous.node.node_id === target.node.node_id
          ? null
          : previous,
      );
      void message.success(
        t("projects.assets.deleteMovedToTrash", "已移入回收站"),
      );
      // Refresh the directory, usage and the trash view state.
      setTrashTick((tick) => tick + 1);
      reload();
    } catch (err: unknown) {
      if (
        seq !== deleteSeq.current ||
        submittedProjectId !== currentProjectId.current
      )
        return;
      if (isNotFoundApiError(err)) {
        applyNotFound(stateKey, err);
        return;
      }
      // Failure keeps the row and the dialog: retry or cancel stay possible.
      setDeleteError(
        trashMutationErrorText(
          err,
          t("projects.assets.deleteFailed", "移入回收站失败"),
        ),
      );
    } finally {
      if (
        seq === deleteSeq.current &&
        submittedProjectId === currentProjectId.current
      )
        setDeleteSubmitting(false);
    }
  };

  const handleRestore = async (item: ProjectAssetTrashItem) => {
    const seq = ++restoreSeq.current;
    const submittedProjectId = projectId;
    setRestoringId(item.node_id);
    setTrashActionError(null);
    try {
      await projectAssetsApi.restoreTrashed(projectId, item.node_id);
      if (
        seq !== restoreSeq.current ||
        submittedProjectId !== currentProjectId.current
      )
        return;
      void message.success(
        t("projects.assets.trashRestored", "已恢复到原位置"),
      );
      // Restore refreshes the trash list, the directory and both usages.
      setTrashTick((tick) => tick + 1);
      reload();
    } catch (err: unknown) {
      if (
        seq !== restoreSeq.current ||
        submittedProjectId !== currentProjectId.current
      )
        return;
      if (isNotFoundApiError(err)) {
        applyNotFound(stateKey, err);
        return;
      }
      // A 409 conflict keeps the trash row exactly where it is.
      setTrashActionError(
        trashMutationErrorText(
          err,
          t("projects.assets.trashRestoreFailed", "恢复失败"),
        ),
      );
    } finally {
      if (
        seq === restoreSeq.current &&
        submittedProjectId === currentProjectId.current
      )
        setRestoringId(null);
    }
  };

  /** 409 / 413 / 403 must stay actionable; the server stays authoritative. */
  const mutationErrorText = (err: unknown, fallback: string): string => {
    const parsed = parseApiError(err);
    if (parsed?.code === "FORBIDDEN" || httpStatus(err) === 403) {
      return t(
        "projects.assets.archived",
        "项目已归档：资产当前只读，无法新建文件夹或上传。",
      );
    }
    return apiErrorMessage(err, fallback, t);
  };

  const openFolderModal = () => {
    setFolderName("");
    setFolderError(null);
    setFolderOpen(true);
  };

  const closeFolderModal = () => {
    if (folderSubmitting) return;
    setFolderOpen(false);
    setFolderError(null);
  };

  const submitFolder = async () => {
    const name = folderName.trim();
    if (!name) {
      setFolderError(
        t("projects.assets.folderNameRequired", "请输入文件夹名称"),
      );
      return;
    }
    const seq = ++mutationSeq.current;
    const submittedProjectId = projectId;
    setFolderSubmitting(true);
    setFolderError(null);
    try {
      await projectAssetsApi.createFolder(projectId, { parentId, name });
      if (
        seq !== mutationSeq.current ||
        submittedProjectId !== currentProjectId.current
      )
        return;
      setFolderOpen(false);
      setFolderName("");
      void message.success(t("projects.assets.folderCreated", "文件夹已创建"));
      reload();
    } catch (err: unknown) {
      if (
        seq !== mutationSeq.current ||
        submittedProjectId !== currentProjectId.current
      )
        return;
      if (isNotFoundApiError(err)) {
        applyNotFound(stateKey, err);
        return;
      }
      setFolderError(
        mutationErrorText(
          err,
          t("projects.assets.folderCreateFailed", "创建文件夹失败"),
        ),
      );
    } finally {
      if (seq === mutationSeq.current) setFolderSubmitting(false);
    }
  };

  const handleUpload = async (file: File) => {
    const seq = ++mutationSeq.current;
    const submittedProjectId = projectId;
    const targetParentId = parentId;
    uploadAbort.current?.abort();
    const controller = new AbortController();
    uploadAbort.current = controller;
    setUploading(true);
    setUploadError(null);
    try {
      await projectAssetsApi.upload(
        projectId,
        { parentId: targetParentId, file },
        { signal: controller.signal },
      );
      if (
        seq !== mutationSeq.current ||
        submittedProjectId !== currentProjectId.current
      )
        return;
      void message.success(t("projects.assets.uploaded", "文件已上传"));
      reload();
    } catch (err: unknown) {
      if (controller.signal.aborted) return;
      if (
        seq !== mutationSeq.current ||
        submittedProjectId !== currentProjectId.current
      )
        return;
      if (isNotFoundApiError(err)) {
        applyNotFound(stateKey, err);
        return;
      }
      setUploadError(
        mutationErrorText(err, t("projects.assets.uploadFailed", "上传失败")),
      );
    } finally {
      if (seq === mutationSeq.current) setUploading(false);
      if (uploadAbort.current === controller) uploadAbort.current = null;
    }
  };

  const handleDownload = async (node: ProjectAssetNode) => {
    const seq = ++downloadSeq.current;
    const submittedProjectId = projectId;
    setDownloadingId(node.node_id);
    setActionError(null);
    try {
      const blob = await projectAssetsApi.download(projectId, node.node_id);
      if (
        seq !== downloadSeq.current ||
        submittedProjectId !== currentProjectId.current
      )
        return;
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = node.name;
      anchor.rel = "noopener";
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      // Let the browser start the download before releasing the object URL.
      window.setTimeout(() => URL.revokeObjectURL(url), 0);
    } catch (err: unknown) {
      if (
        seq !== downloadSeq.current ||
        submittedProjectId !== currentProjectId.current
      )
        return;
      if (isNotFoundApiError(err)) {
        applyNotFound(stateKey, err);
        return;
      }
      setActionError(
        apiErrorMessage(
          err,
          t("projects.assets.downloadFailed", "下载失败"),
          t,
        ),
      );
    } finally {
      if (seq === downloadSeq.current) setDownloadingId(null);
    }
  };

  const enterFolder = (node: ProjectAssetNode) => {
    updateUi({
      stack: [...ui.stack, { node_id: node.node_id, name: node.name }],
    });
  };

  const goToCrumb = (index: number) => {
    updateUi({
      stack: index < 0 ? [] : ui.stack.slice(0, index + 1),
    });
  };

  const filtersActive = ui.query.trim().length > 0 || ui.kind !== "all";

  const renderNode = (node: ProjectAssetNode) => {
    const updated = formatServerDateTime(node.updated_at, timezone);
    if (node.kind === "folder") {
      // 042: folder rows are a container so the trash dropdown is not nested
      // inside the open-folder button; opening stays the primary action.
      return (
        <div
          key={node.node_id}
          data-testid={`project-asset-${node.node_id}`}
          style={rowStyle}
        >
          <button
            type="button"
            aria-label={t(
              "projects.assets.openFolder",
              "进入文件夹：{{name}}",
              {
                name: node.name,
              },
            )}
            onClick={() => enterFolder(node)}
            style={{
              display: "flex",
              alignItems: "center",
              gap: 8,
              flex: 1,
              minWidth: 0,
              padding: 0,
              background: "transparent",
              border: "none",
              color: "inherit",
              font: "inherit",
              textAlign: "left",
              cursor: "pointer",
            }}
          >
            <Folder size={16} aria-hidden />
            <span style={{ flex: 1, minWidth: 0, wordBreak: "break-word" }}>
              {node.name}
            </span>
            <span style={secondaryStyle}>{updated}</span>
            <ChevronRight size={14} aria-hidden />
          </button>
          <Dropdown
            trigger={["click"]}
            placement="bottomRight"
            menu={{
              items: [
                {
                  key: "trash",
                  icon: <Trash2 size={14} />,
                  danger: true,
                  label: t("projects.assets.deleteToTrash", "移入回收站"),
                  onClick: () => openDeleteModal(node),
                },
              ],
            }}
          >
            <Button
              size="small"
              icon={<MoreHorizontal size={14} />}
              aria-label={t("projects.assets.moreActions", "更多操作")}
            />
          </Dropdown>
        </div>
      );
    }
    return (
      <div
        key={node.node_id}
        data-testid={`project-asset-${node.node_id}`}
        style={rowStyle}
      >
        <FileText size={16} aria-hidden />
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ wordBreak: "break-word" }}>{node.name}</div>
          <div style={secondaryStyle}>
            {formatBytes(node.size_bytes)} ·{" "}
            {node.media_type ??
              t("projects.assets.fileTypeUnknown", "未知类型")}{" "}
            · {updated}
          </div>
        </div>
        <Button
          size="small"
          icon={<Download size={14} />}
          loading={downloadingId === node.node_id}
          disabled={downloadingId != null && downloadingId !== node.node_id}
          onClick={() => void handleDownload(node)}
        >
          {t("projects.assets.download", "下载")}
        </Button>
        <Dropdown
          trigger={["click"]}
          placement="bottomRight"
          menu={{
            items: [
              {
                key: "download",
                icon: <Download size={14} />,
                label: t("projects.assets.download", "下载"),
                onClick: () => void handleDownload(node),
              },
              {
                key: "versions",
                icon: <History size={14} />,
                label: t("projects.assets.versionManage", "版本管理"),
                onClick: () => openVersionModal(node),
              },
              { type: "divider" as const },
              {
                key: "trash",
                icon: <Trash2 size={14} />,
                danger: true,
                label: t("projects.assets.deleteToTrash", "移入回收站"),
                onClick: () => openDeleteModal(node),
              },
            ],
          }}
        >
          <Button
            size="small"
            icon={<MoreHorizontal size={14} />}
            aria-label={t("projects.assets.moreActions", "更多操作")}
          />
        </Dropdown>
      </div>
    );
  };

  /**
   * 042 trash rows are inert: safe metadata only, never a download, preview
   * or version action. Restore renders actionable only when the server says
   * `can_restore`; otherwise a disabled button explains why.
   */
  const renderTrashItem = (item: ProjectAssetTrashItem) => {
    const deletedAt = formatServerDateTime(item.deleted_at, timezone);
    const kindLabel =
      item.kind === "folder"
        ? t("projects.assets.kindFolder", "文件夹")
        : t("projects.assets.kindFile", "文件");
    return (
      <div
        key={item.node_id}
        data-testid={`project-asset-trash-${item.node_id}`}
        style={rowStyle}
      >
        {item.kind === "folder" ? (
          <Folder size={16} aria-hidden />
        ) : (
          <FileText size={16} aria-hidden />
        )}
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ wordBreak: "break-word" }}>{item.name}</div>
          <div style={secondaryStyle}>
            {kindLabel} ·{" "}
            {t("projects.assets.trashOriginalPath", "原位置：{{path}}", {
              path: item.original_path || "—",
            })}{" "}
            ·{" "}
            {item.deleted_by_name
              ? t(
                  "projects.assets.trashDeletedMeta",
                  "删除于 {{time}} · 删除人：{{who}}",
                  { time: deletedAt, who: item.deleted_by_name },
                )
              : t(
                  "projects.assets.trashDeletedMetaUnknown",
                  "删除于 {{time}} · 删除人未知",
                  { time: deletedAt },
                )}
          </div>
        </div>
        {item.can_restore ? (
          <Button
            size="small"
            icon={<RotateCcw size={14} />}
            loading={restoringId === item.node_id}
            disabled={restoringId != null && restoringId !== item.node_id}
            onClick={() => void handleRestore(item)}
          >
            {t("projects.assets.trashRestore", "恢复")}
          </Button>
        ) : (
          <Tooltip
            title={t(
              "projects.assets.trashCannotRestore",
              "你没有恢复此项目的权限；如需恢复请联系项目 owner 或管理员。",
            )}
          >
            <span>
              <Button size="small" icon={<RotateCcw size={14} />} disabled>
                {t("projects.assets.trashRestore", "恢复")}
              </Button>
            </span>
          </Tooltip>
        )}
      </div>
    );
  };

  let body: React.ReactNode;
  if (notFound) {
    body = (
      <EmptyState
        variant="error"
        title={t("projects.assets.notFound", "项目不存在或你无权访问")}
        description={t(
          "projects.assets.notFoundHint",
          "项目资产已清除；重新加载成功前不会显示旧内容。",
        )}
        actionLabel={t("common.retry", "重试")}
        onAction={reload}
      />
    );
  } else if (loading && items.length === 0) {
    body = (
      <div style={{ display: "flex", justifyContent: "center", padding: 48 }}>
        <Spin />
      </div>
    );
  } else if (error != null) {
    body = (
      <EmptyState
        variant="error"
        title={t("projects.assets.loadFailed", "加载资产失败")}
        description={apiErrorMessage(
          error,
          t("projects.assets.loadFailed", "加载资产失败"),
          t,
        )}
        actionLabel={t("common.retry", "重试")}
        onAction={reload}
      />
    );
  } else if (items.length === 0) {
    body = (
      <EmptyState
        variant="empty"
        title={
          filtersActive
            ? t("projects.assets.emptySearchTitle", "没有匹配的资产")
            : t("projects.assets.emptyTitle", "此文件夹暂无文件")
        }
        description={
          filtersActive
            ? t("projects.assets.emptySearchHint", "换个关键词或调整类型筛选。")
            : t("projects.assets.emptyHint", "新建文件夹或上传第一个文件。")
        }
      />
    );
  } else {
    body = (
      <div
        data-testid="project-assets-list"
        style={{ display: "flex", flexDirection: "column", gap: 8 }}
      >
        {items.map(renderNode)}
      </div>
    );
  }

  // 042: both counts are current versions only — visible files and what the
  // trash still keeps. Neither is a quota, remaining space or freed disk.
  const usageText = usageValue
    ? t(
        "projects.assets.usageVisible",
        "可见文件（仅当前版本）：{{count}} 个 · {{size}}",
        {
          count: usageValue.file_count,
          size: formatBytes(usageValue.total_bytes),
        },
      )
    : t("projects.assets.usageUnavailable", "容量暂不可用");
  const trashUsageText =
    usageValue != null && usageValue.trash_file_count != null
      ? t(
          "projects.assets.usageTrash",
          "回收站保留（仅当前版本）：{{count}} 个 · {{size}}",
          {
            count: usageValue.trash_file_count,
            size: formatBytes(usageValue.trash_total_bytes),
          },
        )
      : null;

  const trashCurrent =
    trashState != null && trashState.key === projectId ? trashState : null;

  let trashBody: React.ReactNode;
  if (
    trashCurrent == null ||
    (trashCurrent.loading && trashCurrent.items.length === 0)
  ) {
    trashBody = (
      <div
        data-testid="project-assets-trash-loading"
        style={{ display: "flex", justifyContent: "center", padding: 32 }}
      >
        <Spin />
      </div>
    );
  } else if (trashCurrent.error != null) {
    trashBody = (
      <EmptyState
        variant="error"
        title={t("projects.assets.trashLoadFailed", "加载回收站失败")}
        description={apiErrorMessage(
          trashCurrent.error,
          t("projects.assets.trashLoadFailed", "加载回收站失败"),
          t,
        )}
        actionLabel={t("common.retry", "重试")}
        onAction={() => setTrashTick((tick) => tick + 1)}
      />
    );
  } else if (trashCurrent.items.length === 0) {
    trashBody = (
      <EmptyState
        variant="empty"
        title={t("projects.assets.trashEmpty", "回收站为空")}
        description={t(
          "projects.assets.trashEmptyHint",
          "移入回收站的项目会保留在这里，可随时恢复。",
        )}
      />
    );
  } else {
    trashBody = (
      <>
        <Text type="secondary" style={secondaryStyle}>
          {t("projects.assets.totalCount", "共 {{total}} 项", {
            total: trashCurrent.total,
          })}
        </Text>
        {trashCurrent.items.map(renderTrashItem)}
        {trashCurrent.appendError != null && (
          <Alert
            type="error"
            showIcon
            message={apiErrorMessage(
              trashCurrent.appendError,
              t("projects.assets.trashLoadFailed", "加载回收站失败"),
              t,
            )}
            action={
              <Button size="small" onClick={() => void loadMoreTrash()}>
                {t("common.retry", "重试")}
              </Button>
            }
          />
        )}
        {trashCurrent.hasMore && (
          <div style={{ textAlign: "center" }}>
            <Button
              loading={trashCurrent.loadingMore}
              onClick={() => void loadMoreTrash()}
            >
              {t("projects.assets.loadMore", "加载更多")}
            </Button>
          </div>
        )}
      </>
    );
  }

  return (
    <div>
      {!notFound && (
        <>
          <div
            style={{
              display: "flex",
              gap: 8,
              flexWrap: "wrap",
              alignItems: "center",
              marginBottom: 8,
            }}
          >
            <Input.Search
              allowClear
              style={{ maxWidth: 220 }}
              value={ui.searchInput}
              placeholder={t("projects.assets.searchPlaceholder", "搜索名称")}
              aria-label={t("projects.assets.searchPlaceholder", "搜索名称")}
              onChange={(event) =>
                updateUi({ searchInput: event.target.value })
              }
              onSearch={(value) => updateUi({ query: value })}
            />
            <Segmented<KindFilter>
              value={ui.kind}
              aria-label={t("projects.assets.kindFilter", "类型筛选")}
              options={[
                {
                  label: t("projects.assets.kindAll", "全部"),
                  value: "all",
                },
                {
                  label: t("projects.assets.kindFile", "文件"),
                  value: "file",
                },
                {
                  label: t("projects.assets.kindFolder", "文件夹"),
                  value: "folder",
                },
              ]}
              onChange={(value) => updateUi({ kind: value })}
            />
            <Button
              icon={<FolderPlus size={14} />}
              disabled={uploading || folderSubmitting}
              onClick={openFolderModal}
            >
              {t("projects.assets.newFolder", "新建文件夹")}
            </Button>
            <Upload
              showUploadList={false}
              disabled={uploading || folderSubmitting}
              beforeUpload={(file) => {
                void handleUpload(file);
                return false;
              }}
            >
              <Button
                icon={<UploadIcon size={14} />}
                loading={uploading}
                disabled={folderSubmitting}
              >
                {t("projects.assets.upload", "上传")}
              </Button>
            </Upload>
            <Tooltip title={t("projects.assets.refresh", "刷新资产")}>
              <Button
                icon={<RefreshCw size={14} />}
                disabled={loading}
                aria-label={t("projects.assets.refresh", "刷新资产")}
                onClick={reload}
              />
            </Tooltip>
            <Button icon={<Trash2 size={14} />} onClick={openTrashModal}>
              {t("projects.assets.trashEntry", "回收站")}
            </Button>
            <Tooltip
              title={t(
                "projects.assets.addToTaskReason",
                "「添加到任务」需要任务协作者权限（PS-05B），后续批次开放。",
              )}
            >
              <span>
                <Button icon={<ListPlus size={14} />} disabled>
                  {t("projects.assets.addToTask", "添加到任务")}
                </Button>
              </span>
            </Tooltip>
          </div>

          <div
            style={{
              display: "flex",
              gap: 12,
              flexWrap: "wrap",
              alignItems: "center",
              marginBottom: 12,
            }}
          >
            <Text type="secondary" style={secondaryStyle}>
              {usageText}
            </Text>
            {trashUsageText != null && (
              <Text type="secondary" style={secondaryStyle}>
                {trashUsageText}
              </Text>
            )}
            {!loading && error == null && (
              <Text type="secondary" style={secondaryStyle}>
                {t("projects.assets.totalCount", "共 {{total}} 项", {
                  total,
                })}
              </Text>
            )}
            <Text type="secondary" style={secondaryStyle}>
              {t(
                "projects.assets.batchHint",
                "版本管理与回收站已开放；「添加到任务」将在后续批次提供。",
              )}
            </Text>
          </div>
        </>
      )}

      {!notFound && (
        <Breadcrumb
          style={{ marginBottom: 12 }}
          items={[
            {
              title: (
                <Button
                  type="link"
                  size="small"
                  style={{ padding: 0, height: "auto" }}
                  onClick={() => goToCrumb(-1)}
                >
                  {t("projects.assets.root", "项目文件")}
                </Button>
              ),
            },
            ...ui.stack.map((crumb, index) => ({
              title:
                index === ui.stack.length - 1 ? (
                  <span>{crumb.name}</span>
                ) : (
                  <Button
                    type="link"
                    size="small"
                    style={{ padding: 0, height: "auto" }}
                    onClick={() => goToCrumb(index)}
                  >
                    {crumb.name}
                  </Button>
                ),
            })),
          ]}
        />
      )}

      {uploadError != null && (
        <Alert
          type="error"
          showIcon
          closable
          style={{ marginBottom: 12 }}
          message={uploadError}
          onClose={() => setUploadError(null)}
        />
      )}
      {actionError != null && (
        <Alert
          type="error"
          showIcon
          closable
          style={{ marginBottom: 12 }}
          message={actionError}
          onClose={() => setActionError(null)}
        />
      )}
      {appendError != null && !notFound && (
        <Alert
          type="error"
          showIcon
          style={{ marginBottom: 12 }}
          message={apiErrorMessage(
            appendError,
            t("projects.assets.loadFailed", "加载资产失败"),
            t,
          )}
          action={
            <Button size="small" onClick={() => void loadMore()}>
              {t("common.retry", "重试")}
            </Button>
          }
        />
      )}

      {body}

      {hasMore && !notFound && error == null && items.length > 0 && (
        <div style={{ textAlign: "center", marginTop: 12 }}>
          <Button
            loading={loadingMore}
            disabled={loading}
            onClick={() => void loadMore()}
          >
            {t("projects.assets.loadMore", "加载更多")}
          </Button>
        </div>
      )}

      <Modal
        open={folderOpen}
        title={t("projects.assets.folderTitle", "新建文件夹")}
        onCancel={folderSubmitting ? undefined : closeFolderModal}
        maskClosable={false}
        destroyOnHidden
        footer={[
          <Button
            key="cancel"
            onClick={closeFolderModal}
            disabled={folderSubmitting}
          >
            {t("common.cancel", "取消")}
          </Button>,
          <Button
            key="submit"
            type="primary"
            loading={folderSubmitting}
            disabled={folderSubmitting}
            onClick={() => void submitFolder()}
          >
            {t("projects.assets.folderCreate", "创建")}
          </Button>,
        ]}
      >
        <Input
          value={folderName}
          maxLength={MAX_FOLDER_NAME_LENGTH}
          disabled={folderSubmitting}
          placeholder={t(
            "projects.assets.folderNamePlaceholder",
            "输入文件夹名称（1–120 字符）",
          )}
          aria-label={t("projects.assets.folderNameLabel", "文件夹名称")}
          onChange={(event) => setFolderName(event.target.value)}
        />
        {folderError != null && (
          <Alert
            type="warning"
            showIcon
            style={{ marginTop: 12 }}
            message={folderError}
          />
        )}
      </Modal>

      {deleteTarget?.projectId === projectId && (
        <Modal
          open
          title={t("projects.assets.deleteConfirmTitle", "移入回收站")}
          okText={t("projects.assets.deleteConfirmOk", "移入回收站")}
          cancelText={t("common.cancel", "取消")}
          okButtonProps={{ danger: true, loading: deleteSubmitting }}
          cancelButtonProps={{ disabled: deleteSubmitting }}
          maskClosable={false}
          destroyOnHidden
          onOk={() => void confirmDelete()}
          onCancel={closeDeleteModal}
        >
          <p>
            {t(
              "projects.assets.deleteConfirmBody",
              "将「{{name}}」移入回收站？之后可以从回收站恢复，原位置不再显示。",
              { name: deleteTarget.node.name },
            )}
          </p>
          {deleteTarget.node.kind === "folder" && (
            <p style={secondaryStyle}>
              {t(
                "projects.assets.deleteConfirmFolderNote",
                "文件夹会连同其中当前可见的内容一起移入回收站。",
              )}
            </p>
          )}
          {deleteError != null && (
            <Alert
              type="error"
              showIcon
              style={{ marginTop: 4 }}
              message={deleteError}
            />
          )}
        </Modal>
      )}

      {trashOpen && (
        <Modal
          open
          title={t("projects.assets.trashTitle", "回收站")}
          width="min(880px, calc(100vw - 32px))"
          onCancel={closeTrashModal}
          destroyOnHidden
          footer={[
            <Button key="close" onClick={closeTrashModal}>
              {t("common.close", "关闭")}
            </Button>,
          ]}
        >
          <div
            data-testid="project-assets-trash"
            style={{ display: "flex", flexDirection: "column", gap: 8 }}
          >
            <Text type="secondary" style={secondaryStyle}>
              {t(
                "projects.assets.trashHint",
                "回收站中的项目不能下载或预览；恢复后回到原位置。",
              )}
            </Text>
            {trashBody}
          </div>
          {trashActionError != null && (
            <Alert
              type="error"
              showIcon
              closable
              style={{ marginTop: 12 }}
              message={trashActionError}
              onClose={() => setTrashActionError(null)}
            />
          )}
        </Modal>
      )}

      {versionNode?.projectId === projectId && (
        <ProjectAssetVersions
          key={`${projectId}\u0000${versionNode.node.node_id}`}
          projectId={projectId}
          node={versionNode.node}
          onClose={closeVersionModal}
          onChanged={handleVersionChanged}
          onAccessLost={handleVersionAccessLost}
        />
      )}
    </div>
  );
}
