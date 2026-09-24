/**
 * ProjectAssetVersions — wide version-management modal for one project file
 * node (PS-06B-1).
 *
 * Frozen contract (`PROJECT_ASSET_VERSIONS_CONTRACT.md`):
 * - the modal is opened from a file row's “版本管理” action; folder rows never
 *   reach it
 * - the left rail lists versions newest-first (`created_at DESC,
 *   version_id DESC`) with a display-only ordinal derived from `total` and a
 *   current-version badge; `version_id` alone identifies selection, download
 *   and restore
 * - the right pane shows safe metadata only (size / media type / uploader
 *   placeholder / SHA-256 / time) plus an explicit “not rendered here”
 *   preview placeholder — no third-party preview embed and no uploaded HTML
 * - a new-version upload targets the current node id; the multipart filename
 *   never renames the node
 * - every 404 clears this modal and asks the parent to recheck the project;
 *   a missing historical object does not itself prove membership was revoked
 * - an archived-project 403 turns the modal read-only (the server still
 *   rejects independently)
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  Alert,
  Button,
  Modal,
  Popconfirm,
  Spin,
  Tag,
  Tooltip,
  Typography,
  Upload,
} from "antd";
import {
  Download,
  EyeOff,
  RotateCcw,
  Upload as UploadIcon,
} from "lucide-react";
import {
  PROJECT_ASSET_VERSIONS_PAGE_SIZE,
  projectAssetsApi,
  type ProjectAssetNode,
  type ProjectAssetVersion,
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

interface Props {
  projectId: string;
  node: ProjectAssetNode;
  onClose: () => void;
  /** Successful upload/restore: refresh the parent list and usage. */
  onChanged: (originProjectId: string) => void;
  /** 404 from any version call: clear stale rows, close and recheck access. */
  onAccessLost: (error: unknown, originProjectId: string) => void;
}

interface VersionsState {
  items: ProjectAssetVersion[];
  total: number;
  hasMore: boolean;
  nextOffset: number;
  loading: boolean;
  loadingMore: boolean;
  error: unknown;
  appendError: unknown;
}

