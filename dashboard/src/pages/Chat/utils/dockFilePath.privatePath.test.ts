import { describe, expect, it } from "vitest";
import { toPrivateWorkspaceRelPath } from "./dockFilePath";

/**
 * 030A owner-private project-task runtime: true-mode (``from_workspace=true``)
 * file I/O only accepts managed-root-relative keys. The helper must confine
 * dashboard/virtual keys and refuse anything that could address the host.
 */
describe("toPrivateWorkspaceRelPath", () => {
  it("maps relative, virtual /workspace/ and dashboard leading-slash keys to one managed-root-relative key", () => {
    expect(toPrivateWorkspaceRelPath("note.txt")).toBe("note.txt");
    expect(toPrivateWorkspaceRelPath("generated/a.pptx")).toBe(
      "generated/a.pptx",
    );
    expect(toPrivateWorkspaceRelPath("/workspace/note.txt")).toBe("note.txt");
    expect(toPrivateWorkspaceRelPath("/note.txt")).toBe("note.txt");
    expect(toPrivateWorkspaceRelPath("/workspace/generated/a.pptx")).toBe(
      "generated/a.pptx",
    );
    expect(toPrivateWorkspaceRelPath("outbound/report.md")).toBe(
      "outbound/report.md",
    );
  });

  it("treats a leading POSIX slash as a dashboard root key confined under the managed root", () => {
    // True-mode contract: leading '/' is workspace-relative, never a host
    // path — and never collapsed by .octop/agents substring matching.
    expect(
      toPrivateWorkspaceRelPath("/home/wally/.octop/agents/RT1/note.txt"),
    ).toBe("home/wally/.octop/agents/RT1/note.txt");
  });

  it("normalizes backslashes in relative keys", () => {
    expect(toPrivateWorkspaceRelPath("generated\\a.pptx")).toBe(
      "generated/a.pptx",
    );
  });

  it("refuses file:// and other URL schemes instead of collapsing them", () => {
    expect(toPrivateWorkspaceRelPath("file:///workspace/note.txt")).toBeNull();
    expect(toPrivateWorkspaceRelPath("file:///etc/passwd")).toBeNull();
    expect(toPrivateWorkspaceRelPath("FILE:///tmp/a.txt")).toBeNull();
    expect(toPrivateWorkspaceRelPath("http://host/note.txt")).toBeNull();
  });

  it("refuses Windows drive letters and UNC shares", () => {
    expect(toPrivateWorkspaceRelPath("C:\\Users\\wally\\note.txt")).toBeNull();
    expect(toPrivateWorkspaceRelPath("C:/Users/wally/note.txt")).toBeNull();
    expect(toPrivateWorkspaceRelPath("c:/note.txt")).toBeNull();
    expect(toPrivateWorkspaceRelPath("\\\\nas\\share\\note.txt")).toBeNull();
    expect(toPrivateWorkspaceRelPath("//nas/share/note.txt")).toBeNull();
  });

  it("refuses home-prefix paths", () => {
    expect(toPrivateWorkspaceRelPath("~/note.txt")).toBeNull();
    expect(toPrivateWorkspaceRelPath("~wally/note.txt")).toBeNull();
  });

  it("refuses traversal segments anywhere instead of normalizing them away", () => {
    expect(toPrivateWorkspaceRelPath("../etc/passwd")).toBeNull();
    expect(toPrivateWorkspaceRelPath("a/../../b.txt")).toBeNull();
    expect(toPrivateWorkspaceRelPath("/workspace/../secret.txt")).toBeNull();
    expect(toPrivateWorkspaceRelPath("notes/..")).toBeNull();
    expect(toPrivateWorkspaceRelPath("..\\..\\windows\\system32")).toBeNull();
  });

  it("refuses NUL bytes", () => {
    expect(toPrivateWorkspaceRelPath("note\x00.txt")).toBeNull();
    expect(toPrivateWorkspaceRelPath("/workspace/a\x00b.txt")).toBeNull();
  });

  it("refuses empty and root-only inputs", () => {
    expect(toPrivateWorkspaceRelPath("")).toBeNull();
    expect(toPrivateWorkspaceRelPath("   ")).toBeNull();
    expect(toPrivateWorkspaceRelPath("/")).toBeNull();
    expect(toPrivateWorkspaceRelPath("/workspace")).toBeNull();
    expect(toPrivateWorkspaceRelPath("/workspace/")).toBeNull();
  });
});
