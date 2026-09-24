/**
 * ProjectAssets — 项目详情“资产”页签 (PS-06A / 023A).
 *
 * Real private project asset library backed only by the 023A API:
 * - hidden root by default; the folder navigation stack builds the breadcrumb
 *   (no URL deep links, a refresh returns to the root)
 * - name search, file/folder filter and `limit/offset` paging are all
 *   server-side; rows are deduped by `node_id` while paging
 * - usage (`file_count` / `total_bytes`) is the committed current-version
 *   total, never a made-up quota
 * - files download through `requestBlob` into a one-shot `<a download>`
 *   anchor (the JWT header cannot ride a plain anchor), revoked afterwards
 * - request state is keyed by `projectId\0parentId\0query\0kind`, so switching
 *   project, folder or filter never flashes stale rows and late list/upload
 *   responses are dropped
 * - a 404 clears rows and usage and shows the no-access state; 409/413/403
 *   surface recoverable messages; filenames and MIME types are rendered as
 *   inert text from server metadata
 *
 * Version history, delete/restore and “add to task” have no safe backend yet
 * (023B / PS-05B), so they are not rendered as usable controls.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  Alert,
  Breadcrumb,
  Button,
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
  ListPlus,
  RefreshCw,
  Upload as UploadIcon,
} from "lucide-react";
import { EmptyState } from "../../components/EmptyState";
import {
  PROJECT_ASSETS_PAGE_SIZE,
  projectAssetsApi,
  type ProjectAssetKind,
  type ProjectAssetNode,
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

  /** Monotonic guards: late responses never overwrite fresher state. */
  const fetchSeq = useRef(0);
  const usageSeq = useRef(0);
  const mutationSeq = useRef(0);
  const downloadSeq = useRef(0);
  const uploadAbort = useRef<AbortController | null>(null);
  const currentProjectId = useRef(projectId);
  currentProjectId.current = projectId;

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
    fetchSeq.current += 1;
    usageSeq.current += 1;
    mutationSeq.current += 1;
    downloadSeq.current += 1;
    uploadAbort.current?.abort();
    uploadAbort.current = null;
  }, [projectId]);

  const ui = uiState.key === projectId ? uiState : freshUiState(projectId);
  const parentId =
    ui.stack.length > 0 ? ui.stack[ui.stack.length - 1].node_id : null;
  const kindParam: ProjectAssetKind | undefined =
    ui.kind === "all" ? undefined : ui.kind;
  const stateKey = `${projectId}\u0000${parentId ?? ""}\u0000${ui.query}\u0000${
    ui.kind
  }`;

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
          // Revocation: drop the usage in flight and on screen.
          usageSeq.current += 1;
          setUsageState(null);
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
  }, [projectId, parentId, kindParam, ui.query, reloadKey, stateKey]);

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
          },
        });
      })
      .catch(() => {
        if (seq !== usageSeq.current) return;
        setUsageState({ key: projectId, value: null });
      });
  }, [projectId, usageTick]);

  const applyNotFound = useCallback((key: string, err: unknown) => {
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
  }, []);

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
      return (
        <button
          key={node.node_id}
          type="button"
          data-testid={`project-asset-${node.node_id}`}
          aria-label={t("projects.assets.openFolder", "进入文件夹：{{name}}", {
            name: node.name,
          })}
          onClick={() => enterFolder(node)}
          style={{ ...rowStyle, cursor: "pointer" }}
        >
          <Folder size={16} aria-hidden />
          <span style={{ flex: 1, minWidth: 0, wordBreak: "break-word" }}>
            {node.name}
          </span>
          <span style={secondaryStyle}>{updated}</span>
          <ChevronRight size={14} aria-hidden />
        </button>
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

  const usageText = usageValue
    ? t("projects.assets.usage", "容量：{{count}} 个文件 · {{size}}", {
        count: usageValue.file_count,
        size: formatBytes(usageValue.total_bytes),
      })
    : t("projects.assets.usageUnavailable", "容量暂不可用");

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
                "版本历史、删除与「添加到任务」将在后续批次提供。",
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
    </div>
  );
}