function freshVersionsState(): VersionsState {
  return {
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

function mergeUniqueVersions(
  previous: ProjectAssetVersion[],
  incoming: ProjectAssetVersion[],
): ProjectAssetVersion[] {
  const seen = new Set(previous.map((version) => version.version_id));
  const merged = [...previous];
  for (const version of incoming) {
    if (seen.has(version.version_id)) continue;
    seen.add(version.version_id);
    merged.push(version);
  }
  return merged;
}

/** Readable size without inventing precision. */
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

const secondaryStyle: React.CSSProperties = {
  fontSize: 12,
  color: "var(--fn-text-tertiary, rgba(0,0,0,0.45))",
};

const metaLabelStyle: React.CSSProperties = {
  fontSize: 12,
  color: "var(--fn-text-tertiary, rgba(0,0,0,0.45))",
};

const metaValueStyle: React.CSSProperties = {
  fontSize: 12,
  wordBreak: "break-word",
};

export default function ProjectAssetVersions({
  projectId,
  node,
  onClose,
  onChanged,
  onAccessLost,
}: Props) {
  const { t } = useTranslation();
  const timezone = useServerTimezone();

  const [state, setState] = useState<VersionsState>(freshVersionsState);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [loadKey, setLoadKey] = useState(0);

  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [failedUpload, setFailedUpload] = useState<File | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [readOnly, setReadOnly] = useState(false);
  const [restoringId, setRestoringId] = useState<string | null>(null);
  const [downloadingId, setDownloadingId] = useState<string | null>(null);

  /** Guards: late responses never repaint a closed/switched modal. */
  const listSeq = useRef(0);
  const mutationSeq = useRef(0);
  const downloadSeq = useRef(0);
  const uploadAbort = useRef<AbortController | null>(null);
  /** `version_id` to select after the next list refresh (upload/restore). */
  const preferredSelection = useRef<string | null>(null);
  const onAccessLostRef = useRef(onAccessLost);
  onAccessLostRef.current = onAccessLost;
  const scopeKey = `${projectId}\u0000${node.node_id}`;
  const activeScope = useRef({ key: scopeKey, generation: 0 });
  if (activeScope.current.key !== scopeKey) {
    activeScope.current = {
      key: scopeKey,
      generation: activeScope.current.generation + 1,
    };
  }
  const scopeGeneration = activeScope.current.generation;
  const isCurrentScope = useCallback(
    () => activeScope.current.generation === scopeGeneration,
    [scopeGeneration],
  );

  const failWithAccessLoss = useCallback(
    (error: unknown, originProjectId: string, generation: number) => {
      if (activeScope.current.generation !== generation) return;
      listSeq.current += 1;
      mutationSeq.current += 1;
      downloadSeq.current += 1;
      uploadAbort.current?.abort();
      uploadAbort.current = null;
      setState(freshVersionsState());
      setSelectedId(null);
      setUploading(false);
      setRestoringId(null);
      setDownloadingId(null);
      onAccessLostRef.current(error, originProjectId);
    },
    [],
  );

  useEffect(() => {
    mutationSeq.current += 1;
    downloadSeq.current += 1;
    uploadAbort.current?.abort();
    uploadAbort.current = null;
    preferredSelection.current = null;
    setSelectedId(null);
    setUploading(false);
    setUploadError(null);
    setFailedUpload(null);
    setActionError(null);
    setReadOnly(false);
    setRestoringId(null);
    setDownloadingId(null);
  }, [scopeGeneration]);

  useEffect(() => {
    const seq = ++listSeq.current;
    setState(freshVersionsState());
    projectAssetsApi
      .listVersions(projectId, node.node_id, {
        limit: PROJECT_ASSET_VERSIONS_PAGE_SIZE,
        offset: 0,
      })
      .then((data) => {
        if (seq !== listSeq.current || !isCurrentScope()) return;
        const items = Array.isArray(data?.items) ? data.items : [];
        setState({
          items,
          total: data?.total ?? items.length,
          hasMore: Boolean(data?.has_more),
          nextOffset: items.length,
          loading: false,
          loadingMore: false,
          error: null,
          appendError: null,
        });
        const preferred = preferredSelection.current;
        preferredSelection.current = null;
        setSelectedId((previous) => {
          if (
            preferred &&
            items.some((item) => item.version_id === preferred)
          ) {
            return preferred;
          }
          if (previous && items.some((item) => item.version_id === previous)) {
            return previous;
          }
          const current = items.find((item) => item.is_current);
          return current?.version_id ?? items[0]?.version_id ?? null;
        });
      })
      .catch((err: unknown) => {
        if (seq !== listSeq.current || !isCurrentScope()) return;
        if (isNotFoundApiError(err)) {
          failWithAccessLoss(err, projectId, scopeGeneration);
          return;
        }
        setState({
          ...freshVersionsState(),
          loading: false,
          error: err,
        });
      });
  }, [
    projectId,
    node.node_id,
    loadKey,
    failWithAccessLoss,
    isCurrentScope,
    scopeGeneration,
  ]);

  // Unmount (modal close / project switch) invalidates every in-flight call.
  useEffect(() => {
    return () => {
      listSeq.current += 1;
      mutationSeq.current += 1;
      downloadSeq.current += 1;
      uploadAbort.current?.abort();
      uploadAbort.current = null;
    };
  }, []);

  const loadMore = async () => {
    if (state.loading || state.loadingMore || !state.hasMore) return;
    const seq = ++listSeq.current;
    const offset = state.nextOffset;
    setState((previous) => ({
      ...previous,
      loadingMore: true,
      appendError: null,
    }));
    try {
      const data = await projectAssetsApi.listVersions(
        projectId,
        node.node_id,
        { limit: PROJECT_ASSET_VERSIONS_PAGE_SIZE, offset },
      );
      if (seq !== listSeq.current || !isCurrentScope()) return;
      const items = Array.isArray(data?.items) ? data.items : [];
      setState((previous) => ({
        ...previous,
        items: mergeUniqueVersions(previous.items, items),
        total: data?.total ?? previous.total,
        hasMore: Boolean(data?.has_more),
        nextOffset: offset + items.length,
        loadingMore: false,
        appendError: null,
      }));
    } catch (err: unknown) {
      if (seq !== listSeq.current || !isCurrentScope()) return;
      if (isNotFoundApiError(err)) {
        failWithAccessLoss(err, projectId, scopeGeneration);
        return;
      }
      setState((previous) => ({
        ...previous,
        appendError: err,
        loadingMore: false,
      }));
    }
  };

  /** 409 / 413 / 403 must stay actionable; the server stays authoritative. */
  const mutationErrorText = (err: unknown, fallback: string): string => {
    const parsed = parseApiError(err);
    if (parsed?.code === "FORBIDDEN" || httpStatus(err) === 403) {
      return t(
        "projects.assets.versionArchived",
        "项目已归档：版本只读，无法上传或恢复。",
      );
    }
    return apiErrorMessage(err, fallback, t);
  };

  const startUpload = async (file: File) => {
    const seq = ++mutationSeq.current;
    uploadAbort.current?.abort();
    const controller = new AbortController();
    uploadAbort.current = controller;
    setUploading(true);
    setUploadError(null);
    setFailedUpload(null);
    try {
      const result = await projectAssetsApi.uploadVersion(
        projectId,
        node.node_id,
        { file },
        { signal: controller.signal },
      );
      if (
        controller.signal.aborted ||
        seq !== mutationSeq.current ||
        !isCurrentScope()
      )
        return;
      preferredSelection.current = result?.version?.version_id ?? null;
      setLoadKey((key) => key + 1);
      void message.success(
        t("projects.assets.versionUploaded", "新版本已上传"),
      );
      onChanged(projectId);
    } catch (err: unknown) {
      if (controller.signal.aborted) return;
      if (seq !== mutationSeq.current || !isCurrentScope()) return;
      if (isNotFoundApiError(err)) {
        failWithAccessLoss(err, projectId, scopeGeneration);
        return;
      }
      if (isForbidden(err)) {
        setReadOnly(true);
      } else {
        setFailedUpload(file);
      }
      setUploadError(
        mutationErrorText(
          err,
          t("projects.assets.versionUploadFailed", "上传新版本失败"),
        ),
      );
    } finally {
      if (seq === mutationSeq.current && isCurrentScope()) setUploading(false);
      if (uploadAbort.current === controller) uploadAbort.current = null;
    }
  };

  const handleRestore = async (version: ProjectAssetVersion) => {
    const seq = ++mutationSeq.current;
    setRestoringId(version.version_id);
    setActionError(null);
    try {
      await projectAssetsApi.restoreVersion(
        projectId,
        node.node_id,
        version.version_id,
      );
      if (seq !== mutationSeq.current || !isCurrentScope()) return;
      preferredSelection.current = version.version_id;
      setLoadKey((key) => key + 1);
      void message.success(
        t("projects.assets.versionRestored", "已恢复为当前版"),
      );
      onChanged(projectId);
    } catch (err: unknown) {
      if (seq !== mutationSeq.current || !isCurrentScope()) return;
      if (isNotFoundApiError(err)) {
        failWithAccessLoss(err, projectId, scopeGeneration);
        return;
      }
      if (isForbidden(err)) {
        setReadOnly(true);
      }
      setActionError(
        mutationErrorText(
          err,
          t("projects.assets.versionRestoreFailed", "恢复版本失败"),
        ),
      );
    } finally {
      if (seq === mutationSeq.current && isCurrentScope()) setRestoringId(null);
    }
  };

  const handleDownload = async (version: ProjectAssetVersion) => {
    const seq = ++downloadSeq.current;
    setDownloadingId(version.version_id);
    setActionError(null);
    try {
      const blob = await projectAssetsApi.downloadVersion(
        projectId,
        node.node_id,
        version.version_id,
      );
      if (seq !== downloadSeq.current || !isCurrentScope()) return;
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
      if (seq !== downloadSeq.current || !isCurrentScope()) return;
      if (isNotFoundApiError(err)) {
        failWithAccessLoss(err, projectId, scopeGeneration);
        return;
      }
      setActionError(
        apiErrorMessage(
          err,
          t("projects.assets.versionDownloadFailed", "下载该版本失败"),
          t,
        ),
      );
    } finally {
      if (seq === downloadSeq.current && isCurrentScope())
        setDownloadingId(null);
    }
  };

  const selected =
    state.items.find((version) => version.version_id === selectedId) ?? null;
  const selectedIndex = state.items.findIndex(
    (version) => version.version_id === selectedId,
  );
  const ordinalFor = (index: number) =>
    Math.max(1, (state.total || state.items.length) - index);
  const selectedOrdinal = selectedIndex >= 0 ? ordinalFor(selectedIndex) : 0;
  const actionsBusy = uploading || restoringId != null;

  const renderRail = () => {
    if (state.loading) {
      return (
        <div
          data-testid="project-asset-versions-loading"
          style={{ display: "flex", justifyContent: "center", padding: 24 }}
        >
          <Spin size="small" />
        </div>
      );
    }
    if (state.error != null) {
      return (
        <Alert
          type="error"
          showIcon
          message={apiErrorMessage(
            state.error,
            t("projects.assets.versionLoadFailed", "加载版本失败"),
            t,
          )}
          action={
            <Button size="small" onClick={() => setLoadKey((key) => key + 1)}>
              {t("common.retry", "重试")}
            </Button>
          }
        />
      );
    }
    if (state.items.length === 0) {
      return (
        <Text type="secondary" style={secondaryStyle}>
          {t("projects.assets.versionEmpty", "暂无可显示的版本记录")}
        </Text>
      );
    }
    return (
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          gap: 6,
          overflowY: "auto",
          maxHeight: "min(420px, 55vh)",
        }}
      >
        {state.items.map((version) => {
          const index = state.items.indexOf(version);
          const isSelected = version.version_id === selectedId;
          return (
            <button
              key={version.version_id}
              type="button"
              data-testid={`project-asset-version-${version.version_id}`}
              aria-pressed={isSelected}
              aria-label={
                version.is_current
                  ? `${t("projects.assets.versionLabel", "第 {{number}} 版", {
                      number: ordinalFor(index),
                    })} · ${t("projects.assets.versionCurrent", "当前版")}`
                  : t("projects.assets.versionLabel", "第 {{number}} 版", {
                      number: ordinalFor(index),
                    })
              }
              onClick={() => setSelectedId(version.version_id)}
              style={{
                display: "flex",
                flexDirection: "column",
                gap: 2,
                width: "100%",
                padding: "8px 10px",
                textAlign: "left",
                background: "var(--fn-bg-elevated, #fff)",
                border: `1px solid ${
                  isSelected
                    ? "var(--fn-color-brand, #e85d75)"
                    : "var(--fn-border-primary, rgba(0,0,0,0.12))"
                }`,
                borderRadius: 8,
                cursor: "pointer",
              }}
            >
              <span
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  gap: 6,
                }}
              >
                <span>
                  {t("projects.assets.versionLabel", "第 {{number}} 版", {
                    number: ordinalFor(index),
                  })}
                </span>
                {version.is_current && (
                  <Tag color="red" style={{ marginInlineEnd: 0 }}>
                    {t("projects.assets.versionCurrent", "当前版")}
                  </Tag>
                )}
              </span>
              <span style={secondaryStyle}>
                {formatServerDateTime(version.created_at, timezone)}
              </span>
            </button>
          );
        })}
        {state.appendError != null && (
          <Alert
            type="error"
            showIcon
            message={apiErrorMessage(
              state.appendError,
              t("projects.assets.versionLoadFailed", "加载版本失败"),
              t,
            )}
            action={
              <Button size="small" onClick={() => void loadMore()}>
                {t("common.retry", "重试")}
              </Button>
            }
          />
        )}
        {state.hasMore && (
          <Button
            size="small"
            loading={state.loadingMore}
            onClick={() => void loadMore()}
          >
            {t("projects.assets.versionLoadMore", "加载更多版本")}
          </Button>
        )}
      </div>
    );
  };

  return (
    <Modal
      open
      title={t("projects.assets.versionModalTitle", "版本管理：{{name}}", {
        name: node.name,
      })}
      width="min(960px, calc(100vw - 32px))"
      onCancel={onClose}
      destroyOnHidden
      footer={[
        <Button key="close" onClick={onClose}>
          {t("common.close", "关闭")}
        </Button>,
      ]}
    >
      <div
        style={{
          display: "flex",
          flexWrap: "wrap",
          gap: 16,
          alignItems: "flex-start",
        }}
      >
        <aside
          style={{
            flex: "1 1 240px",
            minWidth: 220,
            maxWidth: 320,
            display: "flex",
            flexDirection: "column",
            gap: 8,
          }}
        >
          <div
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              gap: 8,
              flexWrap: "wrap",
            }}
          >
            <Text strong>
              {t("projects.assets.versionListTitle", "版本记录")}
            </Text>
            <Upload
              showUploadList={false}
              disabled={readOnly || actionsBusy}
              beforeUpload={(file) => {
                void startUpload(file);
                return false;
              }}
            >
              <Button
                size="small"
                icon={<UploadIcon size={14} />}
                loading={uploading}
                disabled={readOnly || restoringId != null}
              >
                {t("projects.assets.versionUpload", "上传新版本")}
              </Button>
            </Upload>
          </div>
          <Text type="secondary" style={secondaryStyle}>
            {t(
              "projects.assets.versionUploadTarget",
              "目标文件：{{name}}（文件名不改名、历史版本不覆盖）",
              { name: node.name },
            )}
          </Text>
          {!state.loading && state.error == null && (
            <Text type="secondary" style={secondaryStyle}>
              {t("projects.assets.versionCount", "共 {{total}} 个版本", {
                total: state.total,
              })}
            </Text>
          )}
          {renderRail()}
        </aside>

        <section
          data-testid="project-asset-version-detail"
          style={{
            flex: "2 1 380px",
            minWidth: 260,
            display: "flex",
            flexDirection: "column",
            gap: 12,
          }}
        >
          {selected == null ? (
            <Text type="secondary" style={secondaryStyle}>
              {t("projects.assets.versionSelectHint", "选择一个版本查看详情。")}
            </Text>
          ) : (
            <>
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 8,
                  flexWrap: "wrap",
                }}
              >
                <Text strong style={{ fontSize: 15 }}>
                  {t("projects.assets.versionLabel", "第 {{number}} 版", {
                    number: selectedOrdinal,
                  })}
                </Text>
                {selected.is_current && (
                  <Tag color="red">
                    {t("projects.assets.versionCurrent", "当前版")}
                  </Tag>
                )}
              </div>

              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "88px 1fr",
                  rowGap: 6,
                  columnGap: 8,
                }}
              >
                <span style={metaLabelStyle}>
                  {t("projects.assets.versionUploadedAt", "上传时间")}
                </span>
                <span style={metaValueStyle}>
                  {formatServerDateTime(selected.created_at, timezone)}
                </span>
                <span style={metaLabelStyle}>
                  {t("projects.assets.versionSize", "大小")}
                </span>
                <span style={metaValueStyle}>
                  {formatBytes(selected.size_bytes)}
                </span>
                <span style={metaLabelStyle}>
                  {t("projects.assets.versionMediaType", "类型")}
                </span>
                <span style={metaValueStyle}>
                  {selected.media_type ??
                    t("projects.assets.fileTypeUnknown", "未知类型")}
                </span>
                <span style={metaLabelStyle}>
                  {t("projects.assets.versionUploader", "上传者")}
                </span>
                <span style={metaValueStyle}>
                  {t("projects.assets.versionUploaderUnknown", "上传者未知")}
                </span>
                <span style={metaLabelStyle}>
                  {t("projects.assets.versionSha256", "SHA-256")}
                </span>
                <span
                  style={{
                    ...metaValueStyle,
                    fontFamily:
                      "ui-monospace, SFMono-Regular, Menlo, monospace",
                    wordBreak: "break-all",
                  }}
                >
                  {selected.sha256}
                </span>
              </div>

              <div
                style={{
                  display: "flex",
                  flexDirection: "column",
                  alignItems: "center",
                  gap: 4,
                  padding: 20,
                  border:
                    "1px dashed var(--fn-border-primary, rgba(0,0,0,0.15))",
                  borderRadius: 8,
                  color: "var(--fn-text-tertiary, rgba(0,0,0,0.45))",
                  textAlign: "center",
                }}
              >
                <EyeOff size={22} aria-hidden />
                <span>
                  {t(
                    "projects.assets.versionPreviewTitle",
                    "暂不支持页面内预览",
                  )}
                </span>
                <span style={secondaryStyle}>
                  {t(
                    "projects.assets.versionPreviewHint",
                    "为保护文件安全，本版本不在页面内渲染；请下载后查看。",
                  )}
                </span>
              </div>

              <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                <Button
                  icon={<Download size={14} />}
                  loading={downloadingId === selected.version_id}
                  disabled={
                    downloadingId != null &&
                    downloadingId !== selected.version_id
                  }
                  onClick={() => void handleDownload(selected)}
                >
                  {t("projects.assets.versionDownload", "下载该版本")}
                </Button>
                {selected.is_current ? (
                  <Tooltip
                    title={t(
                      "projects.assets.versionRestoreCurrentReason",
                      "该版本已是当前版。",
                    )}
                  >
                    <span>
                      <Button icon={<RotateCcw size={14} />} disabled>
                        {t("projects.assets.versionRestore", "恢复为当前版")}
                      </Button>
                    </span>
                  </Tooltip>
                ) : (
                  <Popconfirm
                    title={t(
                      "projects.assets.versionRestoreConfirm",
                      "将第 {{number}} 版恢复为当前版？",
                      { number: selectedOrdinal },
                    )}
                    okText={t(
                      "projects.assets.versionRestoreConfirmOk",
                      "恢复",
                    )}
                    cancelText={t("common.cancel", "取消")}
                    okButtonProps={{
                      disabled: readOnly || actionsBusy,
                    }}
                    onConfirm={() => void handleRestore(selected)}
                  >
                    <Button
                      icon={<RotateCcw size={14} />}
                      loading={restoringId === selected.version_id}
                      disabled={readOnly || restoringId != null}
                    >
                      {t("projects.assets.versionRestore", "恢复为当前版")}
                    </Button>
                  </Popconfirm>
                )}
              </div>
            </>
          )}
        </section>
      </div>

      {uploadError != null && (
        <Alert
          type={readOnly ? "warning" : "error"}
          showIcon
          closable
          style={{ marginTop: 12 }}
          message={uploadError}
          onClose={() => setUploadError(null)}
          action={
            failedUpload != null && !readOnly ? (
              <Button
                size="small"
                onClick={() => void startUpload(failedUpload)}
              >
                {t("common.retry", "重试")}
              </Button>
            ) : undefined
          }
        />
      )}
      {actionError != null && (
        <Alert
          type={readOnly ? "warning" : "error"}
          showIcon
          closable
          style={{ marginTop: 12 }}
          message={actionError}
          onClose={() => setActionError(null)}
        />
      )}
    </Modal>
  );
}

function httpStatus(error: unknown): number | null {
  const raw = error instanceof Error ? error.message : String(error ?? "");
  const match = raw.match(/\b(4\d{2}|5\d{2})\b/);
  return match ? Number(match[1]) : null;
}

function isForbidden(error: unknown): boolean {
  const parsed = parseApiError(error);
  return parsed?.code === "FORBIDDEN" || httpStatus(error) === 403;
}
