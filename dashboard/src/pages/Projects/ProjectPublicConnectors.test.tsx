import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { get, api, currentUser } = vi.hoisted(() => ({
  get: vi.fn(),
  api: {
    list: vi.fn(),
    create: vi.fn(),
    rename: vi.fn(),
    replaceCredentials: vi.fn(),
    revoke: vi.fn(),
  },
  currentUser: {
    value: { id: 1, username: "alice" } as {
      id: number;
      username: string;
    } | null,
  },
}));

vi.mock("../../api/modules/projects", () => ({
  projectsApi: { get },
}));

vi.mock("../../api/modules/projectPublicConnectors", () => ({
  projectPublicConnectorsApi: api,
}));

vi.mock("../../hooks/useCurrentUser", () => ({
  useCurrentUser: () => currentUser.value,
}));

vi.mock("../../hooks/useServerTimezone", () => ({
  useServerTimezone: () => "UTC",
}));

import ProjectPublicConnectors from "./ProjectPublicConnectors";

type Role = "owner" | "admin" | "member";

function project(extra: Partial<{ my_role: Role; archived: boolean }> = {}) {
  return {
    project_id: "p1",
    name: "熊宝项目",
    description: "项目Description",
    my_role: "owner" as Role,
    member_count: 2,
    created_at: 1_700_000_000,
    updated_at: 1_700_000_100,
    instructions: "",
    instructions_sha256: "sha-empty",
    archived: false,
    ...extra,
  };
}

function connector(
  extra: Partial<{
    connector_id: string;
    display_name: string;
    description: string;
    state: "active" | "revoked";
    grant_revision: number;
    updated_at: number;
  }> = {},
) {
  return {
    connector_id: "conn-1",
    kind: "http_mcp_static_bearer" as const,
    display_name: "WorkBuddy",
    description: "Public MCP settings",
    state: "active" as const,
    grant_revision: 4,
    created_at: 1_700_000_000,
    updated_at: 1_700_000_200,
    ...extra,
  };
}

function collection(items = [connector()], revision = 11) {
  return { project_id: "p1", public_connectors_revision: revision, items };
}

