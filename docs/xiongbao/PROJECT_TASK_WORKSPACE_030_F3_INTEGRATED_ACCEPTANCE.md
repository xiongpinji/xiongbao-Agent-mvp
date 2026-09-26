# 030A 文件任务 F3 集成候选验收

状态：**允许在创建开关关闭时集成；文件模式尚未激活。** 固定候选 `dde580e5147438215183f6338d8271fdeda16df8` 已推送到 `xiongbao/main`。该结论只覆盖 030A 受控文件任务候选，不核销 WorkBuddy 项目空间或完整本地/云端模式。

## 修复与审查

F3 把私有文件任务的“已发现文件”行下载指向托管工作区路径；后续前缀路径修复在前端发送请求前拒绝 `file://`、Windows 驱动器和混合前导分隔符等不可信主机路径形状。普通 Agent 下载链路保持原行为。F3 集成提交为 `d82b3029`、`dde580e5`，后端安全门禁已包含在更早的集成提交中。

首轮 GLM 只读审查 `qwen-code-review-20260926-103921-aba09a` 正确指出 F3 单独工作树会对 `/file:///...` 发出多余请求，但它检查的工作树没有 B4 后端，因此“集成候选可读主机文件”的影响判断不适用。Codex 核对集成 B4 的私有运行体 HTTP 分支：路径先进入受控根和严格解析器，`file://` 与驱动器路径在文件 I/O 前被拒绝。该纠正不替代 HTTP 实测。

GLM 对完整固定候选 `dde580e5` 的只读审查 `qwen-code-review-20260926-112154-50dd55` 给出**开关关闭时集成 GO，未发现 P0/P1/P2**。三项 P3 留待后续：拒绝行的下载按钮无提示；既有文件标签预览对规范化 `file://` 产物可能发出受控的 404 请求；`my:file.txt` 与 `/my:file.txt` 的前端接受规则不一致。GLM 未执行 Git 或测试命令。

## Codex 独立验证

- 固定集成候选的 10 组前端 dock/路径测试 **137/137** 通过；目标 ESLint、Prettier、Dashboard TypeScript 与 Vite/PWA 构建通过。
- 后端父提交 `66bd2cc3` 的非 live 全量 pytest **4494 通过、15 跳过**；F3 两个提交未改变后端。先前 B4 的 7 组聚焦测试 **155/155** 与相邻普通回归 **946/946** 通过，见 [B4 候选验收](PROJECT_TASK_WORKSPACE_030_B4_CANDIDATE_ACCEPTANCE.md)。
- Windows 实机执行 `scripts/probe_project_task_junction.ps1`：托管文件正例通过；真实 junction 指向托管目录外时，已有文件与新建路径均被纯解析器拒绝；新版本探针自身清理通过。这仅证明解析器路径判断，不证明已认证 HTTP 入口、重解析点所有类型或执行时 TOCTOU 安全。

## 激活前仍需

`PROJECT_TASK_FILES_MODE_ENABLED = False` 保持不变。须验证真实 B2 runtime 与模型可见工具边界、PostgreSQL 迁移及配额并发、已认证的创建—读写—重启—撤权 HTTP/浏览器旅程、Windows 文件入口与重解析点，以及合同要求的 TOCTOU 风险姿态。WorkBuddy 用户自选目录、命令执行、云端协作和逐状态视觉对照仍属于后续范围。
