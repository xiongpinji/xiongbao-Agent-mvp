import { beforeEach, describe, expect, it, vi } from "vitest";

const { request } = vi.hoisted(() => ({ request: vi.fn() }));
vi.mock("../request", () => ({ request }));

import { projectMembershipApi } from "./projectMembership";

beforeEach(() => request.mockClear());

describe("projectMembershipApi", () => {
  it("encodes project and invite identifiers and never puts a token in the URL", () => {
    projectMembershipApi.createInvite("p 1/2", {
      requires_approval: true,
      expires_in_days: 2,
    });
    projectMembershipApi.listInvites("p 1/2");
    projectMembershipApi.revokeInvite("p 1/2", "i/1");
    projectMembershipApi.acceptInvite("secret-token");

    expect(request).toHaveBeenNthCalledWith(1, "/projects/p%201%2F2/invites", {
      method: "POST",
      body: JSON.stringify({ requires_approval: true, expires_in_days: 2 }),
    });
    expect(request).toHaveBeenNthCalledWith(2, "/projects/p%201%2F2/invites");
    expect(request).toHaveBeenNthCalledWith(
      3,
      "/projects/p%201%2F2/invites/i%2F1/revoke",
      { method: "POST" },
    );
    expect(request).toHaveBeenNthCalledWith(4, "/projects/invites/accept", {
      method: "POST",
      body: JSON.stringify({ token: "secret-token" }),
    });
  });

  it("uses authenticated project routes for requests and role changes", () => {
    projectMembershipApi.listJoinRequests("p1", "pending");
    projectMembershipApi.approveJoinRequest("p1", "r 1");
    projectMembershipApi.rejectJoinRequest("p1", "r 2");
    projectMembershipApi.setMemberRole("p1", 7, "admin");
    projectMembershipApi.removeMember("p1", 7);

    expect(request).toHaveBeenNthCalledWith(
      1,
      "/projects/p1/join-requests?status=pending",
    );
    expect(request).toHaveBeenNthCalledWith(
      2,
      "/projects/p1/join-requests/r%201/approve",
      { method: "POST" },
    );
    expect(request).toHaveBeenNthCalledWith(
      3,
      "/projects/p1/join-requests/r%202/reject",
      { method: "POST" },
    );
    expect(request).toHaveBeenNthCalledWith(4, "/projects/p1/members/7", {
      method: "PATCH",
      body: JSON.stringify({ role: "admin" }),
    });
    expect(request).toHaveBeenNthCalledWith(5, "/projects/p1/members/7", {
      method: "DELETE",
    });
  });
});
