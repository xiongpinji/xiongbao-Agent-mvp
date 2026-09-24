import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import en from "../../locales/en.json";
import zh from "../../locales/zh.json";

const { list, postMessage } = vi.hoisted(() => ({
  list: vi.fn(),
  postMessage: vi.fn(),
}));

vi.mock("../../api/modules/projectActivity", async (importOriginal) => {
  const actual = await importOriginal<
    typeof import("../../api/modules/projectActivity")
  >();
  return {
    ...actual,
    projectActivityApi: { list, postMessage },
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

import ProjectActivity from "./ProjectActivity";
import type { ProjectActivityItem } from "../../api/modules/projectActivity";
import { message } from "../../utils/antdMessage";

const projectCreated: ProjectActivityItem = {
  event_id: 11,
  event_type: "project.created",
  actor_user_id: 1,
  actor_name: "alice",
  object_kind: "project",
  object_id: "p1",
  message_body: null,
  created_at: 1_700_000_000,
};

const messageByAlice: ProjectActivityItem = {
  event_id: 12,
  event_type: "project.message_created",
  actor_user_id: 1,
  actor_name: "alice",
  object_kind: "message",
  object_id: "msg-secret-1",
  message_body: "第一条留言",
  created_at: 1_700_000_100,
};

const messageByBob: ProjectActivityItem = {
  event_id: 13,
  event_type: "project.message_created",
  actor_user_id: 2,
  actor_name: "bob",
  object_kind: "message",
  object_id: "msg-secret-2",
  message_body: "第二条留言",
  created_at: 1_700_000_200,
};

const deletedActorMemberEvent: ProjectActivityItem = {
  event_id: 21,
  event_type: "project.member_joined",
  actor_user_id: null,
  actor_name: null,
  object_kind: "member",
  object_id: "secret-member-id",
  message_body: null,
  created_at: 1_700_000_300,
};

function page(items: ProjectActivityItem[], nextCursor: string | null = null) {
  return { items, next_cursor: nextCursor };
}

function renderActivity(projectId = "p1") {
  return render(<ProjectActivity projectId={projectId} />);
}

beforeEach(() => {
  vi.clearAllMocks();
  list.mockResolvedValue(page([]));
  postMessage.mockResolvedValue(messageByAlice);
});

describe("ProjectActivity timeline", () => {
  it("loads related events by default with server-side paging", async () => {
    list.mockResolvedValue(page([projectCreated, messageByAlice]));
    renderActivity("p1");

    expect(await screen.findByText("第一条留言")).toBeInTheDocument();
    expect(screen.getByText("创建了项目")).toBeInTheDocument();
    expect(screen.getAllByText("alice").length).toBeGreaterThan(0);
    expect(list).toHaveBeenCalledWith("p1", {
      scope: "related",
      limit: 20,
      cursor: undefined,
    });
    expect(screen.getByRole("radio", { name: "与我相关" })).toBeChecked();
  });

  it("switches to the members scope without flashing stale related rows", async () => {
    list.mockResolvedValueOnce(page([messageByAlice]));
    renderActivity("p1");
    await screen.findByText("第一条留言");

    let resolveMembers: ((value: ReturnType<typeof page>) => void) | null =
      null;
    list.mockImplementationOnce(
      () => new Promise((resolve) => (resolveMembers = resolve)),
    );
    fireEvent.click(screen.getByRole("radio", { name: "成员动态" }));

    expect(screen.queryByText("第一条留言")).toBeNull();
    expect(list).toHaveBeenLastCalledWith("p1", {
      scope: "members",
      limit: 20,
      cursor: undefined,
    });

    await act(async () => {
      resolveMembers?.(page([messageByBob]));
      await Promise.resolve();
    });
    expect(await screen.findByText("第二条留言")).toBeInTheDocument();
    expect(screen.queryByText("第一条留言")).toBeNull();
  });

  it("refreshes and shows newly inserted member messages", async () => {
    const user = userEvent.setup();
    list.mockResolvedValueOnce(page([messageByAlice]));
    renderActivity("p1");
    await screen.findByText("第一条留言");

    list.mockResolvedValueOnce(page([messageByBob, messageByAlice]));
    await user.click(screen.getByRole("button", { name: "刷新动态" }));

    expect(await screen.findByText("第二条留言")).toBeInTheDocument();
    expect(screen.getByText("第一条留言")).toBeInTheDocument();
    expect(list).toHaveBeenLastCalledWith("p1", {
      scope: "related",
      limit: 20,
      cursor: undefined,
    });
  });

  it("pages with the opaque next_cursor and dedupes repeated event ids", async () => {
    const user = userEvent.setup();
    list.mockResolvedValueOnce(page([messageByAlice], "tok-1"));
    renderActivity("p1");
    await screen.findByText("第一条留言");

    list.mockResolvedValueOnce(page([messageByAlice, messageByBob]));
    await user.click(screen.getByRole("button", { name: "加载更多" }));

    expect(await screen.findByText("第二条留言")).toBeInTheDocument();
    expect(list).toHaveBeenLastCalledWith("p1", {
      scope: "related",
      limit: 20,
      cursor: "tok-1",
    });
    expect(screen.getAllByText("第一条留言")).toHaveLength(1);
    expect(screen.getAllByTestId(/^project-activity-/)).toHaveLength(2);
    expect(
      screen.queryByRole("button", { name: "加载更多" }),
    ).not.toBeInTheDocument();
  });

  it("posts trimmed text, clears the composer and reloads the feed", async () => {
    const user = userEvent.setup();
    renderActivity("p1");
    await screen.findByText("还没有与你相关的动态");

    const textarea = screen.getByPlaceholderText("以纯文本发表留言…");
    await user.type(textarea, "  你好，项目  ");
    const callsBefore = list.mock.calls.length;
    await user.click(screen.getByRole("button", { name: /^发\s*表$/ }));

    await waitFor(() =>
      expect(postMessage).toHaveBeenCalledWith("p1", "你好，项目"),
    );
    await waitFor(() =>
      expect(list.mock.calls.length).toBeGreaterThan(callsBefore),
    );
    expect(textarea).toHaveValue("");
    expect(message.success).toHaveBeenCalledWith("留言已发表");
  });

  it("rejects blank and overlength messages before any request", async () => {
    const user = userEvent.setup();
    renderActivity("p1");
    await screen.findByText("还没有与你相关的动态");
    const textarea = screen.getByPlaceholderText("以纯文本发表留言…");

    await user.type(textarea, "   ");
    await user.click(screen.getByRole("button", { name: /^发\s*表$/ }));
    expect(screen.getByText("请输入留言内容")).toBeInTheDocument();
    expect(postMessage).not.toHaveBeenCalled();

    fireEvent.change(textarea, { target: { value: "x".repeat(4001) } });
    await user.click(screen.getByRole("button", { name: /^发\s*表$/ }));
    expect(screen.getByText("留言最多 4000 个字符")).toBeInTheDocument();
    expect(postMessage).not.toHaveBeenCalled();
  });

  it("prevents duplicate submits while a post is in flight", async () => {
    const user = userEvent.setup();
    let resolvePost: ((value: ProjectActivityItem) => void) | null = null;
    postMessage.mockImplementationOnce(
      () => new Promise((resolve) => (resolvePost = resolve)),
    );
    renderActivity("p1");
    await screen.findByText("还没有与你相关的动态");

    await user.type(
      screen.getByPlaceholderText("以纯文本发表留言…"),
      "只发一次",
    );
    await user.click(screen.getByRole("button", { name: /^发\s*表$/ }));
    await user.click(screen.getByRole("button", { name: /^发\s*表$/ }));
    expect(postMessage).toHaveBeenCalledTimes(1);
    expect(postMessage).toHaveBeenCalledWith("p1", "只发一次");

    await act(async () => {
      resolvePost?.(messageByAlice);
      await Promise.resolve();
    });
    await waitFor(() => expect(list.mock.calls.length).toBeGreaterThan(1));
  });

  it("shows a scope-specific empty state", async () => {
    list.mockResolvedValue(page([]));
    renderActivity("p1");

    expect(await screen.findByText("还没有与你相关的动态")).toBeInTheDocument();
    expect(
      screen.getByText("你的操作、指派给你的待办和你的留言会显示在这里。"),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole("radio", { name: "成员动态" }));
    expect(await screen.findByText("还没有动态")).toBeInTheDocument();
    expect(
      screen.getByText("项目成员的操作和留言会显示在这里。"),
    ).toBeInTheDocument();
  });

  it("recovers from a first-page error with retry", async () => {
    const user = userEvent.setup();
    list.mockRejectedValueOnce(
      new Error("503 - service temporarily unavailable"),
    );
    renderActivity("p1");

    expect(
      await screen.findByText("service temporarily unavailable"),
    ).toBeInTheDocument();

    list.mockResolvedValueOnce(page([messageByAlice]));
    await user.click(screen.getByRole("button", { name: "重试" }));
    expect(await screen.findByText("第一条留言")).toBeInTheDocument();
  });

  it("clears the timeline and shows no-access when a refresh returns 404", async () => {
    const user = userEvent.setup();
    list.mockResolvedValueOnce(page([messageByAlice]));
    renderActivity("p1");
    await screen.findByText("第一条留言");

    list.mockRejectedValueOnce(
      new Error(
        '404 - {"error":{"code":"NOT_FOUND","message":"project not found"}}',
      ),
    );
    await user.click(screen.getByRole("button", { name: "刷新动态" }));

    await waitFor(() => expect(screen.queryByText("第一条留言")).toBeNull());
    expect(
      await screen.findByText("项目不存在或你无权访问"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("项目动态已清除；重新加载成功前不会显示旧内容。"),
    ).toBeInTheDocument();
  });

  it("clears already loaded rows when load more returns 404 after revocation", async () => {
    const user = userEvent.setup();
    list.mockResolvedValueOnce(page([messageByAlice], "next-page"));
    renderActivity("p1");
    await screen.findByText("第一条留言");

    list.mockRejectedValueOnce(
      new Error(
        '404 - {"error":{"code":"NOT_FOUND","message":"project not found"}}',
      ),
    );
    await user.click(screen.getByRole("button", { name: "加载更多" }));

    await waitFor(() => expect(screen.queryByText("第一条留言")).toBeNull());
    expect(
      await screen.findByText("项目不存在或你无权访问"),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "发表" })).toBeNull();
  });

  it("keeps no-access after a post 404 even when an earlier list resolves late", async () => {
    const user = userEvent.setup();
    let resolveList: ((value: ReturnType<typeof page>) => void) | null = null;
    list.mockImplementationOnce(
      () => new Promise((resolve) => (resolveList = resolve)),
    );
    postMessage.mockRejectedValueOnce(
      new Error(
        '404 - {"error":{"code":"NOT_FOUND","message":"project not found"}}',
      ),
    );
    renderActivity("p1");

    await user.type(
      screen.getByPlaceholderText("以纯文本发表留言…"),
      "撤权后不该保留的留言",
    );
    await user.click(screen.getByRole("button", { name: "发表" }));
    expect(
      await screen.findByText("项目不存在或你无权访问"),
    ).toBeInTheDocument();

    await act(async () => {
      resolveList?.(page([messageByAlice]));
      await Promise.resolve();
    });
    expect(screen.queryByText("第一条留言")).toBeNull();
    expect(screen.queryByRole("button", { name: "发表" })).toBeNull();
  });

  it("ignores a late response from the previous project", async () => {
    let resolveP1: ((value: ReturnType<typeof page>) => void) | null = null;
    list.mockImplementationOnce(
      () => new Promise((resolve) => (resolveP1 = resolve)),
    );
    const { rerender } = renderActivity("p1");
    expect(screen.queryByText("第一条留言")).toBeNull();

    list.mockResolvedValueOnce(page([messageByBob]));
    rerender(<ProjectActivity projectId="p2" />);
    expect(await screen.findByText("第二条留言")).toBeInTheDocument();

    await act(async () => {
      resolveP1?.(page([messageByAlice]));
      await Promise.resolve();
    });

    expect(screen.queryByText("第一条留言")).toBeNull();
    expect(screen.getByText("第二条留言")).toBeInTheDocument();
  });

  it("ignores the success of a message posted before switching projects", async () => {
    const user = userEvent.setup();
    let resolveOldPost: ((value: ProjectActivityItem) => void) | null = null;
    postMessage.mockImplementationOnce(
      () => new Promise((resolve) => (resolveOldPost = resolve)),
    );
    const { rerender } = renderActivity("p1");
    await screen.findByText("还没有与你相关的动态");
    await user.type(
      screen.getByPlaceholderText("以纯文本发表留言…"),
      "旧项目留言",
    );
    await user.click(screen.getByRole("button", { name: "发表" }));
    expect(postMessage).toHaveBeenCalledWith("p1", "旧项目留言");

    rerender(<ProjectActivity projectId="p2" />);
    await waitFor(() =>
      expect(list).toHaveBeenLastCalledWith("p2", {
        scope: "related",
        limit: 20,
        cursor: undefined,
      }),
    );
    const newProjectCalls = list.mock.calls.filter(
      (call) => call[0] === "p2",
    ).length;

    await act(async () => {
      resolveOldPost?.(messageByAlice);
      await Promise.resolve();
    });

    expect(list.mock.calls.filter((call) => call[0] === "p2")).toHaveLength(
      newProjectCalls,
    );
    expect(message.success).not.toHaveBeenCalled();
  });

  it("ignores a late response from the previous scope", async () => {
    let resolveRelated: ((value: ReturnType<typeof page>) => void) | null =
      null;
    list.mockImplementationOnce(
      () => new Promise((resolve) => (resolveRelated = resolve)),
    );
    renderActivity("p1");

    list.mockResolvedValueOnce(page([messageByBob]));
    fireEvent.click(screen.getByRole("radio", { name: "成员动态" }));
    expect(await screen.findByText("第二条留言")).toBeInTheDocument();

    await act(async () => {
      resolveRelated?.(page([messageByAlice]));
      await Promise.resolve();
    });

    expect(screen.queryByText("第一条留言")).toBeNull();
    expect(screen.getByText("第二条留言")).toBeInTheDocument();
  });

  it("renders a script payload as inert text", async () => {
    const scriptItem: ProjectActivityItem = {
      ...messageByAlice,
      message_body: '<script>alert("x")</script>',
    };
    list.mockResolvedValue(page([scriptItem]));
    const { container } = renderActivity("p1");

    const rendered = await screen.findByText('<script>alert("x")</script>');
    expect(rendered.querySelector("script")).toBeNull();
    expect(container.querySelectorAll("script")).toHaveLength(0);
  });

  it("shows a deleted actor in localized copy and omits raw target ids", async () => {
    list.mockResolvedValue(page([deletedActorMemberEvent, messageByAlice]));
    renderActivity("p1");

    expect(await screen.findByText("已删除用户")).toBeInTheDocument();
    expect(screen.getByText("加入了项目")).toBeInTheDocument();
    expect(screen.queryByText("secret-member-id")).toBeNull();
    expect(screen.queryByText("msg-secret-1")).toBeNull();
  });

  it("recovers from an invalid cursor with a refresh", async () => {
    const user = userEvent.setup();
    list.mockResolvedValueOnce(page([messageByAlice], "stale-token"));
    renderActivity("p1");
    await screen.findByText("第一条留言");

    list.mockRejectedValueOnce(
      new Error(
        '422 - {"error":{"code":"PROJECT_ACTIVITY_CURSOR_INVALID","message":"invalid cursor"}}',
      ),
    );
    await user.click(screen.getByRole("button", { name: "加载更多" }));

    expect(
      await screen.findByText("动态列表已更新，请刷新后继续浏览。"),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "加载更多" }),
    ).not.toBeInTheDocument();

    list.mockResolvedValueOnce(page([messageByBob, messageByAlice]));
    await user.click(screen.getByRole("button", { name: /^刷\s*新$/ }));
    expect(await screen.findByText("第二条留言")).toBeInTheDocument();
    expect(list).toHaveBeenLastCalledWith("p1", {
      scope: "related",
      limit: 20,
      cursor: undefined,
    });
  });
});

describe("project activity locale parity", () => {
  it("has the same projects.activity keys in zh and en plus the cursor error code", () => {
    const zhActivity = Object.keys(zh.projects.activity).sort();
    const enActivity = Object.keys(en.projects.activity).sort();
    expect(zhActivity).toEqual(enActivity);
    expect(zhActivity.length).toBeGreaterThan(0);
    for (const key of zhActivity) {
      expect(
        (zh.projects.activity as Record<string, string>)[key].trim().length,
      ).toBeGreaterThan(0);
      expect(
        (en.projects.activity as Record<string, string>)[key].trim().length,
      ).toBeGreaterThan(0);
    }
    expect(zh.apiErrors.PROJECT_ACTIVITY_CURSOR_INVALID).toBeTruthy();
    expect(en.apiErrors.PROJECT_ACTIVITY_CURSOR_INVALID).toBeTruthy();
  });
});
