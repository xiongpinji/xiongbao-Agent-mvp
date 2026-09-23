import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import StreamConnectingIndicator from "./index";

describe("StreamConnectingIndicator", () => {
  it("uses the Xiongbao logo while connecting", () => {
    const { container } = render(<StreamConnectingIndicator label="连接中" />);
    const img = container.querySelector("img");
    expect(img).not.toBeNull();
    expect(img).toHaveAttribute("src", "/xiongbao-logo.png");
    expect(container.innerHTML).not.toContain("octop-mascot");
    expect(screen.getByText("连接中")).toBeInTheDocument();
  });

  it("keeps label, hint and compact/dark tone rendering", () => {
    const { container } = render(
      <StreamConnectingIndicator
        label="连接中"
        hint="正在启动远程画面"
        tone="onDark"
        size="sm"
      />,
    );
    expect(screen.getByText("连接中")).toBeInTheDocument();
    expect(screen.getByText("正在启动远程画面")).toBeInTheDocument();
    expect(container.querySelector("img")).toHaveAttribute(
      "src",
      "/xiongbao-logo.png",
    );
  });
});
