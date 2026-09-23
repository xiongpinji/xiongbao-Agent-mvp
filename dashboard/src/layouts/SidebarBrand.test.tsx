import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import SidebarBrand from "./SidebarBrand";

vi.mock("../components/AppVersionBadge", () => ({ default: () => null }));
vi.mock("../components/CurrentVersionBadge", () => ({ default: () => null }));

describe("SidebarBrand", () => {
  it("opens the task workspace when the brand is clicked", async () => {
    const onClick = vi.fn();
    render(
      <SidebarBrand
        name="熊宝-Agent"
        collapsed={false}
        isMobile={false}
        onClick={onClick}
      />,
    );

    const brand = screen.getByRole("button", { name: "熊宝-Agent" });
    expect(brand).toHaveClass("octop-desktop-no-drag");
    expect(brand.querySelector("img")).toHaveAttribute(
      "src",
      "/xiongbao-logo.png",
    );
    await userEvent.click(brand);
    expect(onClick).toHaveBeenCalledOnce();
  });
});
