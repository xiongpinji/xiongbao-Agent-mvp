import { describe, expect, it } from "vitest";
import {
  isHostAbsolutePath,
  stripVirtualWorkspaceRoot,
  toDockWorkspaceApiPath,
  toPrivateWorkspaceRelPath,
  toWorkspaceApiPath,
} from "./workspaceIoPath";

describe("workspaceIoPath", () => {
  it("detects host abs vs outbound keys", () => {
    expect(isHostAbsolutePath("/Users/me/a.html")).toBe(true);
    expect(isHostAbsolutePath("file:///tmp/x")).toBe(true);
    expect(isHostAbsolutePath("/outbound/a.png")).toBe(false);
    expect(isHostAbsolutePath("outbound/a.png")).toBe(false);
    expect(isHostAbsolutePath("generated/a.html")).toBe(false);
  });

  it("strips only virtual /workspace/ prefix", () => {
    expect(stripVirtualWorkspaceRoot("/workspace/x.py")).toBe("x.py");
    expect(stripVirtualWorkspaceRoot("/workspace")).toBe("");
    expect(
      stripVirtualWorkspaceRoot(
        "/Users/jubaoliang/workspace/sapiens_intro/a.pptx",
      ),
    ).toBe("/Users/jubaoliang/workspace/sapiens_intro/a.pptx");
  });

  it("keeps agent-home absolute as file:// for BackendWorkspace failback", () => {
    const abs =
      "/Users/jubaoliang/.octop/agents/SBGM5Q/earth-presentation.html";
    expect(toWorkspaceApiPath(abs)).toBe(`file://${abs}`);
    expect(toDockWorkspaceApiPath(abs)).toBe(`file://${abs}`);
    expect(
      toDockWorkspaceApiPath("/home/wally/.octop/agents/main/generated/a.pptx"),
    ).toBe("file:///home/wally/.octop/agents/main/generated/a.pptx");
  });

  it("rewrites legacy /outbound to relative", () => {
    expect(toWorkspaceApiPath("/outbound/a.txt")).toBe("outbound/a.txt");
    expect(toDockWorkspaceApiPath("/outbound/a.txt")).toBe("outbound/a.txt");
  });
});

describe("toPrivateWorkspaceRelPath — prefixed host shapes", () => {
  it("maps legitimate relative, dashboard-root and virtual-root keys", () => {
    expect(toPrivateWorkspaceRelPath("note.txt")).toBe("note.txt");
    expect(toPrivateWorkspaceRelPath("generated/a.pptx")).toBe(
      "generated/a.pptx",
    );
    expect(toPrivateWorkspaceRelPath("/note.txt")).toBe("note.txt");
    expect(toPrivateWorkspaceRelPath("/workspace/note.txt")).toBe("note.txt");
    expect(toPrivateWorkspaceRelPath("generated\\a.pptx")).toBe(
      "generated/a.pptx",
    );
  });

  it.each([
    ["/file:///etc/passwd"],
    ["/file:///workspace/note.txt"],
    ["/File:///etc/passwd"],
    ["\\file:///etc/passwd"],
    ["\\\\file:///etc/passwd"],
    ["\\/file:///etc/passwd"],
    ["/file:\\home\\wally\\note.txt"],
    ["/C:/Users/wally/note.txt"],
    ["\\C:\\Users\\wally\\note.txt"],
    ["/c:/note.txt"],
    ["/workspace/C:/Users/wally/note.txt"],
    ["/~/secret/note.txt"],
    ["\\~\\secret\\note.txt"],
    ["/workspace/~/secret/note.txt"],
    ["\\/~/secret/note.txt"],
  ])("refuses prefixed host shape %s", (bad) => {
    expect(toPrivateWorkspaceRelPath(bad)).toBeNull();
  });

  it("keeps a relative key that only contains a colon filename", () => {
    expect(toPrivateWorkspaceRelPath("/my:file.txt")).toBe("my:file.txt");
    expect(toPrivateWorkspaceRelPath("dir/my:file.txt")).toBe(
      "dir/my:file.txt",
    );
  });

  it("still refuses a raw scheme-like filename (existing contract)", () => {
    expect(toPrivateWorkspaceRelPath("my:file.txt")).toBeNull();
  });
});
