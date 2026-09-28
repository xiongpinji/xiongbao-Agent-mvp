# PS-04B 项目待办详情实现计划

> **面向 AI 代理的工作者：** 原始派工使用 Agent Orchestrator 指定的三条路由。2026-09-27 两条实现路由未交付后，用户单独授权 Codex 承接 B1/B2；失败记录与授权边界见[编排记录](PROJECT_TODO_DETAIL_PS04B_ORCHESTRATOR.md)。每项先写失败测试，再做最小实现并独立复测。步骤使用复选框跟踪进度。

**目标：** 在熊宝项目空间交付 B1 双栏待办详情、旧纯文本兼容和成员文字评论，再交付 B2 安全 Markdown 描述与受控评论图片。

**架构：** 评论是独立于项目动态留言的新资源，项目动态只写安全事件；图片使用独立私有根与实时成员授权。现有 020 待办 CRUD、版本号和项目成员权限不改变。前后端从同一份 [批准规格](PROJECT_TODO_DETAIL_PS04B_DESIGN.md)实现；两片分别验收。

**技术栈：** Python 3.12、FastAPI、Pydantic、SQLite/PostgreSQL 配对迁移、React 18、TypeScript、Ant Design、Vitest、pytest。

下文任务标题中的 Claude/OpenCode“所有者”保留原始派工记录；经 2026-09-27 用户授权后，尚未交付的 B1/B2 实施责任转给 Codex，前后端文件范围与测试门禁不变。

执行状态：B1 代码提交 `6280fe49` 已推送并按[单独验收记录](PROJECT_TODO_DETAIL_PS04B_B1_EVIDENCE.md)核对；B2 代码提交 `f1e6ccaf` 已通过独立固定 SHA 审查、聚焦回归、真实 PostgreSQL 仓储探针、本地浏览器旅程和全仓非 live 测试，已推送并核对远端，见[B2 验收记录](PROJECT_TODO_DETAIL_PS04B_B2_EVIDENCE.md)。下方复选框保留原始逐步 TDD 任务模板，实际结果以验收记录和编排清单为准。B1 的全量 `make all` 与 GLM 补审仍未完成，不将定向通过写成全量通过。

2026-09-28 补验：e72f7275 基线仅修第二 DB 连接的正式 factory 选择；指定两份测试真实 PG 38/38（34 ASGI + 4 直接 service）、SQLite 38/38，另完成真实 PG 支撑的 13 阶段 TCP 浏览器旅程与正常收尾，两个独立只读审查无可操作 P0/P1/P2。见[补验记录](PROJECT_TODO_DETAIL_PS04B_PG_HTTP_BROWSER_ACCEPTANCE_20260928.md)。完整 PG 并发/故障/浏览器矩阵及 WorkBuddy 视觉仍按记录另验。

---

## 文件与责任边界

| 责任 | 文件 |
| --- | --- |
| B1 评论表与 SQL | 新建 src/octop/infra/db/migrations/031_project_todo_comments.sql 和对应 .pg.sql（迁移目录与编号在动工时以 migrate.py 实际目录/最新版本重核）；新建 src/octop/infra/db/repos/project_todo_comments.py |
| B1 服务/HTTP/动态 | 新建 src/octop/infra/projects/todo_comments.py；修改 src/octop/infra/db/services.py、src/octop/api/routers/project_todos.py、src/octop/infra/db/repos/project_activity.py、src/octop/infra/projects/activity.py、src/octop/api/routers/project_activity.py |
| B1 前端 | 修改 dashboard/src/api/modules/projectTodos.ts、dashboard/src/pages/Projects/ProjectPlan.tsx、ProjectDetail.tsx、ProjectActivity.tsx；新建 ProjectTodoDetail.tsx 与 ProjectTodoDetail.module.less；修改对应 .test.ts/.test.tsx 和 locales/zh.json、locales/en.json |
| B2 格式 | 配对下一号迁移；修改现有 todo repo/service/router、Dashboard ProjectTodo 类型；新建 ProjectTodoMarkdown.tsx 与 .test.tsx |
| B2 图片 | 新建独立评论图片存储模块、配对迁移、仓储/服务/HTTP 测试；扩展 projectTodos.ts 与 ProjectTodoDetail.tsx 的登录态 Blob 读取和粘贴提交 |

