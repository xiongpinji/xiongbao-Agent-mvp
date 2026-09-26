# 042 项目资产回收站验收记录

状态：**042 代码切片本地验收通过，尚未证明用户安装包或线上运行**。本记录只对应下述固定代码提交；不能据此认为 WorkBuddy 1:1 或 PS-06 整项完成。

## 固定设计审查

- 用户在 2026-09-26 明确选择“先放回收站，可恢复”，并同意依据 [设计](PROJECT_ASSET_TRASH_042_DESIGN.md) 实现。
- `qwen-code-review/glm-5.3` 只读审查任务 `qwen-code-review-20260926-043429-93bac3`，固定提交 `f4176ec55ee660a8d6e8bc6bca0f98ec5ca504dd`、干净工作树，结论 **GO，未发现 P0/P1 设计阻断**。审查没有运行测试或迁移。
- 审查逐项核对了嵌套回收根、同名重建后的原子 409、成员撤权并发、旧下载和 PDF 预览 URL、对象孤儿清理及 v29 中断重入。依据是设计、v23 约束和既有代码入口，不是实现后的行为证明。
- 实施时须保留 P2 验收点：已安装数据库处于 v28，故使用配对 v29 迁移与 SQLite 逐列重入；路由顺序和所有历史下载入口逐一测；前端把“可见/回收站字节”标成**当前版本口径**，嵌套回收项提示先恢复父级；并发、PostgreSQL 实库和隔离浏览器旅程仍待证明。

## 实现与独立验证

- 固定代码提交：`605b0033040710772e6901c4764e894a85344d27`，从 `7ba22e56` 合入 v29 SQLite/PostgreSQL 配对迁移、整树软删除/恢复、普通入口隐藏回收项、当前版本用量拆分、ACL、前端确认与回收列表。GLM 在独立干净工作树对该 SHA 做只读审查，任务 `qwen-code-review-20260926-064730-d786fe`，结论 **GO、无 P0/P1**。其 P2 是回收列表索引可能全项目扫描、旧行并发 404 在前端可能误显为项目无权；两项继续跟踪，不当作已修复。
- 后端：隔离候选的资产单元/HTTP 测试 **162/162**、相邻项目回归 **87/87** 通过；Ruff、格式、三个改动模块的 mypy 通过。合并 SHA 上用已锁定的 WSL Python 环境指向本仓库 `src` 重跑资产单元/HTTP **162/162**、中英错误码对照 **12/12**，证实新 `PROJECT_ASSET_PARENT_IN_TRASH` 键跨端一致。原先在 Windows 盘执行 `uv run --locked` 曾卡在重建虚拟环境，已中止该环境安装；上述直接运行的测试不是该安装的结果。
- 前端：Windows Node 24、仓库锁定的 Vitest 3.2.6，对四个资产测试文件 **85/85** 通过；`npm run build`（TypeScript + Vite）通过。构建有既有的动态/静态混合导入及大 chunk 提示，不是失败。首次在 WSL 使用 Windows `node_modules` 时缺少 Linux Rollup 原生包，随后改在 Windows 原生环境验证。
- 本地浏览器：固定提交构建的 Dashboard 加本地隔离 Octop 服务，Chrome **153.0.8010.53** 无头执行十阶段真实页面/API 旅程。包括 owner 在 UI 新建项目并邀请 member、取消删除确认时零 DELETE、确认回收后的普通与历史版 URL 404、同名恢复 409 并保留原版本 ID/哈希、member 仅恢复自建资产、独立回收子项必须先恢复父目录、撤权后 member 页面不显示旧资产且 API 为 404。机器结果见 [result.json](evidence/asset-042/result.json)，页面状态见[删除前](evidence/asset-042/01-before-delete.png)、[回收站](evidence/asset-042/02-in-trash.png)、[恢复后](evidence/asset-042/03-restored.png)、[成员撤权](evidence/asset-042/04-revoked-member.png)。此服务用一次性本地账号绕过测试验证码，不代表生产登录验收。Playwright CLI 因本机旧的 `ms-playwright` 缓存 Junction 指向不存在的目标而无法启动，浏览器旅程改用同一 Playwright Core 与 Chrome 无头执行；没有操作用户的 WorkBuddy 窗口。

## 明确未达

- PostgreSQL **实库**迁移与真实并发交错未运行：现有证据是 SQLite 行为、迁移脚本同形和 PostgreSQL 锁 SQL 静态测试；不能宣称 PG 端已验收。
- WorkBuddy 5.6.2 只观察过项目资产“删除”菜单，未执行其删除确认或恢复；熊宝的“先入回收站”来自用户批准的产品决定，不能写成已复刻 WorkBuddy 删除语义或视觉细节。
- 项目归档后的回收读/写 403 有 HTTP 集成测试，当前公开项目 API 无归档入口，本次浏览器旅程未覆盖归档动作。用户安装包、线上环境和完整 11 条项目空间旅程也未验收。
- 项目级永久删除、历史版本物理总量、额度、任务引用、移动/重命名与 Office 预览不属于 042；PS-06 整项和 WorkBuddy 1:1 对齐仍开放。
