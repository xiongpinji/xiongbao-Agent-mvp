import { request } from "../request";
import type { ProjectMember, ProjectRole } from "./projects";

export interface ProjectInvite {
  invite_id: string;
  project_id: string;
  role: "member";
  requires_approval: boolean;
  created_by: number;
  created_at: number;
  expires_at: number;
  revoked_at: number | null;
  consumed_at: number | null;
  consumed_by_user_id: number | null;
  status: "pending" | "used" | "revoked" | "expired";
}

export interface CreatedProjectInvite extends ProjectInvite {
  /** Returned only once by POST; never present in list responses. */
  token: string;
}

export interface ProjectJoinRequest {
  request_id: string;
  project_id: string;
  invite_id: string | null;
  user_id: number;
  username: string;
  status: "pending" | "approved" | "rejected";
  requested_at: number;
  resolved_at: number | null;
  resolved_by: number | null;
}

export type InviteAcceptance =
  | { status: "joined"; project_id: string; role: ProjectRole }
  | { status: "pending_approval"; project_id: string; request_id: string };

export interface CreateProjectInviteBody {
  requires_approval: boolean;
  expires_in_days: number;
}

const base = (projectId: string) =>
  `/projects/${encodeURIComponent(projectId)}`;

export const projectMembershipApi = {
  createInvite: (projectId: string, body: CreateProjectInviteBody) =>
    request<CreatedProjectInvite>(`${base(projectId)}/invites`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  listInvites: (projectId: string) =>
    request<{ items: ProjectInvite[] }>(`${base(projectId)}/invites`),
  revokeInvite: (projectId: string, inviteId: string) =>
    request<ProjectInvite>(
      `${base(projectId)}/invites/${encodeURIComponent(inviteId)}/revoke`,
      { method: "POST" },
    ),
  acceptInvite: (token: string) =>
    request<InviteAcceptance>("/projects/invites/accept", {
      method: "POST",
      body: JSON.stringify({ token }),
    }),
  listJoinRequests: (
    projectId: string,
    status?: ProjectJoinRequest["status"],
  ) =>
    request<{ items: ProjectJoinRequest[] }>(
      `${base(projectId)}/join-requests${
        status ? `?status=${encodeURIComponent(status)}` : ""
      }`,
    ),
  approveJoinRequest: (projectId: string, requestId: string) =>
    request<ProjectJoinRequest>(
      `${base(projectId)}/join-requests/${encodeURIComponent(
        requestId,
      )}/approve`,
      { method: "POST" },
    ),
  rejectJoinRequest: (projectId: string, requestId: string) =>
    request<ProjectJoinRequest>(
      `${base(projectId)}/join-requests/${encodeURIComponent(
        requestId,
      )}/reject`,
      { method: "POST" },
    ),
  setMemberRole: (
    projectId: string,
    userId: number,
    role: "member" | "admin",
  ) =>
    request<ProjectMember>(`${base(projectId)}/members/${userId}`, {
      method: "PATCH",
      body: JSON.stringify({ role }),
    }),
  removeMember: (projectId: string, userId: number) =>
    request<void>(`${base(projectId)}/members/${userId}`, {
      method: "DELETE",
    }),
};
