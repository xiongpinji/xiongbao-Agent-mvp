# 任务级工作目录与访问边界 — 架构记录（总计划 004）

状态：**设计记录 + 停止实施报告**。本批次未落地任何生产代码 / 迁移 / UI。
与 [PROJECT_SPACE_SPEC.md](PROJECT_SPACE_SPEC.md) 的区别：此处的“工作目录”是任务实际执行文件和 shell 的边界，项目空间是成员共享协作域。项目 CRUD/ACL 可先实施，但不能据此声称任务目录隔离已完成。迁移 018 已由项目空间首批预留；下文原提案的任务目录迁移编号调整为 019（最终以实施时可用编号为准）。
结论先行：在当前 Octop 接线与 004 白名单范围内，**无法让实际 Agent
工具执行遵守按任务（thread）选择的工作目录**；按任务约束"目录选择"若不配合执行链
改造，会形成误导用户的假隔离。依据任务约束（"若无法在本范围内让实际 Agent 工具与
文件 API 都遵守任务工作目录，停止并报告……不要留下会误导用户的可点选 UI"），本批次
停止实施，仅交付本架构记录、差距分析与所需扩大范围。

## 1. 现状：thread → 运行时 / 后端 / 工作目录的绑定

- **一个 Agent 一个 HarnessAgent 实例，全部 thread 共享。**
  `AgentManager.boot()/_start_agent()`（`src/octop/infra/agents/manager.py`）按
  `agent_id` 调用 `HarnessAgentManager.acreate_agent(cfg, ...)`；`cfg` 由
  `_build_harness_config(row)` 组装，其中：
  - `workspace_dir=harness_workspace`：来自 `config_json.workspace_dir`
    （`src/octop/infra/agents/workspace_dir.py` 的 `harness_workspace_path` /
    `resolve_workspace_host_path`），**Agent 启动/重建时固定**。
  - `backend=harness_backend`：来自 `_backend_spec_for_row()` +
    `inject_agent_execute_env()`（`local_shell`/`filesystem`/`docker`/`opensandbox`
    spec，含 `root_dir`、`virtual_mode`、`env`），同样**启动时固定**。
- **流式执行链**：dashboard WS → `api/routers/chat/turn.py::prepare_dashboard_turn`
  → `infra/gateway/process/processor.py` → `AgentManager.stream(agent_id, req)` →
  `_harness_manager.stream(agent_id, req)`。请求体与 `configurable` 的全部键为：
  `messages/thread_id/user/source/agent_id/model/skills/mcp_servers`，以及
  `configurable.{session_key, octop_reasoning_overrides, model_settings,
  max_input_tokens, conversation_mode, extra_read_tools, plugin_tool_configs}`
  （见 `process/harness_request.py::build_harness_request`、
  `agents/runtime_limits.py`、`agents/conversation_mode.py`、
  `manager.py::_prepare_stream_request`）。**不存在任何按请求覆盖
  workspace / cwd / backend 的契约键。**
- **backend 实例携带可变 `cwd` 属性**（`tests/unit/backend/test_resolver.py:41,54`
  断言 `backend.cwd`：POSIX 宿主根为 `/`，Windows 为 workspace 目录）。该属性属于
  Agent 级共享实例；`AgentManager._thread_execution_lock` 按 `(agent_id, thread_id)`
  加锁，**同一 Agent 的不同 thread 可并发执行**，因此按轮次改写共享 backend 的
  `cwd` 必然产生竞态 —— 任务约束也明令禁止（"不要重用同一运行中 Agent 的可变全局
  cwd 处理并发线程"）。
- **threads 行只有元数据**（`infra/db/repos/threads.py::ThreadRow`：title/pinned/
  model_ref/conversation_mode/hitl_policy/artifacts…），无目录字段；
  `infra/gateway/threads.py::ThreadRegistry` 只做 session_key→thread_id 绑定。
- **文件 API 是 Agent 作用域**：`/agents/{agent_id}/workspace/*`
  （`api/routers/workspace.py`）经 `api/common/workspace.py::require_running_workspace`
  取 `HarnessAgent.workspace`（`BackendWorkspace`，绑定 Agent 级 backend +
  workspace_dir）；聊天附件上传固定写入 `{workspace}/inbound/`
  （`api/common/attachments.py` + `api/routers/uploads.py`）；产物记录由
  `ThreadArtifactsMiddleware(workspace_dir=harness_workspace)` 写回 thread 行，
  路径基准同为 Agent 级 workspace。

