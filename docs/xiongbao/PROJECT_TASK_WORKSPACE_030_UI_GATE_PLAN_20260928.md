# 030A 项目文件任务：会话项目空间入口修复

日期：2026-09-28。基线 `849bb7562faa5ec42db4a0d82495dc2545e1486f`。本计划属于已批准的 [030A 合同](PROJECT_TASK_WORKSPACE_030_CONTRACT.md)的前端验收修复；不会开放 `mode="files"` 创建开关，也不宣称 WorkBuddy 项目空间 1:1。

# Goal

本人从已核验归属的内部文件任务聊天线程打开“项目空间”页签时，应看到该任务的工作区目录；普通 Agent 选择器和普通工作区入口仍不得发现内部运行体。

# Decisions and assumptions

已批准的 030A 合同决定私人运行体只经已核验的既有线程进入。当前 RED 测试证明的是前端抽屉误判，不是服务器越权；不需要改动该合同。

- `AgentContext` 继续从公开 `agents` 列表过滤 `internal=true`；只有 `getChatAgentById` 可在已知本人线程链路取最小卡片。
- `ChatPageInner.useInternalTaskRoute` 先用线程 history 校验归属，`chatAgentId` 在核验前为空。`ChatDockPanels` 和 `ChatDockPanel` 已传递 `privateTask`；只有通过该标记的工作区抽屉可以读取内部卡片，抽屉不得自建归属绕过。
- 服务器 `/agents/{id}/workspace/*` 仍按本人授权和 030 文件路径门禁检查；前端只能解决误判“未就绪”，不能替代服务器权限。
- 目前后端只允许内部任务工作区的有限读写入口。抽屉里其它操作按钮是否隐藏是下一片单独验收，不在本片扩大后端权限。

# Constraints and guardrails

只允许本片任务包列出的四个前端文件。遵守 `AGENTS.md` 的窄改动与验证要求；不修改 Python、依赖或配置，不启用文件模式，不提交/推送工作者候选，也不扩大内部卡片发现面。GLM 路由只读审查，不列入实施池。

# Checklist

- [ ] `item-001`：OpenCode/DeepSeek 在独立工作树中先写行为失败测试，再仅修正 `WorkspaceDrawer` 的内部卡片解析和 `ChatDockPanel` 的显式标记传递；普通入口失败关闭。Codex 审核差异、重跑聚焦/类型/构建，独立只读审查后整合推送。

# Validation strategy

先见 `WorkspaceDrawer.privateTask.test.tsx` 的真实行为 RED，修复后跑聚焦 Vitest、相邻私有任务面板用例、`tsc -b`、改动文件 ESLint/Prettier 和前端构建。Codex 独立复跑并审查全部差异；可用时交 GLM 固定 SHA 只读复核，若额度仍为 429 则如实标待补审。

# Completion criteria

固定输入为本人 `internal=true,state=running` 的 `chat_agent_id` 和已核验线程。点击工作区页签后应请求 `/agents/{id}/workspace/tree?path=/&from_workspace=true`；没有私有任务标记的普通抽屉不得发起该请求。普通 Agent 的工作区原行为、Agent 列表过滤、线程归属检查不能倒退。测试使用模拟的 owner 卡片和 API；真实登录浏览器、付费模型、GLM 固定 SHA 和开关开启仍是另外的验收层级。实现任务最多三次 Agent Orchestrator 尝试，超限后按技能门禁征求用户方向。
