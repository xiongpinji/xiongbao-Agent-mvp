import { afterEach, describe, expect, it } from "vitest";
import {
  clearPendingProjectInvite,
  consumePendingProjectInviteDestination,
  rememberProjectInviteFromLocation,
} from "./pendingProjectInvite";

afterEach(() => clearPendingProjectInvite());

describe("pending project invite across login", () => {
  it("stores only a project invite query and consumes it once after login", () => {
    rememberProjectInviteFromLocation("/projects", "?invite=abc_def-123");
    expect(consumePendingProjectInviteDestination()).toBe(
      "/projects?invite=abc_def-123",
    );
    expect(consumePendingProjectInviteDestination()).toBeNull();
  });

  it("ignores unrelated routes and oversized values", () => {
    rememberProjectInviteFromLocation("/chat", "?invite=wrong-place");
    rememberProjectInviteFromLocation(
      "/projects",
      `?invite=${"x".repeat(201)}`,
    );
    expect(consumePendingProjectInviteDestination()).toBeNull();
  });
});
