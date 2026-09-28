import type { ProjectTodoCatalog } from "../../api/modules/projectTodoCatalog";
import zh from "../../locales/zh.json";
export const catalogTestTranslation = () => ({
  t: (
    key: string,
    fallback?: string | Record<string, unknown>,
    args?: Record<string, unknown>,
  ) => {
    const options = typeof fallback === "object" ? fallback : args;
    const value = key.startsWith("projects.todoFields.")
      ? key
          .split(".")
          .reduce<unknown>(
            (node, part) =>
              node && typeof node === "object"
                ? (node as Record<string, unknown>)[part]
                : undefined,
            zh,
          )
      : undefined;
    const template =
      typeof value === "string"
        ? value
        : typeof fallback === "string"
        ? fallback
        : String(options?.defaultValue ?? key);
    return template.replace(/\{\{(\w+)\}\}/g, (match, name: string) =>
      options && name in options ? String(options[name]) : match,
    );
  },
  i18n: { language: "zh", changeLanguage: () => Promise.resolve() },
});
export const catalogFixture: ProjectTodoCatalog = {
  project_id: "p1",
  revision: 1,
  server_today: "2026-09-28",
  server_timezone: "Asia/Shanghai",
  priorities: [
    {
      priority_id: "pr1",
      name: "紧急",
      color: "red",
      position: 0,
      archived_at: null,
      created_at: 1,
      updated_at: 1,
    },
    {
      priority_id: "pr2",
      name: "历史优先级",
      color: "gray",
      position: 1,
      archived_at: 2,
      created_at: 1,
      updated_at: 2,
    },
  ],
  tags: [
    {
      tag_id: "tag1",
      name: "设计",
      color: "blue",
      archived_at: null,
      created_at: 1,
      updated_at: 1,
    },
    {
      tag_id: "tag2",
      name: "历史标签",
      color: "gray",
      archived_at: 2,
      created_at: 1,
      updated_at: 2,
    },
  ],
};
