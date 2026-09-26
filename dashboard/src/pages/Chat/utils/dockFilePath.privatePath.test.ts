import { describe, expect, it } from "vitest";
import { privateDockFileTabId, toPrivateWorkspaceRelPath } from "./dockFilePath";

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
    expect(toPrivateWorkspaceRelPath("/file:///workspace/note.txt")).toBeNull();
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

/**
 * Private-route tab identity. The canonical ``dockFileTabId`` collapses
 * ``file:///workspace/note.png`` and ``/workspace/note.png`` onto the same
 * ``file:note.png`` key, so a refused raw shape could reuse (and be reused
 * by) a safe tab depending on open order. The private id must follow the
 * managed-root-relative contract instead.
 */
describe("privateDockFileTabId", () => {
  it("identifies safe shapes by their managed-root-relative key", () => {
    expect(privateDockFileTabId("note.png")).toBe("file:note.png");
    expect(privateDockFileTabId("/workspace/note.png")).toBe("file:note.png");
    expect(privateDockFileTabId("/note.png")).toBe("file:note.png");
    expect(privateDockFileTabId("generated\\a.pptx")).toBe(
      "file:generated/a.pptx",
    );
  });

  it("never applies the agent-home collapse to a managed key", () => {
    // True-mode contract: a leading slash is a managed-root key, so this is
    // a different file from ``note.txt`` and must not share its identity.
    const id = privateDockFileTabId("/home/wally/.octop/agents/RT1/note.txt");
    expect(id).toBe("file:home/wally/.octop/agents/RT1/note.txt");
    expect(id).not.toBe(privateDockFileTabId("note.txt"));
  });

  it.each([
    ["file:///workspace/note.png"],
    ["file:///etc/passwd"],
    ["/file:///workspace/note.png"],
    ["C:\\Users\\wally\\note.png"],
    ["\\\\nas\\share\\note.png"],
    ["../../etc/passwd"],
    ["notes/../../secret.txt"],
    ["~/secret/note.png"],
    ["note\x00.txt"],
  ])("gives refused %s a raw-keyed id outside the safe namespace", (raw) => {
    const id = privateDockFileTabId(raw);
    expect(id).toBe(`file-refused:${raw}`);
    expect(id.startsWith("file:")).toBe(false);
  });

  it("keeps refused ids distinct from the safe keys they mimic", () => {
    const refused = privateDockFileTabId("file:///workspace/note.png");
    expect(refused).not.toBe(privateDockFileTabId("/workspace/note.png"));
    expect(refused).not.toBe(privateDockFileTabId("note.png"));
    // The reverse direction: a safe key never lands in the refused namespace.
    expect(privateDockFileTabId("/workspace/note.png")).toBe("file:note.png");
    expect(privateDockFileTabId("note.png")).toBe("file:note.png");
  });
});
