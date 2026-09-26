# 项目资产回收站 042 实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。此仓库按用户已选的 Agent Orchestrator 三条 CLI 路由执行：Claude/Qwen 主实现，OpenCode/DeepSeek 并行实现，Qwen Code/GLM 只读审查；Codex 负责集成、验证和推送。

**目标：** 项目文件或文件夹可安全移入回收站并原样恢复；历史版本、成员权限、名称冲突和私有字节均保持一致。

**架构：** v29 迁移给节点增加软删除标记与回收根，仓储在同一成员锁/数据库事务内执行整树删除和恢复。既有普通资产入口统一只读活动节点；独立回收列表/API 暴露安全元数据。前端通过已有项目资产页与 API 模块提供二次确认、回收列表和恢复。

**技术栈：** Python 3、SQLite/PostgreSQL、FastAPI、React/TypeScript、Ant Design、Vitest、Pytest、Ruff、mypy。

**规格与基线：** [042 已批准设计](PROJECT_ASSET_TRASH_042_DESIGN.md)，基线 `dcc1fc2ba8b123dcfedff2e81ff04f259172c7f0`。WorkBuddy 删除后是否可恢复未实测，不能声称 1:1；PS-06 整条旅程继续开放。所有实现 worker 禁止提交、推送、删除工作树或调用其他模型；由 Codex 在审查后选择性提交。其他工作树和未关联文件不得修改。

## 文件职责与并行边界

| 责任 | 路径 | 职责 |
| --- | --- | --- |
| B1 后端迁移 | `src/octop/infra/db/migrations/029_project_asset_trash.sql`、`.pg.sql`，`src/octop/infra/db/migrate.py`，`tests/unit/db/test_project_assets.py` | 配对 schema、停机中断重入与升级测试 |
| B2 后端领域/API | `src/octop/infra/db/repos/project_assets.py`、`src/octop/infra/projects/assets.py`、`src/octop/api/routers/project_assets.py`、`src/octop/infra/errors.py`、`src/octop/i18n/{en,zh}.json`，`tests/unit/db/test_project_assets.py`、`tests/integration/test_project_assets_api.py` | 软删除、恢复、活动门禁、成员与事务 |
| F1 前端 | `dashboard/src/api/modules/projectAssets.ts`、`dashboard/src/pages/Projects/ProjectAssets.tsx`、新建 `dashboard/src/pages/Projects/ProjectAssets.module.less`、`dashboard/src/pages/Projects/ProjectAssets.test.tsx`、`dashboard/src/locales/{en,zh}.json` | 回收 UI、并发/撤权显示和文案 |
| 验收 | `docs/xiongbao/PROJECT_ASSET_TRASH_042_ACCEPTANCE.md`、`docs/xiongbao/PROJECT_SPACE_GAP_AUDIT.md` | 固定 SHA 审查、测试与未达项 |

B1+B2 由一个后端 worker 连续实施，避免同文件冲突；F1 在独立工作树并行，可先针对合同 DTO 写测试，再以 B2 固定接口验收。GLM 在两个候选固定 SHA 上只读审查，不修改任何代码。后端先于前端集成，以免 UI 暴露尚未存在的删除服务。

### 任务 1：v29 迁移与恢复约束（后端 worker）

**文件：** 创建两份 `029_project_asset_trash` 迁移；修改 `migrate.py`、`test_project_assets.py`。

- [ ] 写新库与 v28→v29 升级的失败测试，断言四列存在、旧节点保持活动、回收根索引存在、迁移中断后可重入。例：

```python
columns = {row["name"] for row in conn.execute("PRAGMA table_info(project_asset_nodes)")}
assert {"deleted_at", "deleted_by", "trash_root_id", "original_name_key"} <= columns
assert conn.execute("SELECT deleted_at FROM project_asset_nodes WHERE node_id = ?", (old_id,)).fetchone()[0] is None
```

