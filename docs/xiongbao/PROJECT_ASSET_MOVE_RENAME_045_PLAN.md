# 045 项目资产移动与重命名实现计划

> **面向 Agent Orchestrator 实现者：** 按本计划分片做 RED→GREEN。外部实现任务不提交或推送；Codex 在独立审查与验证后负责提交、整合和推送。只使用已批准的 Claude/Qwen 与 OpenCode/DeepSeek 实现路由；GLM 只读复核。

**目标：** 在同一项目内移动或重命名活动资产，保留节点、版本和私有字节，并让回收站“删除时位置”在后续改名/移动祖先后仍准确。

**架构：** `PATCH` 薄路由调用资产服务；仓储在统一隐藏根锁序下原子修改树元数据。v30 配对迁移为回收根保存历史相对路径。前端复用资产行菜单，按页懒加载目标文件夹，沿用项目切换/撤权的请求代际守卫。

**技术栈：** FastAPI/Pydantic、SQLite/PostgreSQL、React/TypeScript/Ant Design、pytest/Vitest。

**固定规格：** [PROJECT_ASSET_MOVE_RENAME_045_DESIGN.md](PROJECT_ASSET_MOVE_RENAME_045_DESIGN.md)。起点为设计提交 `4b9f83ca1b722ca8b5e9c065eecbf57a0d0043ad`。043 当前版预览修改 `ProjectAssets.tsx`，前端片必须等 043 独立验收并从整合后的固定提交开始。

## 文件职责与工作树

| 片 | 路径 | 职责 |
| --- | --- | --- |
| 045-B | `src/octop/infra/db/migrations/030_project_asset_move_rename.sql`、`.pg.sql`、`src/octop/infra/db/migrate.py` | v30 快照列、现存回收根回填与 SQLite 幂等升级 |
| 045-B | `src/octop/infra/db/repos/project_assets.py` | 原子 move/rename、权限/环/冲突、042 delete/restore 统一根锁、快照写入/读取/清理 |
| 045-B | `src/octop/infra/projects/assets.py`、`src/octop/api/routers/project_assets.py` | 名称校验、结果映射与 PATCH 输入/安全响应 |
| 045-B | `tests/unit/db/test_project_assets.py`、`tests/integration/test_project_assets_api.py`、`tests/unit/db/test_migrate_discovery.py` | 迁移、事务/并发、HTTP 与 ACL 行为 |
| 045-F | `dashboard/src/api/modules/projectAssets.ts` | 可区分“省略父目录”和“移动到根”的 PATCH 客户端 |
| 045-F | `dashboard/src/pages/Projects/ProjectAssets.tsx`、`.test.tsx` | 行菜单、重命名/移动弹窗、逐层分页与生命周期守卫 |
| 045-F | `dashboard/src/locales/zh.json`、`en.json` | 用户可见中英文字串 |

045-B 和 043 可在独立工作树并行；045-F 与 043 文件重叠，须串行。所有路径以本表为主，发现必要额外文件时先向 Codex 报告，不能悄悄扩大白名单。根目录 `AGENTS.md` 的层级、迁移配对与检查要求适用。

## 任务 1：先锁定 v30 回收快照升级

**文件：** 创建配对 `030_project_asset_move_rename.sql/.pg.sql`，修改 `migrate.py`，测试 `test_migrate_discovery.py` 与 `test_project_assets.py`。

- [ ] 写 RED：构造 v29 库，活动目录下有独立回收根及已回收祖先，运行迁移后断言 `deleted_from_path` 分别为隐藏根相对路径；故障注入模拟“列已存在、水线仍为 29”后重启，断言只补空快照且水线为 30。配对 SQL 形状与 fresh SQLite 库各有断言。
- [ ] 运行 `uv run pytest tests/unit/db/test_migrate_discovery.py tests/unit/db/test_project_assets.py -q -k 'migration or trash_path'`，记录因缺列或回填而失败的 RED，不把缺依赖当 RED。
- [ ] 在 SQLite helper 中将“检查列 → 条件 `ALTER TABLE` → 递归回填独立回收根 → 水线 30”放在一个 `db.transaction()` 内；注册 `if version == 30` 专用分支。PostgreSQL 配对文件在迁移事务内完成相同回填。路径不含隐藏根名，已回收祖先仍参与拼接；不改活动行或版本。

