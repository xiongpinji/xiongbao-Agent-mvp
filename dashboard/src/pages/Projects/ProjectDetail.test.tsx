import type { ReactNode } from "react";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Link, MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { get, members, list, usage } = vi.hoisted(() => ({
  get: vi.fn(),
  members: vi.fn(),
  list: vi.fn(),
  usage: vi.fn(),
}));

const { tasksProps } = vi.hoisted(() => ({
  tasksProps: {
    current: null as null | {
      projectId: string;
      members: unknown;
      instructions?: string;
      instructionsSha256?: string;
      onProjectReload?: () => void;
    },
  },
}));

const { expertsProps } = vi.hoisted(() => ({
  expertsProps: {
    current: null as null | { projectId: string; role: string },
  },
}));

const { modalProps } = vi.hoisted(() => ({
  modalProps: {
    current: null as null | {
      open?: boolean;
      editTarget?: { project_id?: string } | null;
      onClose?: () => void;
      onSaved?: (saved: unknown) => void;
    },
  },
}));

vi.mock("../../api/modules/projects", () => ({
  PROJECTS_PAGE_SIZE: 20,
  projectsApi: {
    get,
    members,
    list: vi.fn(),
    create: vi.fn(),
    update: vi.fn(),
  },
}));

vi.mock("../../api/modules/projectAssets", async (importOriginal) => {
  const actual = await importOriginal<
    typeof import("../../api/modules/projectAssets")
  >();
  return {
    ...actual,
    projectAssetsApi: {
      list,
      usage,
      createFolder: vi.fn(),
      upload: vi.fn(),
      download: vi.fn(),
    },
  };
});

vi.mock("../../hooks/useIsMobile", () => ({ useIsMobile: () => false }));
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

vi.mock("../../layouts/PageShell", () => ({
  default: ({
    title,
    actions,
    children,
  }: {
    title: string;
    actions?: ReactNode;
    children: ReactNode;
  }) => (
    <main>
      <h1>{title}</h1>
      {actions && <div>{actions}</div>}
      {children}
    </main>
  ),
}));

vi.mock("../../components/EmptyState", () => ({
  EmptyState: ({
    title,
    description,
  }: {
    title?: string;
    description?: ReactNode;
  }) => (
    <section>
      <h2>{title}</h2>
      <p>{description}</p>
    </section>
  ),
}));

vi.mock("./ProjectActivity", () => ({
  default: () => <div>activity-stub</div>,
}));
vi.mock("./ProjectPlan", () => ({
  default: () => <div>plan-stub</div>,
}));
vi.mock("./ProjectTasks", () => ({
  default: (props: {
    projectId: string;
    members: unknown;
    instructions?: string;
    instructionsSha256?: string;
    onProjectReload?: () => void;
  }) => {
    tasksProps.current = props;
    return <div>tasks-stub</div>;
  },
}));
vi.mock("./ProjectMembersPanel", () => ({
  // Mirror the real panel's outer landmark: ProjectMembersPanel renders its
  // own <section aria-label="成员">, which the detail card wrapper must not
  // duplicate with a second named region.
  default: () => (
    <section aria-label="成员">
      <div>members-stub</div>
    </section>
  ),
}));
vi.mock("./ProjectExperts", () => ({
  default: (props: { projectId: string; role: string }) => {
    expertsProps.current = props;
    return (
      <section aria-label="专家">
        <div>experts-stub</div>
      </section>
    );
  },
}));
vi.mock("./CreateProjectModal", () => ({
  default: (props: {
    open?: boolean;
    editTarget?: { project_id?: string } | null;
    onClose?: () => void;
    onSaved?: (saved: unknown) => void;
  }) => {
    modalProps.current = props;
    return null;
  },
}));

import ProjectDetail from "./ProjectDetail";
import type {
  ProjectAssetNode,
  ProjectAssetListResponse,
} from "../../api/modules/projectAssets";

const summary = {
  project_id: "project-1",
  name: "熊宝项目",
  description: "真实项目描述",
  my_role: "owner" as const,
  member_count: 1,
  created_at: 1_700_000_000,
  updated_at: 1_700_000_000,
};

