import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {
  afterEach,
  beforeAll,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";
import en from "../../locales/en.json";
import zh from "../../locales/zh.json";

const {
  list,
  usage,
  createFolder,
  upload,
  download,
  listVersions,
  uploadVersion,
  restoreVersion,
  downloadVersion,
  deleteToTrash,
  listTrash,
  restoreTrashed,
} = vi.hoisted(() => ({
  list: vi.fn(),
  usage: vi.fn(),
  createFolder: vi.fn(),
  upload: vi.fn(),
  download: vi.fn(),
  listVersions: vi.fn(),
  uploadVersion: vi.fn(),
  restoreVersion: vi.fn(),
  downloadVersion: vi.fn(),
  deleteToTrash: vi.fn(),
  listTrash: vi.fn(),
  restoreTrashed: vi.fn(),
}));

const { request, requestBlob, requestUpload } = vi.hoisted(() => ({
  request: vi.fn(),
  requestBlob: vi.fn(),
  requestUpload: vi.fn(),
}));

vi.mock("../../api/request", () => ({ request, requestBlob, requestUpload }));

vi.mock("../../api/modules/projectAssets", async (importOriginal) => {
  const actual = await importOriginal<
    typeof import("../../api/modules/projectAssets")
  >();
  return {
    ...actual,
    projectAssetsApi: {
      list,
      usage,
      createFolder,
      upload,
      download,
      listVersions,
      uploadVersion,
      restoreVersion,
      downloadVersion,
      deleteToTrash,
      listTrash,
      restoreTrashed,
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

vi.mock("../../components/EmptyState", () => ({
  EmptyState: ({
    title,
    description,
    actionLabel,
    onAction,
  }: {
    title?: string;
    description?: string;
    actionLabel?: string;
    onAction?: () => void;
  }) => (
    <section>
      <h2>{title}</h2>
      <p>{description}</p>
      {actionLabel && onAction && (
        <button onClick={onAction}>{actionLabel}</button>
      )}
    </section>
  ),
}));

import ProjectAssets from "./ProjectAssets";
import {
  PROJECT_ASSETS_PAGE_SIZE,
  PROJECT_ASSET_VERSIONS_PAGE_SIZE,
  type ProjectAssetNode,
  type ProjectAssetTrashItem,
  type ProjectAssetVersion,
} from "../../api/modules/projectAssets";
import { message } from "../../utils/antdMessage";

const folderDesign: ProjectAssetNode = {
  node_id: "folder-1",
  parent_node_id: null,
  kind: "folder",
  name: "设计稿",
  size_bytes: null,
  media_type: null,
  created_at: 1_700_000_000,
  updated_at: 1_700_000_100,
};

const fileSpec: ProjectAssetNode = {
  node_id: "file-1",
  parent_node_id: null,
  kind: "file",
  name: "需求说明.pdf",
  size_bytes: 2048,
  media_type: "application/pdf",
  created_at: 1_700_000_000,
  updated_at: 1_700_000_200,
};

const fileEmpty: ProjectAssetNode = {
  node_id: "file-2",
  parent_node_id: null,
  kind: "file",
  name: "空文件.txt",
  size_bytes: 0,
  media_type: "text/plain",
  created_at: 1_700_000_000,
  updated_at: 1_700_000_300,
};

const versionCurrent: ProjectAssetVersion = {
  version_id: "ver-1",
  size_bytes: 2048,
  sha256: "a".repeat(64),
  media_type: "application/pdf",
  uploaded_by: 42,
  created_at: 1_700_000_200,
  is_current: true,
};

function versionListResponse(items: ProjectAssetVersion[]) {
  return {
    items,
    total: items.length,
    limit: PROJECT_ASSET_VERSIONS_PAGE_SIZE,
    offset: 0,
    has_more: false,
  };
}

function listResponse(
  items: ProjectAssetNode[],
  options: { total?: number; hasMore?: boolean } = {},
) {
  return {
    items,
    total: options.total ?? items.length,
    limit: PROJECT_ASSETS_PAGE_SIZE,
    offset: 0,
    has_more: options.hasMore ?? false,
  };
}

/** 042 trash root: a file the current member deleted and may restore. */
const trashFile: ProjectAssetTrashItem = {
  node_id: "trash-1",
  parent_node_id: null,
  kind: "file",
  name: "旧方案.pdf",
  original_path: "项目文件/设计稿",
  deleted_at: 1_700_000_500,
  deleted_by_name: "张三",
  can_restore: true,
};

/** 042 trash root without restore permission: button must not be actionable. */
const trashFolderLocked: ProjectAssetTrashItem = {
  node_id: "trash-2",
  parent_node_id: null,
  kind: "folder",
  name: "废弃资料",
  original_path: "项目文件",
  deleted_at: 1_700_000_600,
  deleted_by_name: null,
  can_restore: false,
};

function trashResponse(
  items: ProjectAssetTrashItem[],
  options: { total?: number; hasMore?: boolean; offset?: number } = {},
) {
  return {
    items,
    total: options.total ?? items.length,
    limit: PROJECT_ASSETS_PAGE_SIZE,
    offset: options.offset ?? 0,
    has_more: options.hasMore ?? false,
  };
}

let createObjectURL: ReturnType<typeof vi.fn>;
let revokeObjectURL: ReturnType<typeof vi.fn>;
let clickSpy: ReturnType<typeof vi.spyOn>;
let clickedAnchor: { href: string; download: string } | null = null;

function renderAssets(projectId = "p1") {
  return render(<ProjectAssets projectId={projectId} />);
}

function fileInput(container: HTMLElement): HTMLInputElement {
  const input = container.querySelector<HTMLInputElement>('input[type="file"]');
  expect(input).toBeTruthy();
  return input as HTMLInputElement;
}

beforeEach(() => {
  vi.clearAllMocks();
  list.mockResolvedValue(listResponse([folderDesign, fileSpec]));
  usage.mockResolvedValue({ file_count: 2, total_bytes: 2048 });
  createFolder.mockReset();
  upload.mockReset();
  download.mockResolvedValue(new Blob(["bytes"], { type: "application/pdf" }));
  listVersions.mockReset();
  listVersions.mockResolvedValue(versionListResponse([versionCurrent]));
  uploadVersion.mockReset();
  restoreVersion.mockReset();
  downloadVersion.mockReset();
  deleteToTrash.mockReset();
  deleteToTrash.mockResolvedValue(undefined);
  listTrash.mockReset();
  listTrash.mockResolvedValue(trashResponse([]));
  restoreTrashed.mockReset();
  restoreTrashed.mockResolvedValue({ ...fileSpec, node_id: "trash-1" });

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

describe("ProjectAssets against the PS-06A / 023A contract", () => {
  it("loads the hidden root with server-side params, usage and safe metadata", async () => {
    renderAssets("p1");

    expect(await screen.findByText("需求说明.pdf")).toBeInTheDocument();
    expect(screen.getByText("设计稿")).toBeInTheDocument();
    expect(list).toHaveBeenCalledWith("p1", {
      parentId: null,
      q: "",
      kind: undefined,
      limit: PROJECT_ASSETS_PAGE_SIZE,
      offset: 0,
    });
    expect(usage).toHaveBeenCalledWith("p1");
    expect(
      screen.getByText(/可见文件（仅当前版本）：2 个/),
    ).toBeInTheDocument();
    expect(screen.getByText(/共 2 项/)).toBeInTheDocument();
    expect(screen.getByText(/application\/pdf/)).toBeInTheDocument();
  });

  it("allows a zero-byte file to display and download", async () => {
    const user = userEvent.setup();
    list.mockResolvedValue(listResponse([fileEmpty]));
    renderAssets("p1");

    const row = await screen.findByTestId("project-asset-file-2");
    expect(within(row).getByText("空文件.txt")).toBeInTheDocument();
    expect(within(row).getByText(/0 B/)).toBeInTheDocument();

    await user.click(within(row).getByRole("button", { name: "下载" }));
    await waitFor(() => expect(download).toHaveBeenCalledWith("p1", "file-2"));
    await waitFor(() => expect(revokeObjectURL).toHaveBeenCalled());
  });

  it("enters a folder, shows the breadcrumb and returns to the root", async () => {
    const user = userEvent.setup();
    list.mockImplementation(
      (_projectId: string, params: { parentId: string | null }) =>
        Promise.resolve(
          params.parentId === "folder-1"
            ? listResponse([{ ...fileSpec, parent_node_id: "folder-1" }])
            : listResponse([folderDesign]),
        ),
    );
    renderAssets("p1");
    await screen.findByText("设计稿");

    await user.click(
      screen.getByRole("button", { name: "进入文件夹：设计稿" }),
    );
    await waitFor(() =>
      expect(list).toHaveBeenLastCalledWith(
        "p1",
        expect.objectContaining({ parentId: "folder-1", offset: 0 }),
      ),
    );
    expect(await screen.findByText("需求说明.pdf")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "进入文件夹：设计稿" }),
    ).toBeNull();
    expect(screen.getByText("设计稿")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "项目文件" }));
    await waitFor(() =>
      expect(list).toHaveBeenLastCalledWith(
        "p1",
        expect.objectContaining({ parentId: null, offset: 0 }),
      ),
    );
    expect(await screen.findByText("设计稿")).toBeInTheDocument();
  });

  it("clears stale rows immediately when the search changes and ignores a late page", async () => {
    const user = userEvent.setup();
    renderAssets("p1");
    await screen.findByText("需求说明.pdf");

    let resolveSearch:
      | ((value: ReturnType<typeof listResponse>) => void)
      | null = null;
    list.mockImplementationOnce(
      () => new Promise((resolve) => (resolveSearch = resolve)),
    );
    await user.type(screen.getByPlaceholderText("搜索名称"), "报告{enter}");

    expect(list).toHaveBeenLastCalledWith(
      "p1",
      expect.objectContaining({ q: "报告", offset: 0 }),
    );
    expect(screen.queryByText("需求说明.pdf")).toBeNull();

    await act(async () => {
      resolveSearch?.(listResponse([{ ...fileSpec, name: "报告.pdf" }]));
      await Promise.resolve();
    });
    expect(await screen.findByText("报告.pdf")).toBeInTheDocument();
  });

  it("filters by kind on the server and resets the offset", async () => {
    list.mockResolvedValueOnce(listResponse([folderDesign, fileSpec]));
    renderAssets("p1");
    await screen.findByText("需求说明.pdf");

    list.mockResolvedValueOnce(listResponse([fileSpec]));
    fireEvent.click(screen.getByRole("radio", { name: "文件" }));

    await waitFor(() =>
      expect(list).toHaveBeenLastCalledWith(
        "p1",
        expect.objectContaining({ kind: "file", offset: 0 }),
      ),
    );
    await waitFor(() => expect(screen.queryByText("设计稿")).toBeNull());
  });

  it("pages with the server offset and dedupes node ids across pages", async () => {
    const user = userEvent.setup();
    list.mockResolvedValueOnce(
      listResponse([fileSpec], { total: 2, hasMore: true }),
    );
    renderAssets("p1");
    await screen.findByText("需求说明.pdf");

    list.mockResolvedValueOnce(
      listResponse([fileSpec, fileEmpty], { total: 2, hasMore: false }),
    );
    await user.click(screen.getByRole("button", { name: "加载更多" }));

    await waitFor(() =>
      expect(list).toHaveBeenLastCalledWith(
        "p1",
        expect.objectContaining({ offset: 1, limit: PROJECT_ASSETS_PAGE_SIZE }),
      ),
    );
    expect(await screen.findByText("空文件.txt")).toBeInTheDocument();
    expect(screen.getAllByText("需求说明.pdf")).toHaveLength(1);
    expect(
      screen.queryByRole("button", { name: "加载更多" }),
    ).not.toBeInTheDocument();
  });

  it("downloads through requestBlob with a one-shot anchor and revokes the URL", async () => {
    const user = userEvent.setup();
    renderAssets("p1");
    const row = await screen.findByTestId("project-asset-file-1");

    await user.click(within(row).getByRole("button", { name: "下载" }));

    await waitFor(() => expect(download).toHaveBeenCalledWith("p1", "file-1"));
    await waitFor(() => expect(createObjectURL).toHaveBeenCalled());
    await waitFor(() => expect(clickSpy).toHaveBeenCalled());
    expect(clickedAnchor).toEqual({
      href: "blob:mock",
      download: "需求说明.pdf",
    });
    await waitFor(() =>
      expect(revokeObjectURL).toHaveBeenCalledWith("blob:mock"),
    );
  });

  it("creates a folder in the current directory and refreshes list and usage", async () => {
    const user = userEvent.setup();
    renderAssets("p1");
    await screen.findByText("需求说明.pdf");

    const listCalls = list.mock.calls.length;
    const usageCalls = usage.mock.calls.length;
    await user.click(screen.getByRole("button", { name: "新建文件夹" }));
    const input = await screen.findByPlaceholderText(
      "输入文件夹名称（1–120 字符）",
    );
    await user.type(input, "  设计稿  ");
    await user.click(screen.getByRole("button", { name: /^创\s*建$/ }));

    await waitFor(() =>
      expect(createFolder).toHaveBeenCalledWith("p1", {
        parentId: null,
        name: "设计稿",
      }),
    );
    await waitFor(() =>
      expect(list.mock.calls.length).toBeGreaterThan(listCalls),
    );
    await waitFor(() =>
      expect(usage.mock.calls.length).toBeGreaterThan(usageCalls),
    );
    expect(message.success).toHaveBeenCalledWith("文件夹已创建");
    expect(
      screen.queryByPlaceholderText("输入文件夹名称（1–120 字符）"),
    ).toBeNull();
  });

  it("keeps the folder dialog and name after a 409 conflict", async () => {
    const user = userEvent.setup();
    createFolder.mockRejectedValueOnce(
      new Error(
        '409 - {"error":{"code":"PROJECT_ASSET_NAME_CONFLICT","message":"conflict"}}',
      ),
    );
    renderAssets("p1");
    await screen.findByText("需求说明.pdf");

    await user.click(screen.getByRole("button", { name: "新建文件夹" }));
    const input = await screen.findByPlaceholderText(
      "输入文件夹名称（1–120 字符）",
    );
    await user.type(input, "设计稿");
    await user.click(screen.getByRole("button", { name: /^创\s*建$/ }));

    // The i18n test mock resolves apiErrors.<code> to the server message.
    expect(await screen.findByText("conflict")).toBeInTheDocument();
    expect(input).toHaveValue("设计稿");
    expect(message.success).not.toHaveBeenCalled();
  });

  it("uploads a selected file with the current parent and refreshes", async () => {
    upload.mockResolvedValueOnce({ ...fileSpec, version: {} });
    const { container } = renderAssets("p1");
    await screen.findByText("需求说明.pdf");

    const listCalls = list.mock.calls.length;
    const file = new File(["hello"], "说明.txt", { type: "text/plain" });
    fireEvent.change(fileInput(container), { target: { files: [file] } });

    await waitFor(() => expect(upload).toHaveBeenCalledTimes(1));
    const params = upload.mock.calls[0][1] as {
      parentId: string | null;
      file: File;
    };
    expect(upload.mock.calls[0][0]).toBe("p1");
    expect(params.parentId).toBeNull();
    expect(params.file.name).toBe("说明.txt");
    await waitFor(() =>
      expect(list.mock.calls.length).toBeGreaterThan(listCalls),
    );
    expect(message.success).toHaveBeenCalledWith("文件已上传");
    expect(screen.queryByText("文件超过单个文件大小上限。")).toBeNull();
  });

  it("prevents a folder mutation from superseding an in-flight upload", async () => {
    let resolveUpload: ((value: unknown) => void) | null = null;
    upload.mockImplementationOnce(
      () => new Promise((resolve) => (resolveUpload = resolve)),
    );
    const { container } = renderAssets("p1");
    await screen.findByText("需求说明.pdf");

    fireEvent.change(fileInput(container), {
      target: { files: [new File(["hello"], "上传中.txt")] },
    });
    await waitFor(() => expect(upload).toHaveBeenCalledTimes(1));
    expect(screen.getByRole("button", { name: "新建文件夹" })).toBeDisabled();

    await act(async () => {
      resolveUpload?.({ ...fileSpec, version: {} });
      await Promise.resolve();
    });
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "新建文件夹" })).toBeEnabled(),
    );
  });

  it("shows the per-file size limit message on 413 without success", async () => {
    upload.mockRejectedValueOnce(
      new Error(
        '413 - {"error":{"code":"PROJECT_ASSET_TOO_LARGE","message":"too large"}}',
      ),
    );
    const { container } = renderAssets("p1");
    await screen.findByText("需求说明.pdf");

    fireEvent.change(fileInput(container), {
      target: { files: [new File(["x"], "big.bin")] },
    });

    // The i18n test mock resolves apiErrors.<code> to the server message.
    expect(await screen.findByText("too large")).toBeInTheDocument();
    expect(message.success).not.toHaveBeenCalled();
  });

  it("explains an archived-project 403 write without faking success", async () => {
    const user = userEvent.setup();
    createFolder.mockRejectedValueOnce(
      new Error('403 - {"error":{"code":"FORBIDDEN","message":"forbidden"}}'),
    );
    renderAssets("p1");
    await screen.findByText("需求说明.pdf");

    await user.click(screen.getByRole("button", { name: "新建文件夹" }));
    await user.type(
      await screen.findByPlaceholderText("输入文件夹名称（1–120 字符）"),
      "归档后不可写",
    );
    await user.click(screen.getByRole("button", { name: /^创\s*建$/ }));

    expect(
      await screen.findByText(
        "项目已归档：资产当前只读，无法新建文件夹或上传。",
      ),
    ).toBeInTheDocument();
    expect(message.success).not.toHaveBeenCalled();
  });

  it("clears rows and usage and hides writes when a refresh returns 404", async () => {
    const user = userEvent.setup();
    renderAssets("p1");
    await screen.findByText("需求说明.pdf");

    list.mockRejectedValueOnce(
      new Error('404 - {"error":{"code":"NOT_FOUND","message":"not found"}}'),
    );
    await user.click(screen.getByRole("button", { name: "刷新资产" }));

    await waitFor(() => expect(screen.queryByText("需求说明.pdf")).toBeNull());
    expect(
      await screen.findByText("项目不存在或你无权访问"),
    ).toBeInTheDocument();
    expect(screen.queryByText(/可见文件/)).toBeNull();
    expect(screen.queryByRole("button", { name: "新建文件夹" })).toBeNull();
    expect(screen.queryByRole("button", { name: "上传" })).toBeNull();
    expect(screen.queryByRole("button", { name: "回收站" })).toBeNull();
    expect(screen.queryByRole("button", { name: "添加到任务" })).toBeNull();
  });

  it("recovers from a general network failure with retry", async () => {
    const user = userEvent.setup();
    list.mockRejectedValueOnce(
      new Error("503 - service temporarily unavailable"),
    );
    renderAssets("p1");

    expect(
      await screen.findByText("service temporarily unavailable"),
    ).toBeInTheDocument();

    list.mockResolvedValueOnce(listResponse([fileSpec]));
    await user.click(screen.getByRole("button", { name: "重试" }));
    expect(await screen.findByText("需求说明.pdf")).toBeInTheDocument();
  });

  it("ignores a late list response from the previous project", async () => {
    let resolveP1: ((value: ReturnType<typeof listResponse>) => void) | null =
      null;
    list.mockImplementationOnce(
      () => new Promise((resolve) => (resolveP1 = resolve)),
    );
    const { rerender } = renderAssets("p1");

    list.mockResolvedValueOnce(
      listResponse([{ ...fileSpec, name: "新项目.pdf" }]),
    );
    usage.mockResolvedValue({ file_count: 1, total_bytes: 1 });
    rerender(<ProjectAssets projectId="p2" />);
    expect(await screen.findByText("新项目.pdf")).toBeInTheDocument();

    await act(async () => {
      resolveP1?.(
        listResponse([{ ...fileSpec, name: "旧项目.pdf" }], { total: 99 }),
      );
      await Promise.resolve();
    });

    expect(screen.queryByText("旧项目.pdf")).toBeNull();
    expect(screen.getByText("新项目.pdf")).toBeInTheDocument();
    expect(screen.queryByText(/共 99 项/)).toBeNull();
  });

  it("ignores a late upload that belongs to the previous project", async () => {
    let resolveUpload: ((value: unknown) => void) | null = null;
    upload.mockImplementationOnce(
      () => new Promise((resolve) => (resolveUpload = resolve)),
    );
    usage.mockResolvedValue({ file_count: 1, total_bytes: 1 });
    const { container, rerender } = renderAssets("p1");
    await screen.findByText("需求说明.pdf");

    fireEvent.change(fileInput(container), {
      target: { files: [new File(["x"], "旧上传.txt")] },
    });
    await waitFor(() => expect(upload).toHaveBeenCalledTimes(1));

    list.mockResolvedValue(listResponse([{ ...fileSpec, name: "新项目.pdf" }]));
    rerender(<ProjectAssets projectId="p2" />);
    await screen.findByText("新项目.pdf");
    const newProjectCalls = list.mock.calls.filter(
      (call) => call[0] === "p2",
    ).length;

    await act(async () => {
      resolveUpload?.({ ...fileSpec, version: {} });
      await Promise.resolve();
    });

    expect(message.success).not.toHaveBeenCalled();
    expect(list.mock.calls.filter((call) => call[0] === "p2")).toHaveLength(
      newProjectCalls,
    );
  });

  it("renders a script-shaped filename as inert text", async () => {
    list.mockResolvedValue(
      listResponse([
        { ...fileSpec, node_id: "evil", name: '<script>alert("x")</script>' },
      ]),
    );
    const { container } = renderAssets("p1");

    const rendered = await screen.findByText('<script>alert("x")</script>');
    expect(rendered.querySelector("script")).toBeNull();
    expect(container.querySelectorAll("script")).toHaveLength(0);
  });

  it("keeps later-batch controls unavailable while trash is live", async () => {
    renderAssets("p1");
    await screen.findByText("需求说明.pdf");

    const addToTask = screen.getByRole("button", { name: "添加到任务" });
    expect(addToTask).toBeDisabled();
    // 042 ships recoverable trash only — no permanent delete or empty-trash.
    expect(screen.queryByRole("button", { name: /永久删除/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /清空回收站/ })).toBeNull();
    expect(screen.getByRole("button", { name: "回收站" })).toBeEnabled();
    expect(
      screen.getByText(
        "版本管理与回收站已开放；「添加到任务」将在后续批次提供。",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "版本管理" })).toBeNull();

    fireEvent.click(
      within(screen.getByTestId("project-asset-file-1")).getByRole("button", {
        name: "更多操作",
      }),
    );
    expect(await screen.findByRole("menu")).toBeInTheDocument();
    expect(screen.getByText("版本管理")).toBeInTheDocument();
    expect(screen.getByText("移入回收站")).toBeInTheDocument();
  });

  it("opens version management from a file row", async () => {
    const user = userEvent.setup();
    renderAssets("p1");
    const fileRow = await screen.findByTestId("project-asset-file-1");

    await user.click(within(fileRow).getByRole("button", { name: "更多操作" }));
    await user.click(
      within(await screen.findByRole("menu")).getByText("版本管理"),
    );

    expect(
      await screen.findByText("版本管理：需求说明.pdf"),
    ).toBeInTheDocument();
    expect(listVersions).toHaveBeenCalledWith("p1", "file-1", {
      limit: PROJECT_ASSET_VERSIONS_PAGE_SIZE,
      offset: 0,
    });
    expect(
      await screen.findByTestId("project-asset-version-ver-1"),
    ).toBeInTheDocument();
    expect((await screen.findAllByText("当前版")).length).toBeGreaterThan(0);
  });

  it("clears open version details when the project switches", async () => {
    const user = userEvent.setup();
    const { rerender } = renderAssets("p1");
    await screen.findByText("需求说明.pdf");
    await user.click(
      within(screen.getByTestId("project-asset-file-1")).getByRole("button", {
        name: "更多操作",
      }),
    );
    await user.click(
      within(await screen.findByRole("menu")).getByText("版本管理"),
    );
    expect(
      await screen.findByText("版本管理：需求说明.pdf"),
    ).toBeInTheDocument();

    list.mockResolvedValue(listResponse([{ ...fileSpec, name: "新项目.pdf" }]));
    usage.mockResolvedValue({ file_count: 1, total_bytes: 1 });
    rerender(<ProjectAssets projectId="p2" />);

    expect(await screen.findByText("新项目.pdf")).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.queryByText("版本管理：需求说明.pdf")).toBeNull(),
    );
    expect(screen.queryByTestId("project-asset-version-ver-1")).toBeNull();
  });

  it("rechecks project assets after one historical version returns 404", async () => {
    const user = userEvent.setup();
    const oldVersion = {
      ...versionCurrent,
      version_id: "ver-old",
      is_current: false,
    };
    listVersions.mockResolvedValue(
      versionListResponse([versionCurrent, oldVersion]),
    );
    downloadVersion.mockRejectedValueOnce(
      new Error(
        '404 - {"error":{"code":"NOT_FOUND","message":"missing object"}}',
      ),
    );
    renderAssets();
    await screen.findByText("需求说明.pdf");
    await user.click(
      within(screen.getByTestId("project-asset-file-1")).getByRole("button", {
        name: "更多操作",
      }),
    );
    await user.click(
      within(await screen.findByRole("menu")).getByText("版本管理"),
    );
    await user.click(
      await screen.findByTestId("project-asset-version-ver-old"),
    );
    await user.click(
      within(screen.getByTestId("project-asset-version-detail")).getByRole(
        "button",
        { name: "下载该版本" },
      ),
    );

    await waitFor(() => expect(list).toHaveBeenCalledTimes(2));
    expect(await screen.findByText("需求说明.pdf")).toBeInTheDocument();
    expect(screen.queryByText("项目不存在或你无权访问")).toBeNull();
  });

  it("keeps asset rows hidden when the version 404 recheck confirms revocation", async () => {
    list
      .mockResolvedValueOnce(listResponse([fileSpec]))
      .mockRejectedValueOnce(
        new Error('404 - {"error":{"code":"NOT_FOUND","message":"revoked"}}'),
      );
    listVersions.mockRejectedValueOnce(
      new Error('404 - {"error":{"code":"NOT_FOUND","message":"revoked"}}'),
    );
    const user = userEvent.setup();
    renderAssets();
    await screen.findByText("需求说明.pdf");
    await user.click(
      within(screen.getByTestId("project-asset-file-1")).getByRole("button", {
        name: "更多操作",
      }),
    );
    await user.click(
      within(await screen.findByRole("menu")).getByText("版本管理"),
    );

    expect(
      await screen.findByText("项目不存在或你无权访问"),
    ).toBeInTheDocument();
    expect(screen.queryByText("需求说明.pdf")).toBeNull();
    expect(list).toHaveBeenCalledTimes(2);
  });
});