## 2. 执行隔离的平台现实（为什么"线程子目录 + API 前缀"不成立）

依据 `docs/agent-backend-file-io.md` §12 与 backend 解析代码：

| 平台/后端 | shell `execute` 目录狱 | 说明 |
|---|---|---|
| Linux + `local_shell` + `virtual_mode` + 非宿主根 `root_dir` + bwrap | 有（`BubbledLocalShellBackend`） | 狱范围 = **Agent 级 `root_dir`**，构造于 Agent 启动时，非任务级 |
| Linux 无 bwrap / macOS | 无 | 仅把 execute 命令里的虚拟绝对路径改写到 `root_dir` 下再在宿主执行 |
| **Windows `local_shell`（本项目主部署平台）** | **无** | 仅路径字符串改写；`cd C:\other && type …`、绝对路径、UNC 均不受限 |
| `filesystem` backend | 无 execute 能力 | 文件工具经 deepagents `virtual_mode` 限制在 root_dir |
| docker / opensandbox | 容器级 | spec 亦为 Agent 级、启动时固定 |

harness 的 `FilesystemGuardMiddleware` 只覆盖文件工具，**不覆盖 shell execute**
（任务简报与 `manager.py:3073` 注释一致）。因此在 Windows 本地 shell 上，任何
"把 thread 绑定到子目录、API 路径加前缀"的方案都只能约束 Octop 自己的 HTTP 文件
接口，约束不了模型驱动的实际读写/执行：`..`、符号链接、大小写变体、绝对路径、
环境变量展开都可以绕过字符串前缀。这就是"不能只加表单或数据库字段后宣称隔离"的
技术含义。

## 3. 目标态设计（若扩大范围后实施）

以下为目标设计，供扩围后直接采用；**本批次未实现**。

1. **持久化**：迁移 `019_thread_workspace.sql` + `019_thread_workspace.pg.sql`
   成对新增 `threads.workspace_dir TEXT NULL`（创建时已解析的真实宿主路径）与
   `threads.workspace_access_mode TEXT NULL`（如 `task-scoped` /
   `legacy-agent-workspace`）。NULL = 旧线程，保持既有 Agent 工作区行为并在 UI
   标识为"旧 Agent 工作区"。
2. **事务性创建**：`POST /agents/{id}/threads` 接受可选 `workspace_dir`；服务端
   以 `assert_safe_host_path(path, restrict_to_root=effective_workspace_root_dir(
   user_policy))`（`infra/utils/host_dirs.py` + `infra/users/resource_policy.py`
   现有原语）校验：拒绝相对路径、`..`、符号链接逃逸、用户根政策之外的目录；
   拒绝不存在/不可读写的目录（复用 `probe_host_root_dir`）；校验、thread 插入、
   session 重绑定在同一事务边界内完成，失败不留半条任务。
3. **执行链（关键差距所在）**：任务目录必须成为该 thread 每次调用的真实工具根。
   三个候选方案，均超出 004 白名单：
   - **方案 A（优先核验）：harness-agent 上游支持按请求覆盖**——在 stream 契约
     （`configurable`）中接受任务级 workspace/backend（或提供 per-thread backend
     factory），由 harness 在每轮调用中以该范围实例化工具/execute 狱。Octop 侧
     改动收敛在 `manager.py::_prepare_stream_request` + thread 行读取，白名单可
     容纳；但 harness-agent 是第三方包（PyPI `orcakit-harness-agent==1.0.14`），
     本仓不可改。本次 worker 无法读取已安装包的公开接口，故“上游确无此能力”
     尚未核实；当前只能确认 Octop 未传该参数。
   - **方案 B：每任务独立 Agent 实例**——为每个选择了目录的任务在
     `HarnessAgentManager` 注册派生 agent（独立 workspace_dir/backend/检查点）。
     涉及 registry、memory namespace、checkpoint、session、boot/reload、配额等，
     远超白名单文件，且改变 Agent 生命周期语义。
   - **方案 C：平台门控拒绝**——在无法安全约束 shell 的平台（Windows 本地
     shell、无 bwrap 的 Linux、macOS）上，创建任务时**显式拒绝**受限目录配置并
     向 UI 返回能力限制说明；仅在具备支持隔离的后端（如按任务挂载的 docker
     sandbox）时开放选择。docker spec 目前同样是 Agent 级启动时固定，故 C 也
     依赖 A 或 B 的按任务后端实例化。