const composerPlaceholder =
  "项目内直接发送消息尚未开放：请在“任务”页新建项目任务并前往对话";

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

function listResponse(items: ProjectAssetNode[]): ProjectAssetListResponse {
  return { items, total: items.length, limit: 50, offset: 0, has_more: false };
}

function renderDetail(projectId: string, key = projectId) {
  return render(
    <MemoryRouter key={key} initialEntries={[`/projects/${projectId}`]}>
      <Routes>
        <Route path="/projects/:projectId" element={<ProjectDetail />} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  modalProps.current = null;
  get.mockResolvedValue({
    ...summary,
    instructions: "",
    instructions_sha256: "sha-empty",
  });
  members.mockResolvedValue([{ user_id: 1, username: "alice", role: "owner" }]);
  list.mockResolvedValue(listResponse([folderDesign, fileSpec]));
  usage.mockResolvedValue({ file_count: 2, total_bytes: 2048 });
});

describe("ProjectDetail assets tab mount", () => {
  it("mounts the real asset library and calls the 023A API", async () => {
    const user = userEvent.setup();
    renderDetail("project-1");

    const assets = await screen.findByRole("tab", { name: "资产" });
    expect(
      screen.queryByText(
        "资产待建设：项目资产库、版本与容量需要后端资产接口。",
      ),
    ).toBeNull();

    await user.click(assets);

    expect(await screen.findByText("需求说明.pdf")).toBeInTheDocument();
    expect(screen.getByText("设计稿")).toBeInTheDocument();
    expect(list).toHaveBeenCalledWith(
      "project-1",
      expect.objectContaining({ parentId: null, offset: 0 }),
    );
    expect(usage).toHaveBeenCalledWith("project-1");
    expect(screen.getByRole("button", { name: "上传" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "新建文件夹" })).toBeEnabled();
  });

  it("keeps the other project tabs intact around the assets tab", async () => {
    const user = userEvent.setup();
    renderDetail("project-1");

    expect(await screen.findByText("activity-stub")).toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: "计划" }));
    expect(await screen.findByText("plan-stub")).toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: "任务" }));
    expect(await screen.findByText("tasks-stub")).toBeInTheDocument();
    expect(screen.getByText("members-stub")).toBeInTheDocument();
    expect(screen.getByText("项目配置")).toBeInTheDocument();
  });

  it("mounts the project expert panel and keeps the other config cards unavailable", async () => {
    renderDetail("project-1");

    expect(await screen.findByText("experts-stub")).toBeInTheDocument();
    expect(expertsProps.current).toMatchObject({
      projectId: "project-1",
      role: "owner",
    });
    expect(screen.getByText("项目配置")).toBeInTheDocument();
    expect(screen.getAllByText("暂未开放")).toHaveLength(3);
    expect(
      screen.getByText("暂未开放：连接器、技能与定时任务仍需后续后端支持。"),
    ).toBeInTheDocument();
  });

  it("passes the loaded member roster to the tasks tab and states card sharing honestly", async () => {
    const user = userEvent.setup();
    renderDetail("project-1");

    await user.click(await screen.findByRole("tab", { name: "任务" }));
    expect(tasksProps.current).toEqual(
      expect.objectContaining({
        projectId: "project-1",
        members: [{ user_id: 1, username: "alice", role: "owner" }],
        instructions: "",
        instructionsSha256: "sha-empty",
      }),
    );
    expect(typeof tasksProps.current?.onProjectReload).toBe("function");
    expect(
      screen.getByText(
        "在本页直接发送消息、协同写入、本地/云端与移交需后续后端权限；任务卡片摘要可在“任务”页显式分享给指定成员。",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByPlaceholderText(
        "项目内直接发送消息尚未开放：请在“任务”页新建项目任务并前往对话",
      ),
    ).toBeDisabled();
    expect(
      screen.queryByText(
        "本片只登记已有任务归属；项目内创建/发送、共享、本地/云端与移交需后续后端权限。",
      ),
    ).toBeNull();
  });

  it("keeps exactly one disabled composer visible on all four project tabs", async () => {
    const user = userEvent.setup();
    renderDetail("project-1");

    await screen.findByText("activity-stub");

    for (const tab of ["动态", "计划", "任务", "资产"]) {
      await user.click(screen.getByRole("tab", { name: tab }));

      const composers = screen.getAllByPlaceholderText(composerPlaceholder);
      expect(composers).toHaveLength(1);
      expect(composers[0]).toBeVisible();
      expect(composers[0]).toBeDisabled();

      const sendButtons = screen.getAllByRole("button", {
        name: /^发\s*送$/,
      });
      expect(sendButtons).toHaveLength(1);
      expect(sendButtons[0]).toBeDisabled();
      expect(
        screen.getByText(
          "在本页直接发送消息、协同写入、本地/云端与移交需后续后端权限；任务卡片摘要可在“任务”页显式分享给指定成员。",
        ),
      ).toBeVisible();
    }

    await user.click(screen.getByRole("tab", { name: "动态" }));
    expect(screen.getAllByPlaceholderText(composerPlaceholder)).toHaveLength(1);
    expect(screen.getByPlaceholderText(composerPlaceholder)).toBeVisible();
    expect(screen.getByText("activity-stub")).toBeVisible();
  });

  /**
   * Plan 031 regression: the real configuration column stays inline on the
   * desktop right side across every tab switch — never moved behind a
   * drawer or disclosure toggle — alongside the single disabled composer.
   * Pixel geometry (e.g. 800×728 with the global nav collapsed to its
   * rail) belongs to the browser acceptance matrix, not JSDOM.
   */
  it("keeps the inline right configuration and one disabled composer across all four tabs", async () => {
    const user = userEvent.setup();
    renderDetail("project-1");

    await screen.findByText("activity-stub");

    for (const tab of ["动态", "计划", "任务", "资产"]) {
      await user.click(screen.getByRole("tab", { name: tab }));

      // The configuration column is mounted inline — no disclosure button.
      expect(screen.getByText("项目配置")).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "项目配置" })).toBeNull();
      expect(screen.getByText("experts-stub")).toBeInTheDocument();
      expect(screen.getByText("members-stub")).toBeInTheDocument();
      expect(screen.getAllByText("暂未开放")).toHaveLength(3);
      expect(
        screen.getByText("暂未开放：连接器、技能与定时任务仍需后续后端支持。"),
      ).toBeInTheDocument();

      // Exactly one disabled composer survives every tab switch.
      const composers = screen.getAllByPlaceholderText(composerPlaceholder);
      expect(composers).toHaveLength(1);
      expect(composers[0]).toBeVisible();
      expect(composers[0]).toBeDisabled();
      expect(screen.getByRole("button", { name: /^发\s*送$/ })).toBeDisabled();
    }
  });

  it("passes the current instructions digest to the tasks tab and scopes edits to new tasks", async () => {
    get.mockResolvedValue({
      ...summary,
      instructions: "回答必须标注来源。",
      instructions_sha256: "sha-live",
    });
    const user = userEvent.setup();
    renderDetail("project-1");

    await user.click(await screen.findByRole("tab", { name: "任务" }));
    expect(tasksProps.current).toMatchObject({
      instructions: "回答必须标注来源。",
      instructionsSha256: "sha-live",
    });
    expect(
      screen.getByText("仅新任务采用当前指令；修改指令不会改变已创建任务。"),
    ).toBeInTheDocument();
  });

  it("does not show project A files after switching to project B", async () => {
    const user = userEvent.setup();
    get.mockImplementation((projectId: string) =>
      Promise.resolve({
        ...summary,
        project_id: projectId,
        instructions: "",
        instructions_sha256: "sha-empty",
      }),
    );
    list.mockImplementation((projectId: string) =>
      Promise.resolve(
        listResponse(
          projectId === "project-1"
            ? [{ ...fileSpec, name: "项目A文件.pdf" }]
            : [{ ...fileSpec, name: "项目B文件.pdf" }],
        ),
      ),
    );

    const view = renderDetail("project-1");
    await user.click(await screen.findByRole("tab", { name: "资产" }));
    expect(await screen.findByText("项目A文件.pdf")).toBeInTheDocument();

    view.rerender(
      <MemoryRouter key="project-2" initialEntries={["/projects/project-2"]}>
        <Routes>
          <Route path="/projects/:projectId" element={<ProjectDetail />} />
        </Routes>
      </MemoryRouter>,
    );

    await user.click(await screen.findByRole("tab", { name: "资产" }));
    expect(await screen.findByText("项目B文件.pdf")).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByText("项目A文件.pdf")).toBeNull());
  });

  it("never reveals assets when the project itself is not accessible", async () => {
    get.mockRejectedValue(new Error('404 - {"error":{"code":"NOT_FOUND"}}'));
    renderDetail("private-project");

    expect(
      await screen.findByText("项目不存在或你无权访问"),
    ).toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "资产" })).toBeNull();
    expect(screen.queryByPlaceholderText(composerPlaceholder)).toBeNull();
    expect(screen.queryByText("项目配置")).toBeNull();
    expect(list).not.toHaveBeenCalled();
  });
});

