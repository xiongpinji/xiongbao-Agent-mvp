import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { listVersions, uploadVersion, restoreVersion, downloadVersion } =
  vi.hoisted(() => ({
    listVersions: vi.fn(),
    uploadVersion: vi.fn(),
    restoreVersion: vi.fn(),
    downloadVersion: vi.fn(),
  }));

vi.mock("../../api/modules/projectAssets", async (importOriginal) => {
  const actual = await importOriginal<
    typeof import("../../api/modules/projectAssets")
  >();
  return {
    ...actual,
    projectAssetsApi: {
      listVersions,
      uploadVersion,
      restoreVersion,
      downloadVersion,
    },
  };
});

vi.mock("../../hooks/useServerTimezone", () => ({
  useServerTimezone: () => "UTC",
}));

vi.mock("../../utils/antdMessage", () => ({
  message: {
    success: vi.fn(),
    error: vi.fn(),
    warning: vi.fn(),
    info: vi.fn(),
  },
}));

import ProjectAssetVersions from "./ProjectAssetVersions";
import {
  PROJECT_ASSET_VERSIONS_PAGE_SIZE,
  type ProjectAssetNode,
  type ProjectAssetVersion,
} from "../../api/modules/projectAssets";
import { message } from "../../utils/antdMessage";

const nodeFile: ProjectAssetNode = {
  node_id: "file-1",
  parent_node_id: null,
  kind: "file",
  name: "需求说明.pdf",
  size_bytes: 2048,
  media_type: "application/pdf",
  created_at: 1_700_000_000,
  updated_at: 1_700_000_200,
};

const v1: ProjectAssetVersion = {
  version_id: "ver-1",
  size_bytes: 1024,
  sha256: "a".repeat(64),
  media_type: "application/pdf",
  uploaded_by: 42,
  created_at: 1_700_000_100,
  is_current: true,
};

const v2: ProjectAssetVersion = {
  version_id: "ver-2",
  size_bytes: 2048,
  sha256: "b".repeat(64),
  media_type: "application/pdf",
  uploaded_by: null,
  created_at: 1_700_000_200,
  is_current: false,
};

const v3: ProjectAssetVersion = {
  version_id: "ver-3",
  size_bytes: 4096,
  sha256: "c".repeat(64),
  media_type: "application/pdf",
  uploaded_by: null,
  created_at: 1_700_000_300,
  is_current: false,
};

function versionResponse(
  items: ProjectAssetVersion[],
  total = items.length,
  hasMore = false,
) {
  return {
    items,
    total,
    limit: PROJECT_ASSET_VERSIONS_PAGE_SIZE,
    offset: 0,
    has_more: hasMore,
  };
}

let createObjectURL: ReturnType<typeof vi.fn>;
let revokeObjectURL: ReturnType<typeof vi.fn>;
let clickSpy: ReturnType<typeof vi.spyOn>;
let clickedAnchor: { href: string; download: string } | null = null;

let onClose: ReturnType<typeof vi.fn>;
let onChanged: ReturnType<typeof vi.fn>;
let onAccessLost: ReturnType<typeof vi.fn>;

function renderVersions(
  overrides: { projectId?: string; node?: ProjectAssetNode } = {},
) {
  return render(
    <ProjectAssetVersions
      projectId={overrides.projectId ?? "p1"}
      node={overrides.node ?? nodeFile}
      onClose={onClose}
      onChanged={onChanged}
      onAccessLost={onAccessLost}
    />,
  );
}

function fileInput(): HTMLInputElement {
  const input = document.querySelector<HTMLInputElement>('input[type="file"]');
  expect(input).toBeTruthy();
  return input as HTMLInputElement;
}

function detailPane(): HTMLElement {
  return screen.getByTestId("project-asset-version-detail");
}