根目录 AGENTS.md 的层级边界与默认 make all ship bar 适用于每项。src/octop/dashboard 是构建产物，不直接编辑。测试数据只使用隔离环境；真 PostgreSQL 测试只能指向一次性专用数据库，因为测试辅助工具会清空 public schema。2026-09-28 的专用补验 fixture 改为每用例创建全新随机 DB，连接前及迁移前围栏核对独有 DB 前缀、用户、端口和 data_directory，未对既有 schema 做销毁；这不改变原辅助工具的限制。原始三路由派工各在独立工作树；经单独授权后的 Codex B1 实现改用同一干净 Windows 工作树的前后端不重叠文件所有权。实现者不提交或推送；Codex 负责整合、审查、提交和推送。

## B1：文字评论与双栏详情

### 任务 1：评论迁移、仓储和幂等

**所有者：** claude-bailian / qwen3.8-max。**文件：** 上述 B1 SQL、project_todo_comments.py、tests/unit/db/test_project_todo_comments.py、tests/unit/db/test_migrate_discovery.py。参考 020_project_todos、022_project_messages、project_todos.py 的成员锁序。

- [ ] **步骤 1：写失败测试。** 新库和旧库升级后检查 comment_id/todo_id/author_user_id/body_text/client_request_id/request_fingerprint/created_at、(todo_id,created_at,comment_id) 索引与待办内活动作者请求号唯一约束；同秒排序、相同请求号/正文只落一行，冲突正文保持 409 结果。关键断言如下。

        assert first.comment_id == retry.comment_id
        assert first.body == "成员评论"
        assert [item.comment_id for item in page.items] == expected_descending_ids
        assert repo.count_for_todo(todo_id) == 1

- [ ] **步骤 2：运行失败用例。** 在 WSL 独立工作树运行 uv run pytest -q tests/unit/db/test_project_todo_comments.py tests/unit/db/test_migrate_discovery.py；预期新增测试先因表/仓储不存在而失败。
- [ ] **步骤 3：最小实现。** 迁移建独立表，数据库容许 0–4000 字符以支持 B2 图片-only 评论；服务层 B1 拦截空白。仓储返回固定 CommentView 字段 comment_id、todo_id、author_user_id、author_name、body、images、created_at；images 在 B1 恒为 []。请求号为小写 UUID v4，指纹由规范化正文生成。不要复用 project_messages。
- [ ] **步骤 4：重跑定向测试。** 同一命令预期全绿；SQLite 与 .pg.sql 静态配对测试只能证明迁移文本/SQLite，不能写成真 PG 成功。
- [ ] **步骤 5：报告变更。** 列出迁移号、测试结果和对 B2 留出的字段；工作者不自行 commit。

### 任务 2：评论 HTTP、成员撤权、活动事件

**所有者：** claude-bailian / qwen3.8-max。**文件：** todo_comments.py、project_todos.py router、services.py、project_activity.py repo/service/router、tests/integration/test_project_todo_comments_api.py 与 test_project_activity_api.py。可与任务 3 并行，不可与任务 1 在同一工作树并行写相同文件。

