import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { get, listComments, createComment, readCommentImage, update } =
  vi.hoisted(() => ({
    get: vi.fn(),
    listComments: vi.fn(),
    createComment: vi.fn(),
    readCommentImage: vi.fn(),
    update: vi.fn(),
  }));

vi.mock("../../api/modules/projectTodos", async (importOriginal) => {
  const actual = await importOriginal<
    typeof import("../../api/modules/projectTodos")
  >();
  return {
    ...actual,
    projectTodosApi: {
      ...actual.projectTodosApi,
      get,
      listComments,
      createComment,
      readCommentImage,
      update,
    },
  };
});

vi.mock("../../hooks/useServerTimezone", () => ({
  useServerTimezone: () => "UTC",
}));

import ProjectTodoDetail from "./ProjectTodoDetail";

const todo = {
  todo_id: "t1",
  project_id: "p1",
  title: "写周报",
  description: "# 原始文本 <b>\n第二行",
  description_format: "plain" as const,
  status: "todo" as const,
  creator_user_id: 1,
  assignee_user_id: 2,
  version: 3,
  created_at: 1_700_000_000,
  updated_at: 1_700_000_000,
};

const members = [
  { user_id: 1, username: "alice", role: "owner" as const },
  { user_id: 2, username: "bob", role: "member" as const },
];

const comment = {
  comment_id: "c1",
  todo_id: "t1",
  author_user_id: 2,
  author_name: "bob",
  body: "已核对数据",
  images: [],
  created_at: 1_700_000_100,
};

const changed = vi.fn();
const accessLost = vi.fn();

function detail(projectId = "p1", todoId = "t1") {
  return (
    <ProjectTodoDetail
      projectId={projectId}
      todoId={todoId}
      role="member"
      members={members}
      currentUserId={2}
      onClose={vi.fn()}
      onChanged={changed}
      onAccessLost={accessLost}
    />
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  let urlSequence = 0;
  Object.defineProperty(URL, "createObjectURL", {
    configurable: true,
    value: vi.fn(() => `blob:todo-test-${++urlSequence}`),
  });
  Object.defineProperty(URL, "revokeObjectURL", {
    configurable: true,
    value: vi.fn(),
  });
  get.mockResolvedValue(todo);
  listComments.mockResolvedValue({ items: [comment], next_cursor: null });
});