function apiError(code: string, status = 409) {
  return new Error(
    `Request failed: ${status} Error - {"error":{"code":"${code}","message":"${code}"}}`,
  );
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function renderPanel(projectId = "p1") {
  return render(<ProjectPublicConnectors projectId={projectId} />);
}

beforeEach(() => {
  vi.clearAllMocks();
  const nativeGetComputedStyle = window.getComputedStyle.bind(window);
  vi.spyOn(window, "getComputedStyle").mockImplementation((element) =>
    nativeGetComputedStyle(element),
  );
  currentUser.value = { id: 1, username: "alice" };
  get.mockResolvedValue(project());
  api.list.mockResolvedValue(collection());
  api.create.mockResolvedValue(
    collection([connector({ display_name: "Created" })], 12),
  );
  api.rename.mockResolvedValue(
    collection([connector({ display_name: "Renamed" })], 12),
  );
  api.replaceCredentials.mockResolvedValue(collection([connector()], 12));
  api.revoke.mockResolvedValue(
    collection([connector({ state: "revoked" })], 12),
  );
});

afterEach(() => vi.restoreAllMocks());

describe("ProjectPublicConnectors", () => {
  it.each([
    new Error("Network error at https://example.test/private synthetic-secret"),
    new Error(
      'Request failed: 409 Error - {"error":{"code":"PROJECT_PUBLIC_CONNECTOR_UNAVAILABLE","message":"synthetic-secret","details":{"reason":"private_storage_reason"}}}',
    ),
  ])(
    "does not render raw credential-bearing failures or storage reasons (%#)",
    async (failure) => {
      const user = userEvent.setup();
      renderPanel();
      await screen.findByText("WorkBuddy");
      await user.click(
        screen.getByRole("button", { name: "Replace credentials" }),
      );
      await user.type(
        screen.getByLabelText("HTTPS endpoint"),
        "https://example.test/private",
      );
      await user.type(
        screen.getByLabelText("Bearer token"),
        "synthetic-secret",
      );
      api.replaceCredentials.mockRejectedValue(failure);
      await user.click(screen.getByRole("button", { name: "Save" }));
      await waitFor(() =>
        expect(api.replaceCredentials).toHaveBeenCalledTimes(1),
      );
      await waitFor(() =>
        expect(screen.getByLabelText("Bearer token")).toHaveValue(""),
      );
      expect(screen.queryByText(/Network error at/)).toBeNull();
      expect(screen.queryByText(/private_storage_reason/)).toBeNull();
      expect(screen.queryByText("synthetic-secret")).toBeNull();
      expect(screen.getByLabelText("HTTPS endpoint")).toHaveValue("");
    },
  );

  it("disables an open rename modal when conflict refresh loses manager role", async () => {
    const user = userEvent.setup();
    renderPanel();
    await screen.findByText("WorkBuddy");
    await user.click(screen.getByRole("button", { name: "Rename" }));
    get.mockResolvedValue(project({ my_role: "member" }));
    api.rename.mockRejectedValue(apiError("PROJECT_PUBLIC_CONNECTORS_CHANGED"));
    await user.click(screen.getByRole("button", { name: "Save" }));
    await screen.findByText(
      "Members can view safe connector metadata. Owners and admins manage settings.",
    );
    const save = screen.queryByRole("button", { name: "Save" });
    if (save) expect(save).toBeDisabled();
    expect(api.rename).toHaveBeenCalledTimes(1);
  });

  it("clears stale manager state immediately on a 401 without a refresh replay", async () => {
    const user = userEvent.setup();
    renderPanel();
    await screen.findByText("WorkBuddy");
    await user.click(screen.getByRole("button", { name: "Rename" }));
    api.rename.mockRejectedValue(new Error("Unauthorized"));
    await user.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(api.rename).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(screen.queryByText("WorkBuddy")).toBeNull();
    expect(screen.queryByRole("button", { name: "Add connector" })).toBeNull();
    expect(api.list).toHaveBeenCalledTimes(1);
  });

  it.each([403, 404])(
    "drops the current modal and metadata on access loss %i",
    async (status) => {
      const user = userEvent.setup();
      renderPanel();
      await screen.findByText("WorkBuddy");
      await user.click(
        screen.getByRole("button", { name: "Replace credentials" }),
      );
      await user.type(
        screen.getByLabelText("HTTPS endpoint"),
        "https://example.test/private",
      );
      await user.type(
        screen.getByLabelText("Bearer token"),
        "synthetic-secret",
      );
      api.replaceCredentials.mockRejectedValue(
        apiError(status === 403 ? "FORBIDDEN" : "NOT_FOUND", status),
      );
      await user.click(screen.getByRole("button", { name: "Save" }));
      await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
      expect(screen.queryByText("WorkBuddy")).toBeNull();
      expect(screen.queryByDisplayValue("synthetic-secret")).toBeNull();
      expect(api.list).toHaveBeenCalledTimes(1);
    },
  );

  it.each(["archived", "revoked"])(
    "uses fresh %s state to disable a stale rename modal",
    async (state) => {
      const user = userEvent.setup();
      renderPanel();
      await screen.findByText("WorkBuddy");
      await user.click(screen.getByRole("button", { name: "Rename" }));
      api.rename.mockRejectedValue(
        apiError("PROJECT_PUBLIC_CONNECTORS_CHANGED"),
      );
      get.mockResolvedValue(project({ archived: state === "archived" }));
      api.list.mockResolvedValue(
        collection(
          [
            connector({
              state: state === "revoked" ? "revoked" : "active",
              grant_revision: 5,
            }),
          ],
          12,
        ),
      );
      await user.click(screen.getByRole("button", { name: "Save" }));
      await screen.findByText(
        "Connector settings changed on the server. Safe metadata was refreshed; re-enter credentials before submitting again.",
      );
      await waitFor(() =>
        expect(screen.getByRole("button", { name: "Save" })).toBeDisabled(),
      );
      expect(api.rename).toHaveBeenCalledTimes(1);
    },
  );

  it("uses refreshed CAS revisions only after an explicit rename resubmission", async () => {
    const user = userEvent.setup();
    renderPanel();
    await screen.findByText("WorkBuddy");
    await user.click(screen.getByRole("button", { name: "Rename" }));
    api.rename.mockRejectedValueOnce(
      apiError("PROJECT_PUBLIC_CONNECTORS_CHANGED"),
    );
    api.list.mockResolvedValue(
      collection([connector({ grant_revision: 8 })], 18),
    );
    await user.click(screen.getByRole("button", { name: "Save" }));
    await screen.findByText(
      "Connector settings changed on the server. Safe metadata was refreshed; re-enter credentials before submitting again.",
    );
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Save" })).toBeEnabled(),
    );
    expect(api.rename).toHaveBeenCalledTimes(1);
    await user.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(api.rename).toHaveBeenCalledTimes(2));
    expect(api.rename).toHaveBeenLastCalledWith("p1", "conn-1", {
      expected_project_revision: 18,
      expected_grant_revision: 8,
      display_name: "WorkBuddy",
      description: "Public MCP settings",
    });
  });

  it.each(["success", "failure"])(
    "ignores a late modal %s after close and reopen",
    async (outcome) => {
      const user = userEvent.setup();
      const pending = deferred<ReturnType<typeof collection>>();
      api.replaceCredentials.mockReturnValueOnce(pending.promise);
      renderPanel();
      await screen.findByText("WorkBuddy");
      await user.click(
        screen.getByRole("button", { name: "Replace credentials" }),
      );
      await user.type(
        screen.getByLabelText("HTTPS endpoint"),
        "https://example.test/first",
      );
      await user.type(screen.getByLabelText("Bearer token"), "first-draft");
      await user.click(screen.getByRole("button", { name: "Save" }));
      await user.click(screen.getByRole("button", { name: "Cancel" }));
      await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
      await user.click(
        screen.getByRole("button", { name: "Replace credentials" }),
      );
      await user.type(screen.getByLabelText("Bearer token"), "new-draft");
      await act(async () => {
        if (outcome === "success")
          pending.resolve(
            collection([connector({ display_name: "Stale completion" })], 77),
          );
        else pending.reject(apiError("PROJECT_PUBLIC_CONNECTORS_CHANGED"));
      });
      expect(screen.getByLabelText("Bearer token")).toHaveValue("new-draft");
      expect(screen.getByRole("dialog")).toBeInTheDocument();
      expect(screen.queryByText("Stale completion")).toBeNull();
      expect(api.list).toHaveBeenCalledTimes(1);
    },
  );

  it.each(["actor", "project"])(
    "ignores an old %s A to B to A load, including late failure",
    async (change) => {
      const firstGet = deferred<ReturnType<typeof project>>();
      const firstList = deferred<ReturnType<typeof collection>>();
      get.mockReturnValueOnce(firstGet.promise);
      api.list.mockReturnValueOnce(firstList.promise);
      const view = renderPanel();
      if (change === "actor") currentUser.value = { id: 2, username: "bob" };
      view.rerender(
        <ProjectPublicConnectors
          projectId={change === "project" ? "p2" : "p1"}
        />,
      );
      await screen.findByText("WorkBuddy");
      if (change === "actor") currentUser.value = { id: 1, username: "alice" };
      api.list.mockResolvedValue(
        collection([connector({ display_name: "Current A" })], 33),
      );
      view.rerender(<ProjectPublicConnectors projectId="p1" />);
      await screen.findByText("Current A");
      await act(async () => {
        firstGet.reject(apiError("FORBIDDEN", 403));
        firstList.resolve(
          collection([connector({ display_name: "Old A" })], 11),
        );
      });
      expect(screen.getByText("Current A")).toBeInTheDocument();
      expect(screen.queryByText("Old A")).toBeNull();
      expect(
        screen.getByRole("button", { name: "Add connector" }),
      ).toBeEnabled();
    },
  );

  it.each(["success", "failure"])(
    "clears an actor-switch draft and ignores old actor mutation %s after A to B to A",
    async (outcome) => {
      const user = userEvent.setup();
      const pending = deferred<ReturnType<typeof collection>>();
      api.replaceCredentials.mockReturnValueOnce(pending.promise);
      const view = renderPanel();
      await screen.findByText("WorkBuddy");
      await user.click(
        screen.getByRole("button", { name: "Replace credentials" }),
      );
      await user.type(
        screen.getByLabelText("HTTPS endpoint"),
        "https://example.test/old",
      );
      await user.type(
        screen.getByLabelText("Bearer token"),
        "old-actor-secret",
      );
      await user.click(screen.getByRole("button", { name: "Save" }));
      currentUser.value = { id: 2, username: "bob" };
      view.rerender(<ProjectPublicConnectors projectId="p1" />);
      expect(screen.queryByDisplayValue("old-actor-secret")).toBeNull();
      await screen.findByText("WorkBuddy");
      currentUser.value = { id: 1, username: "alice" };
      view.rerender(<ProjectPublicConnectors projectId="p1" />);
      await screen.findByText("WorkBuddy");
      await user.click(
        screen.getByRole("button", { name: "Replace credentials" }),
      );
      await user.type(screen.getByLabelText("Bearer token"), "new-actor-draft");
      await act(async () => {
        if (outcome === "success")
          pending.resolve(
            collection([connector({ display_name: "Old actor mutation" })], 77),
          );
        else pending.reject(apiError("FORBIDDEN", 403));
      });
      expect(screen.getByLabelText("Bearer token")).toHaveValue(
        "new-actor-draft",
      );
      expect(screen.queryByText("Old actor mutation")).toBeNull();
      expect(api.list).toHaveBeenCalledTimes(3);
    },
  );

  it("removes the portal on unmount and cannot mutate a separately mounted panel", async () => {
    const user = userEvent.setup();
    const pending = deferred<ReturnType<typeof collection>>();
    api.replaceCredentials.mockReturnValueOnce(pending.promise);
    const view = renderPanel();
    await screen.findByText("WorkBuddy");
    await user.click(
      screen.getByRole("button", { name: "Replace credentials" }),
    );
    await user.type(
      screen.getByLabelText("HTTPS endpoint"),
      "https://example.test/old",
    );
    await user.type(screen.getByLabelText("Bearer token"), "unmounted-secret");
    await user.click(screen.getByRole("button", { name: "Save" }));
    view.unmount();
    expect(screen.queryByDisplayValue("unmounted-secret")).toBeNull();
    renderPanel();
    await screen.findByText("WorkBuddy");
    await act(async () =>
      pending.resolve(
        collection([connector({ display_name: "Unmounted response" })], 99),
      ),
    );
    expect(screen.queryByText("Unmounted response")).toBeNull();
  });
  it("loads current project detail before showing member-safe metadata", async () => {
    get.mockResolvedValue(project({ my_role: "member" }));

    renderPanel();

    expect(await screen.findByText("WorkBuddy")).toBeInTheDocument();
    expect(get).toHaveBeenCalledWith("p1");
    expect(api.list).toHaveBeenCalledWith("p1");
    expect(
      screen.getByText(
        "Members can view safe connector metadata. Owners and admins manage settings.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Add connector" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Rename" })).toBeNull();
  });

  it("creates a connector with flat CAS fields and clears volatile secrets after success", async () => {
    const user = userEvent.setup();
    api.list.mockResolvedValue(collection([], 21));

    renderPanel();

    await screen.findByText("No public connectors configured.");
    await user.click(screen.getByRole("button", { name: "Add connector" }));
    await user.type(screen.getByLabelText("Display name"), "WorkBuddy");
    await user.type(screen.getByLabelText("Description"), "Public MCP");
    await user.type(
      screen.getByLabelText("HTTPS endpoint"),
      "https://example.test/mcp",
    );
    await user.type(screen.getByLabelText("Bearer token"), "secret-token");
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(api.create).toHaveBeenCalledTimes(1));
    expect(api.create).toHaveBeenCalledWith("p1", {
      expected_project_revision: 21,
      kind: "http_mcp_static_bearer",
      display_name: "WorkBuddy",
      description: "Public MCP",
      endpoint: "https://example.test/mcp",
      bearer_token: "secret-token",
    });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(screen.queryByDisplayValue("https://example.test/mcp")).toBeNull();
    expect(screen.queryByDisplayValue("secret-token")).toBeNull();
  });

  it("keeps archived projects read-safe and allows only active connector revoke", async () => {
    get.mockResolvedValue(project({ archived: true }));
    api.list.mockResolvedValue(
      collection([
        connector({
          connector_id: "active-1",
          display_name: "Active connector",
        }),
        connector({
          connector_id: "revoked-1",
          display_name: "Revoked connector",
          state: "revoked",
        }),
      ]),
    );

    renderPanel();

    expect(await screen.findByText("Active connector")).toBeInTheDocument();
    expect(
      screen.getByText(
        "This project is archived. You can read settings and revoke active connectors, but cannot create, rename, or replace credentials.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Add connector" }),
    ).toBeDisabled();
    const activeRegion = screen.getByText("Active connector").closest("li");
    expect(activeRegion).not.toBeNull();
    expect(
      within(activeRegion as HTMLElement).getByRole("button", {
        name: "Rename",
      }),
    ).toBeDisabled();
    expect(
      within(activeRegion as HTMLElement).getByRole("button", {
        name: "Replace credentials",
      }),
    ).toBeDisabled();
    expect(
      within(activeRegion as HTMLElement).getByRole("button", {
        name: "Revoke",
      }),
    ).toBeEnabled();
    const revokedRegion = screen.getByText("Revoked connector").closest("li");
    expect(revokedRegion).not.toBeNull();
    expect(
      within(revokedRegion as HTMLElement).getByRole("button", {
        name: "Revoke",
      }),
    ).toBeDisabled();
  });

  it("does not replay credentials after a CAS conflict and clears write-only fields", async () => {
    const user = userEvent.setup();
    api.list
      .mockResolvedValueOnce(collection([], 31))
      .mockResolvedValueOnce(
        collection([connector({ display_name: "Server value" })], 32),
      );
    api.create.mockRejectedValue(apiError("PROJECT_PUBLIC_CONNECTORS_CHANGED"));

    renderPanel();

    await screen.findByText("No public connectors configured.");
    await user.click(screen.getByRole("button", { name: "Add connector" }));
    await user.type(screen.getByLabelText("Display name"), "Local draft");
    await user.type(
      screen.getByLabelText("HTTPS endpoint"),
      "https://example.test/mcp",
    );
    await user.type(screen.getByLabelText("Bearer token"), "secret-token");
    await user.click(screen.getByRole("button", { name: "Save" }));

    expect(
      await screen.findByText(
        "Connector settings changed on the server. Safe metadata was refreshed; re-enter credentials before submitting again.",
      ),
    ).toBeInTheDocument();
    expect(api.create).toHaveBeenCalledTimes(1);
    expect(api.list).toHaveBeenCalledTimes(2);
    expect(await screen.findByText("Server value")).toBeInTheDocument();
    expect(screen.getByLabelText("Display name")).toHaveValue("Local draft");
    expect(screen.getByLabelText("HTTPS endpoint")).toHaveValue("");
    expect(screen.getByLabelText("Bearer token")).toHaveValue("");
  });

  it("clears volatile secrets when the modal is canceled", async () => {
    const user = userEvent.setup();

    renderPanel();

    await screen.findByText("WorkBuddy");
    await user.click(
      screen.getByRole("button", { name: "Replace credentials" }),
    );
    await user.type(
      screen.getByLabelText("HTTPS endpoint"),
      "https://example.test/new",
    );
    await user.type(screen.getByLabelText("Bearer token"), "new-secret");
    await user.click(screen.getByRole("button", { name: "Cancel" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    await user.click(
      screen.getByRole("button", { name: "Replace credentials" }),
    );
    expect(screen.getByLabelText("HTTPS endpoint")).toHaveValue("");
    expect(screen.getByLabelText("Bearer token")).toHaveValue("");
  });

  it("drops stale actor responses when the signed-in user changes for the same project", async () => {
    const firstGet = deferred<ReturnType<typeof project>>();
    const firstList = deferred<ReturnType<typeof collection>>();
    const secondGet = deferred<ReturnType<typeof project>>();
    const secondList = deferred<ReturnType<typeof collection>>();
    get
      .mockReturnValueOnce(firstGet.promise)
      .mockReturnValueOnce(secondGet.promise);
    api.list
      .mockReturnValueOnce(firstList.promise)
      .mockReturnValueOnce(secondList.promise);

    const view = renderPanel();
    currentUser.value = { id: 2, username: "bob" };
    view.rerender(<ProjectPublicConnectors projectId="p1" />);

    await act(async () => {
      secondGet.resolve(project({ my_role: "admin" }));
      secondList.resolve(
        collection([connector({ display_name: "Fresh actor data" })], 42),
      );
    });
    expect(await screen.findByText("Fresh actor data")).toBeInTheDocument();

    await act(async () => {
      firstGet.resolve(project());
      firstList.resolve(
        collection([connector({ display_name: "Stale actor data" })], 12),
      );
    });

    expect(screen.getByText("Fresh actor data")).toBeInTheDocument();
    expect(screen.queryByText("Stale actor data")).toBeNull();
  });
});