4. **文件 API / 上传 / 产物 / 任务切换**：全部以已保存的任务范围为准——
   `workspace.py` 各端点接受 thread 作用域、按 `threads.workspace_dir` 构建
   `BackendWorkspace`；附件上传（`api/common/attachments.py`、
   `api/routers/uploads.py`，**不在白名单**）与 `ThreadArtifactsMiddleware`
   （`infra/agents/middleware/`，**不在白名单**）需同步改造；任务切换后不得泄漏
   上一任务的路径/列表缓存。
5. **失败行为**：保存后目录不存在/失效 → 该任务的文件 API 与执行显式失败
   （不静默回退）；权限失败**不得降级**到 Agent 共享根目录；UI 保持当前任务并
   展示错误。
6. **UI**：新任务创建前选择目录（复用 `filesystem.py` 的 dirs/probe/mkdir 选择器
   与用户根政策）、创建后只读展示所选目录与访问范围；旧会话显示"旧 Agent
   工作区"标签；中英文案进 `dashboard/src/locales/{zh,en}.json`。

### 扩围后必须先写的 RED 行为测试矩阵（验收模板）

- 同一 Agent 下任务 A/B 选不同目录：创建后 `threads.workspace_dir` 持久化且
  不同；刷新（重新拉取 thread 列表/历史）后仍返回各自目录。
- 用户 U2 请求 U1 任务的目录信息/文件 API → 403（沿用 `_require_thread` 的
  `row.user_id` 校验模式）。
- `..`、符号链接、大小写变体（Windows）、绝对路径、用户根之外的目录在创建时
  被拒绝；创建失败不留下 thread/session 半状态。
- 文件 API：对任务 A 的 tree/read/write/upload/download 实际落在 A 目录；对
  B 目录内路径的显式请求被拒；切换到 B 后 A 的路径不可达。
- **执行一致性（当前无法满足的核心项）**：任务内让模型执行 `pwd`/写文件，断言
  实际 cwd 与产物落点 == 保存目录；对目录外的写/读尝试被运行时拒绝。

## 4. 本批次停止原因与阻碍清单

1. **架构差距（决定性）**：见 §1–§2。Octop 当前没有传入按请求
   workspace/cwd/backend 的契约；Agent 级 backend 实例共享且不可按轮次安全改写；
   Windows 本地 shell 无执行目录狱；`FilesystemGuardMiddleware` 不覆盖 execute。
   在白名单内只能做到"持久化 + 文件 API 前缀约束"，即任务明令禁止的假隔离形态。
2. **问题通道不可用**：`channel.py event/ask`（含 `--wait-seconds`）在本沙箱中
   一律被权限系统拒绝（"This command requires approval"，即时拒绝而非超时），
   无法按规程在改动公开行为/安全语义前征询 Codex，也无法发布阶段更新。
3. **运行时接口不可核验**：本 WSL ext4 worktree 无 `.venv`；`uv`、`npm`、
   `python3 <script>`、`git -C`、WebFetch/WebSearch、worktree 之外的文件读取
   （含 `/workspace/harness-agent` 与 NTFS 主 checkout 的 site-packages）均被
   沙箱/权限拒绝。"只读检查已安装 harness_agent 公开接口"这一步在本环境无法
   完成，任何关于 harness 是否支持动态 cwd 的断言都无法在此验证。
4. **验证手段缺失**：无法运行 pytest / Vitest / `npx tsc -b` / ESLint /
   `npm run build`。在不能执行 RED→GREEN 的前提下提交测试或实现，违反
   "可验证结果"原则，故本批次不提交任何生产代码与测试。
5. 按约束未提交、未推送、未派生子代理；共享文件（`useSessions.ts`、Chat
   `index.tsx`、locales）保持未动，避免与并行批次产生无意义冲突。

## 5. 变更文件清单（本批次）

- 新增 `docs/xiongbao/TASK_WORKSPACE_ARCHITECTURE.md`（本文件）。
- 其余白名单文件均未改动；未新增迁移；未触碰 OpenCode 品牌任务所有权范围与
  `src/octop/dashboard/` 构建产物。