describe("ProjectAssets 042 recoverable trash", () => {
  async function openDeleteDialog(
    user: ReturnType<typeof userEvent.setup>,
    testId: string,
  ) {
    const row = await screen.findByTestId(testId);
    await user.click(within(row).getByRole("button", { name: "更多操作" }));
    await user.click(
      within(await screen.findByRole("menu")).getByText("移入回收站"),
    );
    return screen.findByRole("dialog");
  }

  async function openTrashDialog(user: ReturnType<typeof userEvent.setup>) {
    await user.click(screen.getByRole("button", { name: "回收站" }));
    return screen.findByRole("dialog");
  }

  it("opens the delete confirmation from the row menu and cancel mutates nothing", async () => {
    const user = userEvent.setup();
    renderAssets("p1");
    const dialog = await openDeleteDialog(user, "project-asset-file-1");

    expect(
      within(dialog).getByText(/将「需求说明.pdf」移入回收站/),
    ).toBeInTheDocument();
    expect(
      within(dialog).getByText(/之后可以从回收站恢复/),
    ).toBeInTheDocument();
    expect(deleteToTrash).not.toHaveBeenCalled();

    await user.click(within(dialog).getByRole("button", { name: /取\s*消/ }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(deleteToTrash).not.toHaveBeenCalled();
    expect(message.success).not.toHaveBeenCalled();
    expect(screen.getByTestId("project-asset-file-1")).toBeInTheDocument();
  });

  it("confirms delete with a real DELETE call and refreshes list and usage", async () => {
    const user = userEvent.setup();
    renderAssets("p1");
    const listCalls = list.mock.calls.length;
    const usageCalls = usage.mock.calls.length;
    const dialog = await openDeleteDialog(user, "project-asset-file-1");

    await user.click(
      within(dialog).getByRole("button", { name: "移入回收站" }),
    );

    await waitFor(() =>
      expect(deleteToTrash).toHaveBeenCalledWith("p1", "file-1"),
    );
    await waitFor(() =>
      expect(list.mock.calls.length).toBeGreaterThan(listCalls),
    );
    await waitFor(() =>
      expect(usage.mock.calls.length).toBeGreaterThan(usageCalls),
    );
    expect(message.success).toHaveBeenCalledWith("已移入回收站");
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("offers only the trash action on folder rows, with the subtree note", async () => {
    const user = userEvent.setup();
    renderAssets("p1");
    const folderRow = await screen.findByTestId("project-asset-folder-1");

    await user.click(
      within(folderRow).getByRole("button", { name: "更多操作" }),
    );
    const menu = await screen.findByRole("menu");
    // Folder rows never expose version management or download.
    expect(within(menu).queryByText("版本管理")).toBeNull();
    expect(within(menu).queryByText("下载")).toBeNull();
    await user.click(within(menu).getByText("移入回收站"));

    const dialog = await screen.findByRole("dialog");
    expect(
      within(dialog).getByText(/文件夹会连同其中当前可见的内容一起移入回收站/),
    ).toBeInTheDocument();
    await user.click(
      within(dialog).getByRole("button", { name: "移入回收站" }),
    );

    await waitFor(() =>
      expect(deleteToTrash).toHaveBeenCalledWith("p1", "folder-1"),
    );
    expect(message.success).toHaveBeenCalledWith("已移入回收站");
  });

  it("keeps the row and shows a retryable error when delete fails", async () => {
    const user = userEvent.setup();
    deleteToTrash.mockRejectedValueOnce(new Error("500 - internal boom"));
    renderAssets("p1");
    const listCalls = list.mock.calls.length;
    const dialog = await openDeleteDialog(user, "project-asset-file-1");

    await user.click(
      within(dialog).getByRole("button", { name: "移入回收站" }),
    );

    expect(
      await within(dialog).findByText("internal boom"),
    ).toBeInTheDocument();
    expect(message.success).not.toHaveBeenCalled();
    // The failed request never removes the row or reloads the directory.
    expect(screen.getByTestId("project-asset-file-1")).toBeInTheDocument();
    expect(list.mock.calls.length).toBe(listCalls);

    // The dialog stays open; retrying after a transient failure succeeds.
    await user.click(
      within(dialog).getByRole("button", { name: "移入回收站" }),
    );
    await waitFor(() => expect(deleteToTrash).toHaveBeenCalledTimes(2));
    expect(message.success).toHaveBeenCalledWith("已移入回收站");
  });

  it("explains an archived or unauthorized 403 delete without dropping the row", async () => {
    const user = userEvent.setup();
    deleteToTrash.mockRejectedValueOnce(
      new Error('403 - {"error":{"code":"FORBIDDEN","message":"forbidden"}}'),
    );
    renderAssets("p1");
    const dialog = await openDeleteDialog(user, "project-asset-file-1");

    await user.click(
      within(dialog).getByRole("button", { name: "移入回收站" }),
    );

    expect(
      await within(dialog).findByText("项目已归档，或你没有管理该资产的权限。"),
    ).toBeInTheDocument();
    expect(message.success).not.toHaveBeenCalled();
    expect(screen.getByTestId("project-asset-file-1")).toBeInTheDocument();
  });

  it("lists trash roots with safe metadata and gates restore on can_restore", async () => {
    const user = userEvent.setup();
    listTrash.mockResolvedValue(trashResponse([trashFile, trashFolderLocked]));
    renderAssets("p1");
    await screen.findByText("需求说明.pdf");

    const dialog = await openTrashDialog(user);

    await waitFor(() =>
      expect(listTrash).toHaveBeenCalledWith("p1", {
        limit: PROJECT_ASSETS_PAGE_SIZE,
        offset: 0,
      }),
    );
    const row1 = await within(dialog).findByTestId(
      "project-asset-trash-trash-1",
    );
    expect(within(row1).getByText("旧方案.pdf")).toBeInTheDocument();
    expect(
      within(row1).getByText(/原位置：项目文件\/设计稿/),
    ).toBeInTheDocument();
    expect(within(row1).getByText(/删除人：张三/)).toBeInTheDocument();
    expect(within(row1).getByRole("button", { name: "恢复" })).toBeEnabled();
    // A trash row is inert: no download, no open, no version actions.
    expect(within(row1).queryByRole("button", { name: "下载" })).toBeNull();
    expect(within(row1).queryByRole("button", { name: "更多操作" })).toBeNull();
    expect(download).not.toHaveBeenCalled();

    const row2 = within(dialog).getByTestId("project-asset-trash-trash-2");
    expect(within(row2).getByText(/删除人未知/)).toBeInTheDocument();
    expect(within(row2).getByRole("button", { name: "恢复" })).toBeDisabled();
  });

  it("restores a trash root and refreshes trash, directory and usage", async () => {
    const user = userEvent.setup();
    listTrash.mockResolvedValue(trashResponse([trashFile]));
    renderAssets("p1");
    await screen.findByText("需求说明.pdf");
    const dialog = await openTrashDialog(user);
    const row = await within(dialog).findByTestId(
      "project-asset-trash-trash-1",
    );
    const listCalls = list.mock.calls.length;
    const usageCalls = usage.mock.calls.length;
    const trashCalls = listTrash.mock.calls.length;

    await user.click(within(row).getByRole("button", { name: "恢复" }));

    await waitFor(() =>
      expect(restoreTrashed).toHaveBeenCalledWith("p1", "trash-1"),
    );
    await waitFor(() =>
      expect(list.mock.calls.length).toBeGreaterThan(listCalls),
    );
    await waitFor(() =>
      expect(usage.mock.calls.length).toBeGreaterThan(usageCalls),
    );
    await waitFor(() =>
      expect(listTrash.mock.calls.length).toBeGreaterThan(trashCalls),
    );
    expect(message.success).toHaveBeenCalledWith("已恢复到原位置");
  });

  it("keeps the trash row and explains a same-name restore conflict", async () => {
    const user = userEvent.setup();
    listTrash.mockResolvedValue(trashResponse([trashFile]));
    restoreTrashed.mockRejectedValueOnce(
      new Error(
        '409 - {"error":{"code":"PROJECT_ASSET_NAME_CONFLICT","message":"conflict"}}',
      ),
    );
    renderAssets("p1");
    await screen.findByText("需求说明.pdf");
    const dialog = await openTrashDialog(user);
    const row = await within(dialog).findByTestId(
      "project-asset-trash-trash-1",
    );

    await user.click(within(row).getByRole("button", { name: "恢复" }));

    expect(
      await within(dialog).findByText(/原位置已有同名文件或文件夹/),
    ).toBeInTheDocument();
    expect(within(dialog).getByText(/请先处理同名项/)).toBeInTheDocument();
    expect(message.success).not.toHaveBeenCalled();
    // 409 keeps the trash row exactly where it was.
    expect(
      within(dialog).getByTestId("project-asset-trash-trash-1"),
    ).toBeInTheDocument();
  });

  it("keeps the trash row and asks to restore the parent first on a parent-in-trash 409", async () => {
    const user = userEvent.setup();
    listTrash.mockResolvedValue(trashResponse([trashFile]));
    restoreTrashed.mockRejectedValueOnce(
      new Error(
        '409 - {"error":{"code":"PROJECT_ASSET_PARENT_IN_TRASH","message":"parent in trash"}}',
      ),
    );
    renderAssets("p1");
    await screen.findByText("需求说明.pdf");
    const dialog = await openTrashDialog(user);
    const row = await within(dialog).findByTestId(
      "project-asset-trash-trash-1",
    );

    await user.click(within(row).getByRole("button", { name: "恢复" }));

    expect(
      await within(dialog).findByText(/原文件夹仍在回收站：请先恢复父级/),
    ).toBeInTheDocument();
    expect(message.success).not.toHaveBeenCalled();
    expect(
      within(dialog).getByTestId("project-asset-trash-trash-1"),
    ).toBeInTheDocument();
  });

  it("pages the trash list with server offsets", async () => {
    const user = userEvent.setup();
    listTrash.mockResolvedValueOnce(
      trashResponse([trashFile], { total: 2, hasMore: true }),
    );
    renderAssets("p1");
    await screen.findByText("需求说明.pdf");
    const dialog = await openTrashDialog(user);
    await within(dialog).findByTestId("project-asset-trash-trash-1");

    listTrash.mockResolvedValueOnce(
      trashResponse([trashFolderLocked], { total: 2, offset: 1 }),
    );
    await user.click(within(dialog).getByRole("button", { name: "加载更多" }));

    await waitFor(() =>
      expect(listTrash).toHaveBeenLastCalledWith("p1", {
        limit: PROJECT_ASSETS_PAGE_SIZE,
        offset: 1,
      }),
    );
    expect(
      await within(dialog).findByTestId("project-asset-trash-trash-2"),
    ).toBeInTheDocument();
    expect(
      within(dialog).getByTestId("project-asset-trash-trash-1"),
    ).toBeInTheDocument();
  });

  it("closes the delete confirmation on project switch without mutating", async () => {
    const user = userEvent.setup();
    const { rerender } = renderAssets("p1");
    await screen.findByText("需求说明.pdf");
    await openDeleteDialog(user, "project-asset-file-1");
    expect(await screen.findByRole("dialog")).toBeInTheDocument();

    list.mockResolvedValue(listResponse([{ ...fileSpec, name: "新项目.pdf" }]));
    usage.mockResolvedValue({ file_count: 1, total_bytes: 1 });
    rerender(<ProjectAssets projectId="p2" />);

    expect(await screen.findByText("新项目.pdf")).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(deleteToTrash).not.toHaveBeenCalled();
  });

  it("closes the trash view on project switch and ignores a late trash response", async () => {
    const user = userEvent.setup();
    let resolveTrash:
      | ((value: ReturnType<typeof trashResponse>) => void)
      | null = null;
    listTrash.mockImplementationOnce(
      () => new Promise((resolve) => (resolveTrash = resolve)),
    );
    const { rerender } = renderAssets("p1");
    await screen.findByText("需求说明.pdf");
    await openTrashDialog(user);
    await waitFor(() => expect(listTrash).toHaveBeenCalledTimes(1));

    list.mockResolvedValue(listResponse([{ ...fileSpec, name: "新项目.pdf" }]));
    usage.mockResolvedValue({ file_count: 1, total_bytes: 1 });
    rerender(<ProjectAssets projectId="p2" />);
    expect(await screen.findByText("新项目.pdf")).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());

    await act(async () => {
      resolveTrash?.(trashResponse([trashFile]));
      await Promise.resolve();
    });
    // The late p1 trash payload must never repopulate under p2.
    expect(screen.queryByText("旧方案.pdf")).toBeNull();
  });

  it("clears rows, usage and the trash dialog when the trash list returns 404", async () => {
    const user = userEvent.setup();
    renderAssets("p1");
    await screen.findByText("需求说明.pdf");
    listTrash.mockRejectedValueOnce(
      new Error('404 - {"error":{"code":"NOT_FOUND","message":"not found"}}'),
    );

    await user.click(screen.getByRole("button", { name: "回收站" }));

    expect(
      await screen.findByText("项目不存在或你无权访问"),
    ).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(screen.queryByText("需求说明.pdf")).toBeNull();
    expect(screen.queryByText(/可见文件/)).toBeNull();
  });

  it("ignores a late restore response from the previous project", async () => {
    const user = userEvent.setup();
    let resolveRestore: ((value: unknown) => void) | null = null;
    listTrash.mockResolvedValue(trashResponse([trashFile]));
    restoreTrashed.mockImplementationOnce(
      () => new Promise((resolve) => (resolveRestore = resolve)),
    );
    const { rerender } = renderAssets("p1");
    await screen.findByText("需求说明.pdf");
    const dialog = await openTrashDialog(user);
    const row = await within(dialog).findByTestId(
      "project-asset-trash-trash-1",
    );
    await user.click(within(row).getByRole("button", { name: "恢复" }));
    await waitFor(() => expect(restoreTrashed).toHaveBeenCalledTimes(1));

    list.mockResolvedValue(listResponse([{ ...fileSpec, name: "新项目.pdf" }]));
    usage.mockResolvedValue({ file_count: 1, total_bytes: 1 });
    rerender(<ProjectAssets projectId="p2" />);
    await screen.findByText("新项目.pdf");
    const p2ListCalls = list.mock.calls.filter(
      (call) => call[0] === "p2",
    ).length;

    await act(async () => {
      resolveRestore?.({ ...fileSpec, node_id: "trash-1" });
      await Promise.resolve();
    });

    expect(message.success).not.toHaveBeenCalled();
    expect(list.mock.calls.filter((call) => call[0] === "p2")).toHaveLength(
      p2ListCalls,
    );
  });

  it("labels both usage counts as current versions only without inventing a quota", async () => {
    usage.mockResolvedValue({
      file_count: 2,
      total_bytes: 2048,
      trash_file_count: 1,
      trash_total_bytes: 512,
    });
    const { container } = renderAssets("p1");
    await screen.findByText("需求说明.pdf");

    expect(
      screen.getByText(/可见文件（仅当前版本）：2 个 · 2\.0 KB/),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/回收站保留（仅当前版本）：1 个 · 512 B/),
    ).toBeInTheDocument();
    // No made-up quota, remaining-space or disk-freed claims on the page.
    expect(container.textContent).not.toMatch(
      /剩余|配额|可用空间|10 ?GB|释放磁盘/,
    );
  });
});

describe("projectAssetsApi path building", () => {
  let realApi: typeof import("../../api/modules/projectAssets").projectAssetsApi;

  beforeAll(async () => {
    ({ projectAssetsApi: realApi } = await vi.importActual<
      typeof import("../../api/modules/projectAssets")
    >("../../api/modules/projectAssets"));
  });

  beforeEach(() => {
    request.mockReset();
    requestBlob.mockReset();
    requestUpload.mockReset();
    request.mockResolvedValue({});
    requestBlob.mockResolvedValue(new Blob());
    requestUpload.mockResolvedValue({});
  });

  it("encodes project ids and sends list filters as query params", () => {
    void realApi.list("p 1/2", {
      parentId: "n/1",
      q: " 报告 ",
      kind: "file",
      limit: 20,
      offset: 40,
    });

    expect(request).toHaveBeenCalledWith(
      "/projects/p%201%2F2/assets?parent_id=n%2F1&q=%E6%8A%A5%E5%91%8A&kind=file&limit=20&offset=40",
    );
  });

  it("omits the parent for the hidden root and defaults limit/offset", () => {
    void realApi.list("p1");

    expect(request).toHaveBeenCalledWith(
      `/projects/p1/assets?limit=${PROJECT_ASSETS_PAGE_SIZE}&offset=0`,
    );
  });

  it("requests real usage for the encoded project", () => {
    void realApi.usage("p 1/2");

    expect(request).toHaveBeenCalledWith("/projects/p%201%2F2/assets/usage");
  });

  it("posts a folder with parent_id only when nested", () => {
    void realApi.createFolder("p 1/2", { parentId: "n/1", name: "设计稿" });
    expect(request).toHaveBeenCalledWith("/projects/p%201%2F2/assets/folders", {
      method: "POST",
      body: JSON.stringify({ parent_id: "n/1", name: "设计稿" }),
    });

    request.mockClear();
    void realApi.createFolder("p1", { name: "根目录" });
    expect(request).toHaveBeenCalledWith("/projects/p1/assets/folders", {
      method: "POST",
      body: JSON.stringify({ name: "根目录" }),
    });
  });

  it("uploads a FormData body without a JSON content type", () => {
    const file = new File(["x"], "a.txt", { type: "text/plain" });
    void realApi.upload("p 1/2", { parentId: "n/1", file });

    expect(requestUpload).toHaveBeenCalledTimes(1);
    const [path, body] = requestUpload.mock.calls[0] as [string, FormData];
    expect(path).toBe("/projects/p%201%2F2/assets/upload");
    expect(body).toBeInstanceOf(FormData);
    expect(body.get("file")).toBe(file);
    expect(body.get("parent_id")).toBe("n/1");

    requestUpload.mockClear();
    void realApi.upload("p1", { file });
    const rootBody = requestUpload.mock.calls[0][1] as FormData;
    expect(rootBody.get("parent_id")).toBeNull();
  });

  it("downloads the encoded node through the blob request", () => {
    void realApi.download("p 1/2", "n/1");

    expect(requestBlob).toHaveBeenCalledWith(
      "/projects/p%201%2F2/assets/n%2F1/download",
    );
  });

  it("moves an encoded node to the trash with a DELETE request", () => {
    void realApi.deleteToTrash("p 1/2", "n/1");

    expect(request).toHaveBeenCalledWith("/projects/p%201%2F2/assets/n%2F1", {
      method: "DELETE",
    });
  });

  it("lists trash roots for the encoded project with paging params", () => {
    void realApi.listTrash("p 1/2", { limit: 20, offset: 40 });
    expect(request).toHaveBeenCalledWith(
      "/projects/p%201%2F2/assets/trash?limit=20&offset=40",
    );

    request.mockClear();
    void realApi.listTrash("p1");
    expect(request).toHaveBeenCalledWith(
      `/projects/p1/assets/trash?limit=${PROJECT_ASSETS_PAGE_SIZE}&offset=0`,
    );
  });

  it("posts restore for the encoded trash node", () => {
    void realApi.restoreTrashed("p 1/2", "n/1");

    expect(request).toHaveBeenCalledWith(
      "/projects/p%201%2F2/assets/trash/n%2F1/restore",
      { method: "POST" },
    );
  });
});

describe("project assets locale parity", () => {
  it("has the same projects.assets keys in zh and en plus the 042 asset error codes", () => {
    const zhAssets = Object.keys(zh.projects.assets).sort();
    const enAssets = Object.keys(en.projects.assets).sort();
    expect(zhAssets).toEqual(enAssets);
    expect(zhAssets.length).toBeGreaterThan(0);
    for (const key of zhAssets) {
      expect(
        (zh.projects.assets as Record<string, string>)[key].trim().length,
      ).toBeGreaterThan(0);
      expect(
        (en.projects.assets as Record<string, string>)[key].trim().length,
      ).toBeGreaterThan(0);
    }
    for (const code of [
      "PROJECT_ASSET_NAME_CONFLICT",
      "PROJECT_ASSET_INVALID",
      "PROJECT_ASSET_TOO_LARGE",
      "PROJECT_ASSET_PARENT_IN_TRASH",
    ] as const) {
      expect(zh.apiErrors[code]).toBeTruthy();
      expect(en.apiErrors[code]).toBeTruthy();
    }
  });
});
