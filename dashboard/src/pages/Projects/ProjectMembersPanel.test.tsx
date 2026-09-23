import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const {
  listInvites,
  listJoinRequests,
  createInvite,
  approveJoinRequest,
  copyText,
} = vi.hoisted(() => ({
  listInvites: vi.fn(),
  listJoinRequests: vi.fn(),
  createInvite: vi.fn(),
  approveJoinRequest: vi.fn(),
  copyText: vi.fn(),
}));

vi.mock("../../api/modules/projectMembership", () => ({
  projectMembershipApi: {
    listInvites,
    listJoinRequests,
    createInvite,
    approveJoinRequest,
  },
}));
vi.mock("../../utils/copyText", () => ({ copyText }));

import ProjectMembersPanel from "./ProjectMembersPanel";

const members = [{ user_id: 1, username: "alice", role: "owner" as const }];

beforeEach(() => {
  vi.clearAllMocks();
  const nativeGetComputedStyle = window.getComputedStyle.bind(window);
  vi.spyOn(window, "getComputedStyle").mockImplementation((element) =>
    nativeGetComputedStyle(element),
  );
  listInvites.mockResolvedValue({ items: [] });
  listJoinRequests.mockResolvedValue({ items: [] });
  copyText.mockResolvedValue(true);
});
afterEach(() => vi.restoreAllMocks());

describe("ProjectMembersPanel", () => {
  it("lets an owner create a one-use link and shows the token only in that response", async () => {
    const user = userEvent.setup();
    createInvite.mockResolvedValue({
      invite_id: "i1",
      project_id: "p1",
      token: "secret-token",
      role: "member",
      requires_approval: false,
      created_by: 1,
      created_at: 1,
      expires_at: 2,
      revoked_at: null,
      consumed_at: null,
      consumed_by_user_id: null,
      status: "pending",
    });

    render(
      <ProjectMembersPanel
        projectId="p1"
        role="owner"
        members={members}
        onChanged={vi.fn()}
      />,
    );

    await user.click(screen.getByRole("button", { name: "邀请成员" }));
    expect(await screen.findByText("尚无邀请链接")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "生成邀请链接" }));

    expect(createInvite).toHaveBeenCalledWith("p1", {
      requires_approval: false,
      expires_in_days: 7,
    });
    expect(
      await screen.findByDisplayValue(/invite=secret-token/),
    ).toBeInTheDocument();
    expect(screen.getByText(/链接仅在此显示一次/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /复\s*制/ }));
    expect(copyText).toHaveBeenCalledWith(
      `${window.location.origin}/projects?invite=secret-token`,
    );
    expect(
      screen.getByRole("button", { name: /已\s*复\s*制/ }),
    ).toBeInTheDocument();
  });

  it("shows members without manager actions to a regular member", () => {
    render(
      <ProjectMembersPanel
        projectId="p1"
        role="member"
        members={[...members, { user_id: 2, username: "bob", role: "member" }]}
        onChanged={vi.fn()}
      />,
    );

    expect(screen.getByText("alice")).toBeInTheDocument();
    expect(screen.getByText("bob")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "邀请成员" })).toBeNull();
    expect(listInvites).not.toHaveBeenCalled();
  });

  it("approves a real pending request and refreshes the member list", async () => {
    const user = userEvent.setup();
    const onChanged = vi.fn();
    listJoinRequests
      .mockResolvedValueOnce({
        items: [
          {
            request_id: "r1",
            project_id: "p1",
            invite_id: "i1",
            user_id: 2,
            username: "bob",
            status: "pending",
            requested_at: 1,
            resolved_at: null,
            resolved_by: null,
          },
        ],
      })
      .mockResolvedValue({ items: [] });
    approveJoinRequest.mockResolvedValue({ status: "approved" });

    render(
      <ProjectMembersPanel
        projectId="p1"
        role="owner"
        members={members}
        onChanged={onChanged}
      />,
    );

    await user.click(screen.getByRole("button", { name: "邀请成员" }));
    expect(await screen.findByText("bob")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "通过" }));
    expect(approveJoinRequest).toHaveBeenCalledWith("p1", "r1");
    expect(onChanged).toHaveBeenCalledTimes(1);
  });

  it("does not offer an admin controls over another admin or the owner", () => {
    render(
      <ProjectMembersPanel
        projectId="p1"
        role="admin"
        members={[
          ...members,
          { user_id: 2, username: "charlie", role: "admin" },
          { user_id: 3, username: "bob", role: "member" },
        ]}
        onChanged={vi.fn()}
      />,
    );

    expect(screen.getAllByRole("button", { name: "移除" })).toHaveLength(1);
    expect(screen.queryByLabelText("更改成员角色")).toBeNull();
  });
});
