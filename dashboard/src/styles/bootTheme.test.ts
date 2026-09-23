import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { THEME_STORAGE_KEY } from "./themePalettes";

const INDEX_HTML = readFileSync(resolve(__dirname, "../../index.html"), "utf8");
const MANIFEST = JSON.parse(
  readFileSync(resolve(__dirname, "../../public/manifest.json"), "utf8"),
) as { name: string; short_name: string; icons: { src: string }[] };

function bootThemeScript(): string {
  const match = INDEX_HTML.match(
    /<script id="octop-boot-theme">([\s\S]*?)<\/script>/,
  );
  if (!match) {
    throw new Error("octop-boot-theme script missing from index.html");
  }
  return match[1];
}

function stubMatchMedia(systemDark: boolean): void {
  window.matchMedia = ((query: string) => ({
    matches: query.includes("prefers-color-scheme: dark") ? systemDark : false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  })) as typeof window.matchMedia;
}

describe("index.html boot theme", () => {
  const originalMatchMedia = window.matchMedia;

  beforeEach(() => {
    localStorage.removeItem(THEME_STORAGE_KEY);
    document.documentElement.removeAttribute("data-theme");
    stubMatchMedia(true);
  });

  afterEach(() => {
    window.matchMedia = originalMatchMedia;
    localStorage.removeItem(THEME_STORAGE_KEY);
    document.documentElement.removeAttribute("data-theme");
  });

  it("styles the splash from data-theme, not OS color scheme", () => {
    expect(INDEX_HTML).toContain('html[data-theme="dark"] #octop-boot');
    expect(INDEX_HTML).not.toMatch(
      /@media \(prefers-color-scheme: dark\)[\s\S]*#octop-boot/,
    );
  });

  it("uses the supplied Xiongbao logo for the splash in both themes", () => {
    expect(INDEX_HTML).toContain('src="/xiongbao-logo.png"');
    expect(INDEX_HTML).toContain(
      'rel="icon" type="image/png" href="/xiongbao-logo.png"',
    );
    expect(INDEX_HTML).toContain(
      'rel="apple-touch-icon" href="/xiongbao-logo.png"',
    );
    expect(INDEX_HTML).not.toContain('src="/logo_vertical_white.svg"');
    expect(INDEX_HTML).not.toContain('src="/logo_vertical_dark.svg"');
  });

  it("uses the Xiongbao name and icon for installed app metadata", () => {
    expect(MANIFEST.name).toBe("熊宝-Agent");
    expect(MANIFEST.short_name).toBe("熊宝");
    expect(
      MANIFEST.icons.every((icon) => icon.src === "/xiongbao-logo.png"),
    ).toBe(true);
  });

  it("keeps a stored light preference over OS dark", () => {
    localStorage.setItem(
      THEME_STORAGE_KEY,
      JSON.stringify({ preference: "light", palette: "rose" }),
    );
    eval(bootThemeScript());
    expect(document.documentElement.getAttribute("data-theme")).toBe("light");
  });

  it("honors a stored dark preference", () => {
    stubMatchMedia(false);
    localStorage.setItem(
      THEME_STORAGE_KEY,
      JSON.stringify({ preference: "dark", palette: "rose" }),
    );
    eval(bootThemeScript());
    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
  });

  it("follows the OS when the stored preference is system", () => {
    localStorage.setItem(THEME_STORAGE_KEY, "system");
    eval(bootThemeScript());
    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");

    document.documentElement.removeAttribute("data-theme");
    stubMatchMedia(false);
    eval(bootThemeScript());
    expect(document.documentElement.getAttribute("data-theme")).toBe("light");
  });

  it("defaults to dark when no preference is stored, even on a light OS", () => {
    stubMatchMedia(false);
    eval(bootThemeScript());
    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
  });

  it("migrates a legacy plain theme string", () => {
    localStorage.setItem(THEME_STORAGE_KEY, "light");
    eval(bootThemeScript());
    expect(document.documentElement.getAttribute("data-theme")).toBe("light");
  });
});
