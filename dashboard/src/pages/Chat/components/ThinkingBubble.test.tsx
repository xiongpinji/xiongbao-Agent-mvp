import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import ThinkingBubble from "./ThinkingBubble";

describe("ThinkingBubble", () => {
  it("uses the Xiongbao logo as the thinking avatar", () => {
    const { container } = render(<ThinkingBubble startedAt={Date.now()} />);
    const img = container.querySelector("img");
    expect(img).not.toBeNull();
    expect(img).toHaveAttribute("src", "/xiongbao-logo.png");
    expect(container.innerHTML).not.toContain("octop-mascot");
  });

  it("keeps the elapsed label and cancel affordance", () => {
    const onCancel = vi.fn();
    render(
      <ThinkingBubble startedAt={Date.now() - 3000} onCancel={onCancel} />,
    );
    expect(screen.getByText(/chat\.thinking/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "common.cancel" }));
    expect(onCancel).toHaveBeenCalledTimes(1);
  });
});