```sql
-- SQLite 回填核心语句：隐藏根路径为空，其他节点递归拼名称。
WITH RECURSIVE paths(project_id, node_id, path) AS (
  SELECT project_id, node_id, '' FROM project_asset_nodes
  WHERE parent_node_id IS NULL
  UNION ALL
  SELECT child.project_id, child.node_id,
    CASE WHEN parent.path = '' THEN child.name
         ELSE parent.path || '/' || child.name END
  FROM project_asset_nodes child
  JOIN paths parent ON parent.project_id = child.project_id
                   AND parent.node_id = child.parent_node_id
)
UPDATE project_asset_nodes
SET deleted_from_path = (
  SELECT path FROM paths
  WHERE paths.project_id = project_asset_nodes.project_id
    AND paths.node_id = project_asset_nodes.node_id
)
WHERE deleted_at IS NOT NULL AND trash_root_id = node_id
  AND deleted_from_path IS NULL;
```

SQLite helper 先用 `PRAGMA table_info(project_asset_nodes)` 判断列是否存在，仅在缺列时 `ALTER TABLE`；在同一个 `db.transaction()` 执行上述回填并最后更新水线 30。PostgreSQL 用等价的递归 `paths` CTE 与 `UPDATE ... FROM paths`，同样只更新独立回收根。

- [ ] 重跑同组测试至 GREEN，再跑 `uv run pytest tests/unit/db -q`；有真实 PG 测试环境时再跑 v29→v30 回填与中断恢复，未跑则明确记未验证。

## 任务 2：先统一 042 与 045 的结构锁序，再写节点更新

**文件：** 修改 `project_assets.py` 仓储，测试 `tests/unit/db/test_project_assets.py`。

- [ ] 写 RED：`trash_node`/`restore_trashed` 与新 move/rename 的并发测试，在 PG 实库验证“恢复独立回收根 R 同时移动活动祖先 P”“删除 P 同时移动子项”“A↔B 双向移动”最终无目录环、死锁或丢写；SQLite 用两连接并发与状态不变量测试补充。给文件与目录各做 member/owner/admin、混合创建者、独立回收根权限用例。
- [ ] 先改 042 两个结构写入：成员锁后取得项目隐藏根的 `FOR UPDATE` 锁，再锁原目标/子树；保留 042 原返回码与已批准行为。所有 045 结构写入也按相同前缀；非结构上传与版本切换不反向再取根锁。
- [ ] 增加仓储 `update_node`，一次事务内检查活动目标、目标文件夹、同项目父链、环、完整后代所有权与独立回收根删除者，按服务层已验证的 `name`/`name_key` 更新。移动到根解析为隐藏根 ID；省略字段保持原值，大小写实际变化更新 `updated_at`，完全无变化不改时间。

```python
# 仓储结果应区分成功/no-op 与这些已有错误语义；不能以调用前的成员检查替代锁内检查。
role = self._member_role_locked(conn, project_id, actor_user_id)
root_id = self._ensure_root_locked(conn, project_id)
# PostgreSQL 对 root_id 取 FOR UPDATE 后再读取目标、父链和后代。
# parent_id == node_id 或目标父链包含 node_id => invalid；唯一键竞争 => name_conflict。
```

- [ ] 写 RED/GREEN 回收快照：新删除前写 `deleted_from_path`，列表只取快照，恢复时清本次树的快照；移动/重命名祖先后旧回收项的 `original_path` 不变，恢复仍挂到当前父节点。验证所有 `node_id/version_id/sha256/current` 不变。
- [ ] 运行 `uv run pytest tests/unit/db/test_project_assets.py -q`、`uv run ruff check src/octop/infra/db/repos/project_assets.py tests/unit/db/test_project_assets.py`、`uv run mypy --strict src/octop/infra/db/repos/project_assets.py`，记录退出码。PG 并发环境缺失时不声称无死锁已验证。

## 任务 3：服务与 HTTP 合同

**文件：** 修改 `src/octop/infra/projects/assets.py`、`src/octop/api/routers/project_assets.py`，测试 `tests/integration/test_project_assets_api.py`。

