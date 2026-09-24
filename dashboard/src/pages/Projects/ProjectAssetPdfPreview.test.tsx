import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ComponentProps } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { downloadVersion } = vi.hoisted(() => ({ downloadVersion: vi.fn() }));

const coreHarness = vi.hoisted(() => ({
  fail: false,
  onProgress: null as ((loaded: number, total: number) => void) | null,
  signal: null as AbortSignal | null,
  result: null as Blob | null,
  error: null as unknown,
}));

vi.mock("../../api/modules/projectAssets", async (importOriginal) => {
  const actual = await importOriginal<
    typeof import("../../api/modules/projectAssets")
  >();
  return {
    ...actual,
    projectAssetsApi: { downloadVersion },
  };
});

vi.mock("../../components/DocumentPreviewCore", async () => {
  const React = await import("react");
  const PreviewCoreHarness = ({
    kind,
    filename,
    fetchBlob,
  }: {
    kind: string;
    filename: string;
    fetchBlob: (
      onProgress?: (loaded: number, total: number) => void,
      signal?: AbortSignal,
    ) => Promise<Blob>;
  }) => {
    React.useEffect(() => {
      const controller = new AbortController();
      coreHarness.signal = controller.signal;
      coreHarness.result = null;
      coreHarness.error = null;
      coreHarness.onProgress = (loaded: number, total: number) => {
        void loaded;
        void total;
      };
      fetchBlob(coreHarness.onProgress, controller.signal).then(
        (blob) => {
          if (!controller.signal.aborted) coreHarness.result = blob;
        },
        (error: unknown) => {
          coreHarness.error = error;
        },
      );
      return () => {
        controller.abort();
      };
    }, [fetchBlob]);
    return (
      <div
        data-testid="preview-core"
        data-kind={kind}
        data-filename={filename}
      />
    );
  };
  return {
    get default() {
      if (coreHarness.fail) throw new Error("preview core chunk failed");
      return PreviewCoreHarness;
    },
  };
});

import ProjectAssetPdfPreview, {
  PROJECT_ASSET_PDF_PREVIEW_MAX_BYTES,
} from "./ProjectAssetPdfPreview";

let onAccessLost: ReturnType<typeof vi.fn>;

function pdfBlob(): Blob {
  return new Blob(["%PDF-1.4\n"], { type: "application/pdf" });
}

function notFoundError(): Error {
  return new Error(
    '404 - {"error":{"code":"NOT_FOUND","message":"not found"}}',
  );
}

function renderPreview(
  overrides: Partial<ComponentProps<typeof ProjectAssetPdfPreview>> = {},
) {
  return render(
    <ProjectAssetPdfPreview
      projectId="p1"
      nodeId="file-1"
      versionId="ver-1"
      filename="需求说明.pdf"
      onAccessLost={onAccessLost}
      {...overrides}
    />,
  );
}

function lastCall() {
  return downloadVersion.mock.calls[downloadVersion.mock.calls.length - 1];
}

beforeEach(() => {
  vi.clearAllMocks();
  downloadVersion.mockReset();
  downloadVersion.mockResolvedValue(pdfBlob());
  onAccessLost = vi.fn();
  coreHarness.fail = false;
  coreHarness.onProgress = null;
  coreHarness.signal = null;
  coreHarness.result = null;
  coreHarness.error = null;
});

