"use strict";

// Disposable QA only. No network interception, real-account login, or model calls.
// Run only after the coordinator binds these loopback URLs to its fresh PG server.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
const { execFileSync } = require("node:child_process");

const IMAGE_FIXTURES = [
  {
    name: "synthetic-red-3x2.png", type: "image/png",
    base64: "iVBORw0KGgoAAAANSUhEUgAAAAMAAAACCAIAAAASFvFNAAAAFElEQVR4nGN8oqjGAAZMEIqBgQEAFgABL8FUdEkAAAAASUVORK5CYII=",
  },
  {
    name: "synthetic-red-3x2.jpg", type: "image/jpeg",
    base64: "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJCQwLDBgNDRgyIRwhMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjL/wAARCAACAAMDASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwDm6KKK8Q/Vj//Z",
  },
  {
    name: "synthetic-red-3x2.webp", type: "image/webp",
    base64: "UklGRjwAAABXRUJQVlA4IDAAAADQAQCdASoDAAIAAUAmJaACdLoB+AADsAD+7/3D/wq+NyFrf/4U18Ka+FNf8IEAAAA=",
  },
].map((item) => ({ ...item, bytes: Buffer.from(item.base64, "base64") }));

const passwords = new Set();
const secretValues = new Set();
let report = null;
let resultFile = null;

function scrub(value) {
  let text = String(value);
  for (const secret of [...passwords, ...secretValues].sort((a, b) => b.length - a.length)) {
    if (secret) text = text.split(secret).join("[REDACTED]");
  }
  return text.replace(/eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+/g, "[REDACTED_JWT]");
}

function errorInfo(error) {
  return { name: error.name || "Error", message: scrub(error.message || error),
    stack: scrub(error.stack || "") };
}

function persist() {
  if (!resultFile) return;
  const temporary = resultFile + ".tmp";
  fs.writeFileSync(temporary, JSON.stringify(report, null, 2) + "\n", "utf8");
  fs.renameSync(temporary, resultFile);
}

async function phase(name, action) {
  const item = { name, state: "RUNNING", started_at: new Date().toISOString() };
  report.phases.push(item);
  persist();
  try {
    const detail = await action();
    item.state = "PASS";
    if (detail !== undefined) item.details = detail;
    return detail;
  } catch (error) {
    item.state = "FAIL";
    item.error = errorInfo(error);
    throw error;
  } finally {
    item.finished_at = new Date().toISOString();
    persist();
  }
}

function requiredLoopback(name) {
  assert.ok(process.env[name], name + " must be explicitly configured");
  const url = new URL(process.env[name]);
  assert.equal(url.protocol, "http:", "QA transport must be plain loopback HTTP");
  assert.ok(["127.0.0.1", "localhost", "[::1]"].includes(url.hostname), "Loopback host required");
  assert.ok(url.port, "An explicit isolated port is required");
  assert.equal(url.pathname, "/", "Configure an origin, without an /api prefix");
  assert.ok(!url.username && !url.password && !url.search && !url.hash);
  return url.origin;
}

function routeInfo(url) {
  const parsed = new URL(url);
  return { origin: parsed.origin, pathname: parsed.pathname };
}

function gitHead(repo) {
  return execFileSync("git", ["-C", repo, "rev-parse", "HEAD"], { encoding: "utf8" }).trim();
}

function sourceBindings(repo) {
  const files = [
    "dashboard/src/pages/Login/index.tsx",
    "dashboard/src/pages/Projects/ProjectDetail.tsx",
    "dashboard/src/pages/Projects/ProjectPlan.tsx",
    "dashboard/src/pages/Projects/ProjectTodoDetail.tsx",
    "dashboard/src/pages/Projects/ProjectTodoDetail.module.less",
    "dashboard/src/pages/Projects/ProjectTodoMarkdown.tsx",
    "dashboard/src/api/modules/projectTodos.ts",
    "dashboard/src/api/modules/projects.ts",
    "dashboard/src/api/request.ts",
    "dashboard/vite.config.ts",
  ];
  return Object.fromEntries(files.map((file) => {
    const bytes = fs.readFileSync(path.join(repo, file));
    return [file, { bytes: bytes.length, sha256: crypto.createHash("sha256").update(bytes).digest("hex") }];
  }));
}