describe("ProjectDetail compact project shell", () => {
  it("keeps the compact path/action header, restores a page-level h1 and wires the project-info disclosure", async () => {
    const user = userEvent.setup();
    const { container } = renderDetail("project-1");

    await screen.findByText("activity-stub");

    expect(container.querySelector("header")).not.toBeNull();
    expect(screen.getByRole("link", { name: "项目" })).toHaveAttribute(
      "href",
      "/projects",
    );
    // The compact header replaced PageShell's title, so the project name is
    // restored as exactly one (visually hidden) h1 beside the breadcrumb:
    // h1 + visible breadcrumb last item are the only two text matches.
    const headings = screen.getAllByRole("heading", { level: 1 });
    expect(headings).toHaveLength(1);
    expect(headings[0]).toHaveTextContent("熊宝项目");
    expect(screen.getAllByText("熊宝项目")).toHaveLength(2);

    const info = screen.getByRole("button", { name: "查看详情" });
    expect(info).toHaveAttribute("aria-expanded", "false");
    expect(info).toHaveAttribute("aria-controls", "project-detail-info");

    const panel = container.querySelector("#project-detail-info");
    expect(panel).toHaveAttribute("hidden");
    expect(panel).toHaveTextContent("真实项目描述");
    expect(panel).toHaveTextContent("所有者");
    expect(panel).toHaveTextContent("1 名成员");
    expect(panel).toHaveTextContent(/更新于/);

    info.focus();
    await user.keyboard("{Enter}");
    expect(info).toHaveAttribute("aria-expanded", "true");
    expect(panel).not.toHaveAttribute("hidden");
  });

  it("keeps member role, description, member count and updated time reachable without an edit action", async () => {
    get.mockResolvedValue({
      ...summary,
      my_role: "member",
      instructions: "",
      instructions_sha256: "sha-empty",
    });
    const user = userEvent.setup();
    renderDetail("project-1");

    await screen.findByText("activity-stub");

    expect(screen.queryByRole("button", { name: "编辑项目资料" })).toBeNull();

    const info = screen.getByRole("button", { name: "查看详情" });
    await user.click(info);

    expect(screen.getByText("成员", { exact: true })).toBeVisible();
    expect(screen.getByText("真实项目描述")).toBeVisible();
    expect(screen.getByText("1 名成员")).toBeVisible();
    expect(screen.getByText(/更新于/)).toBeVisible();

    await user.click(info);
    expect(info).toHaveAttribute("aria-expanded", "false");
  });

  it("wires the owner edit action to the same project modal", async () => {
    const user = userEvent.setup();
    renderDetail("project-1");

    await screen.findByText("activity-stub");

    await user.click(screen.getByRole("button", { name: "编辑项目资料" }));

    expect(modalProps.current?.open).toBe(true);
    expect(modalProps.current?.editTarget).toMatchObject({
      project_id: "project-1",
    });
  });
});

