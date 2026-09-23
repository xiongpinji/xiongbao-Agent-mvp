# Goal

以 [PROJECT_SPACE_SPEC.md](PROJECT_SPACE_SPEC.md) 为合同，将熊宝-Agent 项目空间做成真实的多人协作域，逐项达到 WorkBuddy 5.5.6 已观察和官方记录的项目行为；完成可验证的服务端授权、前端旅程、独立只读审查和持续推送。

# Decisions and assumptions

- Codex 总指挥；`claude-bailian/qwen3.8-max` 是后端主实现；`opencode-bailian/bailian-token-plan-personal/deepseek-v4.1-flash` 是互不重叠的前端并行实现；`qwen-code-review/glm-5.3` 只读审查。每位实施者在独立 worktree 工作，不创建子代理，不提交/推送/部署/改凭据/清理用户数据。
- 首片仅做真实项目 CRUD、owner 成员记录和 ACL。表格/看板、资产、项目任务与配置在有实际数据源前保持诚实的待建设状态；绝不用专家团 `teams`、RAG 资料库或 Agent 工作区伪装项目空间。

# Constraints and guardrails

- 项目成员资格不授予任何私密任务正文或本地文件权限。所有服务端入口做成员/资源授权，包括搜索、下载、深链、事件与 WebSocket。首片 API 和角色见规格文档。
- 共享接线文件 `api/app.py`、`infra/db/services.py`、`dashboard/src/routes/index.tsx`、`dashboard/src/layouts/sidebarNav.tsx` 分属各片明示的唯一所有者；主仓库只由 Codex 整合。已推送项目基座迁移 018；成员片使用 019，计划待办预留 020，任务工作目录原 019 提案改为 021，后续迁移按实际集成顺序顺延，避免已有用户升级时跳过较低版本。
- 先写行为测试再实现，SQLite/PostgreSQL 都验证；不能将 UI 展示、源码存在、单测、实机旅程或 1:1 验收混为一谈。外部登录/邮件/云任务等没有真实授权的环节以可控本地样例验证，保留真实验收缺口。

# Checklist

- [x] 001 · Claude 主线：018 双数据库迁移、项目 repo/service、`GET/POST/GET:id/PATCH` 和成员列表 API，事务内 owner/事件，非成员与角色隔离测试。只拥有后端项目新文件和明确接线文件。
- [ ] 002 · OpenCode 并行线：按规格中固定 API 合同实现项目左栏入口、项目搜索/创建/详情四页签布局、成员与项目配置只读真实展示/未支持提示、加载/空/错误态与测试。只拥有前端项目文件和明确接线文件，不写后端。
- [x] 003 · GLM 只读：对 001/002 合并快照核查越权、404/403、SQL 兼容、UI 假能力及 WorkBuddy 行为差距，列 P0/P1 和验收样例。
- [x] 004 · Claude 主线：成员邀请/申请/审批、角色变更和撤权，安全令牌、审计、双用户权限测试。依赖 001。
- [ ] 005 · OpenCode 并行线：计划待办真实 CRUD/流转/指派、表格与看板、筛选和刷新一致性；需要独立后端契约与分工，依赖 001。
- [ ] 006 · Claude 主线：项目任务关联、私密默认、显式共享/协同/移交及历史/文件/流权限；项目配置快照和连接器凭据隔离。依赖 004 和任务工作目录安全方案，必要时拆片。
- [ ] 007 · OpenCode 并行线：项目资产库与版本、添加到任务、容量和权限；从独立真实存储模型起步，不复用未授权的本地路径。依赖 004/006，必要时拆片。
- [ ] 008 · Codex + GLM：本地/云端模式、动态/审计、双用户与非成员 E2E、1280×768 逐页 UI 对照、定向/全量测试、Windows 构建、差距矩阵核销、批次推送。依赖各已实施片。

# Validation strategy

2026-09-24 进度注记：001/003/004 已在 Agent Orchestrator 台账通过并推送；002 的项目 UI 已由 Codex 整合推送，但 OpenCode 原任务未交付可接受差异，台账保持进行中；005 的 OpenCode 初次尝试未交付实现，现由 Claude 在独立工作树承担 020 后端，前端表格/看板仍待完成。复核以调度器台账与远端提交为准，不凭此复选框推断真实旅程或视觉验收。

每个实施片由 Codex 检查完整 diff 与路径白名单、独立重跑定向测试、类型/lint/构建；根目录 `make all` 是最终 ship bar。首次安全批次验收用 owner、member、outsider 三身份分别验证列表、详情、改名和成员页，刷新后数据一致。后续逐项跑 [PROJECT_SPACE_SPEC.md](PROJECT_SPACE_SPEC.md) 的 PS-01–11。GLM 只读审查不能替代 Codex 复测。

官方 5.0.0 更新日志补充确认了外部数据源定时导入/Webhook 与个人消息中心。当前 Agent Orchestrator 八项快照在此发现前创建；以下是后继批次的显式待派工，**不是已实现或当前八项快照的完成证据**：Claude 负责数据源凭据、签名/重放保护、调度和失败重试；OpenCode 负责项目数据源配置与消息中心 UI；GLM 审查越权与事件泄漏；Codex 整合并以 PS-10/11 验收。后继批次需绑定新的持久计划项，不能仅在 008 中口头核销。

# Completion criteria

每批已接受变更推送用户仓库后记录远端 SHA；全部旅程和视觉证据齐全或明确保留项后才可声明项目空间对齐。
