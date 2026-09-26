import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { Tooltip } from "antd";
import { message } from "@/utils/antdMessage";

import {
  Pencil,
  Save,
  ArrowDownToLine,
  RefreshCw,
  FileX,
  Eye,
  Code2,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import { probeAuthResource, request, requestBlob } from "../../../api/request";
import { isNotFoundApiError } from "../../../utils/apiError";
import { withFromWorkspace } from "../../../utils/fromWorkspace";
import FileViewer from "../../Agent/Workspace/components/FileViewer";
import { getDocKind } from "../../Agent/Workspace/utils/docKind";
import { isProbablyText } from "../../Agent/Workspace/utils/fileKind";
import { getMediaKind } from "../../Agent/Workspace/utils/mediaKind";
import {
  getPreviewKind,
  previewNeedsFillLayout,
  defaultPreviewMode,
} from "../../Agent/Workspace/components/FilePreview";
import {
  canonicalizeDockFilePath,
  dockFileBasename,
  isHostAbsolutePath,
  normalizeDockFilePath,
  toPrivateWorkspaceRelPath,
  toWorkspaceApiPath,
} from "../utils/dockFilePath";
import styles from "../index.module.less";

interface FilePanelContentProps {
  agentId: string;
  /** Single workspace file path for this tab. */
  filePath: string;
  /**
   * Owner-private 030 file-task route. File I/O then requires
   * ``from_workspace=true`` with a proven managed-root-relative path;
   * unsafe shapes are refused locally without any request. Only a
   * server-verified route carries a non-empty ``agentId``, so an
   * unverified/refused route performs zero file I/O.
   */
  privateTask?: boolean;
  /** Lift toolbar actions into the shared dock shell (active tab only). */
  onActionsChange?: (actions: ReactNode | null) => void;
}

/**
 * Shared file viewer/editor body used by a dock file tab (write/edit/send
 * tool results and preview/download cards).
 */
export default function FilePanelContent({
  agentId,
  filePath,
  privateTask = false,
  onActionsChange,
}: FilePanelContentProps) {
  const { t } = useTranslation();
  const resolvedPath = normalizeDockFilePath(filePath);
  const [content, setContent] = useState<string>("");
  const [editMode, setEditMode] = useState(false);
  const [previewMode, setPreviewMode] = useState(true);
  const [fileLoading, setFileLoading] = useState(false);
  const [fileMissing, setFileMissing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [refreshToken, setRefreshToken] = useState(0);

  /**
   * 030A private runtime key, computed from the **raw** tool path so
   * ``file://`` / drive / UNC / ``~`` / ``..`` shapes are refused instead of
   * silently collapsed by normalization. ``null`` = refused.
   */
  const privateRelPath = useMemo(
    () => (privateTask ? toPrivateWorkspaceRelPath(filePath) : null),
    [privateTask, filePath],
  );
  /**
   * A private tab without a verified runtime (empty ``agentId`` while the
   * route is checking/refused) or with a refused path performs zero file
   * I/O — keep-alive tabs lose the prior route's authorization as soon as
   * the verified id is gone.
   */
  const privateBlocked =
    privateTask && (!agentId || (Boolean(resolvedPath) && !privateRelPath));
  const fileUnavailable = fileMissing || privateBlocked;

  const docKind = resolvedPath ? getDocKind(resolvedPath) : null;
  const mediaKind = resolvedPath ? getMediaKind(resolvedPath) : null;
  const previewKind = resolvedPath ? getPreviewKind(resolvedPath) : null;
  const isText = resolvedPath ? isProbablyText(resolvedPath) : false;
  const showEditButton = isText && !fileUnavailable;
  const showPreviewToggle =
    isText &&
    previewKind !== null &&
    !editMode &&
    content !== "" &&
    !fileUnavailable;

  const apiFilePath = useMemo(
    () =>
      privateTask ? privateRelPath ?? "" : toWorkspaceApiPath(resolvedPath),
    [privateTask, privateRelPath, resolvedPath],
  );

  /** Agent-workspace I/O URL; a private task always goes through true mode. */
  const workspaceIoUrl = useCallback(
    (endpoint: "file" | "download") => {
      const url = `/agents/${agentId}/workspace/${endpoint}?path=${encodeURIComponent(
        apiFilePath,
      )}`;
      return privateTask ? withFromWorkspace(url) : url;
    },
    [agentId, apiFilePath, privateTask],
  );

  useEffect(() => {
    if (!resolvedPath || !agentId || privateBlocked) {
      if (privateBlocked) {
        // Refused route/path — including a keep-alive tab whose verified
        // route disappeared: drop the previous route's content and edit
        // state so nothing stale can be shown or saved.
        setContent("");
        setEditMode(false);
        setPreviewMode(true);
        setFileLoading(false);
        setFileMissing(false);
      }
      return;
    }
    setEditMode(false);
    setPreviewMode(defaultPreviewMode(resolvedPath));
    setContent("");
    setFileMissing(false);

    let cancelled = false;
    setFileLoading(true);

    const finishOk = () => {
      if (!cancelled) {
        setFileMissing(false);
        setFileLoading(false);
      }
    };
    const finishError = (err: unknown) => {
      if (cancelled) return;
      setFileLoading(false);
      if (isNotFoundApiError(err)) {
        setFileMissing(true);
        return;
      }
      message.error(
        (err instanceof Error ? err.message : String(err)) ||
          t("workspace.readFailed", "读取失败"),
      );
    };

    // Text: load content. Unknown/binary: light existence probe (no body buffer).
    // Media/doc: viewers fetch themselves and surface 404 locally.
    if (isText) {
      request<{ content: string }>(workspaceIoUrl("file"))
        .then((r) => {
          if (!cancelled) {
            setContent(r.content);
            finishOk();
          }
        })
        .catch(finishError);
    } else if (mediaKind || docKind) {
      finishOk();
    } else {
      probeAuthResource(workspaceIoUrl("download"))
        .then(() => finishOk())
        .catch(finishError);
    }

    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    resolvedPath,
    apiFilePath,
    agentId,
    isText,
    mediaKind,
    docKind,
    refreshToken,
    privateBlocked,
    workspaceIoUrl,
  ]);

  const refresh = useCallback(() => {
    setEditMode(false);
    setRefreshToken((n) => n + 1);
  }, []);

  const save = useCallback(async () => {
    if (!resolvedPath || privateBlocked) return;
    setSaving(true);
    try {
      await request(workspaceIoUrl("file"), {
        method: "PUT",
        body: JSON.stringify({ content }),
      });
      message.success(t("workspace.saved", "已保存"));
      setEditMode(false);
    } catch (err: unknown) {
      message.error(
        (err instanceof Error ? err.message : String(err)) ||
          t("workspace.saveFailed", "保存失败"),
      );
    } finally {
      setSaving(false);
    }
  }, [content, privateBlocked, resolvedPath, t, workspaceIoUrl]);

  const download = useCallback(async () => {
    if (!resolvedPath || privateBlocked) return;
    try {
      const blob = await requestBlob(workspaceIoUrl("download"));
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = dockFileBasename(resolvedPath) || "download";
      a.click();
      URL.revokeObjectURL(a.href);
    } catch (err: unknown) {
      if (isNotFoundApiError(err)) {
        setFileMissing(true);
        message.warning(
          t(
            "chat.dockFileMaybeDeleted",
            "该文件可能为处理过程中的临时文件，当前已经被删除。",
          ),
        );
        return;
      }
      message.error(
        (err instanceof Error ? err.message : String(err)) ||
          t("workspace.downloadFailed", "下载失败"),
      );
    }
  }, [privateBlocked, resolvedPath, t, workspaceIoUrl]);

  const bodyFill =
    !fileUnavailable &&
    (editMode ||
      docKind !== null ||
      (previewMode && previewNeedsFillLayout(previewKind)));

  useLayoutEffect(() => {
    if (!onActionsChange) return;

    const actions = (
      <>
        {showPreviewToggle && (
          <Tooltip
            title={
              previewMode ? t("workspace.source", "源码") : t("common.preview")
            }
          >
            <button
              type="button"
              className={`${styles.fileModalIconBtn} ${
                previewMode ? styles.fileModalIconBtnActive : ""
              }`}
              onClick={() => setPreviewMode((v) => !v)}
              aria-label={
                previewMode
                  ? t("workspace.source", "源码")
                  : t("common.preview")
              }
            >
              {previewMode ? (
                <Code2 size={16} strokeWidth={2} />
              ) : (
                <Eye size={16} strokeWidth={2} />
              )}
            </button>
          </Tooltip>
        )}
        <Tooltip title={t("common.refresh")}>
          <button
            type="button"
            className={styles.fileModalIconBtn}
            onClick={refresh}
            disabled={!resolvedPath || fileLoading || privateBlocked}
            aria-label={t("common.refresh")}
          >
            <RefreshCw size={16} strokeWidth={2} />
          </button>
        </Tooltip>
        <Tooltip title={t("common.download")}>
          <button
            type="button"
            className={styles.fileModalIconBtn}
            onClick={() => void download()}
            disabled={fileUnavailable}
            aria-label={t("common.download")}
          >
            <ArrowDownToLine size={16} strokeWidth={2} />
          </button>
        </Tooltip>
        {showEditButton &&
          (editMode ? (
            <Tooltip title={t("common.save")}>
              <button
                type="button"
                className={`${styles.fileModalIconBtn} ${styles.fileModalIconBtnPrimary}`}
                onClick={() => void save()}
                disabled={saving}
                aria-label={t("common.save")}
              >
                <Save size={16} strokeWidth={2} />
              </button>
            </Tooltip>
          ) : (
            <Tooltip title={t("common.edit")}>
              <button
                type="button"
                className={styles.fileModalIconBtn}
                onClick={() => {
                  setPreviewMode(false);
                  setEditMode(true);
                }}
                aria-label={t("common.edit")}
              >
                <Pencil size={16} strokeWidth={2} />
              </button>
            </Tooltip>
          ))}
      </>
    );

    onActionsChange(actions);
  }, [
    onActionsChange,
    resolvedPath,
    showPreviewToggle,
    previewMode,
    refresh,
    download,
    save,
    fileLoading,
    fileUnavailable,
    privateBlocked,
    showEditButton,
    editMode,
    saving,
    t,
  ]);

  useEffect(() => {
    return () => {
      onActionsChange?.(null);
    };
  }, [onActionsChange]);

  // Keep host-absolute tool paths for API I/O (virtual root_dir failback).
  // Only collapse relative / already-canonical keys for the viewer.
  // A private task instead hands the viewer the proven managed-root-relative
  // key so media/doc previews build true-mode URLs.
  const viewerPath = useMemo(() => {
    if (privateTask) return privateRelPath ?? "";
    const n = normalizeDockFilePath(resolvedPath);
    if (isHostAbsolutePath(n)) return n;
    return canonicalizeDockFilePath(resolvedPath, agentId) || resolvedPath;
  }, [privateTask, privateRelPath, resolvedPath, agentId]);

  return (
    <div className={styles.filePanelBody}>
      <div
        className={`${styles.fileModalBody} ${
          bodyFill ? styles.fileModalBodyFill : ""
        }`}
      >
        {fileUnavailable ? (
          <div className={styles.fileMissingState} role="status">
            <FileX
              size={40}
              strokeWidth={1.5}
              className={styles.fileMissingIcon}
              aria-hidden
            />
            <p className={styles.fileMissingTitle}>
              {privateBlocked
                ? t(
                    "chat.dockFileUnavailable",
                    "该文件当前不可访问：任务未通过校验，或路径不在受控工作区内。",
                  )
                : t(
                    "chat.dockFileMaybeDeleted",
                    "该文件可能为处理过程中的临时文件，当前已经被删除。",
                  )}
            </p>
          </div>
        ) : (
          viewerPath && (
            <FileViewer
              agentId={agentId}
              path={viewerPath}
              fromWorkspace={privateTask}
              editMode={editMode}
              value={content}
              onChange={setContent}
              fileLoading={fileLoading}
              previewMode={previewMode}
              refreshToken={refreshToken}
            />
          )
        )}
      </div>
    </div>
  );
}