describe("ProjectTodoDetail B1", () => {
  it("shows old description literally, comments on the left and editable fields on the right", async () => {
    render(detail());

    const dialog = await screen.findByRole("dialog", { name: "待办详情" });
    expect(
      within(dialog).getByRole("heading", { name: "写周报" }),
    ).toBeVisible();
    const description = within(dialog).getByTestId("todo-description");
    expect(description.textContent).toBe("# 原始文本 <b>\n第二行");
    expect(description.querySelector("b")).toBeNull();
    expect(within(dialog).getByText("已核对数据")).toBeVisible();
    expect(within(dialog).getByText("状态")).toBeVisible();
    expect(within(dialog).getByText("处理人")).toBeVisible();
  });

  it("keeps a failed text draft and reuses its UUID v4 on an explicit retry", async () => {
    const user = userEvent.setup();
    createComment
      .mockRejectedValueOnce(new Error("网络暂时不可用"))
      .mockResolvedValueOnce(comment);
    render(detail());
    const dialog = await screen.findByRole("dialog", { name: "待办详情" });
    const input = within(dialog).getByRole("textbox", { name: "评论" });
    await user.type(input, "未发送草稿");
    await user.click(within(dialog).getByRole("button", { name: "发表评论" }));
    expect(await screen.findByText("网络暂时不可用")).toBeVisible();
    expect(input).toHaveValue("未发送草稿");

    const firstBody = createComment.mock.calls[0][2];
    expect(firstBody.body).toBe("未发送草稿");
    expect(firstBody.client_request_id).toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
    );
    await user.click(within(dialog).getByRole("button", { name: "发表评论" }));
    await waitFor(() => expect(createComment).toHaveBeenCalledTimes(2));
    expect(createComment.mock.calls[1][2]).toEqual(firstBody);
    await waitFor(() => expect(input).toHaveValue(""));
  });

  it("refreshes comments after a successful post so concurrent comments appear", async () => {
    const user = userEvent.setup();
    const posted = { ...comment, comment_id: "c2", body: "我的评论" };
    const concurrent = {
      ...comment,
      comment_id: "c3",
      body: "另一位成员的评论",
    };
    createComment.mockResolvedValueOnce(posted);
    listComments
      .mockResolvedValueOnce({ items: [comment], next_cursor: null })
      .mockResolvedValueOnce({
        items: [concurrent, posted, comment],
        next_cursor: null,
      });

    render(detail());
    await screen.findByText("已核对数据");
    await user.type(screen.getByRole("textbox", { name: "评论" }), "我的评论");
    await user.click(screen.getByRole("button", { name: "发表评论" }));

    await waitFor(() => expect(listComments).toHaveBeenCalledTimes(2));
    expect(await screen.findByText("另一位成员的评论")).toBeVisible();
    expect(screen.getByRole("textbox", { name: "评论" })).toHaveValue("");
  });

  it("removes an old project's private title and comments immediately on a route switch", async () => {
    const view = render(detail());
    expect(await screen.findByText("已核对数据")).toBeVisible();
    get.mockImplementationOnce(() => new Promise(() => {}));
    listComments.mockImplementationOnce(() => new Promise(() => {}));
    view.rerender(detail("p2", "t2"));
    expect(screen.queryByText("已核对数据")).toBeNull();
    expect(screen.queryByRole("heading", { name: "写周报" })).toBeNull();
  });

  it("clears loaded detail on a comment-list 404 after revocation", async () => {
    listComments.mockRejectedValueOnce(
      new Error('404 - {"error":{"code":"NOT_FOUND"}}'),
    );
    render(detail());
    expect(await screen.findByText("待办不存在或你无权访问")).toBeVisible();
    expect(screen.queryByRole("heading", { name: "写周报" })).toBeNull();
  });

  it("keeps the comment draft across a status conflict and a manual refresh", async () => {
    const user = userEvent.setup();
    update.mockRejectedValueOnce(
      new Error('409 - {"error":{"code":"CONFLICT"}}'),
    );
    render(detail());
    const dialog = await screen.findByRole("dialog", { name: "待办详情" });
    await screen.findByText("已核对数据");
    const input = within(dialog).getByRole("textbox", { name: "评论" });
    await user.type(input, "准备发送的评论");
    fireEvent.mouseDown(within(dialog).getByRole("combobox"));
    fireEvent.click(screen.getByText("已完成"));
    expect(
      await screen.findByText("待办已被更新，请刷新后比较再保存。"),
    ).toBeVisible();
    expect(input).toHaveValue("准备发送的评论");
    await user.click(within(dialog).getByRole("button", { name: /刷\s*新/ }));
    await waitFor(() => expect(get).toHaveBeenCalledTimes(2));
    await waitFor(() =>
      expect(screen.getByRole("textbox", { name: "评论" })).toHaveValue(
        "准备发送的评论",
      ),
    );
  });

  it("does not backfill the prior project's detail when its GET arrives late", async () => {
    let resolvePrior: ((value: typeof todo) => void) | undefined;
    get.mockImplementationOnce(
      () => new Promise<typeof todo>((resolve) => (resolvePrior = resolve)),
    );
    get.mockResolvedValueOnce({
      ...todo,
      project_id: "p2",
      todo_id: "t2",
      title: "项目二待办",
    });
    listComments.mockResolvedValueOnce({ items: [comment], next_cursor: null });
    listComments.mockResolvedValueOnce({ items: [], next_cursor: null });
    const view = render(detail());
    view.rerender(detail("p2", "t2"));
    expect(
      await screen.findByRole("heading", { name: "项目二待办" }),
    ).toBeVisible();
    resolvePrior?.(todo);
    await waitFor(() =>
      expect(screen.queryByRole("heading", { name: "写周报" })).toBeNull(),
    );
    expect(screen.queryByText("已核对数据")).toBeNull();
  });

  it("clears previously loaded private data if posting finds membership revoked", async () => {
    const user = userEvent.setup();
    createComment.mockRejectedValueOnce(
      new Error('404 - {"error":{"code":"NOT_FOUND"}}'),
    );
    render(detail());
    await screen.findByText("已核对数据");
    await user.type(screen.getByRole("textbox", { name: "评论" }), "新的评论");
    await user.click(screen.getByRole("button", { name: "发表评论" }));
    expect(await screen.findByText("待办不存在或你无权访问")).toBeVisible();
    expect(screen.queryByText("已核对数据")).toBeNull();
    expect(screen.queryByRole("heading", { name: "写周报" })).toBeNull();
  });

  it("ignores an old project's late POST while a new project draft is being submitted", async () => {
    const user = userEvent.setup();
    let resolveOld: ((value: typeof comment) => void) | undefined;
    let resolveNew: ((value: typeof comment) => void) | undefined;
    createComment
      .mockImplementationOnce(
        () => new Promise<typeof comment>((resolve) => (resolveOld = resolve)),
      )
      .mockImplementationOnce(
        () => new Promise<typeof comment>((resolve) => (resolveNew = resolve)),
      );
    get.mockImplementation((projectId: string) =>
      Promise.resolve(
        projectId === "p1"
          ? todo
          : { ...todo, project_id: "p2", todo_id: "t2", title: "项目二待办" },
      ),
    );
    const view = render(detail());
    await screen.findByText("已核对数据");
    await user.type(
      screen.getByRole("textbox", { name: "评论" }),
      "项目一草稿",
    );
    await user.click(screen.getByRole("button", { name: "发表评论" }));
    await waitFor(() => expect(createComment).toHaveBeenCalledTimes(1));

    view.rerender(detail("p2", "t2"));
    await screen.findByRole("heading", { name: "项目二待办" });
    await user.type(
      screen.getByRole("textbox", { name: "评论" }),
      "项目二草稿",
    );
    await user.click(screen.getByRole("button", { name: "发表评论" }));
    await waitFor(() => expect(createComment).toHaveBeenCalledTimes(2));
    resolveOld?.(comment);
    await waitFor(() =>
      expect(screen.getByRole("textbox", { name: "评论" })).toHaveValue(
        "项目二草稿",
      ),
    );
    expect(screen.getByRole("button", { name: /发表评论/ })).toBeDisabled();
    resolveNew?.({ ...comment, todo_id: "t2", comment_id: "c2" });
    await waitFor(() =>
      expect(screen.getByRole("textbox", { name: "评论" })).toHaveValue(""),
    );
  });

  it("ignores an old project's late PATCH 404 after switching to another todo", async () => {
    let rejectOld: ((reason: Error) => void) | undefined;
    update.mockImplementationOnce(
      () => new Promise((_, reject) => (rejectOld = reject)),
    );
    get.mockImplementation((projectId: string) =>
      Promise.resolve(
        projectId === "p1"
          ? todo
          : { ...todo, project_id: "p2", todo_id: "t2", title: "项目二待办" },
      ),
    );
    const view = render(detail());
    await screen.findByText("已核对数据");
    fireEvent.mouseDown(screen.getByRole("combobox"));
    fireEvent.click(screen.getByText("已完成"));
    await waitFor(() => expect(update).toHaveBeenCalledTimes(1));

    view.rerender(detail("p2", "t2"));
    await screen.findByRole("heading", { name: "项目二待办" });
    rejectOld?.(new Error('404 - {"error":{"code":"NOT_FOUND"}}'));
    await waitFor(() =>
      expect(screen.getByRole("heading", { name: "项目二待办" })).toBeVisible(),
    );
    expect(screen.queryByText("待办不存在或你无权访问")).toBeNull();
    expect(accessLost).not.toHaveBeenCalled();
    expect(changed).not.toHaveBeenCalled();
  });
});