describe("ProjectAssetPdfPreview", () => {
  it("loads the core on demand and fetches the exact version with signal and progress", async () => {
    renderPreview();

    expect(await screen.findByTestId("preview-core")).toHaveAttribute(
      "data-kind",
      "pdf",
    );
    await waitFor(() => expect(downloadVersion).toHaveBeenCalled());
    const [projectId, nodeId, versionId, options, onProgress] = lastCall() as [
      string,
      string,
      string,
      RequestInit,
      (loaded: number, total: number) => void,
    ];
    expect([projectId, nodeId, versionId]).toEqual(["p1", "file-1", "ver-1"]);
    expect(options.signal).toBe(coreHarness.signal);
    expect(onProgress).toBe(coreHarness.onProgress);
    expect(coreHarness.signal?.aborted).toBe(false);

    await waitFor(() => expect(coreHarness.result).not.toBeNull());
    expect(onAccessLost).not.toHaveBeenCalled();
  });

  it("fails the preview for bytes without the %PDF- signature", async () => {
    downloadVersion.mockResolvedValueOnce(
      new Blob(["<svg onload=alert(1)>"], { type: "application/pdf" }),
    );
    renderPreview();

    await waitFor(() => expect(coreHarness.error).toBeInstanceOf(Error));
    expect(coreHarness.result).toBeNull();
    expect(onAccessLost).not.toHaveBeenCalled();
  });

  it("rejects an actual blob above the shared size limit", async () => {
    const oversize = pdfBlob();
    Object.defineProperty(oversize, "size", {
      value: PROJECT_ASSET_PDF_PREVIEW_MAX_BYTES + 1,
    });
    downloadVersion.mockResolvedValueOnce(oversize);
    renderPreview();

    await waitFor(() => expect(coreHarness.error).toBeInstanceOf(Error));
    expect(coreHarness.result).toBeNull();
  });

  it("rejects a zero-byte blob", async () => {
    downloadVersion.mockResolvedValueOnce(
      new Blob([], { type: "application/pdf" }),
    );
    renderPreview();

    await waitFor(() => expect(coreHarness.error).toBeInstanceOf(Error));
    expect(coreHarness.result).toBeNull();
  });

  it("surfaces a 404 to the parent and rejects the fetch", async () => {
    const notFound = notFoundError();
    downloadVersion.mockRejectedValueOnce(notFound);
    renderPreview();

    await waitFor(() => expect(onAccessLost).toHaveBeenCalledWith(notFound));
    await waitFor(() => expect(coreHarness.error).toBe(notFound));
  });

  it("stays silent when the request itself aborts", async () => {
    downloadVersion.mockRejectedValueOnce(
      new DOMException("aborted", "AbortError"),
    );
    renderPreview();

    await waitFor(() => expect(coreHarness.error).toBeInstanceOf(DOMException));
    expect((coreHarness.error as DOMException).name).toBe("AbortError");
    expect(onAccessLost).not.toHaveBeenCalled();
  });

  it("aborts the in-flight fetch on unmount and ignores late bytes", async () => {
    let resolveBlob: ((blob: Blob) => void) | null = null;
    downloadVersion.mockImplementationOnce(
      () => new Promise((resolve) => (resolveBlob = resolve)),
    );
    const { unmount } = renderPreview();
    await waitFor(() => expect(coreHarness.signal).not.toBeNull());

    unmount();
    expect(coreHarness.signal?.aborted).toBe(true);

    await act(async () => {
      resolveBlob?.(pdfBlob());
      await Promise.resolve();
    });
    expect(coreHarness.result).toBeNull();
    expect(onAccessLost).not.toHaveBeenCalled();
  });

  it("ignores a late 404 after unmount", async () => {
    let rejectBlob: ((reason: unknown) => void) | null = null;
    downloadVersion.mockImplementationOnce(
      () => new Promise((_resolve, reject) => (rejectBlob = reject)),
    );
    const { unmount } = renderPreview();
    await waitFor(() => expect(coreHarness.signal).not.toBeNull());

    unmount();
    await act(async () => {
      rejectBlob?.(notFoundError());
      await Promise.resolve();
    });
    expect(onAccessLost).not.toHaveBeenCalled();
  });

  it("shows a local retryable error when the core module cannot load", async () => {
    const user = userEvent.setup();
    coreHarness.fail = true;
    renderPreview();

    expect(
      await screen.findByText("预览组件加载失败，请重试或下载该版本。"),
    ).toBeInTheDocument();
    expect(screen.queryByTestId("preview-core")).toBeNull();

    coreHarness.fail = false;
    await user.click(screen.getByRole("button", { name: /^重\s*试$/ }));
    expect(await screen.findByTestId("preview-core")).toBeInTheDocument();
    expect(onAccessLost).not.toHaveBeenCalled();
  });
});