const INSTRUCTION_BODY_ID = "project-detail-instructions";
const COMPOSER_HINT_ID = "project-detail-composer-hint";
const shortInstructions = "回答必须标注来源。";
/** > preview budget, with a tail that must stay hidden until expanded. */
const longInstructions = `${"先阅读项目资料并标注来源，".repeat(
  9,
)}尾部标记-完成度检查。`;

describe("ProjectDetail configuration cards", () => {
  it("renders exactly one labeled region per config card, including expert and member panels", async () => {
    renderDetail("project-1");

    await screen.findByText("activity-stub");

    // 专家/成员 regions come from the child panels' own named <section>s —
    // the styled card wrappers must not duplicate them into a second
    // identically named region (033 review P2: card semantics).
    for (const name of ["指令", "连接器", "专家", "技能", "定时任务", "成员"]) {
      expect(screen.getAllByRole("region", { name })).toHaveLength(1);
    }
    expect(
      within(screen.getByRole("region", { name: "专家" })).getByText(
        "experts-stub",
      ),
    ).toBeInTheDocument();
    expect(
      within(screen.getByRole("region", { name: "成员" })).getByText(
        "members-stub",
      ),
    ).toBeInTheDocument();

    // Unavailable cards never fake an action or a count.
    for (const name of ["连接器", "技能", "定时任务"]) {
      const region = screen.getByRole("region", { name });
      expect(within(region).getByText("暂未开放")).toBeInTheDocument();
      expect(within(region).queryByRole("button")).toBeNull();
    }
  });

  it("shows long instructions as a compact preview and reveals the verbatim text from the keyboard", async () => {
    get.mockResolvedValue({
      ...summary,
      instructions: longInstructions,
      instructions_sha256: "sha-long",
    });
    const user = userEvent.setup();
    renderDetail("project-1");

    await screen.findByText("activity-stub");

    const body = document.getElementById(INSTRUCTION_BODY_ID);
    expect(body).not.toBeNull();
    expect(body).not.toHaveTextContent(longInstructions);
    expect(body).not.toHaveTextContent("尾部标记");
    expect(body).toHaveTextContent("…");

    const toggle = screen.getByRole("button", { name: "展开全部" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(toggle).toHaveAttribute("aria-controls", INSTRUCTION_BODY_ID);

    toggle.focus();
    await user.keyboard("{Enter}");
    expect(screen.getByRole("button", { name: "收起" })).toHaveAttribute(
      "aria-expanded",
      "true",
    );
    expect(body).toHaveTextContent(longInstructions);
    expect(body).toHaveTextContent("尾部标记");

    await user.keyboard("{Enter}");
    expect(screen.getByRole("button", { name: "展开全部" })).toHaveAttribute(
      "aria-expanded",
      "false",
    );
    expect(body).not.toHaveTextContent("尾部标记");
  });

  it("offers no disclosure for short or empty instructions", async () => {
    get.mockResolvedValue({
      ...summary,
      instructions: shortInstructions,
      instructions_sha256: "sha-short",
    });
    const short = renderDetail("project-1");

    await screen.findByText("activity-stub");
    expect(screen.getByText(shortInstructions)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "展开全部" })).toBeNull();
    expect(screen.queryByRole("button", { name: "收起" })).toBeNull();
    short.unmount();

    get.mockResolvedValue({
      ...summary,
      instructions: "",
      instructions_sha256: "sha-empty",
    });
    renderDetail("project-empty");

    await screen.findByText("activity-stub");
    expect(screen.getByText("还没有项目指令。")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "展开全部" })).toBeNull();
  });

  it("keeps a short instruction readable when saved with surrounding whitespace", async () => {
    get.mockResolvedValue({
      ...summary,
      instructions: `${" ".repeat(82)}有效短指令`,
      instructions_sha256: "sha-padded-short",
    });
    renderDetail("project-1");

    await screen.findByText("activity-stub");

    expect(document.getElementById(INSTRUCTION_BODY_ID)).toHaveTextContent(
      "有效短指令",
    );
    expect(screen.queryByRole("button", { name: "展开全部" })).toBeNull();
  });

  it("resets the instruction disclosure when the instruction digest changes", async () => {
    get.mockResolvedValue({
      ...summary,
      instructions: longInstructions,
      instructions_sha256: "sha-long",
    });
    const user = userEvent.setup();
    renderDetail("project-1");

    await screen.findByText("activity-stub");
    await user.click(screen.getByRole("button", { name: "展开全部" }));
    expect(screen.getByRole("button", { name: "收起" })).toBeInTheDocument();

    act(() => {
      modalProps.current?.onSaved?.({
        ...summary,
        instructions: `${longInstructions} 已更新`,
        instructions_sha256: "sha-next",
      });
    });

    expect(screen.getByRole("button", { name: "展开全部" })).toHaveAttribute(
      "aria-expanded",
      "false",
    );
    expect(document.getElementById(INSTRUCTION_BODY_ID)).not.toHaveTextContent(
      "尾部标记",
    );
  });

  it("gives owner/admin a genuine instruction edit entry into the existing modal", async () => {
    const user = userEvent.setup();
    renderDetail("project-1");

    await screen.findByText("activity-stub");

    await user.click(screen.getByRole("button", { name: "编辑指令" }));

    expect(modalProps.current?.open).toBe(true);
    expect(modalProps.current?.editTarget).toMatchObject({
      project_id: "project-1",
    });
  });

  it("hides the instruction edit entry from members while keeping the disclosure", async () => {
    get.mockResolvedValue({
      ...summary,
      my_role: "member",
      instructions: longInstructions,
      instructions_sha256: "sha-long",
    });
    renderDetail("project-1");

    await screen.findByText("activity-stub");

    expect(screen.queryByRole("button", { name: "编辑指令" })).toBeNull();
    expect(screen.queryByRole("button", { name: "编辑项目资料" })).toBeNull();
    expect(
      screen.getByRole("button", { name: "展开全部" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText("仅新任务采用当前指令；修改指令不会改变已创建任务。"),
    ).toBeInTheDocument();
  });

  it("ties the disabled composer to its visible hint with aria-describedby", async () => {
    renderDetail("project-1");

    await screen.findByText("activity-stub");

    const composer = screen.getByPlaceholderText(composerPlaceholder);
    expect(composer).toBeDisabled();
    expect(composer).toHaveAttribute("aria-describedby", COMPOSER_HINT_ID);

    const hint = document.getElementById(COMPOSER_HINT_ID);
    expect(hint).not.toBeNull();
    expect(hint).toHaveTextContent(
      "在本页直接发送消息、协同写入、本地/云端与移交需后续后端权限；任务卡片摘要可在“任务”页显式分享给指定成员。",
    );
  });
});