describe("ProjectTodoDetail B2 Markdown", () => {
  it("renders a markdown description while preserving plain legacy descriptions literally", async () => {
    get.mockResolvedValueOnce({
      ...todo,
      description_format: "markdown",
      description: "# 本周进展\n\n**完成** [链接](https://example.com)",
    });
    render(detail());

    const description = await screen.findByTestId("todo-description");
    expect(
      within(description).getByRole("heading", { name: "本周进展" }),
    ).toBeVisible();
    expect(within(description).getByText("完成").tagName).toBe("STRONG");
    expect(
      within(description).getByRole("link", { name: "链接" }),
    ).toHaveAttribute("rel", "noopener noreferrer");
  });

  it("edits a description with preview and saves Markdown with the current version", async () => {
    const user = userEvent.setup();
    const nextDescription = "# 新标题\n\n**本周完成**";
    update.mockResolvedValueOnce({
      ...todo,
      description: nextDescription,
      description_format: "markdown",
      version: 4,
    });
    render(detail());
    await screen.findByTestId("todo-description");
    await user.click(screen.getByRole("button", { name: "编辑描述" }));

    const editor = screen.getByRole("textbox", { name: "待办描述" });
    expect(editor).toHaveValue(todo.description);
    await user.clear(editor);
    await user.type(editor, nextDescription);
    await user.click(screen.getByRole("button", { name: "预览" }));
    expect(screen.getByRole("heading", { name: "新标题" })).toBeVisible();
    await user.click(screen.getByRole("button", { name: "保存描述" }));

    await waitFor(() =>
      expect(update).toHaveBeenCalledWith("p1", "t1", {
        expected_version: 3,
        description: nextDescription,
        description_format: "markdown",
      }),
    );
    expect(
      await screen.findByRole("heading", { name: "新标题" }),
    ).toBeVisible();
    expect(screen.queryByRole("textbox", { name: "待办描述" })).toBeNull();
  });

  it("offers keyboard-accessible Markdown formatting controls", async () => {
    const user = userEvent.setup();
    get.mockResolvedValueOnce({ ...todo, description: "" });
    render(detail());
    await screen.findByTestId("todo-description");
    await user.click(screen.getByRole("button", { name: "编辑描述" }));
    await user.click(screen.getByRole("button", { name: "粗体" }));
    expect(screen.getByRole("textbox", { name: "待办描述" })).toHaveValue(
      "**粗体文字**",
    );
    await user.click(screen.getByRole("button", { name: "链接" }));
    expect(screen.getByRole("textbox", { name: "待办描述" })).toHaveValue(
      "**粗体文字**[链接文字](https://)",
    );
  });

  it("preserves an unsaved description through 409 and displays the refreshed server text for comparison", async () => {
    const user = userEvent.setup();
    get.mockResolvedValueOnce(todo).mockResolvedValueOnce({
      ...todo,
      version: 4,
      description: "其他成员更新的正文",
      description_format: "plain",
    });
    update.mockRejectedValueOnce(
      new Error('409 - {"error":{"code":"CONFLICT"}}'),
    );
    render(detail());
    await screen.findByTestId("todo-description");
    await user.click(screen.getByRole("button", { name: "编辑描述" }));
    const editor = screen.getByRole("textbox", { name: "待办描述" });
    await user.clear(editor);
    await user.type(editor, "# 我的草稿");
    await user.click(screen.getByRole("button", { name: "保存描述" }));
    expect(
      await screen.findByText("待办已被更新，请刷新后比较再保存。"),
    ).toBeVisible();
    expect(editor).toHaveValue("# 我的草稿");
    await user.click(screen.getByRole("button", { name: /刷\s*新/ }));
    await waitFor(() => expect(get).toHaveBeenCalledTimes(2));
    expect(screen.getByRole("textbox", { name: "待办描述" })).toHaveValue(
      "# 我的草稿",
    );
    expect(await screen.findByText("其他成员更新的正文")).toBeVisible();
  });
});

