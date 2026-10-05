# Goal

连续完成熊宝-Agent 的 WorkBuddy 功能、交互、UI 和 Windows 交付对齐。以用户已确认的熊宝品牌为视觉素材，逐项核销原 25 项矩阵及项目空间 PS01–11；每一片通过独立审查、必要测试和用户旅程后及时推送功能分支并进入 develop，最终给出目标环境验收及剩余外部依赖记录。整体未达完成标准时不把本计划或某一 QA 片称为产品交付完成。

# Decisions and assumptions

- 依据 2026-10-04 用户“**一次性完成整体 WorkBuddy 功能与 UI 对齐**”，把剩余工作作为连续目标管理；一次性安排全量依赖和分工，业务实现仍按可复核切片推进。
- 唯一活动源码库为 `D:/AI编程库/项目库/进行中的项目/xiongbao-Agent`，WSL 使用同一目录 `/mnt/d/AI编程库/项目库/进行中的项目/xiongbao-Agent`。不建立额外源码副本或 worktree。Orchestrator 当前 preferences 已为 project。
- 工作分支 `feature/workbuddy-full-parity-20261004` 从 develop `187c70a30c4f6ab0edba240157fd22daf07774f7` 建立；上一片分页 QA `71b58f48` 独立保留在 PR #1，当前 CI Linux/Windows success，live 33 项全部跳过。该 PR 未合并，本分支不冒充已包含这四个文件。两个提交的业务源码一致。
- Codex 总指挥；原配置继续为主实现 `claude-bailian/qwen3.8-max`、并行实现 `opencode-bailian/bailian-token-plan-personal/deepseek-v4.1-flash`、只读 `qwen-code-review/glm-5.3`。本机 doctor/choices 通过只证明 CLI 安装，不证明额度可用。
- 045-F 原三次尝试已到上限，authoritative checkpoint 为 `plan-20260927-023702-6ada0e/item-003`。反馈 `feedback-20260927-200851-b903de` 已用本轮用户统一承接授权回答。Claude 原结果 quota429，OpenCode 原有效工作树尝试 7200s 超时且无改动；原尝试次数保留，新实施记录为 native 承接。
- 030UI、PS04D 与 046 的原特定门禁分别记录；本轮 native 承接及独立替代审查已经获得统一授权，后续依此连续执行。文件模式激活和运行时安全门禁仍需实际验证。
- 用户本轮已明确回复“统一授权全量承接与替代审查”和“批准两项按门禁连续推进”：Codex/按文件分工的 native 实施代理获准承接固定规格的剩余工作，GLM 不可用时由独立 native 只读审查和本地测试验收；PS04D 第三版进入实现，046 先 M0 隔离探针、通过后固定接口实施。这项新授权覆盖 045-F/030UI 等后续承接，不授权忽略技术失败或复制不安全实现。原外部路由和旧失败次数保持原样，native 承接用单独私有执行台账记录，不伪造外部 job 或 GLM 运行。
- WorkBuddy 参考为仓库已有 5.5.6/5.6.2 实机记录、用户参考图，以及腾讯官方[项目说明](https://cloud.tencent.com/document/product/1831/138797)、[入门指南](https://cloud.tencent.com/document/product/1831/134389)、[右侧边栏](https://cloud.tencent.com/document/product/1831/134400)。官方页面说明不替代实机视觉或实际生成结果。
- 当前会话的原生电脑控制不可用；浏览器工具不能读取原生 WorkBuddy 窗口。因此先复用已有实机素材和官方说明，当前原生窗口的逐状态视觉留作独立证据门禁。不得声称已经看过本轮 WorkBuddy 窗口。
- 需要真实第三方账号、供应商付费生成、生产发布/签名分发的项目，先完成可独立验证的实现、受控本地旅程及候选工件，再在具体对象和成本明确时取得相应授权。

# Constraints and guardrails

- 遵守当前 AGENTS.md：分层、i18n、服务器时区、跨平台、成对迁移、uv pytest、RED→GREEN、真实权限边界。只编辑 dashboard 源码；构建产物由构建生成。
- 单库可并行的前提是文件白名单不相交；locale、迁移编号、db services、app、manager、聚合 DTO、ProjectDetail/Chat 等接线只有一个所有者。代码 worker 不嵌套代理、不提交/推送、不改凭据、不删用户资料。
- 只读探索代理只盘点现状，不冒充实施者。实现者输出完整 diff 和失败记录，独立审查者不改源码。Root 完成检查与整合，不把 worker 的退出或口头报告当验收。
- 不扩大四文件 QA 的一次性临时 hook 例外。全量发布仍要求普通 make/format/lint/typecheck/test/build 门禁；若环境不适配，先做有界环境修复与明确验证，不能直接套旧例外。
- 030 files 开关当前 False；UI 接线完成不启用它。运行时/Windows 路径能力须实际探针及安全复核通过，失败回到方案，不放宽 ACL。
- 原源码、验收、失败/超时和历史台账保存。新状态引用当前 SHA，旧状态保留原历史语境；不上报虚构完成率或把 0/11 双验收误说为 11 项都无源码。
- 开发交付为 feature→develop；main 只走 AGENTS.md 的 release/hotfix，生产 tag 必须在 main。禁止强推、批量推 archive refs 或把私有日志/认证正文公开。

# Checklist

- [ ] W00：锁定全量执行授权、当前分支/源代码与原 job 状态，建立当前默认质量门禁和每批发布回执；保留原 25 行及 PS01–11 的逐项状态，不重做已接受功能。
- [ ] W01A：完成 045-F 资产改名/移动：PATCH 客户端、文件/目录行菜单、重命名及逐层分页的目标目录弹窗，根目录 null、环/同名冲突、取消/失败、跨项目/撤权/晚到响应；验证 node/version/字节和回收站历史位置。依赖原 045-B 已接受，045-F 承接授权。
- [ ] W01B：修复 030UI 私密任务工作区抽屉接线：显式 privateTask、内部本人任务 readiness、普通 Agent 与误匹配拒绝。保持 files=False。与 W01A 文件不相交，可并行。
- [ ] W02：完成 PS04D D1 待办附件：先 Windows/POSIX 存储隔离探针，再独立附件存储/配额/幂等/撤权/备份、display_revision 双水位、表格附件字段及真实多身份 UI；复用第三版 GO 规格，固定任务包后实施。
- [ ] W03：完成 PS04D D2 一级子待办：关系/事务、root-only 旧列表、query/schema/cursor v2、table show_subtodos 显式字段语义、子详情与并发/旧库升级；依赖 D1，串行持有 todos 聚合和迁移。
- [ ] W04：完成 046 项目资产进入任务：M0 双用户/双任务/共享专家隔离探针→R1 固定版本预检/首轮幂等状态→R2 选择器/卡片/私密任务→R3 模型通过受控工具实际读取。M0 不通过则改设计，元数据卡片不能算正文读取；ProjectAssets 接线等待 W01A。
- [ ] W05：完成 PS07 项目公共能力：先消除共享 Agent 个人连接器 P1，固定 current user/thread 工具上下文、公共凭证、技能/连接器/cron 配置快照、撤销与失败恢复；后接项目配置 UI，保留个人能力。
- [ ] W06：完成 PS05 协作/移交和完整资源 ACL：覆盖所有发送/恢复/订阅/正文/附件/执行入口、并发移除/授予/归档、队列与交接状态、逐消息分享；依赖稳定资源与公共工具合同，不重做既有摘要/文本分享安全修复。
- [ ] W07：完成 PS09 归档/恢复/资源保留/故障恢复/可读审计，再完成 PS11 消息中心（未读、邀请审批、相关动态、协作深链）；后补 PS03 提及/图片/富文本。先固定数据合同，通知预览不得泄漏撤权资源。
- [ ] W08：完成 PS10 外部源定时导入/Webhook（签名、重放、来源、凭据隔离、重试/撤权停用）；完成 PS06 产物回存、真实配额、安全 Office 预览。读入和回存独立验收，依赖 W05/W07。
- [ ] W09：完成任务历史搜索/筛选/持久置顶改名/归档恢复：先修现有主 hook 与 Minimal 导航失败回滚，再完整分页检索及状态/日期，当前任务删除/归档安全导航。
- [ ] W10：完成任务目录与结果区：030 安全方案和运行时门禁→明确目录/权限→可追溯增改删与 diff API→变更标签→网页运行/停止/刷新；统一产物/文件/变更/预览/浏览器来源，禁止借 UI 启用未过门禁能力。
- [ ] W11：完成普通专家/技能/连接器/资料库/自动化的真实可控调用旅程，以及 Plan/Ask/模型/权限/附件/停止继续重试的刷新与失败状态。优先补现有源码实际缺口，再做 Word/PPT/Excel/PDF 真实文件结果和引用溯源；真实供应商结果单独记录。
- [ ] W12：完成专家团实际分工/并行/失败隔离/合并与成本权限可见，现有渠道的身份关联和跨端续接；未配置的外部渠道先完成适配与可控测试，实接明确列出。
- [ ] W13：统一 UI 逐页对照：熊宝深色暖金三栏、项目四页签和各弹窗/菜单/空错态，固定两个视口及宽屏/窄屏；键盘/焦点/读屏/中英/缩放/深浅主题。每项绑定同状态参考与当前截图，不能凭首页核销全 UI。
- [ ] W14：完成 Windows 原生品牌/图标/通知/文件关联/可安装候选、安装升级卸载和目标环境验收；完整回归、SQLite/PG 升级/并发、多身份旅程和 25 行/PS01–11 最终独立审查；合入 develop 后按 release 流程准备 main 交付。

## 第一波文件所有权

| 角色 | 独占写入范围 | 依赖/禁止并行范围 |
| --- | --- | --- |
| 045-F 前端实现 | `dashboard/src/api/modules/projectAssets.ts`、`dashboard/src/pages/Projects/ProjectAssets.tsx`、`ProjectAssets.test.tsx`、`dashboard/src/locales/{zh,en}.json`，五文件 | 不改后端/依赖；locale 被占用期间其他实施者只提出需要的键；046 资产接线排后 |
| 030UI 实现 | `dashboard/src/pages/Agent/Workspace/components/WorkspaceDrawer.tsx`、新增 `WorkspaceDrawer.privateTask.test.tsx`、`dashboard/src/pages/Chat/components/ChatDockPanel.tsx`、`ChatDockPanel.workspaceFiles.test.tsx`，四文件 | 不写 locale/后端/开关；后续运行时改造与此片串行 |
| 规格/探针 | PS04D/046 合同和独立私有探针；不能同时改 shared runtime/todos/迁移 | 按固定包分配，不让探索暗中写业务代码 |
| Root | 本计划、质量/授权记录、独立验证、共享最终接线、Git/PR/发布回执 | 不和实施者同时写其所有权文件 |
| 独立审查 | 固定候选差异、测试与实际运行证据；只新增专有审查报告 | 不修本人审查的代码，不自称 GLM 已执行 |

后继每一项在派工前编译独立任务包，指定现行符号、文件范围、API 与产品规则、RED/失败/撤权/迟到/幂等测试、停止条件及预期报告。未知运行能力先探针；稳定接口形成前不派后继 UI。任务执行记录放 Orchestrator 外部持久状态，私有原件放本库 `_local/qa/`，仅必要安全摘要入 Git。

# Validation strategy

步骤均为“固定合同与行为 RED → 最小实现 GREEN → Root 独立定向/相邻回归 → 固定候选独立只读审查 → 必要真实 TCP/多身份浏览器/PG/Windows探针 → 普通质量门禁 → feature 分支推送/PR→develop → 更新台账”。相关迁移含 fresh/upgrade/idempotency/备份和 SQLite/PG 差异；权限含撤权及并发；UI 含错误/草稿/迟到/finally/焦点和两个视口。

AGENTS.md 的默认 ship bar 是 `make all` 及前端 `npx tsc -b`/构建；实际 hook 目前为 make precommit 加 dashboard build，两者均需如实记录。前片缺 make/CRLF 例外不是本计划全量放行依据。完整回归采用当前字节，任何 source 变化不能挪用旧全量结论。GitHub CI 结论分别绑定各 PR 和提交 SHA，不证明未提交的后续实现。

单元、组件、离线合成链路、当前登录浏览器、PostgreSQL、原生 WorkBuddy 视觉、真实账号/模型和目标环境各是独立证据。每个运行要有来源 SHA、失败记录、资源归属与关闭回执；未知提交结果不自动重试付费任务。

# Completion criteria

原 25 项矩阵及项目空间 PS01–11 每项都有当前实现/实际测试/独立审查/用户旅程/同状态交互视觉结果；适用 Windows 安装交付与目标环境检查完成；普通质量与跨平台 CI 绿，发布分支/远端/候选工件与验收 SHA 一致，单库干净，owned 资源关闭。仍待原生能力、具体账号/付费/第三方平台或目标环境授权的事项明确列为未完成，不能宣布整体 1:1 或差距归零。全量计划和所有派工本身不属于完成证据。

# 2026-10-04 第一波当前记录

W01A 045-F 和 W01B 030UI 已完成有界前端实现、固定候选独立审查，分别有 110 项及 Root 重跑 23 项相关测试通过。普通 `make all`（5329 passed、319 skipped）、前端全量（251 文件/2008 测试）、类型、lint、格式与构建均通过。W01A 合成 owner/admin 浏览器已实际执行文件/文件夹改名移动、冲突保持、根目录选择和 PDF/版本保留；测试运行总 exit 2 的被拦截专家市场请求及各资源关闭、数据不变性分别保留，不能混写为浏览器总通过。详情见 [第一波验收记录](WORKBUDDY_WAVE01_20261004_REPORT.md)。这些证据没有核销 member/撤权实机、PostgreSQL、原生 WorkBuddy 同状态视觉和整体交付。

W02 D1 严格存储能力和 W04 046 M0 仍 NO-GO，继续最小能力工程和复验，不激活功能。W05 已完成只读合同接缝盘点；W09 已安排独立准备既有 pin/rename 持久化失败回滚的有界合同，避免重做已存在的分页。

# 2026-10-04 第二波当前记录

第一波 `414b3319` 已推送同一功能分支并进入 [PR #2](https://github.com/xiongpinji/xiongbao-Agent-mvp/pull/2)，固定 SHA 的 Linux/Windows Hosted CI 均成功；Windows 5,333 项通过、315 项跳过，Live 33 项全部跳过。它不证明第二波候选或真实供应商调用。

W05 已完成个人 MCP 调用上下文、模型描述及工具复核、服务端入口与过程内暂停恢复的有界实现和独立审查；W09 已完成四处会话改名/置顶失败回滚和迟到响应保护，合成账号浏览器观察已保留。浏览器总运行 exit 2 的观察器日志失败与实际 UI、资源关闭证据分别记录，不能把整次运行称为通过。折叠导航可访问名称与当前页语义完成有界修复。详情与本批质量门禁见 [第二波验收记录](WORKBUDDY_WAVE02_20261004_REPORT.md)。

待办附件的 display/catalog 双水位、v2 游标、严格表格模型和完整 D1 迁移已进入技术任务编译；原第三版批准与统一承接授权继续有效，严格存储与 046 M0 仍 NO_GO，D2 前置顺序保持。标题检索后继包单独固定完整候选查询、权限和字面匹配规则；状态/日期/归档以及其余全量项目继续待完成。此记录不是整体 1:1 或最终交付验收。

# 2026-10-06 表格与真实 PostgreSQL 当前记录

W03 表格“显示子待办”已随 `cf4a0710` 推送，Windows/WSL PG 测试路径修复已随 `0d4d06d1` 推送。完整本地质量门禁及前端默认全量通过；原始表格层级/游标/查询预算 PG 节点在真实新建集群 1 项通过，正常资源关闭经独立只读复核。详情及来源边界见[第九波验证记录](WORKBUDDY_WAVE09_TABLE_PG_20261006_REPORT.md)。W03 浏览器及原生视觉仍待验收，不能因唯一 PG 节点通过而核销全量项目。

私有 SDK 恢复会话候选有 16 项语义测试通过，但严格方法仍因两处被拦截的第三方初始化探针 NO-GO；候选未发布，046 M0 与 030 文件模式未因此放行。项目通知仍在设计阶段。Hosted V2 快照中 Linux 与四平台桌面构建已通过、Windows CI 尚在运行；随后 V3 确认同一 CI 整体成功，Windows 5,834 项通过、319 项跳过、21 条警告。真实模型、安装/升级/卸载及完整 WorkBuddy 功能与 UI 对齐继续待完成。