- [ ] **步骤 1：写失败 API 测试。** 复用 tests/integration/test_project_todos_api.py 的 env、create_user 与 _base 范式，建立 owner/member/outsider 和一个待办；验证 GET 评论分页、POST 文本、相同请求号重试 200、冲突 409、撤权/跨项目/已删 404、评论不改变待办 version、事件 payload 不含正文。

        path = "/api/projects/" + pid + "/todos/" + tid + "/comments"
        first = await client.post(path, headers=member_auth, json={
            "body": "  成员评论  ",
            "client_request_id": "d6e47312-3f3d-4a27-a43a-23c5df13218b",
        })
        assert first.status_code == 201
        assert first.json()["body"] == "成员评论"
        retry = await client.post(path, headers=member_auth, json={
            "body": "成员评论",
            "client_request_id": "d6e47312-3f3d-4a27-a43a-23c5df13218b",
        })
        assert retry.status_code == 200
        assert retry.json()["comment_id"] == first.json()["comment_id"]

- [ ] **步骤 2：运行失败用例。** uv run pytest -q tests/integration/test_project_todo_comments_api.py tests/integration/test_project_activity_api.py；新增路径预期 404 或导入失败。
- [ ] **步骤 3：实现服务与薄路由。** GET limit 1–50、稳定 (created_at DESC,comment_id DESC) cursor；POST JSON {body,client_request_id}，额外 actor 字段 422。每次重新验证当前项目成员与未删除且同项目待办；成员撤销/删除和评论提交按成员→待办→评论/事件锁序串行化。活动只加入 project.todo_comment_created、object_id 为 todo_id、正文/图像/路径均不得入事件；related scope 只显示本人评论。
- [ ] **步骤 4：重跑定向测试及 todo 旧回归。** uv run pytest -q tests/unit/db/test_project_todos.py tests/integration/test_project_todos_api.py tests/unit/db/test_project_activity.py tests/integration/test_project_activity_api.py tests/integration/test_project_todo_comments_api.py；预期全绿。
- [ ] **步骤 5：报告故障注入证据。** 强制事件写失败后评论与事件均不得留下；证明所有拒绝读取返回统一 404。

### 任务 3：双栏详情、文本评论与旧描述

**所有者：** opencode-bailian / bailian-token-plan-personal/deepseek-v4.1-flash。**文件：** projectTodos.ts/.test.ts、ProjectPlan.tsx/.test.tsx、新建 ProjectTodoDetail.tsx/.module.less/.test.tsx、zh.json、en.json。只用 API 模块调用后端，不改通用 request.ts。

- [ ] **步骤 1：写失败组件测试。** 表格行、看板卡打开同一 todo ID；标题、description、评论左栏，状态/处理人右栏；旧正文含 # 与 <b> 只按文字展示，换行保留。member 能提交文字评论；409 保留输入草稿；800×728 独立滚动且输入区可见。

        expect(screen.getByText("# 原始文本 <b>")).toBeInTheDocument();
        expect(screen.getByRole("textbox", { name: "评论" })).toHaveValue("未发送草稿");
        expect(screen.getByText("状态")).toBeVisible();

- [ ] **步骤 2：运行失败用例。** cd dashboard && npm test -- src/pages/Projects/ProjectPlan.test.tsx src/pages/Projects/ProjectTodoDetail.test.tsx src/api/modules/projectTodos.test.ts；新详情测试预期失败。
- [ ] **步骤 3：实现固定合同。** API 模块新增 CommentDTO {comment_id,todo_id,author_user_id,author_name,body,images,created_at} 与 list/create；images 在 B1 为空。新详情组件仅按纯文本显示描述，提交生成并保持 UUID v4 request ID，成功后刷新评论；失败保留草稿。复用 020 状态/处理人版本化 PATCH 权限，不能套用 ProjectPlan 现有“409 关闭编辑弹窗”逻辑。
- [ ] **步骤 4：重跑定向测试、类型检查。** 上述 npm test 命令与 cd dashboard && npx tsc -b；预期全绿。
- [ ] **步骤 5：记录视觉证据边界。** 留下 1280×768、800×728 的本地 UI 观察点；工作者不能宣称 WorkBuddy 逐状态 1:1。