describe("ProjectTodoDetail B2 comment images", () => {
  it("previews a pasted PNG locally and removes its Blob URL on demand", async () => {
    render(detail());
    await screen.findByText("已核对数据");
    const image = new File(["png"], "pasted.png", { type: "image/png" });
    fireEvent.paste(screen.getByRole("textbox", { name: "评论" }), {
      clipboardData: { files: [image] },
    });

    expect(screen.getByRole("img", { name: "待发送图片 1" })).toHaveAttribute(
      "src",
      "blob:todo-test-1",
    );
    expect(createComment).not.toHaveBeenCalled();
    await userEvent
      .setup()
      .click(screen.getByRole("button", { name: "移除图片 1" }));
    expect(screen.queryByRole("img", { name: "待发送图片 1" })).toBeNull();
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:todo-test-1");
  });

  it("rejects unsupported, over-count, per-file and aggregate image limits before upload", async () => {
    const file = (size: number, name: string, type = "image/png") =>
      new File([new Uint8Array(size)], name, { type });
    render(detail());
    await screen.findByText("已核对数据");
    const input = screen.getByRole("textbox", { name: "评论" });
    fireEvent.paste(input, {
      clipboardData: { files: [file(2, "bad.gif", "image/gif")] },
    });
    expect(screen.getByText("仅支持 PNG、JPEG 或 WebP 图片")).toBeVisible();

    fireEvent.paste(input, {
      clipboardData: {
        files: Array.from({ length: 6 }, (_, index) =>
          file(2, `many-${index}.png`),
        ),
      },
    });
    expect(screen.getByText("最多粘贴 5 张图片")).toBeVisible();

    fireEvent.paste(input, {
      clipboardData: { files: [file(8 * 1024 * 1024 + 1, "large.png")] },
    });
    expect(screen.getByText("每张图片最多 8 MiB")).toBeVisible();

    fireEvent.paste(input, {
      clipboardData: {
        files: [
          file(7 * 1024 * 1024, "one.png"),
          file(7 * 1024 * 1024, "two.png"),
          file(7 * 1024 * 1024, "three.png"),
        ],
      },
    });
    expect(screen.getByText("图片合计最多 20 MiB")).toBeVisible();
    expect(screen.queryByRole("img", { name: /待发送图片/ })).toBeNull();
    expect(createComment).not.toHaveBeenCalled();
  });

  it("keeps an image-only draft and its idempotency UUID through a failed upload retry", async () => {
    const user = userEvent.setup();
    const image = new File(["png"], "pasted.png", { type: "image/png" });
    createComment
      .mockRejectedValueOnce(new Error("网络中断"))
      .mockResolvedValueOnce({ ...comment, comment_id: "c2", images: [] });
    render(detail());
    await screen.findByText("已核对数据");
    fireEvent.paste(screen.getByRole("textbox", { name: "评论" }), {
      clipboardData: { files: [image] },
    });
    await user.click(screen.getByRole("button", { name: "发表评论" }));
    expect(await screen.findByText("网络中断")).toBeVisible();
    expect(screen.getByRole("img", { name: "待发送图片 1" })).toBeVisible();
    const first = createComment.mock.calls[0][2];
    expect(first).toMatchObject({ body: "", images: [image] });
    expect(first.client_request_id).toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
    );

    await user.click(screen.getByRole("button", { name: "发表评论" }));
    await waitFor(() => expect(createComment).toHaveBeenCalledTimes(2));
    expect(createComment.mock.calls[1][2]).toEqual(first);
    await waitFor(() =>
      expect(screen.queryByRole("img", { name: "待发送图片 1" })).toBeNull(),
    );
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:todo-test-1");
  });

  it("reads persisted images with auth and revokes their Blob URL when the project changes", async () => {
    const imageComment = {
      ...comment,
      images: [
        {
          image_id: "i1",
          media_type: "image/png",
          size_bytes: 3,
          position: 0,
        },
      ],
    };
    listComments.mockResolvedValueOnce({
      items: [imageComment],
      next_cursor: null,
    });
    listComments.mockResolvedValueOnce({ items: [], next_cursor: null });
    readCommentImage.mockResolvedValueOnce(
      new Blob(["png"], { type: "image/png" }),
    );
    get.mockImplementation((projectId: string) =>
      Promise.resolve(
        projectId === "p1"
          ? todo
          : { ...todo, project_id: "p2", todo_id: "t2", title: "项目二待办" },
      ),
    );
    const view = render(detail());
    const persisted = await screen.findByRole("img", { name: "评论图片 1" });
    expect(persisted).toHaveAttribute("src", "blob:todo-test-1");
    expect(readCommentImage).toHaveBeenCalledWith("p1", "t1", "c1", "i1", {
      signal: expect.any(AbortSignal),
    });

    view.rerender(detail("p2", "t2"));
    await screen.findByRole("heading", { name: "项目二待办" });
    expect(screen.queryByRole("img", { name: "评论图片 1" })).toBeNull();
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:todo-test-1");
  });

  it("replaces private image Blob URLs after comments are reloaded", async () => {
    const user = userEvent.setup();
    const imageComment = {
      ...comment,
      images: [
        { image_id: "i1", media_type: "image/png", size_bytes: 3, position: 0 },
      ],
    };
    const posted = { ...comment, comment_id: "c2", body: "新评论", images: [] };
    listComments
      .mockResolvedValueOnce({ items: [imageComment], next_cursor: null })
      .mockResolvedValueOnce({
        items: [imageComment, posted],
        next_cursor: null,
      });
    readCommentImage.mockResolvedValue(
      new Blob(["png"], { type: "image/png" }),
    );
    createComment.mockResolvedValueOnce(posted);
    render(detail());
    expect(
      await screen.findByRole("img", { name: "评论图片 1" }),
    ).toHaveAttribute("src", "blob:todo-test-1");
    await user.type(screen.getByRole("textbox", { name: "评论" }), "新评论");
    await user.click(screen.getByRole("button", { name: "发表评论" }));
    await waitFor(() => expect(listComments).toHaveBeenCalledTimes(2));
    expect(
      await screen.findByRole("img", { name: "评论图片 1" }),
    ).toHaveAttribute("src", "blob:todo-test-2");
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:todo-test-1");
  });

  it("aborts a late private image GET without creating a Blob URL for a different project", async () => {
    const imageComment = {
      ...comment,
      images: [
        { image_id: "i1", media_type: "image/png", size_bytes: 3, position: 0 },
      ],
    };
    listComments
      .mockResolvedValueOnce({ items: [imageComment], next_cursor: null })
      .mockResolvedValueOnce({ items: [], next_cursor: null });
    let resolvePrior: ((blob: Blob) => void) | undefined;
    readCommentImage.mockImplementationOnce(
      () => new Promise<Blob>((resolve) => (resolvePrior = resolve)),
    );
    get.mockImplementation((projectId: string) =>
      Promise.resolve(
        projectId === "p1"
          ? todo
          : { ...todo, project_id: "p2", todo_id: "t2", title: "项目二待办" },
      ),
    );
    const view = render(detail());
    await waitFor(() => expect(readCommentImage).toHaveBeenCalledTimes(1));
    const signal: AbortSignal = readCommentImage.mock.calls[0][4].signal;
    view.rerender(detail("p2", "t2"));
    await screen.findByRole("heading", { name: "项目二待办" });
    expect(signal.aborted).toBe(true);
    resolvePrior?.(new Blob(["png"], { type: "image/png" }));
    await waitFor(() => expect(URL.createObjectURL).not.toHaveBeenCalled());
    expect(screen.queryByRole("img", { name: "评论图片 1" })).toBeNull();
  });

  it("clears private todo content if an image read discovers revoked access", async () => {
    listComments.mockResolvedValueOnce({
      items: [
        {
          ...comment,
          images: [
            {
              image_id: "i1",
              media_type: "image/png",
              size_bytes: 3,
              position: 0,
            },
          ],
        },
      ],
      next_cursor: null,
    });
    readCommentImage.mockRejectedValueOnce(
      new Error('404 - {"error":{"code":"NOT_FOUND"}}'),
    );
    render(detail());
    expect(await screen.findByText("待办不存在或你无权访问")).toBeVisible();
    expect(screen.queryByRole("heading", { name: "写周报" })).toBeNull();
    expect(screen.queryByText("已核对数据")).toBeNull();
    expect(accessLost).toHaveBeenCalledTimes(1);
    expect(URL.createObjectURL).not.toHaveBeenCalled();
  });

  it("shows upload progress and aborts a pending image POST on project switch", async () => {
    const user = userEvent.setup();
    const image = new File(["png"], "pasted.png", { type: "image/png" });
    let resolvePrior: ((value: typeof comment) => void) | undefined;
    createComment.mockImplementationOnce(
      (
        _projectId: string,
        _todoId: string,
        _body: unknown,
        _options: RequestInit,
        onProgress: (percent: number) => void,
      ) => {
        onProgress(37);
        return new Promise<typeof comment>(
          (resolve) => (resolvePrior = resolve),
        );
      },
    );
    get.mockImplementation((projectId: string) =>
      Promise.resolve(
        projectId === "p1"
          ? todo
          : { ...todo, project_id: "p2", todo_id: "t2", title: "项目二待办" },
      ),
    );
    listComments
      .mockResolvedValueOnce({ items: [comment], next_cursor: null })
      .mockResolvedValueOnce({ items: [], next_cursor: null });
    const view = render(detail());
    await screen.findByText("已核对数据");
    fireEvent.paste(screen.getByRole("textbox", { name: "评论" }), {
      clipboardData: { files: [image] },
    });
    await user.click(screen.getByRole("button", { name: "发表评论" }));
    await waitFor(() => expect(createComment).toHaveBeenCalledTimes(1));
    expect(
      screen.getByRole("progressbar", { name: "图片上传进度" }),
    ).toHaveValue(37);
    const signal: AbortSignal = createComment.mock.calls[0][3].signal;

    view.rerender(detail("p2", "t2"));
    expect(screen.queryByRole("img", { name: "待发送图片 1" })).toBeNull();
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:todo-test-1");
    await screen.findByRole("heading", { name: "项目二待办" });
    expect(signal.aborted).toBe(true);
    resolvePrior?.(comment);
    expect(
      screen.queryByRole("progressbar", { name: "图片上传进度" }),
    ).toBeNull();
    expect(screen.queryByRole("img", { name: "待发送图片 1" })).toBeNull();
    expect(screen.queryByText("已核对数据")).toBeNull();
  });
});
