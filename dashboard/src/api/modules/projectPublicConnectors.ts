import { request } from "../request";

export type ProjectPublicConnectorKind = "http_mcp_static_bearer";
export type ProjectPublicConnectorState = "active" | "revoked";

export interface ProjectPublicConnector {
  connector_id: string;
  kind: ProjectPublicConnectorKind;
  display_name: string;
  description: string;
  state: ProjectPublicConnectorState;
  grant_revision: number;
  created_at: number;
  updated_at: number;
}

export interface ProjectPublicConnectorsResponse {
  project_id: string;
  public_connectors_revision: number;
  items: ProjectPublicConnector[];
}

export interface ProjectPublicConnectorCreateBody {
  expected_project_revision: number;
  kind: ProjectPublicConnectorKind;
  display_name: string;
  description: string;
  endpoint: string;
  bearer_token: string;
}

export interface ProjectPublicConnectorRenameBody {
  expected_project_revision: number;
  expected_grant_revision: number;
  display_name: string;
  description: string;
}

export interface ProjectPublicConnectorCredentialBody {
  expected_project_revision: number;
  expected_grant_revision: number;
  endpoint: string;
  bearer_token: string;
}

export interface ProjectPublicConnectorRevokeBody {
  expected_project_revision: number;
  expected_grant_revision: number;
}

const connectorsPath = (projectId: string) =>
  `/projects/${encodeURIComponent(projectId)}/public-connectors`;

const connectorPath = (projectId: string, connectorId: string) =>
  `${connectorsPath(projectId)}/${encodeURIComponent(connectorId)}`;

export const projectPublicConnectorsApi = {
  list: (projectId: string) =>
    request<ProjectPublicConnectorsResponse>(connectorsPath(projectId)),
  create: (projectId: string, body: ProjectPublicConnectorCreateBody) =>
    request<ProjectPublicConnectorsResponse>(connectorsPath(projectId), {
      method: "POST",
      body: JSON.stringify({
        expected_project_revision: body.expected_project_revision,
        kind: body.kind,
        display_name: body.display_name,
        description: body.description,
        endpoint: body.endpoint,
        bearer_token: body.bearer_token,
      }),
    }),
  rename: (
    projectId: string,
    connectorId: string,
    body: ProjectPublicConnectorRenameBody,
  ) =>
    request<ProjectPublicConnectorsResponse>(
      connectorPath(projectId, connectorId),
      {
        method: "PATCH",
        body: JSON.stringify({
          expected_project_revision: body.expected_project_revision,
          expected_grant_revision: body.expected_grant_revision,
          display_name: body.display_name,
          description: body.description,
        }),
      },
    ),
  replaceCredentials: (
    projectId: string,
    connectorId: string,
    body: ProjectPublicConnectorCredentialBody,
  ) =>
    request<ProjectPublicConnectorsResponse>(
      `${connectorPath(projectId, connectorId)}/credentials`,
      {
        method: "PUT",
        body: JSON.stringify({
          expected_project_revision: body.expected_project_revision,
          expected_grant_revision: body.expected_grant_revision,
          endpoint: body.endpoint,
          bearer_token: body.bearer_token,
        }),
      },
    ),
  revoke: (
    projectId: string,
    connectorId: string,
    body: ProjectPublicConnectorRevokeBody,
  ) =>
    request<ProjectPublicConnectorsResponse>(
      `${connectorPath(projectId, connectorId)}/revoke`,
      {
        method: "POST",
        body: JSON.stringify({
          expected_project_revision: body.expected_project_revision,
          expected_grant_revision: body.expected_grant_revision,
        }),
      },
    ),
};