- [ ] 写 RED：PATCH 仅 name、仅 parent、显式 `parent_id:null`、两项同时、空体/未知字段、同父折叠重名 409、环/文件作父 422、跨项目/已删/撤权 404、无管理权限/归档 403。响应字段与普通列表节点完全相同，不能含对象键或私有路径。
- [ ] 复用 `validate_asset_name` 和 `asset_name_key`；Pydantic `model_fields_set` 区分缺省和显式 null；薄路由只解析输入、调用服务、映射安全 DTO，权限和环判断留在仓储事务内。

```python
class UpdateAssetBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = None
    parent_id: str | None = None

# 路由：if not body.model_fields_set: raise 422；
# parent_id in model_fields_set 且值为 None 才表示移动到隐藏根。
```

- [ ] 运行 `uv run pytest tests/integration/test_project_assets_api.py tests/unit/db/test_project_assets.py -q`，并跑改动 Python 文件的 Ruff、mypy；重跑已有 042 回收测试，确认锁序修改不改变 API 行为。

## 任务 4：客户端请求与菜单的 RED 测试

**文件：** 修改 `dashboard/src/api/modules/projectAssets.ts`、`dashboard/src/pages/Projects/ProjectAssets.test.tsx`。

- [ ] 写 RED：重命名/移动菜单对文件和文件夹均可触发；点击文件行仍执行 043 当前版预览，菜单/下载/版本/回收动作不触发行点击；`parent_id:null` 真正发送 JSON null；取消或 API 失败不让行消失；409 文案与旧回收冲突文案一致。
- [ ] 写 RED：单层 101+ 文件夹可通过下一页选到第 101 个，深层展开逐层翻页；源文件夹和其后代禁选；切项目/撤权时旧页结果和晚到提交不能污染新项目。
- [ ] 运行 `npx vitest run src/pages/Projects/ProjectAssets.test.tsx` 取得真正行为失败，不以元素存在测试代替用户点击与接口断言。

## 任务 5：资产页实现并验证

**文件：** 修改 `dashboard/src/pages/Projects/ProjectAssets.tsx`、`projectAssets.ts`、中英 locale。

- [ ] 增加 `projectAssetsApi.update(projectId,nodeId,patch)`，用显式对象键发送 PATCH；不可用 `patch.parent_id ?? undefined`，否则根目录 null 丢失。

```ts
type ProjectAssetPatch = { name?: string; parent_id?: string | null };
update: (projectId: string, nodeId: string, patch: ProjectAssetPatch) =>
  request<ProjectAssetNode>(nodeBase(projectId, nodeId), {
    method: "PATCH",
    body: JSON.stringify(patch),
  }),
```

- [ ] 沿用现有行的 `Dropdown` 和项目代际引用，加重命名/移动弹窗；目录选择逐层 `kind=folder` 分页到 `has_more=false`，使用目录 ID 而非显示路径提交。成功刷新列表；面包屑若含被修改节点重置到根；已回收、被撤权与切项目时清状态。仅展示 042 已冻结的“删除时位置”说明。
- [ ] 运行三组相邻 Vitest：`npx vitest run src/pages/Projects/ProjectAssets.test.tsx src/pages/Projects/ProjectAssetPdfPreview.test.tsx src/pages/Projects/ProjectAssetVersions.test.tsx`，再 `npx tsc -b`、改动文件 ESLint、Prettier `--check`、`npm run build`。真实浏览器核对 1273×634 和 1280×768 下弹窗、键盘、焦点与长目录。

## 任务 6：独立验收、整合与推送

- [ ] Codex 对 045-B 和 045-F 各自检查完整 diff、许可路径、无密钥/依赖/开关变更，独立运行上述定向测试，再按 `AGENTS.md` 跑 `make all` 和 `make build-frontend`。记录通过、失败、跳过和未验证，不能用 Linux 结果宣称 Windows/PG 通过。
- [ ] GLM-5.3 对**固定提交**只读审查迁移回填、成员锁/并发、目录环、回收路径、前端跨项目与 043 预览兼容。GLM 额度不可用时保持候选，不把其他审查冒充指定路由。
- [ ] 完成隔离 owner/member/撤权用户的真实浏览器改名→移动→下载/历史版→删除→祖先移动→回收/恢复旅程；对照 WorkBuddy 已观察的行菜单与给定 UI 截图。按审核通过的确切文件提交/整合后推送 `xiongbao/main`，校验远端 SHA；更新 PS-06 台账，但仍保持未完成项目空间全链路的状态。