### 任务 4：深链、后退、撤权清空与活动入口

**所有者：** opencode-bailian / DeepSeek 路由，与任务 3 同一工作者顺序实施。**文件：** ProjectDetail.tsx/.test.tsx、ProjectPlan.tsx/.test.tsx、ProjectActivity.tsx/.test.tsx。

- [ ] **步骤 1：写失败路由测试。** /projects/p1?tab=plan&todo=t1 直接开详情；Escape 关闭且焦点回卡片，URL 去掉 todo；后退回原计划视图；切 p2 或评论 GET 404 不能闪现 p1 的标题/评论；动态点击评论事件可打开对应待办。

        expect(window.location.search).toContain("tab=plan");
        expect(screen.queryByText("旧项目私有评论")).not.toBeInTheDocument();
        expect(screen.getByRole("button", { name: "关闭待办详情" })).toBeVisible();

- [ ] **步骤 2：运行失败用例。** cd dashboard && npm test -- src/pages/Projects/ProjectDetail.test.tsx src/pages/Projects/ProjectPlan.test.tsx src/pages/Projects/ProjectActivity.test.tsx；预期新测试失败。
- [ ] **步骤 3：实现查询参数同步。** ProjectDetail 初始化/监听 location.search；ProjectPlan 负责当前 todo 详情 ID；关闭时只移除 todo 保留其他参数；所有加载按 projectId+todoId generation/Abort 隔离；404 清除详情/评论。活动新事件仅显示“某成员评论了待办”及目标入口。
- [ ] **步骤 4：重跑测试和构建。** 定向 npm test、npx tsc -b、npm run lint、npm run build；预期全绿。
- [ ] **步骤 5：交接 B1 候选。** 工作树仅含前端白名单文件；向 Codex 报告测试与 409/撤权状态处理。

### B1 整合与独立门禁

- [ ] Codex 逐文件审查前后端候选 diff，特别复核成员锁序、事件白名单、403/404、深链 404 和旧纯文本字面显示；若任一实现责任未交付，就不宣称 B1 完成。
- [ ] Codex 在合并候选上独立运行上述后端/前端定向测试、SQLite 新库和旧库升级，随后运行仓库 make all 或记录环境限制及等价细项；真实 PostgreSQL 专用库升级另报证据等级。
- [ ] 固定提交 SHA 后请求 qwen-code-review/glm-5.3 只读审查；2026-09-27 用户已批准额度未恢复期间由独立只读代码审查和本地测试替代当前发布门禁，并在 GLM 恢复后补审。替代审查的主体、固定 SHA 与未覆盖风险须明确记录。
- [ ] 登录态三身份本地浏览器验证 owner/member/outsider、撤权后旧深链、表格/看板双入口；保存截图与 API 证据。仅接受的 B1 内容可快进推送 xiongbao/main。

## B2：安全格式与受控评论图片

### 任务 5：待办格式后端合同

**所有者：** claude-bailian / Qwen 路由。**文件：** 下一号 SQLite/.pg.sql 迁移、project_todos.py repo/service/router、tests/unit/db/test_project_todos.py、tests/integration/test_project_todos_api.py。

- [ ] **步骤 1：写失败测试。** 旧行迁移 description_format=plain；所有列表/详情/创建/PATCH/bulk 响应带 plain|markdown；PATCH 状态-only 保留格式，传 description 无 format 置 plain，只有 format 无 description 返回 422。

        assert old_row["description_format"] == "plain"
        assert status_only.json()["description_format"] == "markdown"
        assert body_without_format.json()["description_format"] == "plain"
        assert format_without_body.status_code == 422