- [ ] 在隔离环境运行 `PYTHONPATH=src uv run --locked pytest tests/unit/db/test_project_assets.py -q`，预期新增断言因 v29 缺列失败；旧测试不应被改坏。
- [ ] 编写 SQLite/PostgreSQL 配对 `ALTER TABLE`、索引和版本水位；若 SQLite 多语句中断会留下部分列，沿用 `migrate.py` 的 v27/v28 列检查模式逐列补齐，再升水位。两数据库均用可空字段保留旧数据。关键约束：

```sql
ALTER TABLE project_asset_nodes ADD COLUMN deleted_at INTEGER;
ALTER TABLE project_asset_nodes ADD COLUMN deleted_by INTEGER REFERENCES users(id) ON DELETE SET NULL;
ALTER TABLE project_asset_nodes ADD COLUMN trash_root_id TEXT;
ALTER TABLE project_asset_nodes ADD COLUMN original_name_key TEXT;
CREATE INDEX IF NOT EXISTS idx_project_asset_nodes_trash
  ON project_asset_nodes(project_id, trash_root_id, deleted_at);
```

- [ ] 跑新库/升级/中断重入测试；静态对照 `.sql`/`.pg.sql` 列、索引和水位。未连真实 PostgreSQL 时标明未验证。
- [ ] 保存最小差异供 Codex 审查，worker 不提交。

### 任务 2：仓储原子删除/恢复与全读路径门禁（后端 worker）

**文件：** `repos/project_assets.py`、`assets.py`；测试 `test_project_assets.py`。

- [ ] 先添加失败测试：删除文件、含活动/已独立回收子项的目录；同名重新上传后恢复必须 409 且回收状态未变；跨项目/撤权/归档/隐藏根；新版本、旧版本、下载、搜索、父目录与使用量的已删拒绝。一个核心断言：

```python
assert service.prepare_download(project_id, user_id=owner_id, node_id=file_id)  # 删除前
service.trash_asset(project_id, user_id=owner_id, node_id=file_id)
with pytest.raises(OctopError) as denied:
    service.prepare_download(project_id, user_id=owner_id, node_id=file_id)
assert denied.value.code == ErrorCode.NOT_FOUND
```

- [ ] 跑 `PYTHONPATH=src uv run --locked pytest tests/unit/db/test_project_assets.py -q`，确认新增用例因无方法/未过滤而红。
- [ ] 在仓储事务中锁成员、验证角色和项目，递归找目标活动子树，仅标记 `deleted_at IS NULL` 的节点，目标改冲突键 `"\x1f" + node_id`；恢复只更新 `trash_root_id=目标` 的节点，并在同事务检查原父活动和原名唯一。用户可构造的名称经 `validate_asset_name()` 禁控制字符。活动判定被 `get_node`、列表、版本、下载、上传父目录、版本切换共享，不在前端重复代替服务端授权。
- [ ] 使用同一个事务锁序覆盖删除/上传、恢复/同名创建、撤权/恢复；使用量拆为活动当前版本与回收当前版本，历史版本和真实物理配额仍不计。补无对象孤儿回收回归：所有版本 `object_key` 仍被仓储引用。
- [ ] 跑聚焦测试与 `uv run --locked ruff check`、`uv run --locked ruff format --check`，保存差异。

### 任务 3：安全 HTTP 合同与双语错误（后端 worker）

**文件：** `api/routers/project_assets.py`、`infra/errors.py`、服务端 `i18n/{en,zh}.json`，`tests/integration/test_project_assets_api.py`。

