import { useState, type ComponentProps, type ReactNode } from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { requestMock, requestBlobMock, probeMock, viewerProps, messageMock } =
  vi.hoisted(() => ({
    requestMock: vi.fn(),
    requestBlobMock: vi.fn(),
    probeMock: vi.fn(),
    viewerProps: [] as Array<Record<string, unknown>>,
    messageMock: {
      error: vi.fn(),
      success: vi.fn(),
      warning: vi.fn(),
    },
  }));

vi.mock("../../../api/request", () => ({
  request: requestMock,
  requestBlob: requestBlobMock,
  probeAuthResource: probeMock,
}));

vi.mock("@/utils/antdMessage", () => ({ message: messageMock }));

vi.mock("../../Agent/Workspace/components/FileViewer", () => ({
  default: (props: Record<string, unknown>) => {
    viewerProps.push(props);
    return (
      <div
        data-testid="file-viewer"
        data-path={String(props.path)}
        data-from-workspace={String(props.fromWorkspace)}
      />
    );
  },
}));

// The global setup's `useTranslation` returns a fresh `t` every render.
// FilePanelContent lists `t` (and callbacks derived from it) in its toolbar
// layout-effect deps, so an unstable `t` re-runs the effect, lifts new actions
// into the shell and re-renders forever. Real react-i18next keeps `t` stable.
const { stableT } = vi.hoisted(() => ({
  stableT: (
    key: string,
    fallback?: string | { defaultValue?: string },
  ): string => {
    if (typeof fallback === "string") return fallback;
    if (fallback && typeof fallback === "object") {
      return fallback.defaultValue ?? key;
    }
    return key;
  },
}));

vi.mock("react-i18next", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-i18next")>();
  return {
    ...actual,
    useTranslation: () => ({
      t: stableT,
      i18n: { language: "zh", changeLanguage: () => Promise.resolve() },
    }),
    Trans: ({ children }: { children?: ReactNode }) => children,
  };
});

import FilePanelContent from "./FilePanelContent";

/** Render the panel plus the toolbar actions it lifts into the dock shell. */
function Harness(props: ComponentProps<typeof FilePanelContent>) {
  const [actions, setActions] = useState<ReactNode>(null);
  return (
    <>
      <FilePanelContent {...props} onActionsChange={setActions} />
      <div data-testid="dock-actions">{actions}</div>
    </>
  );
}

const lastViewerProps = () => viewerProps[viewerProps.length - 1];

const PRIVATE_READ_URL =
  "/agents/RT1/workspace/file?path=note.txt&from_workspace=true";
const PRIVATE_DOWNLOAD_URL =
  "/agents/RT1/workspace/download?path=note.txt&from_workspace=true";
const DELETED_COPY = "该文件可能为处理过程中的临时文件，当前已经被删除。";
const REFUSED_COPY =
  "该文件当前不可访问：任务未通过校验，或路径不在受控工作区内。";