const INFO_PANEL_ID = "project-detail-info";
const INFO_PANEL_LABEL = "项目信息";

/** Same route pattern with different params keeps one component instance. */
function renderDetailWithSwitchLink(fromId: string, toId: string) {
  return render(
    <MemoryRouter initialEntries={[`/projects/${fromId}`]}>
      <Link to={`/projects/${toId}`}>switch-project</Link>
      <Routes>
        <Route path="/projects/:projectId" element={<ProjectDetail />} />
      </Routes>
    </MemoryRouter>,
  );
}

/**
 * 033 review P2: the info disclosure only had a trigger-to-close path and
 * could retain open state across a route project switch. These tests pin
 * the dismissal contract: Escape (from the trigger or from inside the
 * panel), click/tap outside, and route project-ID changes close it, while
 * clicks inside the readable content keep it open and focus stays
 * predictable instead of trapped. Pixel bounding of the long description
 * (viewport-relative max-height + internal scroll) is CSS; it belongs to
 * the browser acceptance matrix, not JSDOM.
 */
describe("ProjectDetail info disclosure dismissal", () => {
  it("keeps a long description inside a native keyboard-operable disclosure", async () => {
    const longDescription = "很长的项目描述，需要滚动才能读完。".repeat(24);
    get.mockResolvedValue({
      ...summary,
      description: longDescription,
      instructions: "",
      instructions_sha256: "sha-empty",
    });
    const user = userEvent.setup();
    renderDetail("project-1");

    await screen.findByText("activity-stub");

    const trigger = screen.getByRole("button", { name: "查看详情" });
    // Native button: Space/Enter activation and focusability come free.
    expect(trigger.tagName).toBe("BUTTON");
    expect(trigger).toHaveAttribute("type", "button");
    trigger.focus();
    expect(trigger).toHaveFocus();

    await user.keyboard("{Enter}");
    expect(trigger).toHaveAttribute("aria-expanded", "true");

    const panel = screen.getByRole("group", { name: INFO_PANEL_LABEL });
    expect(trigger).toHaveAttribute("aria-controls", INFO_PANEL_ID);
    expect(panel.id).toBe(INFO_PANEL_ID);
    // Focusable so keyboard users can scroll the bounded panel.
    expect(panel).toHaveAttribute("tabindex", "0");
    // The full description stays in the content (CSS bounds it with an
    // internal scrollport at desktop heights).
    expect(panel).toHaveTextContent(longDescription);

    await user.keyboard("{Enter}");
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    expect(panel).toHaveAttribute("hidden");
  });

  it("closes on Escape from the trigger or the panel and refocuses the trigger", async () => {
    const user = userEvent.setup();
    renderDetail("project-1");

    await screen.findByText("activity-stub");

    const trigger = screen.getByRole("button", { name: "查看详情" });

    // Escape while the trigger holds focus.
    await user.click(trigger);
    expect(trigger).toHaveAttribute("aria-expanded", "true");
    await user.keyboard("{Escape}");
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    expect(trigger).toHaveFocus();
    expect(document.getElementById(INFO_PANEL_ID)).toHaveAttribute("hidden");

    // Escape while focus is inside the panel: focus returns to the trigger,
    // so reopening stays one keystroke away and nothing is lost.
    await user.keyboard("{Enter}");
    const panel = screen.getByRole("group", { name: INFO_PANEL_LABEL });
    panel.focus();
    expect(panel).toHaveFocus();
    await user.keyboard("{Escape}");
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    expect(panel).toHaveAttribute("hidden");
    expect(trigger).toHaveFocus();
  });

  it("lets keyboard focus move on past the open panel instead of trapping it", async () => {
    const user = userEvent.setup();
    renderDetail("project-1");

    await screen.findByText("activity-stub");

    const trigger = screen.getByRole("button", { name: "查看详情" });
    await user.click(trigger);
    const panel = screen.getByRole("group", { name: INFO_PANEL_LABEL });
    panel.focus();
    expect(panel).toHaveFocus();

    await user.tab();
    expect(panel.contains(document.activeElement)).toBe(false);
    expect(document.activeElement).not.toBe(trigger);
  });

  it("closes on clicks outside but not while reading inside the panel", async () => {
    const user = userEvent.setup();
    renderDetail("project-1");

    await screen.findByText("activity-stub");

    const trigger = screen.getByRole("button", { name: "查看详情" });
    await user.click(trigger);
    const panel = screen.getByRole("group", { name: INFO_PANEL_LABEL });

    // Clicking inside the readable content (selecting/copying the
    // description or metadata) must not dismiss the panel.
    await user.click(screen.getByText("真实项目描述"));
    expect(trigger).toHaveAttribute("aria-expanded", "true");
    expect(panel).not.toHaveAttribute("hidden");

    // A click/tap anywhere outside dismisses it.
    await user.click(screen.getByText("项目配置"));
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    expect(panel).toHaveAttribute("hidden");
  });

  it("closes the stale disclosure when the route project ID changes", async () => {
    get.mockImplementation((projectId: string) =>
      Promise.resolve({
        ...summary,
        project_id: projectId,
        instructions: "",
        instructions_sha256: "sha-empty",
      }),
    );
    const user = userEvent.setup();
    renderDetailWithSwitchLink("project-1", "project-2");

    await screen.findByText("activity-stub");
    const trigger = screen.getByRole("button", { name: "查看详情" });
    await user.click(trigger);
    expect(trigger).toHaveAttribute("aria-expanded", "true");

    // Same route pattern, different :projectId — the component instance
    // persists, so a stale open disclosure must be reset explicitly.
    await user.click(screen.getByText("switch-project"));
    await screen.findByText("activity-stub");

    expect(screen.getByRole("button", { name: "查看详情" })).toHaveAttribute(
      "aria-expanded",
      "false",
    );
    expect(document.getElementById(INFO_PANEL_ID)).toHaveAttribute("hidden");
  });
});