async function main() {
  const api = requiredLoopback("PS04B_API");
  const web = requiredLoopback("PS04B_WEB");
  assert.ok(process.env.PS04B_BROWSER_OUTPUT, "PS04B_BROWSER_OUTPUT is required");
  assert.ok(path.isAbsolute(process.env.PS04B_BROWSER_OUTPUT), "An absolute output path is required");
  const output = path.resolve(process.env.PS04B_BROWSER_OUTPUT);
  const outputRelative = path.relative(path.resolve(__dirname), output);
  assert.ok(outputRelative && !path.isAbsolute(outputRelative) &&
    outputRelative !== ".." && !outputRelative.startsWith(".." + path.sep),
  "Output must be a fresh nested child of this QA directory");
  assert.ok(!fs.existsSync(output), "Refuse to overwrite an existing browser evidence directory");
  const realParentRelative = path.relative(fs.realpathSync(__dirname), fs.realpathSync(path.dirname(output)));
  assert.ok(!path.isAbsolute(realParentRelative) && realParentRelative !== ".." &&
    !realParentRelative.startsWith(".." + path.sep), "Output parent must stay inside this QA directory");
  fs.mkdirSync(output);
  fs.mkdirSync(path.join(output, "screenshots"));
  resultFile = path.join(output, "result.json");
  const repo = process.env.PS04B_REPO;
  const password = process.env.PS04B_TEST_PASSWORD || "TestPass12";
  passwords.add(password);
  const usernames = {
    owner: process.env.PS04B_OWNER || "ps04b_owner",
    member: process.env.PS04B_MEMBER || "ps04b_member",
    outsider: process.env.PS04B_OUTSIDER || "ps04b_outsider",
  };
  const run = crypto.randomUUID().slice(0, 8);
  const plainTitle = "PS04B 旧文本 " + run;
  const markdownTitle = "PS04B 富文本 " + run;
  const plainDescription = "# 保留符号 <strong>不解析</strong>\n[普通文字](javascript:alert(1))";
  const textComment = "成员文字评论 " + run;
  const imageComment = "有序三图评论 " + run;
  const mdHeading = "QA主标题 " + run;
  const externalImages = [
    "https://example.com/ps04b-synthetic-markdown-" + run + ".png",
    "https://example.com/ps04b-synthetic-html-" + run + ".png",
  ];
  const tick = String.fromCharCode(96);
  const markdown = [
    "# " + mdHeading, "", "## QA次标题", "", "QA段落 **QA粗体** *QA斜体*", "",
    "- QA条目一", "- QA条目二", "", "1. QA编号一", "2. QA编号二", "", "> QA引用", "",
    tick + "QA行内代码" + tick, "", tick.repeat(3), "QA代码块", tick.repeat(3), "",
    "[安全链接](https://example.com/ps04b-safe)",
    "[危险链接](javascript:alert(1))", "[data链接](data:text/html,synthetic)", "",
    "<script>window.ps04bXss=1</script>", "",
    '<iframe src="https://example.com/ps04b-synthetic-frame"></iframe>', "",
    '<img src="' + externalImages[1] + '" onerror="window.ps04bXss=1">', "",
    "![外站图片](" + externalImages[0] + ")", "",
    "![data图片](data:image/png;base64," + IMAGE_FIXTURES[0].base64 + ")",
  ].join("\n");

  report = {
    result: "RUNNING", state: "STARTING", started_at: new Date().toISOString(),
    api_origin: api, web_origin: web, source_repo: repo,
    script_sha256: crypto.createHash("sha256").update(fs.readFileSync(__filename)).digest("hex"),
    phases: [], coverage: {}, authentication: {}, node_http: [], browser_http: [],
    page_errors: [], request_failures: [], external_image_requests: [], screenshots: [],
    cleanup: { contexts: [], browser_closed: false },
    database_boundary: "Pair this TCP browser result with the coordinator's independently verified PG server binding; no DB driver is inferred from a URL.",
    clipboard_boundary: "Synthetic File/DataTransfer/ClipboardEvent dispatched to the real textarea; not an OS clipboard test.",
    unverified: [
      "Real failed image upload retaining a draft and an explicit manual retry",
      "Blob URL memory reclamation; no claim of retracting bytes already in client memory",
      "Late concurrent GET/POST/PATCH/image responses during direct project prop changes",
      "Concurrent quota/revocation/deletion race matrix and backup/restore",
      "Existing database migration records (the browser legacy-format todo is newly created)",
      "Full keyboard and scrolling geometry matrix; screenshot checks are not pixel parity",
      "WorkBuddy per-state visual parity, dates, priority, tags, configurable views",
      "Paid provider/model calls, GLM review, TLS, non-loopback deployment acceptance",
    ],
  };
  persist();
  let browser = null;
  const contexts = [];
  let owner, member, outsider, ownerNarrow, memberNarrow, outsiderNarrow;
  let ownerToken, memberToken, outsiderToken;
  let projectId, plainId, markdownId, secondProjectId, secondTodoId;
  let storedComment;
  const screenshots = path.join(output, "screenshots");
  const todoPath = (id) => "/projects/" + projectId + "/todos/" + id;
  const commentsPath = () => todoPath(markdownId) + "/comments";
  const detailUrl = (id) => web + "/projects/" + projectId + "?tab=plan&todo=" + id;

  async function request(token, route, options = {}) {
    const method = options.method || "GET";
    const headers = { ...(options.headers || {}) };
    if (token) headers.authorization = "Bearer " + token;
    if (options.body && typeof options.body === "string") headers["content-type"] = "application/json";
    const item = { method, pathname: "/api" + route, state: "STARTING" };
    report.node_http.push(item);
    try {
      const response = await fetch(api + "/api" + route, {
        ...options, headers, redirect: "error", signal: AbortSignal.timeout(15000),
      });
      item.status = response.status;
      item.state = "RESPONSE_RECEIVED";
      return response;
    } catch (error) {
      item.state = "FAILED";
      item.error = errorInfo(error);
      throw error;
    }
  }

  async function jsonRequest(token, route, method, data, expected) {
    const response = await request(token, route, {
      method, ...(data === undefined ? {} : { body: JSON.stringify(data) }),
    });
    assert.equal(response.status, expected, method + " " + route + " status");
    if (expected === 204) return null;
    return response.json();
  }

  async function apiLogin(role) {
    const data = await jsonRequest(null, "/auth/login", "POST",
      { username: usernames[role], password }, 200);
    assert.ok(data.access_token);
    secretValues.add(data.access_token);
    report.authentication[role] = { method: "real_api_login_then_localStorage_token", status: 200 };
    return data.access_token;
  }

  async function pageFor(role, token, width = 1280, height = 768) {
    const context = await browser.newContext({
      viewport: { width, height }, locale: "zh-CN", serviceWorkers: "block",
    });
    const holder = { role, width, height, context, page: null };
    contexts.push(holder);
    await context.addInitScript((value) => {
      localStorage.setItem("i18nextLng", "zh");
      if (value) localStorage.setItem("auth_token", value);
    }, token || null);
    const page = await context.newPage();
    holder.page = page;
    page.setDefaultTimeout(15000);
    page.setDefaultNavigationTimeout(30000);
    page.on("pageerror", (error) => report.page_errors.push({ role, width, error: errorInfo(error) }));
    page.on("request", (event) => {
      const url = event.url();
      if (externalImages.includes(url)) {
        report.external_image_requests.push({ role, width, resource_type: event.resourceType(), ...routeInfo(url) });
      }
    });
    page.on("response", (event) => {
      const info = routeInfo(event.url());
      if (info.pathname.startsWith("/api/")) report.browser_http.push({
        role, width, method: event.request().method(), ...info, status: event.status(),
      });
    });
    page.on("requestfailed", (event) => report.request_failures.push({
      role, width, method: event.method(), ...routeInfo(event.url()),
      failure: scrub(event.failure()?.errorText || "unknown"),
    }));
    return holder;
  }

  function waitResponse(page, route, method, action) {
    const pending = page.waitForResponse((response) =>
      new URL(response.url()).pathname === "/api" + route &&
      response.request().method() === method,
    { timeout: 20000 });
    return Promise.all([pending, action()]).then(([response]) => response);
  }

  async function openDetail(holder, id, title) {
    const response = await waitResponse(holder.page, todoPath(id), "GET", () =>
      holder.page.goto(detailUrl(id), { waitUntil: "domcontentloaded" }));
    assert.equal(response.status(), 200);
    const detail = holder.page.getByRole("dialog", { name: "待办详情", exact: true });
    await detail.getByRole("heading", { name: title, exact: true }).waitFor();
    return detail;
  }

  async function screenshot(holder, name) {
    const filename = name + "-" + holder.width + "x" + holder.height + ".png";
    await holder.page.screenshot({ path: path.join(screenshots, filename) });
    report.screenshots.push({ role: holder.role, width: holder.width, height: holder.height,
      file: "screenshots/" + filename });
  }

  async function assertPrivateImages(detail) {
    const article = detail.locator("article").filter({ hasText: imageComment });
    await article.getByRole("img", { name: "评论图片 3", exact: true }).waitFor();
    const images = article.getByRole("img");
    assert.equal(await images.count(), 3);
    for (let i = 0; i < 3; i += 1) {
      const facts = await images.nth(i).evaluate(async (element) => {
        await element.decode();
        return { width: element.naturalWidth, height: element.naturalHeight,
          blob: element.currentSrc.startsWith("blob:") };
      });
      assert.ok(facts.width > 0 && facts.height > 0 && facts.blob);
      assert.equal(await images.nth(i).getAttribute("alt"), "评论图片 " + (i + 1));
    }
  }

  async function assertSafeMarkdown(container, page) {
    await container.getByRole("heading", { name: mdHeading, exact: true, level: 1 }).waitFor();
    assert.equal(await container.getByRole("heading", { name: "QA次标题", exact: true }).count(), 1);
    assert.equal(await container.locator("strong").filter({ hasText: "QA粗体" }).count(), 1);
    assert.equal(await container.locator("em").filter({ hasText: "QA斜体" }).count(), 1);
    assert.equal(await container.locator("ul > li").count(), 2);
    assert.equal(await container.locator("ol > li").count(), 2);
    assert.equal(await container.locator("blockquote").filter({ hasText: "QA引用" }).count(), 1);
    assert.equal(await container.locator("code").filter({ hasText: "QA行内代码" }).count(), 1);
    assert.equal(await container.locator("pre code").filter({ hasText: "QA代码块" }).count(), 1);
    const safe = container.getByRole("link", { name: "安全链接", exact: true });
    assert.equal(await safe.getAttribute("target"), "_blank");
    const rel = (await safe.getAttribute("rel") || "").split(/\s+/);
    assert.ok(rel.includes("noopener") && rel.includes("noreferrer"));
    assert.equal(await container.locator("script,iframe,img,object,embed,style").count(), 0);
    const hrefs = await container.locator("a").evaluateAll((elements) => elements.map((element) => element.href));
    assert.ok(hrefs.every((href) => /^https?:\/\//i.test(href)));
    assert.equal(await page.evaluate(() => window.ps04bXss), undefined);
    assert.deepEqual(report.external_image_requests, []);
  }

  async function assertUnavailable(holder, action, deletedTodo = false) {
    const route = deletedTodo ? todoPath(markdownId) : "/projects/" + projectId;
    const response = await waitResponse(holder.page, route, "GET", action);
    assert.equal(response.status(), 404);
    await holder.page.getByText(deletedTodo ? "待办不存在或你无权访问" : "项目不存在或你无权访问", { exact: true }).waitFor();
    await holder.page.waitForLoadState("networkidle");
    assert.equal(await holder.page.getByText(markdownTitle, { exact: true }).count(), 0);
    assert.equal(await holder.page.getByText(imageComment, { exact: true }).count(), 0);
    assert.equal(await holder.page.getByText(textComment, { exact: true }).count(), 0);
    assert.equal(await holder.page.getByRole("img", { name: /^评论图片 / }).count(), 0);
    assert.equal(await holder.page.getByTestId("todo-description").count(), 0);
  }

  function imageRoute(image) {
    return commentsPath() + "/" + storedComment.comment_id + "/images/" + image.image_id;
  }

  async function assertDeniedReads(token) {
    const routes = [todoPath(markdownId), commentsPath(), ...storedComment.images.map(imageRoute)];
    for (const route of routes) assert.equal((await request(token, route)).status, 404, "Old scoped path must be denied");
    return routes.length;
  }

  try {
    await phase("source_and_dependencies", async () => {
      assert.ok(repo && path.isAbsolute(repo), "PS04B_REPO must be an explicit absolute checkout path");
      assert.ok(Object.values(usernames).every((name) => /^ps04b_[A-Za-z0-9_]+$/.test(name)), "Synthetic account names only");
      report.source_head_before = gitHead(repo);
      const expected = process.env.PS04B_EXPECTED_SOURCE_SHA;
      if (expected) assert.equal(report.source_head_before, expected);
      report.source_bindings = sourceBindings(repo);
      const playwrightPath = path.resolve(__dirname,
        "../../../pw-local/npm-cache/_npx/31e32ef8478fbf80/node_modules/playwright-core");
      const { chromium } = require(playwrightPath);
      report.playwright_core_version = JSON.parse(fs.readFileSync(path.join(playwrightPath, "package.json"), "utf8")).version;
      const executablePath = "C:/Program Files/Google/Chrome/Application/chrome.exe";
      assert.ok(fs.existsSync(executablePath));
      report.image_fixtures = IMAGE_FIXTURES.map((image) => ({
        name: image.name, media_type: image.type, bytes: image.bytes.length,
        sha256: crypto.createHash("sha256").update(image.bytes).digest("hex"),
      }));
      browser = await chromium.launch({ executablePath, headless: true });
      report.chrome_version = browser.version();
      const chromeRaw = execFileSync("powershell.exe", ["-NoProfile", "-NonInteractive", "-Command",
        `Get-CimInstance Win32_Process -Filter "ParentProcessId=${process.pid} AND Name='chrome.exe'" | Select-Object ProcessId,ParentProcessId,CommandLine | ConvertTo-Json -Compress`], { encoding: "utf8" }).trim();
      const chromeValue = JSON.parse(chromeRaw);
      const chromeChildren = Array.isArray(chromeValue) ? chromeValue : [chromeValue];
      assert.equal(chromeChildren.length, 1, "Own browser child must have one Chrome launcher");
      const ownChrome = chromeChildren[0];
      assert.equal(ownChrome.ParentProcessId, process.pid);
      const directoryArgument = /--user-data-dir=(?:"([^"]+)"|(\S+))/.exec(ownChrome.CommandLine);
      assert.ok(directoryArgument, "Chrome must have an explicit private user data directory");
      const privateProfile = path.resolve(directoryArgument[1] || directoryArgument[2]);
      assert.ok(path.basename(privateProfile).startsWith("playwright_chromiumdev_profile-"));
      assert.notEqual(privateProfile.toLowerCase(), path.resolve(process.env.LOCALAPPDATA, "Google/Chrome/User Data").toLowerCase());
      assert.ok(fs.existsSync(privateProfile));
      report.chrome_process_binding = { pid: ownChrome.ProcessId, parent_pid: ownChrome.ParentProcessId,
        private_data_directory: privateProfile, explicit_private_profile_verified: true };
      return { browser_launched: true, fixture_formats: ["PNG", "JPEG", "WebP"] };
    });

    await phase("owner_real_page_login", async () => {
      owner = await pageFor("owner", null);
      await owner.page.goto(web + "/login", { waitUntil: "domcontentloaded" });
      await owner.page.getByPlaceholder("用户名或邮箱", { exact: true }).fill(usernames.owner);
      await owner.page.getByPlaceholder("密码", { exact: true }).fill(password);
      const track = owner.page.getByTestId("slide-captcha-track");
      const thumb = owner.page.getByTestId("slide-captcha-thumb");
      await track.waitFor();
      const trackBounds = await track.boundingBox();
      const thumbBounds = await thumb.boundingBox();
      assert.ok(trackBounds && thumbBounds && trackBounds.width > thumbBounds.width);
      const centerY = thumbBounds.y + thumbBounds.height / 2;
      await owner.page.mouse.move(thumbBounds.x + thumbBounds.width / 2, centerY);
      await owner.page.mouse.down();
      await owner.page.mouse.move(trackBounds.x + trackBounds.width - thumbBounds.width / 2 - 1, centerY, { steps: 12 });
      await owner.page.mouse.up();
      await owner.page.waitForFunction(() => document.querySelector('[data-testid="slide-captcha-track"]')?.getAttribute("aria-valuenow") === "100");
      report.authentication.local_slider_gesture = true;
      const response = await waitResponse(owner.page, "/auth/login", "POST", () =>
        owner.page.getByRole("button", { name: /^登\s*录$/ }).click());
      assert.equal(response.status(), 200);
      await owner.page.waitForURL((url) => url.origin === web && url.pathname === "/chat");
      ownerToken = await owner.page.evaluate(() => localStorage.getItem("auth_token"));
      assert.ok(ownerToken);
      secretValues.add(ownerToken);
      report.authentication.owner = { method: "real_login_page", status: 200,
        returned_to_chat: true, token_present: true };
      report.coverage.owner_ui_login = true;
    });

    await phase("synthetic_project_todos_and_membership", async () => {
      memberToken = await apiLogin("member");
      outsiderToken = await apiLogin("outsider");
      const project = await jsonRequest(ownerToken, "/projects", "POST",
        { name: "PS04B-PG-" + run }, 201);
      projectId = project.project_id;
      const plain = await jsonRequest(ownerToken, "/projects/" + projectId + "/todos", "POST",
        { title: plainTitle, description: plainDescription }, 201);
      assert.equal(plain.description_format, "plain");
      plainId = plain.todo_id;
      const rich = await jsonRequest(ownerToken, "/projects/" + projectId + "/todos", "POST",
        { title: markdownTitle, description: markdown, description_format: "markdown" }, 201);
      assert.equal(rich.description_format, "markdown");
      markdownId = rich.todo_id;
      const invite = await jsonRequest(ownerToken, "/projects/" + projectId + "/invites", "POST",
        { requires_approval: false, expires_in_days: 1 }, 201);
      secretValues.add(invite.token);
      await jsonRequest(memberToken, "/projects/invites/accept", "POST", { token: invite.token }, 200);
      report.project_id = projectId;
      report.todo_ids = { plain: plainId, markdown: markdownId };
      return { project_id: projectId, plain_todo_id: plainId, markdown_todo_id: markdownId };
    });

    await phase("plain_table_board_deeplink_navigation_focus", async () => {
      await owner.page.goto(web + "/projects/" + projectId + "?tab=plan", { waitUntil: "domcontentloaded" });
      const trigger = owner.page.getByRole("button", { name: "查看待办：" + plainTitle, exact: true });
      await trigger.click();
      let detail = owner.page.getByRole("dialog", { name: "待办详情", exact: true });
      await detail.getByTestId("todo-description").waitFor();
      assert.equal(await detail.getByTestId("todo-description").innerText(), plainDescription);
      assert.equal(await detail.getByTestId("todo-description").locator("strong,a,h1").count(), 0);
      assert.equal(new URL(owner.page.url()).searchParams.get("todo"), plainId);
      await detail.getByRole("textbox", { name: "评论", exact: true }).press("Escape");
      await owner.page.waitForURL((url) => !url.searchParams.has("todo"));
      await detail.waitFor({ state: "hidden" });
      await owner.page.waitForFunction((element) => document.activeElement === element, await trigger.elementHandle());
      assert.equal(await trigger.evaluate((element) => document.activeElement === element), true);
      await owner.page.locator(".octop-segmented-item-label").filter({ hasText: "看板" }).click();
      const card = owner.page.getByTestId("todo-card-" + plainId)
        .getByRole("button", { name: "查看待办：" + plainTitle, exact: true });
      await card.focus();
      await card.press("Enter");
      detail = owner.page.getByRole("dialog", { name: "待办详情", exact: true });
      await detail.getByTestId("todo-description").waitFor();
      assert.equal(new URL(owner.page.url()).searchParams.get("todo"), plainId);
      await detail.getByRole("button", { name: "关闭待办详情", exact: true }).press("Escape");
      await owner.page.waitForURL((url) => !url.searchParams.has("todo"));
      await detail.waitFor({ state: "hidden" });
      await owner.page.waitForFunction((element) => document.activeElement === element, await card.elementHandle());
      assert.equal(await card.evaluate((element) => document.activeElement === element), true);
      await card.click();
      await owner.page.getByRole("dialog", { name: "待办详情", exact: true }).waitFor();
      await owner.page.goBack({ waitUntil: "domcontentloaded" });
      await owner.page.waitForURL((url) => !url.searchParams.has("todo"));
      await owner.page.goForward({ waitUntil: "domcontentloaded" });
      await owner.page.getByRole("dialog", { name: "待办详情", exact: true }).waitFor();
      await owner.page.reload({ waitUntil: "domcontentloaded" });
      await owner.page.getByRole("dialog", { name: "待办详情", exact: true })
        .getByTestId("todo-description").waitFor();
      assert.equal(new URL(owner.page.url()).searchParams.get("todo"), plainId);
      await screenshot(owner, "owner-plain-detail");
      report.coverage.plain_literal_table_board_same_id = true;
      report.coverage.deep_link_reload_back_forward_escape_trigger_focus = true;
    });

    await phase("safe_markdown_view_and_preview", async () => {
      const detail = await openDetail(owner, markdownId, markdownTitle);
      await assertSafeMarkdown(detail.getByTestId("todo-description"), owner.page);
      await detail.getByRole("button", { name: "编辑描述", exact: true }).click();
      await detail.getByRole("button", { name: /^预\s*览$/ }).click();
      const preview = detail.getByRole("heading", { name: mdHeading, level: 1, exact: true }).locator("..");
      await assertSafeMarkdown(preview, owner.page);
      await detail.getByRole("button", { name: /^编\s*辑$/ }).click();
      await detail.getByRole("button", { name: /^取\s*消$/ }).click();
      await screenshot(owner, "owner-safe-markdown");
      report.coverage.safe_markdown_view_and_editor_preview = true;
      report.coverage.no_payload_external_image_requests = true;
    });

    await phase("description_save_refresh_and_real_version_conflict", async () => {
      let detail = owner.page.getByRole("dialog", { name: "待办详情", exact: true });
      const before = await jsonRequest(ownerToken, todoPath(markdownId), "GET", undefined, 200);
      const savedText = "## 已保存标题 " + run + "\n\n**已保存正文**";
      await detail.getByRole("button", { name: "编辑描述", exact: true }).click();
      await detail.getByRole("textbox", { name: "待办描述", exact: true }).fill(savedText);
      const saveResponse = await waitResponse(owner.page, todoPath(markdownId), "PATCH", () =>
        detail.getByRole("button", { name: "保存描述", exact: true }).click());
      assert.equal(saveResponse.status(), 200);
      await owner.page.reload({ waitUntil: "domcontentloaded" });
      detail = owner.page.getByRole("dialog", { name: "待办详情", exact: true });
      await detail.getByTestId("todo-description").getByRole("heading",
        { name: "已保存标题 " + run, exact: true }).waitFor();
      const saved = await jsonRequest(ownerToken, todoPath(markdownId), "GET", undefined, 200);
      assert.equal(saved.description, savedText);
      assert.equal(saved.description_format, "markdown");
      assert.equal(saved.version, before.version + 1);
      await detail.getByRole("button", { name: "编辑描述", exact: true }).click();
      const draft = "## 保留草稿 " + run + "\n\n**尚未保存的正文**";
      await detail.getByRole("textbox", { name: "待办描述", exact: true }).fill(draft);
      const externalText = "## 外部版本 " + run + "\n\n另一 HTTP PATCH 的正文";
      const external = await jsonRequest(ownerToken, todoPath(markdownId), "PATCH", {
        expected_version: saved.version, description: externalText, description_format: "markdown",
      }, 200);
      assert.equal(external.version, saved.version + 1);
      const conflict = await waitResponse(owner.page, todoPath(markdownId), "PATCH", () =>
        detail.getByRole("button", { name: "保存描述", exact: true }).click());
      assert.equal(conflict.status(), 409);
      await detail.getByText("待办已被更新，请刷新后比较再保存。", { exact: true }).waitFor();
      assert.equal(await detail.getByRole("textbox", { name: "待办描述", exact: true }).inputValue(), draft);
      await detail.getByRole("button", { name: /^刷\s*新$/ }).click();
      await detail.getByRole("heading", { name: "外部版本 " + run, exact: true }).waitFor();
      assert.equal(await detail.getByRole("textbox", { name: "待办描述", exact: true }).inputValue(), draft);
      const retry = await waitResponse(owner.page, todoPath(markdownId), "PATCH", () =>
        detail.getByRole("button", { name: "保存描述", exact: true }).click());
      assert.equal(retry.status(), 200);
      await detail.getByTestId("todo-description").getByRole("heading",
        { name: "保留草稿 " + run, exact: true }).waitFor();
      const final = await jsonRequest(ownerToken, todoPath(markdownId), "GET", undefined, 200);
      assert.equal(final.description, draft);
      assert.equal(final.version, external.version + 1);
      report.coverage.markdown_save_refresh_exact_body_version = true;
      report.coverage.real_409_draft_preserved_refresh_compare_manual_save = true;
      return { first_version: before.version, saved_version: saved.version,
        concurrent_version: external.version, final_version: final.version, conflict_status: 409 };
    });

    await phase("member_text_comment", async () => {
      member = await pageFor("member", memberToken);
      const detail = await openDetail(member, markdownId, markdownTitle);
      assert.equal(await detail.getByRole("button", { name: "编辑描述", exact: true }).count(), 0);
      await detail.getByRole("textbox", { name: "评论", exact: true }).fill(textComment);
      const response = await waitResponse(member.page, commentsPath(), "POST", () =>
        detail.getByRole("button", { name: "发表评论", exact: true }).click());
      assert.equal(response.status(), 201);
      await detail.getByText(textComment, { exact: true }).waitFor();
      await owner.page.reload({ waitUntil: "domcontentloaded" });
      await owner.page.getByRole("dialog", { name: "待办详情", exact: true })
        .getByText(textComment, { exact: true }).waitFor();
      report.coverage.member_text_comment_owner_refresh = true;
    });

    await phase("member_ordered_png_jpeg_webp_comment_and_private_bytes", async () => {
      const detail = member.page.getByRole("dialog", { name: "待办详情", exact: true });
      await detail.getByRole("textbox", { name: "评论", exact: true }).fill(imageComment);
      await detail.getByRole("textbox", { name: "评论", exact: true }).evaluate((element, fixtures) => {
        const transfer = new DataTransfer();
        for (const image of fixtures) {
          const bytes = Uint8Array.from(atob(image.base64), (character) => character.charCodeAt(0));
          transfer.items.add(new File([bytes], image.name, { type: image.type }));
        }
        element.dispatchEvent(new ClipboardEvent("paste",
          { bubbles: true, cancelable: true, clipboardData: transfer }));
      }, IMAGE_FIXTURES.map(({ name, type, base64 }) => ({ name, type, base64 })));
      await detail.getByRole("img", { name: "待发送图片 3", exact: true }).waitFor();
      await screenshot(member, "member-local-three-image-preview");
      const response = await waitResponse(member.page, commentsPath(), "POST", () =>
        detail.getByRole("button", { name: "发表评论", exact: true }).click());
      assert.equal(response.status(), 201);
      await assertPrivateImages(detail);
      const comments = await jsonRequest(memberToken, commentsPath(), "GET", undefined, 200);
      const matches = comments.items.filter((item) => item.body === imageComment);
      assert.equal(matches.length, 1);
      storedComment = matches[0];
      assert.equal(storedComment.images.length, 3);
      assert.deepEqual(storedComment.images.map((image) => image.position), [0, 1, 2]);
      assert.deepEqual(storedComment.images.map((image) => image.media_type), IMAGE_FIXTURES.map((image) => image.type));
      for (let i = 0; i < 3; i += 1) {
        const image = storedComment.images[i];
        assert.deepEqual(Object.keys(image).sort(), ["image_id", "media_type", "position", "size_bytes"].sort());
        for (const token of [ownerToken, memberToken]) {
          const read = await request(token, imageRoute(image));
          assert.equal(read.status, 200);
          assert.equal(read.headers.get("cache-control"), "private, no-store");
          assert.equal(read.headers.get("x-content-type-options"), "nosniff");
          assert.equal(read.headers.get("content-type"), IMAGE_FIXTURES[i].type);
          assert.deepEqual(Buffer.from(await read.arrayBuffer()), IMAGE_FIXTURES[i].bytes);
        }
      }
      report.coverage.synthetic_paste_three_formats_ordered_mixed_comment = true;
      report.coverage.owner_member_private_image_headers_exact_bytes = true;
      return { comment_id: storedComment.comment_id,
        image_ids: storedComment.images.map((image) => image.image_id), images: 3 };
    });

    await phase("owner_member_outsider_two_viewports", async () => {
      ownerNarrow = await pageFor("owner", ownerToken, 800, 728);
      memberNarrow = await pageFor("member", memberToken, 800, 728);
      outsider = await pageFor("outsider", outsiderToken);
      outsiderNarrow = await pageFor("outsider", outsiderToken, 800, 728);
      for (const holder of [owner, ownerNarrow, member, memberNarrow]) {
        const detail = await openDetail(holder, markdownId, markdownTitle);
        await detail.getByText(textComment, { exact: true }).waitFor();
        await assertPrivateImages(detail);
        await screenshot(holder, holder.role + "-authorized-detail");
      }
      assert.equal(await assertDeniedReads(outsiderToken), 5);
      for (const holder of [outsider, outsiderNarrow]) {
        await assertUnavailable(holder, () => holder.page.goto(detailUrl(markdownId),
          { waitUntil: "domcontentloaded" }));
        await screenshot(holder, "outsider-denied-deeplink");
      }
      report.coverage.owner_member_outsider_1280x768_and_800x728 = true;
      report.coverage.outsider_scoped_todo_comments_three_images_404 = true;
    });

    await phase("spa_project_switch_does_not_carry_private_comments", async () => {
      const secondName = "PS04B-PG-new-" + run;
      const newTitle = "新项目待办 " + run;
      const second = await jsonRequest(ownerToken, "/projects", "POST", { name: secondName }, 201);
      secondProjectId = second.project_id;
      const todo = await jsonRequest(ownerToken, "/projects/" + secondProjectId + "/todos", "POST",
        { title: newTitle, description: "新项目独立描述" }, 201);
      secondTodoId = todo.todo_id;
      const oldDetail = owner.page.getByRole("dialog", { name: "待办详情", exact: true });
      await oldDetail.getByRole("textbox", { name: "评论", exact: true }).fill("不应带入新项目 " + run);
      await oldDetail.getByRole("button", { name: "关闭待办详情", exact: true }).click();
      await oldDetail.waitFor({ state: "hidden" });
      await owner.page.waitForURL((url) => !url.searchParams.has("todo"));
      await owner.page.locator('a[href="/projects"]').first().click();
      await owner.page.waitForURL((url) => url.pathname === "/projects");
      await owner.page.getByText(secondName, { exact: true }).click();
      await owner.page.waitForURL((url) => url.pathname === "/projects/" + secondProjectId);
      await owner.page.getByRole("tab", { name: "计划", exact: true }).click();
      await owner.page.getByRole("button", { name: "查看待办：" + newTitle, exact: true }).click();
      const detail = owner.page.getByRole("dialog", { name: "待办详情", exact: true });
      await detail.getByTestId("todo-description").waitFor();
      assert.equal(await detail.getByTestId("todo-description").innerText(), "新项目独立描述");
      assert.equal(await detail.getByText(textComment, { exact: true }).count(), 0);
      assert.equal(await detail.getByText(imageComment, { exact: true }).count(), 0);
      assert.equal(await detail.getByRole("img", { name: /^评论图片 / }).count(), 0);
      assert.equal(await detail.getByRole("textbox", { name: "评论", exact: true }).inputValue(), "");
      report.coverage.spa_switch_via_projects_list_clears_private_comment_and_draft = true;
      return { new_project_id: secondProjectId, new_todo_id: secondTodoId,
        boundary: "Close old detail then normal SPA navigation via project list; not a late-response/direct-prop-switch race test" };
    });

    await phase("revoked_old_member_token_and_refreshed_pages", async () => {
      const members = await jsonRequest(ownerToken, "/projects/" + projectId + "/members", "GET", undefined, 200);
      const row = members.find((item) => item.username === usernames.member);
      assert.ok(row);
      await jsonRequest(ownerToken, "/projects/" + projectId + "/members/" + row.user_id,
        "DELETE", undefined, 204);
      assert.equal(await assertDeniedReads(memberToken), 5);
      for (const holder of [member, memberNarrow]) {
        await assertUnavailable(holder, () => holder.page.reload({ waitUntil: "domcontentloaded" }));
        await screenshot(holder, "revoked-member-refreshed-deeplink");
      }
      report.coverage.revoked_old_token_todo_comments_three_images_404 = true;
      report.coverage.revoked_refresh_clears_private_title_body_comments_images = true;
    });

    await phase("soft_deleted_todo_old_scoped_urls", async () => {
      const todo = await jsonRequest(ownerToken, todoPath(markdownId), "GET", undefined, 200);
      await jsonRequest(ownerToken, todoPath(markdownId) + "?expected_version=" + todo.version,
        "DELETE", undefined, 204);
      assert.equal(await assertDeniedReads(ownerToken), 5);
      await assertUnavailable(owner, () => owner.page.goto(detailUrl(markdownId),
        { waitUntil: "domcontentloaded" }), true);
      await screenshot(owner, "owner-deleted-todo-deeplink");
      report.coverage.deleted_todo_owner_old_todo_comments_three_images_404 = true;
    });

    await phase("final_evidence_checks", async () => {
      assert.deepEqual(report.page_errors, [], "No uncaught page errors");
      assert.deepEqual(report.external_image_requests, [], "Payload external images must never load");
      report.source_head_after = gitHead(repo);
      report.source_bindings_after = sourceBindings(repo);
      assert.equal(report.source_head_after, report.source_head_before, "Source HEAD drift");
      assert.deepEqual(report.source_bindings_after, report.source_bindings, "Bound frontend source drift");
      assert.equal(crypto.createHash("sha256").update(fs.readFileSync(__filename)).digest("hex"),
        report.script_sha256, "Browser script drift");
      return { page_errors: 0, payload_external_image_requests: 0,
        source_head_unchanged: true, bound_frontend_files_unchanged: true };
    });
    report.result = "PASS";
    report.state = "PASS_BROWSER_TCP_JOURNEY";
    report.finished_at = new Date().toISOString();
    persist();
  } catch (error) {
    report.result = "FAIL";
    report.state = "FAIL_BROWSER_TCP_JOURNEY";
    report.error = errorInfo(error);
    report.failure_diagnostics = [];
    for (const holder of contexts) {
      if (!holder.page || holder.page.isClosed()) continue;
      const diagnostic = { role: holder.role, width: holder.width, url: scrub(holder.page.url()) };
      try {
        diagnostic.buttons = (await holder.page.getByRole("button").allTextContents()).map(scrub);
        const filename = `failure-${holder.role}-${holder.width}x${holder.height}.png`;
        await holder.page.screenshot({ path: path.join(screenshots, filename), timeout: 3000 });
        diagnostic.screenshot = "screenshots/" + filename;
      } catch (diagnosticError) { diagnostic.error = errorInfo(diagnosticError); }
      report.failure_diagnostics.push(diagnostic);
    }
    report.finished_at = new Date().toISOString();
    persist();
  } finally {
    // The outcome and any failure reason already exist before cleanup starts.
    report.cleanup.started_at = new Date().toISOString();
    persist();
    for (const holder of contexts) {
      const item = { role: holder.role, width: holder.width, closed: false };
      report.cleanup.contexts.push(item);
      try {
        await holder.context.close();
        item.closed = true;
      } catch (error) {
        item.error = errorInfo(error);
        report.result = "FAIL";
        if (report.state === "PASS_BROWSER_TCP_JOURNEY") report.state = "INCONCLUSIVE_CLEANUP";
      }
      persist();
    }
    if (browser) {
      try {
        await browser.close();
        report.cleanup.browser_closed = true;
      } catch (error) {
        report.cleanup.browser_error = errorInfo(error);
        report.result = "FAIL";
        if (report.state === "PASS_BROWSER_TCP_JOURNEY") report.state = "INCONCLUSIVE_CLEANUP";
      }
    }
    if (report.state === "PASS_BROWSER_TCP_JOURNEY" &&
      (report.page_errors.length || report.external_image_requests.length)) {
      report.result = "FAIL";
      report.state = "FAIL_LATE_BROWSER_ACTIVITY";
      report.error = { name: "LateBrowserActivity", message: "Page error or payload external image request arrived after the final phase" };
    }
    report.cleanup.finished_at = new Date().toISOString();
    persist();
  }
  const summary = { result: report.result, state: report.state, result_file: resultFile,
    phases_passed: report.phases.filter((item) => item.state === "PASS").length,
    phases_failed: report.phases.filter((item) => item.state === "FAIL").length,
    page_errors: report.page_errors.length, payload_external_image_requests: report.external_image_requests.length,
    unverified: report.unverified };
  if (report.error) summary.error = report.error.message;
  console.log(JSON.stringify(summary));
  return report.state === "PASS_BROWSER_TCP_JOURNEY" ? 0 : 1;
}

main().then((code) => { process.exitCode = code; }).catch((error) => {
  console.error(JSON.stringify({ state: "REFUSED_OR_PRE_RUN_ERROR", error: errorInfo(error) }));
  process.exitCode = 1;
});
