import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { Suspense } from "react";
import { beforeEach, expect, it, vi } from "vitest";
import AvatarDropdown from "./AvatarDropdown";
import { routeConfigs } from "../routes";
import type { OctopUser } from "../api/modules/auth";
const { mobile } = vi.hoisted(() => ({ mobile: { value: false } }));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key, i18n: { language: "en" } }),
}));
vi.mock("../hooks/useUserRole", () => ({ useUserRole: () => "user" }));
vi.mock("../hooks/useIsMobile", () => ({ useIsMobile: () => mobile.value }));
vi.mock("../context/LayoutModeContext", () => ({
  useLayoutMode: () => ({ layoutMode: "classic", setLayoutMode: vi.fn() }),
}));
vi.mock("./ThemeSwitcher", () => ({ default: () => null }));
vi.mock("./PaletteSwitcher", () => ({ default: () => null }));
vi.mock("../api/modules/auth", () => ({
  authApi: {
    me: vi.fn(async () => ({ id: 2 })),
    getOauthStatus: vi.fn(async () => ({ providers: [] })),
  },
}));
vi.mock("../pages/Settings/DataManagement", () => ({
  default: () => (
    <div data-testid="ordinary-data-route">data route mounted</div>
  ),
}));
const user = {
  id: 2,
  username: "ordinary",
  role: "user",
  display_name: "Ordinary",
  locale: "en",
  permissions: [],
} as OctopUser;
beforeEach(() => {
  mobile.value = false;
});
function mount(before = vi.fn()) {
  const route = routeConfigs.find((item) => item.path === "/settings/data")!;
  render(
    <MemoryRouter initialEntries={["/experts"]}>
      <AvatarDropdown
        user={user}
        placement="sidebar"
        onBeforeOpenSettings={before}
      />
      <Suspense fallback="loading">
        <Routes>
          <Route path="/experts" element={<span>experts</span>} />
          <Route path={route.path} element={route.element} />
        </Routes>
      </Suspense>
    </MemoryRouter>,
  );
  return before;
}
it("mounts the actual data route through the ordinary avatar entry with keyboard activation", async () => {
  const before = mount();
  const keyboard = userEvent.setup();
  fireEvent.click(screen.getByRole("button", { name: /Ordinary/ }));
  const entry = await screen.findByRole("button", {
    name: "dataManagement.title",
  });
  entry.focus();
  await keyboard.keyboard("{Enter}");
  expect(await screen.findByTestId("ordinary-data-route")).toBeInTheDocument();
  expect(before).toHaveBeenCalledOnce();
  expect(
    screen.queryByRole("button", { name: "account.checkUpdates" }),
  ).not.toBeInTheDocument();
});
it.each([false, true])(
  "offers the same data route inside personal settings (narrow=%s)",
  async (narrow) => {
    mobile.value = narrow;
    const before = mount();
    fireEvent.click(screen.getByRole("button", { name: /Ordinary/ }));
    fireEvent.click(
      await screen.findByRole("button", { name: "account.settings" }),
    );
    const panel = await screen.findByRole("dialog");
    fireEvent.click(
      within(panel).getByRole("button", { name: "dataManagement.title" }),
    );
    expect(
      await screen.findByTestId("ordinary-data-route"),
    ).toBeInTheDocument();
    expect(before).toHaveBeenCalledTimes(2);
  },
);
