# 030A PS-08 当前主线文件任务验证

日期：2026-09-28。验证的产品代码基线为 `5ccbfd8babb0bd24c7f3c00e96ba8976e4bc06bc`；本片只新增贯通测试和验收记录，没有改动生产代码。**文件任务创建开关仍为 `False`，不批准激活。** 既有分片验收见 [F3 集成候选](PROJECT_TASK_WORKSPACE_030_F3_INTEGRATED_ACCEPTANCE.md)。

## 本轮补充的证据

| 层级 | 当前主线结果 | 能证明的边界 |
| --- | --- | --- |
| Windows 已认证 HTTP 路径 | 真实 NTFS junction 越界、种植 junction 路由矩阵和子树 hardlink 的三个聚焦测试 **3 passed** | 测试夹具通过 ASGI HTTP 和 SQLite，但 B2 runtime 为假；不能证明运行时执行时的 TOCTOU 安全。 |
| 真实 Harness 组件 | 工具白名单/伪造调用拒绝、内部 runtime 创建两个测试 **2 passed** | 真实 Harness 与本地录制模型；不是 HTTP、浏览器或付费模型调用。 |
| 关闭门禁及邻近 HTTP/重启 | 关闭时能力提示、隐私投影、托管根正例、绑定聊天线程、runtime 重启五个测试 **5 passed** | HTTP 测试中的 B2 runtime 由夹具替代；runtime 重启单测不是完整 HTTP 链。 |
| 新增真实 runtime + HTTP 贯通 | `tests/integration/test_project_task_file_real_runtime_http.py` **1 passed**；Ruff 和格式检查通过 | 仅把 LLM 替换为本地录制模型；项目文件任务通过公共 ASGI 入口创建，真实 AgentManager/Harness 启动；本人 HTTP 文件写、读、下载及重启后读取成功，外部用户被拒。项目成员移除后项目卡片、项目详情、资产入口返回 404，服务端指令快照不再返回；本人私有运行体文件仍可读取，符合已批准合同。开关只在测试作用域打开。 |
| 独立 PostgreSQL 18.6 | `tests/unit/db/test_project_task_file_runtime_postgres.py -m postgresql` **3 passed** | 新建一次性集群和数据库，执行前 `SHOW data_directory` 与新目录一致；覆盖迁移形状、配额边界与两连接争最后席位。测试后停机、确认端口不再监听并移除临时集群。测试每例会重建 `public` schema，因此没有接触已有数据库。 |
| 独立只读复核 | 对新增贯通测试的最终字节复核，无 P0/P1/P2 可操作发现；根代理再次运行聚焦测试、Ruff 和格式检查均通过 | 审查者是内部代码审查子代理，不是原定的 GLM 固定 SHA 补审。 |

新贯通测试的撤权断言有意遵守 [030A 合同](PROJECT_TASK_WORKSPACE_030_CONTRACT.md)：成员移出项目只撤掉项目链接与指令；已存在的私人 thread、runtime 和文件仍归本人。不能把“私人文件仍可读”误报为越权。首轮没有驱动模型文件工具、WebSocket 对话轮次或真实浏览器。

## 后续真实 WebSocket 补验

产品代码基线 `89026e51d31ef8fe58bf186ec4b5da7173bd1dcc` 上，同一测试文件新增独立的绑定线程 WebSocket 用例。它只用本地脚本模型，通过真实 ASGI WebSocket、Gateway 与 Harness 图实际执行 `write_file`，并让伪造的 `execute` 得到错误工具结果；每轮模型可见名称集合恰为批准的六个文件工具。两条贯通用例合跑 **2 passed、3 条依赖弃用警告**，Ruff 检查与格式检查通过。独立只读复核对新增差异无 P0/P1/P2，审查者另行复跑新用例 **1 passed**。这补强的是运行时调用链，不是付费模型或浏览器验收。

## 尚未核销

- 已确定一个前端断链：内部 runtime 被普通 Agent 列表过滤后，`WorkspaceDrawer` 始终误判未就绪。标记正例的行为 RED 失败、普通入口负例通过；两条实现路由分别零输出超时和月额度 429，目前无候选，见[本片实施状态](PROJECT_TASK_WORKSPACE_030_UI_GATE_EXECUTION_STATUS_20260928.md)。
- `PROJECT_TASK_FILES_MODE_ENABLED = False` 仍是产品默认值；本轮没有运行开关开启后的登录态浏览器旅程，也没有启用生产文件模式。
- PostgreSQL 专项不是完整 PostgreSQL HTTP/浏览器创建—读写—重启—撤权矩阵，也不是历史生产库升级演练。
- Windows junction/hardlink HTTP 用例与真实 runtime 贯通用例是分开的；尚缺真实 runtime 下的重解析点执行时竞争验证与明确的 TOCTOU 风险结论。
- 本地录制模型只在 WebSocket 旅程中实际执行了 `write_file`，没有逐个执行其余五个文件工具；也没有证明真实供应商模型的调用质量或 WorkBuddy 本地/云端能力对齐。GLM 固定 SHA 补审和 WorkBuddy 逐状态视觉对照另列待办。

本轮 Python 命令使用 `uv run --no-sync`。Windows 没有 `make` 可执行文件，未宣称执行 `make all`；也未重复全仓非 live 回归。上述结果仅是当前主线聚焦验证，激活门禁需独立决策与后续验收。
