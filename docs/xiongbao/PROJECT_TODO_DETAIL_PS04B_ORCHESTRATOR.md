# Goal

在 xiongbao/main 的已批准 PS-04B 书面规格上，交付并分别验收 B1 双栏待办详情、旧纯文本兼容及成员文字评论，以及 B2 安全 Markdown 描述和受控评论图片。保留未达到的 WorkBuddy 视觉/字段差距，不把任一片描述为整体 1:1。

# Decisions and assumptions

- 用户在 2026-09-27 批准 [书面规格](PROJECT_TODO_DETAIL_PS04B_DESIGN.md)及 B1→B2 连续两片交付；详细 TDD 步骤见 [实施计划](PROJECT_TODO_DETAIL_PS04B_PLAN.md)。
- Codex 为总指挥。原始实施分工是后端 claude-bailian/qwen3.8-max、前端 opencode-bailian/bailian-token-plan-personal/deepseek-v4.1-flash；qwen-code-review/glm-5.3 只读审查和验收。原始两条实现路由各使用独立工作树与文件白名单。2026-09-27 的单独授权与失败记录见下文；Codex 直接实现是该授权之后的例外，不改写原始分工。
- B1 和 B2 各自能形成可测试的增量；B1 后端与前端可按固定 DTO 并行，B2 要等 B1 审查和整合后开始。B2 格式与图片按独立小项拆分。
- 书面规格批准时迁移最高编号为 030、用户远端 SHA 为 4ddf49d3；派工基线以本计划提交后的 xiongbao/main 为准并再次核对迁移号，若基线变化则先安全快进/重排迁移号。

# Constraints and guardrails

- 只改项目待办及活动相关模块，现有 020 CRUD、版本号、成员 ACL、项目私密任务/资产根不得扩权或破坏。待办评论不是项目动态留言。
- 撤权、跨项目、软删后的评论/图片读取统一拒绝；活动只存无正文/图片/内部路径的白名单事件；Markdown 不接受原始 HTML、可执行链接或外站/数据图片。
- 评论图片在独立私有根存储，复用安全路径原语但不得混入只有 asset_versions 引用的资产清理集合。并发容量、磁盘/DB 回滚与幂等重试均需测试。
- 执行者不嵌套创建子代理，不提交/推送、不改凭据/生产环境、不清理其他人的工作树。Codex 检查白名单 diff、独立运行测试、固定 SHA 的 GLM 或用户授权替代只读审查后才选择性整合和推送 xiongbao/main；Tencent origin 不推。
- 用户此前仅授权 Codex 直接实现 043、045 后端等指定片；2026-09-27 已另外明确授权 Codex 完成 PS-04B B1/B2，使用独立只读代码审查与本地测试验收，GLM 恢复后补审。原三路由的失败和新授权必须保留，不能将 Codex 候选记为原路由交付。

## 2026-09-27 路由中断与承接

- 持久计划 `plan-20260927-131950-a3c076` 从 `7bbbe222eaa54f98bc685f266e4aa49144a8388f` 派工；两个 OpenCode 工作树起始干净，后续没有任何产品代码差异。
- B1 后端 `claude-bailian-20260927-132246-c25168` 在启动后立即收到月额度 429（预计 10 月 22 日重置），没有代码改动。B1 前端 `opencode-bailian-20260927-132305-1aacf5`、B1 后端承接 `opencode-bailian-20260927-132443-e8b00b` 在 WSL 实例约 13:44 UTC 重启时都变成 `lost`；两项均为零标准输出、零标准错误、零工作树改动，不能视为候选。GLM 路由保持只读。
- 用户随后明确批准 Codex 直接完成 B1/B2，并以独立只读代码审查和本地测试复核，待 GLM 额度恢复再补审。Codex 在同一干净 Windows 工作树给 B1 后端和前端分配不重叠文件所有权；B1 固定候选验收后才继续 B2。原失效任务不接受、不合并。

# Checklist

- [x] B1 后端：配对评论迁移、评论仓储/服务/HTTP、稳定分页和请求号幂等、成员/待办锁序、只含安全元数据的活动事件，含定向测试。
- [x] B1 前端：双栏详情、表格/看板同 ID 打开、文字评论、旧纯文本字面展示、深链/后退/Escape/焦点、撤权清空与活动入口，含定向测试。
- [x] B1 固定候选：Codex 整合与真新库/旧库、后端/前端回归、三身份登录态浏览器验证；独立只读审查按 2026-09-27 用户授权的替代门禁执行；代码提交 `6280fe49` 已推送并核对 `xiongbao/main`。验证范围、`make all` 未完成及 GLM 补审见[B1 验收记录](PROJECT_TODO_DETAIL_PS04B_B1_EVIDENCE.md)。
- [ ] B2 格式后端：description_format 配对迁移、旧客户端 create/PATCH 兼容、所有 DTO 与版本规则，含定向测试。
- [ ] B2 格式前端：ProjectTodo 必填格式、专用安全 Markdown 编辑/渲染与 409 草稿保留，含 XSS 用例。
- [ ] B2 图片后端：私有评论图片表与容量表、multipart 校验/幂等/并发限额/失败回收、实时成员图片 GET，含真 PG 和故障注入。
- [ ] B2 图片前端：本地粘贴预览、进度与重试、登录态读取、Blob URL/Abort 生命周期、项目切换与撤权，含定向测试。
- [ ] B2 固定候选：Codex 独立复测与三身份浏览器；固定 SHA 只读审查按用户授权的替代门禁执行，GLM 恢复后补审；差距台账更新、仅接受字节推送并核对远端。

# Validation strategy

各实现项先写失败测试并观察红灯，再写最少实现。后端运行项目待办/活动/评论 unit 和 integration，双数据库配对迁移和一次性真 PostgreSQL 新库及旧库升级；前端运行四个受影响 Vitest 文件、新组件测试、tsc、lint、build，最终 make all。关键竞态、幂等、XSS、路径与回收要故障注入。B1/B2 分别做 owner/member/outsider 的真实登录态本地浏览器旅程；WorkBuddy 既有只读观察只作设计参照，完整逐状态视觉验收单列。

# Completion criteria

每个适用清单项有白名单 diff、独立复测结果、只读审查结论和固定 SHA；B1/B2 分别达到批准规格的行为与安全条件并安全快进到 xiongbao/main，远端 SHA 已核对。若原三条路由额度/超时无法交付，清单维持未完成并留下反馈/后续授权记录，不以计划或代码存在冒充验收。
