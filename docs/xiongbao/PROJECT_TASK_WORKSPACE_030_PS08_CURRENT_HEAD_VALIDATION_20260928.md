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

## 后续 TCP 浏览器 RED

绑定 `44a2fd47f863700d9d4a50df2e51047887a53353` 的真实 Chrome 与 TCP 后端夹具已执行：界面创建本人文件任务 201、已有 thread history 200，独立 HTTP 读写/下载/列举 200，project owner、outsider 和 admin 的 tree/download/history 共 9 次 403。打开工作区却显示“请先选择一个专家”，10 秒观察窗口内未收到匹配的根目录树响应，结果为 RED，不能算工作区验收通过。原始脚本未独立采集请求事件，不能据 `rootRequested=false` 宣称零请求。开关仅临时进程打开，产品默认仍为 False，最终模型推理次数为 0。完整边界、失败脚本修正和截图见[本轮浏览器记录](PROJECT_TASK_WORKSPACE_030_PS08_TCP_BROWSER_RED_20260928.md)。

## 后续 Windows 检查与使用竞态诊断

源码固定 `60341df2fb02daee97a1da34cb5332f976d5fcb3`，Windows NTFS 的真实 Harness 组件图在同线程干净基线后执行四组诊断。稳定种植 junction 的读、写均被原 guard 拒绝；在检查通过、原 `os.open` 打开前确定性替换子目录，两组分别返回根外合成 canary 和创建根外合成文件。各 4 次本地录制模型调用、共 16 次，每次可见名称恰为六个文件工具；没有经过外部 provider。运行体与目录均复原，Temp 留存且剩余重解析点为 0。结论是同账号、精确时点注入条件下的残余窗口已确认，不是安全通过；HTTP、其余四工具及全链验收没有随之完成。原始结果、源码绑定、脚本和边界见[Windows 竞态诊断](PROJECT_TASK_WORKSPACE_030_WINDOWS_TOCTOU_PROBE_20260928.md)。产品开关仍为 False，激活 NO-GO。

## 后续 Windows HTTP 检查与使用竞态诊断

源码固定 `cb72fe48`，真实公共 ASGI 创建四个隔离合成文件运行体均为 201，干净 HTTP 基线均为 200。稳定 junction 的 GET／PUT 均被原 guard 拒绝为 403、目标打开 0；检查成功、精确打开前交换子目录后，GET 返回根外 canary，PUT 覆盖根外已有 `target.txt`。每组另有四次非 owner／as_user 拒绝，共 16 次 403，目标 guard／backend／open 均为 0。实际本地模型调用 0，四组 stop 和目录复原完成，独立非跟随遍历 878 项、reparse 0。watchdog 未超时、子进程和入口均退出 0；结果明确为 `COMPLETE_WITH_HTTP_CHECK_USE_ESCAPE`，不是安全通过。第一轮探针因读取错误发行包名在服务启动前退出 1，原脚本和错误保留；V2 在新目录仅修正版本读取后执行。脚本、原始结果、独立复核与限制见[HTTP 竞态记录](PROJECT_TASK_WORKSPACE_030_WINDOWS_HTTP_TOCTOU_PROBE_20260928.md)。产品开关仍为 False，激活 NO-GO。

## 尚未核销

- 已确定一个前端断链：内部 runtime 被普通 Agent 列表过滤后，`WorkspaceDrawer` 始终误判未就绪。标记正例的行为 RED 失败、普通入口负例通过；两条实现路由分别零输出超时和月额度 429，目前无候选，见[本片实施状态](PROJECT_TASK_WORKSPACE_030_UI_GATE_EXECUTION_STATUS_20260928.md)。
- `PROJECT_TASK_FILES_MODE_ENABLED = False` 仍是产品默认值；后续临时进程开启的登录态浏览器旅程复现了工作区 RED，刷新/重启/撤权的浏览器矩阵仍未核销，也没有启用生产文件模式。
- PostgreSQL 专项不是完整 PostgreSQL HTTP/浏览器创建—读写—重启—撤权矩阵，也不是历史生产库升级演练。
- Windows junction/hardlink HTTP 用例与 runtime 贯通用例是分开的；同账号替换受管子目录的真实 Harness 读写及两个真实 HTTP 文本读写入口竞态已确定性复现；TOCTOU 风险处置、其他 HTTP 与工具入口、根及祖先链/特殊 reparse 竞争矩阵仍未核销。
- 本地录制模型只在 WebSocket 旅程中实际执行了 `write_file`，没有逐个执行其余五个文件工具；也没有证明真实供应商模型的调用质量或 WorkBuddy 本地/云端能力对齐。GLM 固定 SHA 补审和 WorkBuddy 逐状态视觉对照另列待办。

上述 pytest 验证使用 `uv run --no-sync`；后续竞态探针直接使用本 checkout 的 `.venv\Scripts\python.exe`，具体绑定见诊断记录。Windows 没有 `make` 可执行文件，未宣称执行 `make all`；也未重复全仓非 live 回归。上述结果仅是当前主线聚焦验证，激活门禁需独立决策与后续验收。