beforeEach(() => {
  vi.clearAllMocks();
  listVersions.mockResolvedValue(versionResponse([v2, v1]));
  uploadVersion.mockReset();
  restoreVersion.mockReset();
  downloadVersion.mockResolvedValue(
    new Blob(["old"], { type: "application/pdf" }),
  );
  onClose = vi.fn();
  onChanged = vi.fn();
  onAccessLost = vi.fn();

  createObjectURL = vi.fn(() => "blob:mock");
  revokeObjectURL = vi.fn();
  Object.defineProperty(URL, "createObjectURL", {
    writable: true,
    configurable: true,
    value: createObjectURL,
  });
  Object.defineProperty(URL, "revokeObjectURL", {
    writable: true,
    configurable: true,
    value: revokeObjectURL,
  });
  clickedAnchor = null;
  clickSpy = vi
    .spyOn(HTMLAnchorElement.prototype, "click")
    .mockImplementation(function (this: HTMLAnchorElement) {
      clickedAnchor = { href: this.href, download: this.download };
    });
});

afterEach(() => {
  clickSpy.mockRestore();
});

describe("ProjectAssetVersions against the PS-06B-1 contract", () => {
  it("treats a numeric uploader id as unverified metadata", async () => {
    listVersions.mockResolvedValueOnce(
      versionResponse([{ ...v1, uploaded_by: 42 }]),
    );
    renderVersions();

    await screen.findByTestId("project-asset-version-ver-1");
    expect(within(detailPane()).getByText("上传者未知")).toBeInTheDocument();
  });

  it("lists versions newest-first with ordinal labels and the current marker", async () => {
    renderVersions();

    const v2Row = await screen.findByTestId("project-asset-version-ver-2");
    const v1Row = screen.getByTestId("project-asset-version-ver-1");
    expect(within(v2Row).getByText("第 2 版")).toBeInTheDocument();
    expect(within(v1Row).getByText("第 1 版")).toBeInTheDocument();
    expect(within(v1Row).getByText("当前版")).toBeInTheDocument();
    expect(within(v2Row).queryByText("当前版")).toBeNull();
    expect(listVersions).toHaveBeenCalledWith("p1", "file-1", {
      limit: PROJECT_ASSET_VERSIONS_PAGE_SIZE,
      offset: 0,
    });

    // The current version is selected by default and shows safe metadata.
    expect(within(detailPane()).getByText("第 1 版")).toBeInTheDocument();
    expect(within(detailPane()).getByText("a".repeat(64))).toBeInTheDocument();
    expect(within(detailPane()).getByText("上传者未知")).toBeInTheDocument();
  });

  it("downloads the selected historical version with auth and revokes the one-shot URL", async () => {
    const user = userEvent.setup();
    renderVersions();
    const v2Row = await screen.findByTestId("project-asset-version-ver-2");
    await user.click(v2Row);
    expect(within(detailPane()).getByText("第 2 版")).toBeInTheDocument();

    await user.click(
      within(detailPane()).getByRole("button", { name: "下载该版本" }),
    );

    await waitFor(() =>
      expect(downloadVersion).toHaveBeenCalledWith("p1", "file-1", "ver-2"),
    );
    await waitFor(() => expect(createObjectURL).toHaveBeenCalled());
    expect(clickedAnchor).toEqual({
      href: "blob:mock",
      download: "需求说明.pdf",
    });
    await waitFor(() =>
      expect(revokeObjectURL).toHaveBeenCalledWith("blob:mock"),
    );
  });

  it("shows permission/file unavailable when a historical download 404s", async () => {
    const user = userEvent.setup();
    downloadVersion.mockRejectedValueOnce(
      new Error('404 - {"error":{"code":"NOT_FOUND","message":"not found"}}'),
    );
    renderVersions();
    await user.click(await screen.findByTestId("project-asset-version-ver-2"));
    await user.click(
      within(detailPane()).getByRole("button", { name: "下载该版本" }),
    );

    await waitFor(() => expect(onAccessLost).toHaveBeenCalled());
    expect(clickSpy).not.toHaveBeenCalled();
    expect(screen.queryByTestId("project-asset-version-ver-2")).toBeNull();
  });

  it("restores an old version, moves the current marker and refreshes the parent", async () => {
    const user = userEvent.setup();
    listVersions
      .mockResolvedValueOnce(versionResponse([v2, v1]))
      .mockResolvedValueOnce(
        versionResponse([
          { ...v2, is_current: true },
          { ...v1, is_current: false },
        ]),
      );
    restoreVersion.mockResolvedValueOnce({
      ...nodeFile,
      size_bytes: v2.size_bytes,
      version: {
        version_id: "ver-2",
        size_bytes: v2.size_bytes,
        sha256: v2.sha256,
        media_type: v2.media_type,
      },
    });
    renderVersions();
    await user.click(await screen.findByTestId("project-asset-version-ver-2"));
    await user.click(
      within(detailPane()).getByRole("button", { name: "恢复为当前版" }),
    );
    await user.click(await screen.findByRole("button", { name: /^恢\s*复$/ }));

    await waitFor(() =>
      expect(restoreVersion).toHaveBeenCalledWith("p1", "file-1", "ver-2"),
    );
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    await waitFor(() => expect(listVersions).toHaveBeenCalledTimes(2));
    await waitFor(() =>
      expect(
        within(screen.getByTestId("project-asset-version-ver-2")).getByText(
          "当前版",
        ),
      ).toBeInTheDocument(),
    );
    // Selection stays on the restored version, now the current one.
    expect(within(detailPane()).getByText("第 2 版")).toBeInTheDocument();
    expect(
      within(detailPane()).getByRole("button", { name: "恢复为当前版" }),
    ).toBeDisabled();
    expect(message.success).toHaveBeenCalledWith("已恢复为当前版");
  });

  it("keeps the selected version and retries a failed new-version upload", async () => {
    const user = userEvent.setup();
    listVersions
      .mockResolvedValueOnce(versionResponse([v2, v1]))
      .mockResolvedValueOnce(versionResponse([v3, v2, v1]))
      .mockResolvedValueOnce(versionResponse([v3, v2, v1]));
    uploadVersion
      .mockRejectedValueOnce(
        new Error(
          '413 - {"error":{"code":"PROJECT_ASSET_TOO_LARGE","message":"too large"}}',
        ),
      )
      .mockResolvedValueOnce({
        ...nodeFile,
        size_bytes: 4096,
        version: {
          version_id: "ver-3",
          size_bytes: 4096,
          sha256: "c".repeat(64),
          media_type: "application/pdf",
        },
      });
    renderVersions();
    await user.click(await screen.findByTestId("project-asset-version-ver-2"));

    const file = new File(["new"], "说明-v2.pdf", {
      type: "application/pdf",
    });
    fireEvent.change(fileInput(), { target: { files: [file] } });

    await waitFor(() => expect(uploadVersion).toHaveBeenCalledTimes(1));
    expect(uploadVersion.mock.calls[0][0]).toBe("p1");
    expect(uploadVersion.mock.calls[0][1]).toBe("file-1");
    expect((uploadVersion.mock.calls[0][2] as { file: File }).file.name).toBe(
      "说明-v2.pdf",
    );
    expect(await screen.findByText("too large")).toBeInTheDocument();
    expect(within(detailPane()).getByText("第 2 版")).toBeInTheDocument();
    expect(onChanged).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: /^重\s*试$/ }));
    await waitFor(() => expect(uploadVersion).toHaveBeenCalledTimes(2));
    expect((uploadVersion.mock.calls[1][2] as { file: File }).file).toBe(file);
    await waitFor(() => expect(onChanged).toHaveBeenCalledTimes(1));
    const v3Row = await screen.findByTestId("project-asset-version-ver-3");
    expect(within(v3Row).getByText("第 3 版")).toBeInTheDocument();
    await waitFor(() =>
      expect(within(detailPane()).getByText("第 3 版")).toBeInTheDocument(),
    );
    expect(message.success).toHaveBeenCalledWith("新版本已上传");
  });

  it("switches to read-only with the archived message after a 403 upload", async () => {
    uploadVersion.mockRejectedValueOnce(
      new Error('403 - {"error":{"code":"FORBIDDEN","message":"forbidden"}}'),
    );
    renderVersions();
    await screen.findByTestId("project-asset-version-ver-2");

    fireEvent.change(fileInput(), {
      target: { files: [new File(["x"], "归档.bin")] },
    });

    expect(
      await screen.findByText("项目已归档：版本只读，无法上传或恢复。"),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "上传新版本" })).toBeDisabled();
  });

  it("paginates with the server offset and derives ordinals from total", async () => {
    const user = userEvent.setup();
    listVersions
      .mockResolvedValueOnce(versionResponse([v3, v2], 3, true))
      .mockResolvedValueOnce(versionResponse([v1], 3, false));
    renderVersions();

    const v3Row = await screen.findByTestId("project-asset-version-ver-3");
    expect(within(v3Row).getByText("第 3 版")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "加载更多版本" }));

    await waitFor(() =>
      expect(listVersions).toHaveBeenLastCalledWith("p1", "file-1", {
        limit: PROJECT_ASSET_VERSIONS_PAGE_SIZE,
        offset: 2,
      }),
    );
    const v1Row = await screen.findByTestId("project-asset-version-ver-1");
    expect(within(v1Row).getByText("第 1 版")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "加载更多版本" })).toBeNull();
  });

  it("clears version content and reports access loss on a 404 list", async () => {
    listVersions.mockRejectedValueOnce(
      new Error('404 - {"error":{"code":"NOT_FOUND","message":"not found"}}'),
    );
    renderVersions();

    await waitFor(() => expect(onAccessLost).toHaveBeenCalled());
    expect(screen.queryByTestId("project-asset-version-ver-1")).toBeNull();
    expect(screen.getByText("选择一个版本查看详情。")).toBeInTheDocument();
  });

  it("ignores a late version list response after unmount", async () => {
    let resolveList: ((value: unknown) => void) | null = null;
    listVersions.mockImplementationOnce(
      () => new Promise((resolve) => (resolveList = resolve)),
    );
    const { unmount } = renderVersions();
    unmount();

    await act(async () => {
      resolveList?.(versionResponse([v1]));
      await Promise.resolve();
    });

    expect(onAccessLost).not.toHaveBeenCalled();
    expect(onChanged).not.toHaveBeenCalled();
  });

  it("ignores an old project's late upload 404 after switching projects", async () => {
    let rejectUpload: ((reason: unknown) => void) | null = null;
    uploadVersion.mockImplementationOnce(
      () => new Promise((_resolve, reject) => (rejectUpload = reject)),
    );
    const { rerender } = renderVersions();
    await screen.findByTestId("project-asset-version-ver-1");
    fireEvent.change(fileInput(), {
      target: { files: [new File(["x"], "new.pdf")] },
    });
    await waitFor(() => expect(uploadVersion).toHaveBeenCalledTimes(1));

    rerender(
      <ProjectAssetVersions
        projectId="p2"
        node={{ ...nodeFile, node_id: "file-2" }}
        onClose={onClose}
        onChanged={onChanged}
        onAccessLost={onAccessLost}
      />,
    );
    await act(async () => {
      rejectUpload?.(
        new Error(
          '404 - {"error":{"code":"NOT_FOUND","message":"old project"}}',
        ),
      );
      await Promise.resolve();
    });

    expect(onAccessLost).not.toHaveBeenCalled();
    expect(onChanged).not.toHaveBeenCalled();
    expect(listVersions).toHaveBeenLastCalledWith("p2", "file-2", {
      limit: PROJECT_ASSET_VERSIONS_PAGE_SIZE,
      offset: 0,
    });
  });

  it("shows the empty state when there are no versions", async () => {
    listVersions.mockResolvedValue(versionResponse([], 0, false));
    renderVersions();

    expect(await screen.findByText("暂无可显示的版本记录")).toBeInTheDocument();
    expect(screen.getByText("选择一个版本查看详情。")).toBeInTheDocument();
  });

  it("shows an unsupported-preview placeholder and renders names as inert text", async () => {
    const evilName = '<img src=x onerror="alert(1)">';
    listVersions.mockResolvedValue(versionResponse([v1]));
    renderVersions({ node: { ...nodeFile, name: evilName } });

    await screen.findByTestId("project-asset-version-ver-1");
    expect(screen.getByText("暂不支持页面内预览")).toBeInTheDocument();
    expect(document.querySelector("iframe")).toBeNull();
    expect(document.body.querySelector("img")).toBeNull();
    expect(document.body.textContent).toContain(evilName);
  });
});
