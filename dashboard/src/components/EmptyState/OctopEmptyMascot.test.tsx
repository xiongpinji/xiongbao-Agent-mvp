import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { EmptyState, OctopEmptyMascot } from "./index";
import { OCTOP_EMPTY_MASCOT_SRC } from "../../assets/mascot";

describe("OctopEmptyMascot", () => {
  it("renders the Xiongbao logo for the shared mascot export", () => {
    const { container } = render(<OctopEmptyMascot />);
    const img = container.querySelector("img");
    expect(img).not.toBeNull();
    expect(img).toHaveAttribute("src", "/xiongbao-logo.png");
    expect(img).toHaveAttribute("src", OCTOP_EMPTY_MASCOT_SRC);
  });

  it("keeps the default and explicit size contract", () => {
    const { container, rerender } = render(<OctopEmptyMascot />);
    let img = container.querySelector("img") as HTMLImageElement;
    expect(img.style.width).toBe("");
    expect(img.style.height).toBe("");

    rerender(<OctopEmptyMascot size={96} />);
    img = container.querySelector("img") as HTMLImageElement;
    expect(img.style.width).toBe("96px");
    expect(img.style.height).toBe("96px");
  });

  it("renders the Xiongbao logo through EmptyState variant='mascot'", () => {
    const { container } = render(
      <EmptyState variant="mascot" title="暂无内容" />,
    );
    expect(container.querySelector("img")).toHaveAttribute(
      "src",
      "/xiongbao-logo.png",
    );
    expect(screen.getByText("暂无内容")).toBeInTheDocument();
  });
});
