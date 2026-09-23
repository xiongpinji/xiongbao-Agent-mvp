import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import SidebarBrand from "./SidebarBrand";

vi.mock("../components/AppVersionBadge", () => ({
  default: () => <button type="button">更新</button>,
}));
vi.mock("../components/CurrentVersionBadge", () => ({
  default: () => <button type="button">版本</button>,
}));

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

  it("keeps version controls independent from brand navigation", async () => {
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
    const update = screen.getByRole("button", { name: "更新" });
    expect(brand).not.toContainElement(update);
    await userEvent.click(update);
    expect(onClick).not.toHaveBeenCalled();
  });
});