- [ ] 先加 HTTP 失败测试：`DELETE /api/projects/{p}/assets/{node}` → 204；`GET /api/projects/{p}/assets/trash` 分页只含回收根且不含对象键；`POST /api/projects/{p}/assets/trash/{node}/restore` → 活动节点；重复操作 404、同名 409、无权限 403、撤权和外人 404。历史下载返回 404，恢复后字节与 SHA 不变。
- [ ] 跑 `PYTHONPATH=src uv run --locked pytest tests/integration/test_project_assets_api.py -q`，确认新路由未实现的预期失败。
- [ ] 静态 `/assets/trash` 路由注册在动态 `/{node_id}` 之前；在 `run_in_executor` 调用同步服务，维持现有错误封套。响应回收项仅有安全字段，`deleted_by` 只经成员安全显示名映射，不回传邮箱、私有路径或对象键。使用量新字段与旧字段兼容，例如：

```json
{"file_count": 1, "total_bytes": 32, "trash_file_count": 2, "trash_total_bytes": 64}
```

- [ ] 跑资产单元/集成、错误键对齐、Ruff/format/mypy；记录 PostgreSQL 实库是否执行。
- [ ] 把最终路径列表、结果和已知风险交给 Codex；worker 不提交。

### 任务 4：资产页回收交互（前端 worker）

**文件：** `dashboard/src/api/modules/projectAssets.ts`、`ProjectAssets.tsx`、`ProjectAssets.module.less`、`ProjectAssets.test.tsx`、`locales/{en,zh}.json`。

- [ ] 先写 Vitest RED：点击删除仅打开确认，取消不发送请求；确认调用真实 `DELETE` 并刷新；回收站分页、恢复同名 409 保留行；切项目或 404 撤权清空旧列表/晚到响应；两类用量标注不冒充配额。
- [ ] 跑 `cd dashboard && pnpm exec vitest run src/pages/Projects/ProjectAssets.test.tsx`，确认新行为的失败是缺功能而非测试装置错误。
- [ ] API 类型增加 `ProjectAssetTrashItem` 与 `trash/restoreTrashed/deleteToTrash`，全部 ID `encodeURIComponent`；页面复用现有 Abort/序号模式，不让晚到请求覆盖新项目。确认文案是“移入回收站，可恢复”，恢复失败留在回收列表；删除后的普通版本弹窗关闭。保留“添加到任务”禁用。UI 以独立回收视图/弹窗呈现，1280 与窄屏都可操作。
- [ ] 跑该测试、Projects 相关测试、ESLint、Prettier、`tsc -b`、Vite build；不把 mock-only 测试称为真实浏览器验收。
- [ ] 保存最小差异与测试摘要供 Codex 固定 SHA 审查，worker 不提交。

### 任务 5：独立审查、集成与真实验收（Codex + GLM）

- [ ] Codex 核对后端/前端候选 diff、allowlist、固定 HEAD 与未提交变更；GLM 只读审查授权、并发、迁移、历史下载、双语和 UI 失效态。P0/P1 必须修复并复审；P2 留在验收记录。
- [ ] 先集成后端，独立跑资产迁移/单元/HTTP/项目回归及 Ruff/mypy；再集成前端，跑 Vitest/ESLint/Prettier/`tsc -b`/build。新增测试不得只镜像实现。
- [ ] 隔离浏览器以 owner/member/撤权身份实际删除、同名冲突、恢复、刷新与历史下载；保存截图和机器可读结果。PostgreSQL 实库若未运行不作通过声明。WorkBuddy 删除动作未由用户授权实测，不推断其回收语义。
- [ ] 更新 `PROJECT_ASSET_TRASH_042_ACCEPTANCE.md` 与 `PROJECT_SPACE_GAP_AUDIT.md`，选择性提交和推送 `xiongbao/main`，确认远端 SHA 等于本地；只在双重旅程/视觉完整时核销对应差距。

## 范围自检

本计划覆盖迁移、权限、整树并发、所有活动入口、历史版本字节、回收 UI、两类用量与真实浏览器验收。永久清理、配额、移动/重命名、任务引用、产物回存和 Office 预览仍在后续独立任务；没有用 042 冒充 PS-06 或项目空间 1:1 完成。
