import { beforeEach, describe, expect, it, vi } from "vitest";

const { request, requestBlob, requestUpload } = vi.hoisted(() => ({
  request: vi.fn(),
  requestBlob: vi.fn(),
  requestUpload: vi.fn(),
}));

vi.mock("../request", () => ({ request, requestBlob, requestUpload }));

import {
  PROJECT_ASSET_VERSIONS_PAGE_SIZE,
  projectAssetsApi,
} from "./projectAssets";

beforeEach(() => {
  vi.clearAllMocks();
  request.mockResolvedValue({});
  requestBlob.mockResolvedValue(new Blob());
  requestUpload.mockResolvedValue({});
});

describe("projectAssetsApi version paths (PS-06B-1)", () => {
  it("lists encoded node versions with default pagination", () => {
    void projectAssetsApi.listVersions("p1", "node-1");

    expect(request).toHaveBeenCalledWith(
      `/projects/p1/assets/node-1/versions?limit=${PROJECT_ASSET_VERSIONS_PAGE_SIZE}&offset=0`,
    );
  });

  it("encodes project, node and explicit pagination", () => {
    void projectAssetsApi.listVersions("p 1/2", "n/1", {
      limit: 20,
      offset: 40,
    });

    expect(request).toHaveBeenCalledWith(
      "/projects/p%201%2F2/assets/n%2F1/versions?limit=20&offset=40",
    );
  });

  it("uploads a new version as multipart without a rename field", () => {
    const file = new File(["x"], "换个名字.txt", { type: "text/plain" });
    void projectAssetsApi.uploadVersion("p 1/2", "n/1", { file });

    expect(requestUpload).toHaveBeenCalledTimes(1);
    const [path, body] = requestUpload.mock.calls[0] as [string, FormData];
    expect(path).toBe("/projects/p%201%2F2/assets/n%2F1/versions");
    expect(body.get("file")).toBe(file);
    expect(body.get("name")).toBeNull();
    expect(body.get("parent_id")).toBeNull();
  });

  it("restores the encoded version with POST and no body", () => {
    void projectAssetsApi.restoreVersion("p 1/2", "n/1", "v/1");

    expect(request).toHaveBeenCalledWith(
      "/projects/p%201%2F2/assets/n%2F1/versions/v%2F1/restore",
      { method: "POST" },
    );
  });

  it("downloads the encoded historical version through the blob request", () => {
    void projectAssetsApi.downloadVersion("p 1/2", "n/1", "v/1");

    expect(requestBlob).toHaveBeenCalledWith(
      "/projects/p%201%2F2/assets/n%2F1/versions/v%2F1/download",
    );
  });
});