beforeEach(() => {
  requestMock.mockReset();
  requestBlobMock.mockReset();
  probeMock.mockReset();
  viewerProps.length = 0;
  URL.createObjectURL = vi.fn(() => "blob:mock");
  URL.revokeObjectURL = vi.fn();
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("FilePanelContent — verified owner-private 030 file task", () => {
  it("reads a verified private text file in true mode with a managed-root-relative path", async () => {
    requestMock.mockResolvedValue({ content: "hello" });
    render(<Harness agentId="RT1" filePath="note.txt" privateTask />);

    await waitFor(() => expect(requestMock).toHaveBeenCalledTimes(1));
    expect(requestMock.mock.calls[0][0]).toBe(PRIVATE_READ_URL);
    expect(screen.getByTestId("file-viewer").dataset.path).toBe("note.txt");
    expect(screen.getByTestId("file-viewer").dataset.fromWorkspace).toBe(
      "true",
    );
  });

  it.each([["/workspace/note.txt"], ["/note.txt"], ["note.txt"]])(
    "maps %s to the same true-mode managed-root-relative request",
    async (filePath) => {
      requestMock.mockResolvedValue({ content: "hello" });
      render(<Harness agentId="RT1" filePath={filePath} privateTask />);

      await waitFor(() => expect(requestMock).toHaveBeenCalledTimes(1));
      expect(requestMock.mock.calls[0][0]).toBe(PRIVATE_READ_URL);
      expect(lastViewerProps()?.path).toBe("note.txt");
      expect(lastViewerProps()?.fromWorkspace).toBe(true);
    },
  );

  it("saves edits through the true-mode file URL", async () => {
    requestMock.mockResolvedValue({ content: "hello" });
    const user = userEvent.setup();
    render(<Harness agentId="RT1" filePath="note.txt" privateTask />);

    await screen.findByTestId("file-viewer");
    await user.click(screen.getByRole("button", { name: "common.edit" }));
    await user.click(screen.getByRole("button", { name: "common.save" }));

    await waitFor(() => expect(requestMock).toHaveBeenCalledTimes(2));
    const [url, options] = requestMock.mock.calls[1];
    expect(url).toBe(PRIVATE_READ_URL);
    expect(options.method).toBe("PUT");
    expect(JSON.parse(options.body as string)).toEqual({ content: "hello" });
  });

  it("downloads through the true-mode download URL", async () => {
    requestMock.mockResolvedValue({ content: "hello" });
    requestBlobMock.mockResolvedValue(new Blob(["bytes"]));
    const user = userEvent.setup();
    render(<Harness agentId="RT1" filePath="note.txt" privateTask />);

    await screen.findByTestId("file-viewer");
    await user.click(screen.getByRole("button", { name: "common.download" }));

    await waitFor(() => expect(requestBlobMock).toHaveBeenCalledTimes(1));
    expect(requestBlobMock.mock.calls[0][0]).toBe(PRIVATE_DOWNLOAD_URL);
  });

  it("probes an unknown binary private file with the true-mode download URL", async () => {
    probeMock.mockResolvedValue(undefined);
    render(<Harness agentId="RT1" filePath="archive.bin" privateTask />);

    await waitFor(() => expect(probeMock).toHaveBeenCalledTimes(1));
    expect(probeMock.mock.calls[0][0]).toBe(
      "/agents/RT1/workspace/download?path=archive.bin&from_workspace=true",
    );
    expect(requestMock).not.toHaveBeenCalled();
  });

  it("hands private media and document previews to the viewer in true mode without panel requests", async () => {
    render(<Harness agentId="RT1" filePath="chart.png" privateTask />);
    await waitFor(() => expect(lastViewerProps()?.path).toBe("chart.png"));
    expect(lastViewerProps()?.fromWorkspace).toBe(true);

    render(<Harness agentId="RT1" filePath="report.pdf" privateTask />);
    await waitFor(() => expect(lastViewerProps()?.path).toBe("report.pdf"));
    expect(lastViewerProps()?.fromWorkspace).toBe(true);

    expect(requestMock).not.toHaveBeenCalled();
    expect(requestBlobMock).not.toHaveBeenCalled();
    expect(probeMock).not.toHaveBeenCalled();
  });
});

describe("FilePanelContent — private route refusals perform zero file I/O", () => {
  it.each([
    ["file:///etc/passwd"],
    ["file:///workspace/note.txt"],
    ["C:\\Users\\wally\\note.txt"],
    ["\\\\nas\\share\\note.txt"],
    ["../../etc/passwd"],
    ["notes/../../secret.txt"],
    ["~/secret/note.txt"],
    ["note\x00.txt"],
  ])("refuses %s with no request and an unavailable state", async (bad) => {
    render(<Harness agentId="RT1" filePath={bad} privateTask />);

    // Let any (forbidden) effect-driven request flush before asserting.
    await waitFor(() => expect(screen.getByRole("status")).toBeTruthy());
    expect(requestMock).not.toHaveBeenCalled();
    expect(requestBlobMock).not.toHaveBeenCalled();
    expect(probeMock).not.toHaveBeenCalled();
    expect(screen.queryByTestId("file-viewer")).toBeNull();
    expect(
      screen.getByRole("button", { name: "common.download" }),
    ).toBeDisabled();
    expect(screen.queryByRole("button", { name: "common.edit" })).toBeNull();
    // Refusal copy is truthful — never the deleted/temporary-file copy.
    expect(screen.getByText(REFUSED_COPY)).toBeTruthy();
    expect(screen.queryByText(DELETED_COPY)).toBeNull();
  });

  it("performs zero I/O on a private route before existing-thread verification", async () => {
    render(<Harness agentId="" filePath="note.txt" privateTask />);

    await waitFor(() => expect(screen.getByText(REFUSED_COPY)).toBeTruthy());
    expect(requestMock).not.toHaveBeenCalled();
    expect(requestBlobMock).not.toHaveBeenCalled();
    expect(probeMock).not.toHaveBeenCalled();
    expect(screen.queryByTestId("file-viewer")).toBeNull();
    expect(screen.queryByText(DELETED_COPY)).toBeNull();
  });

  it("keeps the deleted/temporary copy for a genuine private 404", async () => {
    requestMock.mockRejectedValue(new Error("Request failed with status 404"));
    render(<Harness agentId="RT1" filePath="note.txt" privateTask />);

    await waitFor(() => expect(screen.getByText(DELETED_COPY)).toBeTruthy());
    expect(screen.queryByText(REFUSED_COPY)).toBeNull();
  });

  it("shows the refusal copy (not deleted) when a 404 tab's route is later refused", async () => {
    requestMock.mockRejectedValue(new Error("Request failed with status 404"));
    const { rerender } = render(
      <Harness agentId="RT1" filePath="note.txt" privateTask />,
    );
    await waitFor(() => expect(screen.getByText(DELETED_COPY)).toBeTruthy());

    rerender(<Harness agentId="" filePath="note.txt" privateTask />);

    await waitFor(() => expect(screen.getByText(REFUSED_COPY)).toBeTruthy());
    expect(screen.queryByText(DELETED_COPY)).toBeNull();
    expect(requestMock).toHaveBeenCalledTimes(1);
  });

  it("drops a keep-alive tab's authorization when a verified route becomes refused", async () => {
    requestMock.mockResolvedValue({ content: "hello" });
    const { rerender } = render(
      <Harness agentId="RT1" filePath="note.txt" privateTask />,
    );
    await waitFor(() => expect(requestMock).toHaveBeenCalledTimes(1));
    expect(screen.getByTestId("file-viewer")).toBeTruthy();

    // Route refused / re-checking: the verified chatAgentId is gone, so the
    // stale mounted tab must not keep reading, saving or downloading.
    rerender(<Harness agentId="" filePath="note.txt" privateTask />);

    await waitFor(() => expect(screen.getByText(REFUSED_COPY)).toBeTruthy());
    expect(requestMock).toHaveBeenCalledTimes(1);
    expect(screen.queryByTestId("file-viewer")).toBeNull();
    expect(
      screen.getByRole("button", { name: "common.download" }),
    ).toBeDisabled();
    expect(screen.queryByText(DELETED_COPY)).toBeNull();
  });
});

describe("FilePanelContent — ordinary agent dock behavior is unchanged", () => {
  it("reads an ordinary relative text file in host mode without from_workspace", async () => {
    requestMock.mockResolvedValue({ content: "hello" });
    render(<Harness agentId="main" filePath="outbound/a.txt" />);

    await waitFor(() => expect(requestMock).toHaveBeenCalledTimes(1));
    const url = requestMock.mock.calls[0][0] as string;
    expect(url).toBe("/agents/main/workspace/file?path=outbound%2Fa.txt");
    expect(url).not.toContain("from_workspace");
    expect(screen.getByTestId("file-viewer").dataset.fromWorkspace).toBe(
      "false",
    );
    expect(screen.getByTestId("file-viewer").dataset.path).toBe(
      "outbound/a.txt",
    );
  });

  it("keeps host-absolute tool paths as file:// for I/O and the viewer", async () => {
    requestMock.mockResolvedValue({ content: "hello" });
    render(
      <Harness
        agentId="main"
        filePath="/home/wally/.octop/agents/main/notes.txt"
      />,
    );

    await waitFor(() => expect(requestMock).toHaveBeenCalledTimes(1));
    expect(requestMock.mock.calls[0][0]).toBe(
      "/agents/main/workspace/file?path=file%3A%2F%2F%2Fhome%2Fwally%2F.octop%2Fagents%2Fmain%2Fnotes.txt",
    );
    expect(screen.getByTestId("file-viewer").dataset.path).toBe(
      "/home/wally/.octop/agents/main/notes.txt",
    );
    expect(screen.getByTestId("file-viewer").dataset.fromWorkspace).toBe(
      "false",
    );
  });

  it("probes and downloads ordinary files without from_workspace", async () => {
    probeMock.mockResolvedValue(undefined);
    requestBlobMock.mockResolvedValue(new Blob(["bytes"]));
    const user = userEvent.setup();
    render(<Harness agentId="main" filePath="archive.bin" />);

    await waitFor(() => expect(probeMock).toHaveBeenCalledTimes(1));
    expect(probeMock.mock.calls[0][0]).toBe(
      "/agents/main/workspace/download?path=archive.bin",
    );

    await user.click(screen.getByRole("button", { name: "common.download" }));
    await waitFor(() => expect(requestBlobMock).toHaveBeenCalledTimes(1));
    expect(requestBlobMock.mock.calls[0][0]).toBe(
      "/agents/main/workspace/download?path=archive.bin",
    );
  });
});
