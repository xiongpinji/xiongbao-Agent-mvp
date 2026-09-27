# 030A 私有任务工作区抽屉：实施路由状态

日期：2026-09-28。产品代码基线 `849bb7562faa5ec42db4a0d82495dc2545e1486f`；后续 TCP 浏览器复核绑定 `44a2fd47f863700d9d4a50df2e51047887a53353`，中间提交仅改动四份计划和验收文档。**尚无本片产品代码候选，文件任务创建开关仍为 `False`。**

## 已确定的断链

普通 `useAgent().agents` 正确过滤内部运行体；聊天深链先用 `getChatAgentById` 取得本人最小内部卡片，再验证已有 thread 归属，验证通过前不向聊天与文件入口提供运行体 ID。但是 `WorkspaceDrawer` 只查普通列表，因此本人从该聊天打开工作区也被误判为未就绪，不发目录树请求。协调者在主线保留了一份未跟踪、未提交的行为测试：真实抽屉在 `agents=[]`、专用 getter 返回运行中内部卡片、显式标记 `privateTask` 的条件下，预期请求 `/agents/private-runtime/workspace/tree?path=/&from_workspace=true`，实际零请求而失败；不带标记的负例通过。聚焦结果为 **2 项、1 失败/1 通过**；这是 RED 复现，不是验收通过。

计划只允许四个前端文件：`WorkspaceDrawer.tsx`、新的 `WorkspaceDrawer.privateTask.test.tsx`、`ChatDockPanel.tsx` 和 `ChatDockPanel.workspaceFiles.test.tsx`。普通选择器、后端授权、模式开关及六工具边界不在本片修改范围。通用抽屉仍显示服务器未允许的目录新建、移动、删除和打包操作，另列后继 UI 差距。

## 两条实现路由

| Agent Orchestrator job | 路由与结果 | 可交付差异 |
| --- | --- | --- |
| `opencode-bailian-20260927-220811-271075` | DeepSeek V4.1 Flash；2026-09-27 22:08:12 UTC 启动，22:53:15 UTC 达到声明的 2700 秒上限，调度器标记 `timed_out`，退出码 -15。最后心跳仍正常，没有提前取消。 | stdout/stderr 均为 0 字节；相对基线没有 tracked 改动。 |
| `claude-bailian-20260927-225518-c95c7e` | Qwen3.8 Max；第二份有界任务包保留同一四文件范围。5.638 秒后退出码 1，实际 API 返回月额度 429，提示 10-22 16:00 UTC 重置；CLI 另有 `unrecognized_model` 提示。 | 没有 tracked 改动；没有运行实施测试。 |

独立 WSL 工作树 `/home/canqu/work/xiongbao-030-drawer-gate-20260928` 在两次结束后都仅保留启动前就有的协调者 `dashboard/node_modules` 符号链接；该链接被记录为脏基线，没有归入候选。`executor-options` 对 `plan-20260927-220546-3947a0 / item-001` 返回批准池已用尽、未试项为空。最大三次尝试是上限，不要求把同一失败路由重复到第三次。GLM 是只读审查路由，不能改派成实施者；本计划没有预先批准自动回退。

## 下一步的有界交付与验收

若用户单独授权 Codex 承接，则仍按已经推送的计划，仅为已核验的私人任务传递显式标记并窄用专用 getter；先保留真实 RED，再验证标记正例、普通入口负例、普通运行 Agent、缺失/错配/未就绪内部卡片。候选需要独立只读审查、聚焦与相邻测试、TypeScript、目标 lint/格式和生产构建，之后才整合推送。原定 GLM 固定 SHA 审查目前未完成，临时替代审查需在这次授权中明确，额度恢复后仍保留补审。

后续已在全新系统临时数据目录、三个合成普通用户及一个合成 admin、两个独立 loopback 端口完成 TCP 浏览器复核。临时后端启用夹具开关并使用本地录制模型，未改产品默认值，最终模型推理次数为 0。真实界面创建文件任务 201、本人 thread history 200、本人直接 HTTP 文件读写/下载/列举 200，其他三个身份的 tree/download/history 共 9 次 403；真实工作区显示“请先选择一个专家”，10 秒观察窗口内未收到匹配的根目录树响应，结果为 RED。原始 JSON 的 `rootRequested=false` 由等待响应超时得出，不是独立请求计数；证据和截图见[浏览器记录](PROJECT_TASK_WORKSPACE_030_PS08_TCP_BROWSER_RED_20260928.md)。浏览器刷新、后端重启及成员撤出矩阵仍未运行；撤出后的预期遵守 [030A 合同](PROJECT_TASK_WORKSPACE_030_CONTRACT.md)，不借用 ASGI 单测宣称浏览器通过。

当前不得把本片记为已实现、把工作区目录可见性记为通过，或激活文件模式。真正用户自选本地文件夹、云端执行、团队协作及 WorkBuddy 逐状态视觉仍未核销。
