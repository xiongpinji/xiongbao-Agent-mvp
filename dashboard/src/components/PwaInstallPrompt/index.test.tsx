import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import PwaInstallPrompt, { DesktopInstallGuide, IosGuide } from "./index";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string) => (key === "app.brandName" ? "熊宝-Agent" : key),
  }),
}));

describe("PwaInstallPrompt in desktop shell", () => {
  afterEach(() => {
    delete (window as Window & { _wails?: unknown })._wails;
  });

  it("hides the install button when the Wails bridge is present", () => {
    Object.defineProperty(window, "_wails", {
      configurable: true,
      value: { invoke: () => undefined },
    });
    render(<PwaInstallPrompt appearance="chatFloat" />);
    expect(screen.queryByLabelText("安装应用")).toBeNull();
  });

  it("still offers install in a regular browser", () => {
    render(<PwaInstallPrompt appearance="chatFloat" />);
    expect(screen.getByLabelText("安装应用")).toBeInTheDocument();
  });

  it("uses the Xiongbao name in both installation guides", () => {
    const ios = render(<IosGuide onClose={() => undefined} />);
    expect(screen.getByText(/熊宝-Agent/)).toBeInTheDocument();
    expect(screen.queryByText(/Octop/)).toBeNull();
    ios.unmount();

    render(<DesktopInstallGuide onClose={() => undefined} />);
    expect(screen.getByText(/熊宝-Agent/)).toBeInTheDocument();
    expect(screen.queryByText(/Octop/)).toBeNull();
  });
});
