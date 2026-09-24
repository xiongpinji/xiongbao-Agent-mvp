/**
 * ProjectAssetPdfPreview — authenticated, version-keyed PDF preview for the
 * project version-management modal (027).
 *
 * Only the selected authorized historical version is fetched, through the
 * existing authenticated project-asset download endpoint, and rendered by the
 * existing local PDF.js core. The core is imported on demand so unrelated
 * project screens never pull the PDF bundle. No third-party preview service,
 * no public file URL and no uploaded HTML/SVG is ever rendered.
 */

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type CSSProperties,
  type ReactNode,
} from "react";
import { useTranslation } from "react-i18next";
import { Button, Spin } from "antd";
import { projectAssetsApi } from "../../api/modules/projectAssets";
import { isNotFoundApiError } from "../../utils/apiError";

/** 027 contract: metadata and actual blobs above this size never render. */
export const PROJECT_ASSET_PDF_PREVIEW_MAX_BYTES = 25 * 1024 * 1024;

type DocumentPreviewCoreComponent =
  typeof import("../../components/DocumentPreviewCore")["default"];

const PDF_SIGNATURE_BYTES = [0x25, 0x50, 0x44, 0x46, 0x2d];

const containerStyle: CSSProperties = {
  height: "min(440px, 52vh)",
  minHeight: 220,
  overflow: "hidden",
  border: "1px solid var(--fn-border-primary, rgba(0,0,0,0.12))",
  borderRadius: 8,
};

const centeredStyle: CSSProperties = {
  height: "100%",
  display: "flex",
  flexDirection: "column",
  alignItems: "center",
  justifyContent: "center",
  gap: 8,
  padding: 20,
  textAlign: "center",
  color: "var(--fn-text-tertiary, rgba(0,0,0,0.45))",
};

function isAbortError(error: unknown): boolean {
  return (
    (error instanceof DOMException && error.name === "AbortError") ||
    (error instanceof Error && error.name === "AbortError")
  );
}

function abortError(): DOMException {
  return new DOMException("Preview aborted", "AbortError");
}

function readPdfSignature(blob: Blob): Promise<boolean> {
  return new Promise((resolve) => {
    const reader = new FileReader();
    reader.onload = () => {
      const bytes = new Uint8Array(
        reader.result instanceof ArrayBuffer
          ? reader.result
          : new ArrayBuffer(0),
      );
      resolve(
        PDF_SIGNATURE_BYTES.every((byte, index) => bytes[index] === byte),
      );
    };
    reader.onerror = () => resolve(false);
    reader.readAsArrayBuffer(blob.slice(0, PDF_SIGNATURE_BYTES.length));
  });
}

interface Props {
  projectId: string;
  nodeId: string;
  versionId: string;
  filename: string;
  /** 404 on the authorized download: missing object or revoked membership. */
  onAccessLost: (error: unknown) => void;
}

export default function ProjectAssetPdfPreview({
  projectId,
  nodeId,
  versionId,
  filename,
  onAccessLost,
}: Props) {
  const { t } = useTranslation();
  const onAccessLostRef = useRef(onAccessLost);
  onAccessLostRef.current = onAccessLost;
  const aliveRef = useRef(true);

  const [PreviewCore, setPreviewCore] =
    useState<DocumentPreviewCoreComponent | null>(null);
  const [coreLoadFailed, setCoreLoadFailed] = useState(false);
  const [coreAttempt, setCoreAttempt] = useState(0);

  useEffect(() => {
    aliveRef.current = true;
    return () => {
      aliveRef.current = false;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    setCoreLoadFailed(false);
    import("../../components/DocumentPreviewCore")
      .then((module) => {
        const Core = module.default;
        if (cancelled) return;
        setPreviewCore(() => Core);
      })
      .catch(() => {
        if (cancelled) return;
        setCoreLoadFailed(true);
      });
    return () => {
      cancelled = true;
    };
  }, [coreAttempt]);

  const fetchBlob = useCallback(
    async (
      onProgress?: (loaded: number, total: number) => void,
      signal?: AbortSignal,
    ): Promise<Blob> => {
      const rejectAborted = () => Promise.reject(abortError());
      if (signal?.aborted || !aliveRef.current) return rejectAborted();

      let blob: Blob;
      try {
        blob = await projectAssetsApi.downloadVersion(
          projectId,
          nodeId,
          versionId,
          signal ? { signal } : {},
          onProgress,
        );
      } catch (error) {
        if (signal?.aborted || isAbortError(error) || !aliveRef.current) {
          return rejectAborted();
        }
        if (isNotFoundApiError(error)) onAccessLostRef.current(error);
        throw error;
      }

      if (signal?.aborted || !aliveRef.current) return rejectAborted();
      if (blob.size <= 0 || blob.size > PROJECT_ASSET_PDF_PREVIEW_MAX_BYTES) {
        throw new Error("Project asset PDF preview rejected by size");
      }

      const hasPdfSignature = await readPdfSignature(blob);
      if (signal?.aborted || !aliveRef.current) return rejectAborted();
      if (!hasPdfSignature) {
        throw new Error("Project asset PDF preview rejected by signature");
      }
      return blob;
    },
    [projectId, nodeId, versionId],
  );

  let content: ReactNode;
  if (coreLoadFailed) {
    content = (
      <div style={centeredStyle}>
        <span>
          {t(
            "projects.assets.versionPreviewLoadFailed",
            "预览组件加载失败，请重试或下载该版本。",
          )}
        </span>
        <Button
          size="small"
          onClick={() => setCoreAttempt((value) => value + 1)}
        >
          {t("common.retry", "重试")}
        </Button>
      </div>
    );
  } else if (PreviewCore == null) {
    content = (
      <div style={centeredStyle}>
        <Spin size="small" />
      </div>
    );
  } else {
    content = (
      <PreviewCore kind="pdf" filename={filename} fetchBlob={fetchBlob} />
    );
  }

  return (
    <div data-testid="project-asset-version-pdf-preview" style={containerStyle}>
      {content}
    </div>
  );
}
