import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

const { getChatAgentById, history, agentLoading } = vi.hoisted(() => ({
  getChatAgentById: vi.fn(),
  history: vi.fn(),
  agentLoading: { value: false },
}));

vi.mock("../../context/AgentContext", async (importOriginal) => {
  const actual = await importOriginal<
    typeof import("../../context/AgentContext")
  >();
  return {
    ...actual,
    useAgent: () => ({ getChatAgentById, loading: agentLoading.value }),
  };
});

vi.mock("../../api/modules/octopThreads", async (importOriginal) => {
  const actual = await importOriginal<
    typeof import("../../api/modules/octopThreads")
  >();
  return {
    ...actual,
    octopThreadsApi: { ...actual.octopThreadsApi, history },
  };
});

// ``react-pdf`` pulls in pdf.js, which needs browser canvas globals that jsdom
// does not implement; the route hook under test never renders a PDF.
vi.mock("react-pdf", () => ({
  Document: () => null,
  Page: () => null,
  pdfjs: { GlobalWorkerOptions: {}, version: "0" },
}));

import { useInternalTaskRoute } from "./index";
import type { OctopAgent } from "../../context/AgentContext";

const ordinaryCard: OctopAgent = {
  id: 1,
  agent_id: "researcher",
  name: "研究员",
  description: null,
  persona_mbti: null,
  default_model: null,
  system_prompt: null,
  template_name: null,
  state: "running",
  last_error: null,
  icon: null,
  icon_name: null,
  icon_url: null,
  color: null,
  config: {},
};

const internalCard: OctopAgent = {
  ...ordinaryCard,
  id: 9,
  agent_id: "runtime-1",
  name: "私有文件任务",
  internal: true,
  config: undefined,
};

beforeEach(() => {
  getChatAgentById.mockReset();
  history.mockReset();
  agentLoading.value = false;
});

it("leaves an ordinary agent route untouched and never probes", () => {
  getChatAgentById.mockReturnValue(ordinaryCard);
  const { result } = renderHook(() => useInternalTaskRoute("researcher", "t1"));

  expect(result.current.isInternal).toBe(false);
  expect(result.current.status).toBe("idle");
  expect(result.current.agent?.name).toBe("研究员");
  expect(history).not.toHaveBeenCalled();
});

it("verifies an owner's existing internal thread before enabling chat", async () => {
  getChatAgentById.mockReturnValue(internalCard);
  history.mockResolvedValue({ thread_id: "t1", messages: [] });

  const { result } = renderHook(() => useInternalTaskRoute("runtime-1", "t1"));

  await waitFor(() => expect(result.current.status).toBe("verified"));
  expect(result.current.isInternal).toBe(true);
  expect(history).toHaveBeenCalledWith("runtime-1", "t1", {
    limit: 1,
    offset: 0,
  });
});

it("refuses an internal route without an explicit thread and never probes", () => {
  getChatAgentById.mockReturnValue(internalCard);

  const { result } = renderHook(() =>
    useInternalTaskRoute("runtime-1", undefined),
  );

  expect(result.current.isInternal).toBe(true);
  expect(result.current.status).toBe("refused");
  expect(result.current.error).toBeNull();
  expect(history).not.toHaveBeenCalled();
});

it("refuses a forged or foreign internal thread when the server rejects it", async () => {
  getChatAgentById.mockReturnValue(internalCard);
  history.mockRejectedValue(
    new Error('404 - {"error":{"code":"NOT_FOUND","message":"not found"}}'),
  );

  const { result } = renderHook(() =>
    useInternalTaskRoute("runtime-1", "forged"),
  );

  await waitFor(() => expect(result.current.status).toBe("refused"));
  expect(result.current.error).toBeInstanceOf(Error);
});

it("refuses an unknown route id instead of treating it as an ordinary agent", () => {
  getChatAgentById.mockReturnValue(null);
  const { result } = renderHook(() =>
    useInternalTaskRoute("unknown-agent", "t1"),
  );

  expect(result.current.isInternal).toBe(true);
  expect(result.current.agent).toBeNull();
  expect(result.current.status).toBe("refused");
  expect(history).not.toHaveBeenCalled();
});

it("holds an unresolved route closed while the owner agent list loads", () => {
  agentLoading.value = true;
  getChatAgentById.mockReturnValue(null);
  const { result } = renderHook(() =>
    useInternalTaskRoute("runtime-pending", "t1"),
  );
  expect(result.current.isInternal).toBe(true);
  expect(result.current.status).toBe("checking");
  expect(history).not.toHaveBeenCalled();
});

it("holds a verified t1 -> t2 route switch at checking until t2 verifies", async () => {
  getChatAgentById.mockReturnValue(internalCard);
  let resolveT2: ((value: unknown) => void) | null = null;
  history.mockImplementation((_agentId: string, threadId: string) =>
    threadId === "t1"
      ? Promise.resolve({ thread_id: "t1", messages: [] })
      : new Promise((resolve) => {
          resolveT2 = resolve;
        }),
  );

  const statuses: string[] = [];
  const { result, rerender } = renderHook(
    ({ tid }: { tid: string }) => {
      const route = useInternalTaskRoute("runtime-1", tid);
      statuses.push(route.status);
      return route;
    },
    { initialProps: { tid: "t1" } },
  );

  await waitFor(() => expect(result.current.status).toBe("verified"));

  statuses.length = 0;
  rerender({ tid: "t2" });

  // The very first t2 render — before the new probe effect runs — must not
  // reuse t1's successful probe; the route stays closed ("checking").
  expect(statuses[0]).toBe("checking");
  expect(statuses).not.toContain("verified");
  expect(result.current.status).toBe("checking");
  expect(history).toHaveBeenCalledWith("runtime-1", "t2", {
    limit: 1,
    offset: 0,
  });

  await act(async () => {
    resolveT2?.({ thread_id: "t2", messages: [] });
  });
  await waitFor(() => expect(result.current.status).toBe("verified"));
});

it("refuses a forged t2 after a verified t1 without reusing authorization", async () => {
  getChatAgentById.mockReturnValue(internalCard);
  const forgedError = new Error(
    '404 - {"error":{"code":"NOT_FOUND","message":"not found"}}',
  );
  history.mockImplementation((_agentId: string, threadId: string) =>
    threadId === "t1"
      ? Promise.resolve({ thread_id: "t1", messages: [] })
      : Promise.reject(forgedError),
  );

  const statuses: string[] = [];
  const { result, rerender } = renderHook(
    ({ tid }: { tid: string }) => {
      const route = useInternalTaskRoute("runtime-1", tid);
      statuses.push(route.status);
      return route;
    },
    { initialProps: { tid: "t1" } },
  );

  await waitFor(() => expect(result.current.status).toBe("verified"));

  statuses.length = 0;
  rerender({ tid: "t2" });
  expect(statuses[0]).toBe("checking");

  await waitFor(() => expect(result.current.status).toBe("refused"));
  expect(result.current.error).toBe(forgedError);
  // t1's verified status was never projected onto the forged t2 route, not
  // even for the single render before the rejecting probe settled.
  expect(statuses).not.toContain("verified");
});
