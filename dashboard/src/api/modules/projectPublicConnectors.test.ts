import { beforeEach, describe, expect, it, vi } from "vitest";

const { request } = vi.hoisted(() => ({
  request: vi.fn(),
}));

vi.mock("../request", () => ({ request }));

import { projectPublicConnectorsApi } from "./projectPublicConnectors";

beforeEach(() => {
  request.mockReset();
});

describe("projectPublicConnectorsApi", () => {
  it("lists the connector collection with encoded project ids", async () => {
    request.mockResolvedValue({
      project_id: "project/one",
      public_connectors_revision: 1,
      items: [],
    });

    await projectPublicConnectorsApi.list("project/one");

    expect(request).toHaveBeenCalledWith(
      "/projects/project%2Fone/public-connectors",
    );
  });

  it("creates connectors with the fixed flat write-only credential body", async () => {
    request.mockResolvedValue({
      project_id: "p1",
      public_connectors_revision: 2,
      items: [],
    });

    await projectPublicConnectorsApi.create("p1", {
      expected_project_revision: 7,
      kind: "http_mcp_static_bearer",
      display_name: "WorkBuddy",
      description: "Public MCP",
      endpoint: "https://example.test/mcp",
      bearer_token: "secret-token",
    });

    expect(request).toHaveBeenCalledWith("/projects/p1/public-connectors", {
      method: "POST",
      body: JSON.stringify({
        expected_project_revision: 7,
        kind: "http_mcp_static_bearer",
        display_name: "WorkBuddy",
        description: "Public MCP",
        endpoint: "https://example.test/mcp",
        bearer_token: "secret-token",
      }),
    });
    expect(JSON.parse(request.mock.calls[0][1].body)).not.toHaveProperty(
      "credential",
    );
  });

  it("renames connectors with project and grant CAS in a flat body", async () => {
    request.mockResolvedValue({
      project_id: "p1",
      public_connectors_revision: 3,
      items: [],
    });

    await projectPublicConnectorsApi.rename("p1", "conn/1", {
      expected_project_revision: 8,
      expected_grant_revision: 4,
      display_name: "New name",
      description: "Safe description",
    });

    expect(request).toHaveBeenCalledWith(
      "/projects/p1/public-connectors/conn%2F1",
      {
        method: "PATCH",
        body: JSON.stringify({
          expected_project_revision: 8,
          expected_grant_revision: 4,
          display_name: "New name",
          description: "Safe description",
        }),
      },
    );
  });

  it("replaces credentials with the fixed flat endpoint and bearer_token fields", async () => {
    request.mockResolvedValue({
      project_id: "p1",
      public_connectors_revision: 4,
      items: [],
    });

    await projectPublicConnectorsApi.replaceCredentials("p1", "conn-1", {
      expected_project_revision: 9,
      expected_grant_revision: 5,
      endpoint: "https://example.test/new",
      bearer_token: "new-secret",
    });

    expect(request).toHaveBeenCalledWith(
      "/projects/p1/public-connectors/conn-1/credentials",
      {
        method: "PUT",
        body: JSON.stringify({
          expected_project_revision: 9,
          expected_grant_revision: 5,
          endpoint: "https://example.test/new",
          bearer_token: "new-secret",
        }),
      },
    );
    expect(JSON.parse(request.mock.calls[0][1].body)).not.toHaveProperty(
      "credential",
    );
  });

  it("revokes connectors with only both CAS fields", async () => {
    request.mockResolvedValue({
      project_id: "p1",
      public_connectors_revision: 5,
      items: [],
    });

    await projectPublicConnectorsApi.revoke("p1", "conn-1", {
      expected_project_revision: 10,
      expected_grant_revision: 6,
    });

    expect(request).toHaveBeenCalledWith(
      "/projects/p1/public-connectors/conn-1/revoke",
      {
        method: "POST",
        body: JSON.stringify({
          expected_project_revision: 10,
          expected_grant_revision: 6,
        }),
      },
    );
  });
});
