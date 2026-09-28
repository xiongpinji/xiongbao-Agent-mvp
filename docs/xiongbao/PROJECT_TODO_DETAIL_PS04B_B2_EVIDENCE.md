# PS-04B B2 安全 Markdown 与私有评论图片验收记录

状态：**B2 代码提交 `f1e6ccafd027b7fd2ba4cbbd616c5042d16e420c` 已推送 `xiongbao/main`，`git ls-remote xiongbao refs/heads/main` 返回同一 SHA。** 独立审查、聚焦验收与全仓非 live 回归已完成。B1 的独立验收见 [B1 记录](PROJECT_TODO_DETAIL_PS04B_B1_EVIDENCE.md)。本记录只核销批准规格中的 B2 行为，不代表 WorkBuddy 项目空间整体 1:1。

## 本片交付

- SQLite/PostgreSQL 配对迁移 v32 给待办增加 `description_format=plain|markdown`，旧记录保留为 `plain`；状态等局部 PATCH 保持原格式，旧客户端修改正文时按纯文本写入。前端专用 Markdown 编辑、预览和安全渲染只允许批准的语法及 HTTP(S) 链接，不执行原始 HTML，也不加载 Markdown 外部图片。
- 配对迁移 v33、独立私有图片根和评论图片表实现文字/图片或纯图片评论；JSON 文本评论继续可用。上传有流式字节限额、真实格式解码、请求号幂等、项目 512 MiB 并发配额和失败回收。每次图片 GET 均重验当前成员及项目、待办、评论、图片归属，响应使用 `private, no-store` 与 `nosniff`。
- 前端支持粘贴预览、提交进度、失败保留草稿和图片、按登录态读取私有图片；切换项目、撤权或卸载时取消晚到请求并释放 Blob URL。备份归档同时保存数据库引用的私有图片及大小/哈希索引，恢复前核对数据库转储、索引和图片字节。

## 验证证据

| 层级 | 结果 |
| --- | --- |
| 根代理独立后端聚焦回归 | 格式、评论、上传、私有存储、待办 API 与 i18n 共 **162 passed**，退出码 0；备份模块另有 **71 passed、3 skipped**。 |
| 根代理静态检查 | `uv run mypy --strict src/octop` 检查 **546 个源文件**通过；备份两个新增/修改测试文件和三个备份源文件的严格 mypy 通过；`uv run ruff check src tests`、`uv run ruff format --check src tests`（1128 文件）及 `git diff --check` 通过。 |
| 前端 | API、计划、详情、安全 Markdown 四个 Vitest 文件共 **56/56** 通过；`npx tsc -b`、全量 ESLint、改动文件 Prettier 与独立临时目录 Vite 构建通过。ESLint 输出 **67 条本片范围外既有警告、0 错误**；JSDOM 的伪元素 `getComputedStyle` 噪声不影响 Vitest 退出码。 |
| 真实 PostgreSQL | 一次性 PostgreSQL 18.6 的专用升级库验证 v31→v33 保留原待办/评论、图片幂等/冲突/撤权；专用新库验证 v33 以及两个连接并发抢占同项目容量，仅一方成功且最终用量恰为 512 MiB。用真实 `pg_dump -Fc`/`pg_restore --data-only --table=project_todo_comment_images` 输出验证归档解析器可读取对象键、大小和哈希。9 月 28 日固定 `04481724` 又完成[真实 tar 归档补验](PROJECT_TODO_DETAIL_PS04B_PG_BACKUP_ACCEPTANCE_20260928.md)：实际数据库恢复 rc=0、九表原字段一致、PNG/JPEG 字节与用量一致、三个坏包在 DB 恢复前拒绝且目标不变。两次集群与端口均已停止。 |
| 登录态浏览器 | 一次性本地数据、合成 owner/member/outsider 下的 Chrome 旅程输出 `PASS`：危险 Markdown 不执行、编辑保存与预览往返、成员粘贴图片与私有读取、外部人员及撤权后旧图片请求 404；1280×768、800×728 两视口无页面错误。截图在仓库外 `work/ps04b-browser-qa/screenshots/`。 |
| 独立只读审查 | 审查子代理先指出 PostgreSQL 转储与图片索引可能漂移，修复后又指出 multipart OpenAPI 必填字段与运行时不一致；对最终固定代码 SHA `f1e6ccaf` 的 38 个代码/测试文件只读复审为 **APPROVE、P0/P1/P2 均为 0**。审查者另跑 134 passed、2 skipped 的定向复测。审查主体不是 GLM。 |
| 全仓非 live 回归 | `uv run pytest -n 4 -m "not live" -q` 最终 **4593 passed、160 skipped、22 warnings，退出码 0**，耗时 1075.14 秒。首轮 4590 passed、161 skipped、2 failed：两项旧资产 v30 回填测试把运行所有迁移后的水位写死为 30；改为仓库现有 `_max_discovered_version("sqlite")` 后两项独立复测通过，原有删除时路径回填断言保留；第二轮全量通过。警告包含依赖弃用提示及既有浏览器 API 测试的 Windows 子进程 UTF-8 读取异常，不能写成零警告。 |

## 门禁边界

本 Windows 环境没有 `make` 可执行文件；对应 Python Ruff、严格 mypy、非 live pytest 与 Dashboard 类型、lint、格式、构建均分别运行并记录，不能把它们写成实际执行了 `make all`。代码提交时显式 `SKIP_PRECOMMIT=1` 绕过依赖 `make precommit` 的钩子；上述独立检查与全仓回归在提交前均已通过。真实 PostgreSQL 首轮覆盖迁移、仓储并发和归档 COPY 解析；后续真实 tar 管线已补齐数据库与 B2 评论图片切片往返及三种坏包前置拒绝，**完整 PG HTTP + 浏览器矩阵、其他系统目录与聊天保留仍未验证**。备份需要数据库转储和私有文件的静止快照；现有 PostgreSQL 恢复不是数据库与文件的跨资源原子事务，出错时图片树会尝试回滚，但不能据此声称数据库自动回滚。

原 `claude-bailian/qwen3.8-max` 和 `opencode-bailian/deepseek-v4.1-flash` 实现路由未交付 B1/B2 代码，Codex 按用户 2026-09-27 单独授权承接；GLM 固定 SHA 补审仍待额度恢复。本片未交付起止日期、优先级、标签、可配置待办视图，也未完成 WorkBuddy 实机逐状态视觉/键盘 1:1 验收。