- [ ] **步骤 2：运行失败测试。** uv run pytest -q tests/unit/db/test_project_todos.py tests/integration/test_project_todos_api.py；预期格式断言失败。
- [ ] **步骤 3：最小实现。** SQL 字段为 description_format TEXT NOT NULL DEFAULT 'plain' CHECK (description_format IN ('plain','markdown'))；现有 description 仍最多 4,000 字符，PATCH 仍要求 expected_version，成功版本 +1；所有 TodoView/HTTP 读写路径同步。
- [ ] **步骤 4：重跑相同测试。** 预期全绿，另核对真 PostgreSQL 迁移不把 SQLite 语法照搬。
- [ ] **步骤 5：报告兼容性。** 给出旧客户端不传字段的 create/PATCH 结果，不能破坏 020 API。

### 任务 6：安全 Markdown 描述

**所有者：** opencode-bailian / DeepSeek 路由。**文件：** projectTodos.ts/.test.ts、新建 ProjectTodoMarkdown.tsx/.test.tsx、ProjectTodoDetail.tsx/.test.tsx、zh.json、en.json。

- [ ] **步骤 1：写失败测试。** plain 旧记录即使含 Markdown/HTML 符号仍字面显示；markdown 允许标题/强调/列表/引用/代码/HTTP(S) 链接；原始 HTML、脚本、iframe、图片节点、javascript:、data: 均不呈现为可执行内容；409 保留编辑草稿。

        expect(screen.queryByRole("img")).not.toBeInTheDocument();
        expect(screen.queryByRole("link", { name: "危险" })).not.toBeInTheDocument();
        expect(screen.getByText("未保存草稿")).toBeVisible();

- [ ] **步骤 2：运行失败测试。** cd dashboard && npm test -- src/pages/Projects/ProjectTodoMarkdown.test.tsx src/pages/Projects/ProjectTodoDetail.test.tsx src/api/modules/projectTodos.test.ts；预期新用例失败。
- [ ] **步骤 3：实现专用渲染器。** 不复用全局聊天 Markdown，不用 dangerouslySetInnerHTML；链接仅 http/https 且 target=_blank 时 rel=noopener noreferrer；描述格式字段是必填 DTO，旧记录由后端迁移显式给 plain；工具栏/预览只写已批准语法。
- [ ] **步骤 4：重跑定向测试、tsc/lint/build。** 预期全绿。
- [ ] **步骤 5：报告未含能力。** 待办正文不支持嵌图，日期/优先级/标签不在本片。

### 任务 7：私有评论图片存储与 HTTP

**所有者：** claude-bailian / Qwen 路由。**文件：** 配对新迁移、project_todo_comments.py repo/service/router、新建评论图片私有存储模块、tests/unit/db/test_project_todo_comments.py、tests/integration/test_project_todo_comments_api.py 与存储故障注入测试。不可把图片写进现有资产根，因为资产重启清理只认识 asset_versions object keys。

- [ ] **步骤 1：写失败测试。** 有效 PNG/JPEG/WebP 的 multipart 文字+图片或纯图评论成功；同请求号、规范化正文和实际类型+SHA256+顺序重试只存一次；错误 MIME/SVG/GIF/8 MiB 单图/20 MiB 总量/5 张上限拒绝；两个并发请求不能突破项目 512 MiB 配额；撤权、软删、跨项目旧图片 URL 404；磁盘成功 DB 失败能回收临时/已发布字节。

        assert first.status_code == 201
        assert [i["position"] for i in first.json()["images"]] == [0, 1]
        assert same_retry.status_code == 200
        assert revoked_image.status_code == 404

- [ ] **步骤 2：运行失败测试。** uv run pytest -q tests/unit/db/test_project_todo_comments.py tests/integration/test_project_todo_comments_api.py；预期 multipart 与图片路径用例失败。
- [ ] **步骤 3：实现配对迁移/存储/接口。** multipart 键固定 client_request_id、可省略 body、重复有序 images；请求体边读边按字节上限截断到受控临时文件，再对实际字节做签名和解码检查，失败即清理。评论图片表保存 image_id/comment_id/object_key/size_bytes/sha256/media_type/position/created_at，容量表按 project_id 原子预留；DTO 只返回 image_id/media_type/size_bytes/position。GET 图片每次做成员→todo→comment→image 归属校验，返回 private,no-store 与 nosniff；别把 object_key 发给前端。
- [ ] **步骤 4：重跑定向、故障与 PG 专用库测试。** SQLite、真 PostgreSQL 分开记录；真 PG 禁用共享/生产库。
- [ ] **步骤 5：报告原子性。** 明确临时文件/DB 失败回收、软删图片配额仍占用、孤儿重启清理的测试证据。

### 任务 8：粘贴图片、受控读取与 Blob 生命周期

**所有者：** opencode-bailian / DeepSeek 路由。**文件：** projectTodos.ts/.test.ts、ProjectTodoDetail.tsx/.test.tsx/.module.less；可参考 request.ts 的登录态 Blob/上传和 DocumentPreviewCore.tsx 的 Abort/URL.revokeObjectURL，不修改通用 request.ts。

- [ ] **步骤 1：写失败测试。** 粘贴有效图本地预览；过大/不支持文件显示错误；提交失败保留文字+图片草稿和稳定 request ID；成功按 position 渲染；切项目、卸载、撤权和晚到请求释放所有 Blob URL、不显示旧图。

        expect(createObjectURL).toHaveBeenCalledTimes(1);
        expect(revokeObjectURL).toHaveBeenCalledWith(firstUrl);
        expect(screen.queryByAltText("旧项目评论图片")).not.toBeInTheDocument();

- [ ] **步骤 2：运行失败测试。** cd dashboard && npm test -- src/pages/Projects/ProjectTodoDetail.test.tsx src/api/modules/projectTodos.test.ts；预期新增图片用例失败。
- [ ] **步骤 3：实现两种提交。** 无图继续 JSON POST；有图按选定次序 FormData append，登录态 fetch 图片字节后生成本地 Blob URL；响应只读取 image_id/position 等元数据。每次替换/卸载/项目切换 revoke，并对旧请求 Abort；无公共静态 URL 与外站内嵌。
- [ ] **步骤 4：重跑测试、tsc/lint/build。** 预期全绿，失败手动重试不得偷偷新增第二条评论。
- [ ] **步骤 5：报告浏览器状态。** 单独列出粘贴、上传进度、失败重试、撤权后重新获取失败的实测范围。

### B2 整合与完成门禁

- [ ] Codex 对 B2 后端/前端完整 diff 与迁移升级路径做独立审查，尤其核对格式兼容、XSS、路径安全、撤权、限额并发和文件/DB 回收。
- [ ] 跑 B1+B2 定向用例、make all；真 PG 新库/旧库升级和三身份真实登录态本地浏览器分别出报告。所有新接口在无权限/旧链接下按批准规格给 404。
- [ ] 固定 SHA 请求 GLM 只读审查并处理 P0/P1；2026-09-27 用户已批准额度未恢复期间采用独立只读代码审查和本地测试替代当前发布门禁，GLM 恢复后补审。不得将替代结果写成 GLM 通过。
- [ ] 更新 docs/xiongbao/PROJECT_SPACE_GAP_AUDIT.md 与 PROJECT_SPACE_EXECUTION_PLAN.md：只核销实际通过的 B1/B2，保留起止日期、优先级、标签、完整 WorkBuddy 视觉/键盘差距；推送 xiongbao/main 并核对远端 SHA。

## 自检

规格各章节已有对应任务：B1 数据/API/ACL/动态对应 1–2，双栏/深链/旧文本对应 3–4；B2 格式对应 5–6，媒体/撤权/容量对应 7–8；两个整合门禁分开。任务之间 DTO 与路由均以批准规格为唯一合同；迁移号、文件目录和独立工作树基线在派工前复核。计划中的代码片段是精确行为断言，执行者须在现有 fixture/类型中实现并先观察红灯。
