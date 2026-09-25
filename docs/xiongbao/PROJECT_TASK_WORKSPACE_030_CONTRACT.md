# 030A · 项目任务受控文件工作区合同

状态：第三版设计经 `qwen-code-review-20260925-083120-73e484` 于固定源码 `32610c59` 只读审查 **GO、无 P0/P1**，只接受设计与 M0 优先顺序；该审查没有运行测试。首稿和第二稿分别经 `qwen-code-review-20260925-072159-741e0c`、`qwen-code-review-20260925-074749-68ba6f` 判定为 NO-GO。此处补入第三版审查指出的两条 P2 入口名称。030A 将可执行本地 shell 移出，先在 Windows 与 Linux 交付**真实的任务级文件工具边界**；M0 通过前不开放功能。文档不是功能完成证据。

## 范围与产品语义

028/029 项目新任务仍使用所选专家的 Agent 工作区，多个任务共享文件、记忆与工具。只在任务表存一个目录路径无法改变已经按 Agent 构造的 harness 后端。030A 新增 `mode="files"`：系统托管、每任务独立的文件工作区，Agent 可在其中用 `ls/read_file/write_file/edit_file/glob/grep` 工作；**无 shell、终端、浏览器、ACP、MCP、插件、cron、知识搜索、媒体生成、子 Agent 或个人连接器**。界面明确叫“受控文件任务（无终端）”，不把它冒充 WorkBuddy 的完整“本地”模式。既有 `mode="chat"`、项目资产、卡片/文本分享和指令快照保持原行为。用户自选既有文件夹、真实 Windows/Linux 命令执行、云端和多人共写仍在 030B/后继片，PS-08 继续未核销。

## API 与可见性

- `GET /api/projects/task-capabilities` 在 `projects.py` 的 `/{project_id}` 路由**之前**注册，返回 `{files:{available:boolean,reason:string|null}}`；服务端检查托管根可写且文件后端可构造。能力只是 UI 提示，创建时必须重检。不可用时 API 返回稳定 `PROJECT_TASK_FILES_UNSUPPORTED`（422），不留 thread/link/context/runtime/目录。错误码补 Python 状态映射、中英翻译及前端 `apiErrors` 对齐。若服务故障暂时不可用，用另外的稳定错误码与 503。
- `POST /api/projects/{project_id}/tasks` 新增可选 `mode:"chat"|"files"`，缺省 `chat`；项目成员、未归档、指令摘要、专家名单修订号和原有 Agent ACL 均沿用 028/029。`files` 成功后返回任务 `mode="files"`、`source_expert_id` 与仅本人可见的 `chat_agent_id`；`agent_id` 的公开展示语义固定为**所选专家 ID**，聊天导航用 `chat_agent_id`。旧任务和手动关联行报告 `mode="chat"`，其 `chat_agent_id=agent_id` 仅本人可见。共享卡片不能收到 runtime Agent ID、主机路径或文件权限。
- 本人 `GET /api/agents` 可以包含其 `internal=true` 的专属任务 Agent **最小卡片**，供已有 `/chat/{chat_agent_id}/{thread_id}` 深链在 `AgentContext` 中解析；管理员 `scope=all`、其他用户列表不可列出。卡片仅含 `id/agent_id/name/state/kind/internal/icon/icon_name/icon_url/color/is_owner` 与不含秘密的必要显示字段，不含 `config`、`system_prompt`、工作区路径、私有目录、连接器配置。`AgentContext` 内保留 raw 列表供**既有线程**按 ID 解析，同时只向 `useAgent().agents` 暴露过滤 `internal` 的普通列表；`Chat/index.tsx` 通过专用 `getChatAgentById` 解析 URL 中的内部 Agent，且必须先校验 thread 归属。默认 Agent 的 `persistAndApply(list[0])` 回退和本地存储恢复也必须先在**过滤后的普通列表**中选择，不可因为内部卡片排第一就默认进入内部 runtime。所有 31 个现有 `useAgent().agents` 普通候选消费者由此中央过滤，`Experts` 网格、`AgentSelector`、个人化页、cron 与终端选择器不得看见内部 Agent；聊天侧栏和 @ 选择器也只用过滤后的列表。只有对应既有线程导航可以使用它；不得把该 runtime 加到 029 项目专家候选。Agent 名称采用不含项目 ID/任务标题的随机不冲突名。
- 本人现有任务列表与项目卡片仅显示源专家；普通 Agent 详情、配置、共享、启停、新对话、IM 绑定等管理能力对内部 Agent 默认拒绝。其现有线程的聊天、历史、受控文件访问和显式整任务删除是窄白名单。文件下载仍逐请求验证任务拥有者；024/025 的卡片/纯文本被分享者不因此得到文件。

## 持久化、创建与资源上限

- 因用户仓库与本地数据库可已到 027 水位，新增 SQLite/PostgreSQL 对应的 **028** 迁移，不改已使用的 026/027。`project_task_contexts` 增 `mode`（旧行默认 `chat`）、可空的 `source_expert_id` 和唯一可空的 `runtime_agent_id`，`files` 行要求三者与 thread 关联一致；`agents` 增专用 `runtime_kind` 列，默认 `standard`。只有内部创建路径可写 `project_task_files`；公开 Agent 创建/更新的自由 `config` 字段不能伪造或修改该列。手动关联仍无 context 行。
- 每个用户最多 **8** 个已注册 `project_task_files` runtime，全实例最多 **64** 个；包括已从项目解绑但仍保留的个人文件任务。固定上限，不加可变配置；达到上限返回 `PROJECT_TASK_FILES_QUOTA`（409）且无半成品。创建预检先数额，新增专用 `agent_repo.create_project_task_runtime_with_quota`：在同一个事务内重计数并插入 Agent 行；SQLite 用 `BEGIN IMMEDIATE`，PostgreSQL 用能串行化配额判断的固定锁顺序/序列化事务并在冲突时有界重试，不得仅依赖单进程的 `AgentManager._lock`。所有内部创建入口只用此方法；两个并发请求不得突破上限。重启时既有任务照常加载，新建仍按同一固定上限。实现须有 SQLite 双连接与 PostgreSQL 锁序测试；缺 PG 实例时如实标未验。
- 先做无副作用的成员/摘要/名单/专家/容量预检；然后通过内部入口创建归请求人的私有 runtime Agent，立即同步启动并验证受控文件后端及实际工具集，不能 `defer_bootstrap` 后给出假可用任务。该 Agent 只复制已授权专家的必要模型/人格提示，不复制源 `backend`、技能目录、包、子 Agent、MCP、ACP、插件、知识库、浏览器环境或凭据。内部工作区初始化须激活所选提示且不装入默认外部技能/子 Agent；若运行体起不来，回收本次目录/Agent。
- 在一个数据库事务内再次锁定并检查成员、归档、指令摘要、专家名单修订号和本人专家权限，再写 thread/projection/link/context；`threads.agent_id=runtime_agent_id`，上下文保留 `source_expert_id`。失败补偿清理 runtime；进程在补偿前崩溃，由启动时有界扫描清理**无任何 thread 引用且超过 24 小时**的内部 Agent。不得删除有私有 thread 的、已从项目解绑的 runtime。线程及快照重启后可恢复，指令仍由 `_stamp_project_task_context` 在有效成员关系下逐轮服务端注入。

## 访问与工具边界

- **M0 是后端/前端功能实现的阻断门槛。** 先用仓库测试构造与内部 runtime 相同的已安装 `harness-agent` 配置和 filesystem backend；断言真实模型可见工具名集合包含必要六项且为其子集，并用伪造 `task`/`execute`/web 调用证明在执行入口拒绝（不是只验证模型看不见）。再测 `BackendWorkspace` 与 HTTP 适配器对 host 绝对路径、`..`、`file://`、POSIX symlink、Windows junction 的逃逸；不能创建 junction 的机器标为跳过，Linux CI 必跑 symlink。若 `tools_disabled` 超集加 `subagents_auto_load=False` 仍留下危险工具，添加调用前拒绝中间件并重跑；若无法可靠 fail closed，则不开放 `files` 创建。M0 RED→GREEN 证据形成后才分派其余后端和前端功能包。
- runtime backend 固定 `{"type":"filesystem","root_dir": <系统托管私有目录>,"virtual_mode":true}`，不提供 execute。构造后验证真实 backend 类型、根、`execute` 不存在和最终模型可调用工具集合。工具只允许 `ls/read_file/write_file/edit_file/glob/grep`；`task`/子 Agent、`ask_user_question`、web、env-file、desktop、send-file、current-time 和其它内置工具均不在白名单。不能只靠 prompt 或 `tools_disabled`，因为目录工具与 `task` 在现有目录中被标为 critical；需在工具调用边界 fail closed，并测试伪造调用也不能执行。若无法可靠压缩工具面，此模式不可开放。
- `_build_harness_config` 对 `runtime_kind=project_task_files` 禁用 cron、knowledge、mobile、全局及 Agent 插件、ACP runners、启动时个人 MCP、媒体、技能包/目录和自动子 Agent；`manager.py` 启动时跳过内置/插件 skill 同步。每轮在 `chat/turn.py` 的 `prepare_dashboard_turn` 调用 `manager.py` 的 `prepare_chat_mcp` 之前、`processor.py` 的 `_resolve_turn_mcp_servers`、`meta["skills"]` 和 `knowledge_base_ids` 注入处逐层拒绝内部 runtime 的客户端 picks；表驱动测试分别覆盖这三个出口，不能把个人连接器再挂回。显式初始化 bootstrap 状态，使源专家提示生效；验证安全中间件仍启用，记忆只写本任务命名空间。
- 服务端对 `/api/agents/{id}` 及相关 WS 路由设**默认拒绝**的内部 runtime 门禁，只有明确枚举的本人现有线程聊天/历史、受控工作区读写/下载/预览与完整删除通过；路径模式与方法进入表驱动测试。`require_agent_row`/`require_agent_owner_row`、`as_user` 管理员代入路径和绕过它们的路由、CLI Agent 管理均不得让普通所有者或管理员编辑、共享、绑定频道、开新线程或打开 host PTY。`project_tasks.create` 的 `authorize_create_target`/`create_with_context` 和 `attach` 也必须拒绝 `runtime_kind != standard` 作为普通专家目标，避免给内部 Agent 另建线程或跨项目挂载。允许的聊天请求必须匹配 `threads.agent_id`、本人 `user_id` 与**显式提供的既有 thread ID**；Dashboard 的 `resolve_thread_id` 与 CLI 的 `prepare_cli_turn`/`chats send` 对内部 runtime 均不得自动创建线程，拒绝客户端伪造后台/工作区参数。权限拒绝先于任何文件或连接器操作。
- 内部 runtime 的 HTTP 文件路径强制按真实托管根解析，不接受 `from_workspace=false`、宿主绝对路径、`..`、符号链接/Windows junction 越界或下载回退；范围包括 `workspace.py`、附件、artifacts、媒体和预览。若某入口不能证明路径安全，本片直接拒绝内部 runtime，不能开一个旁路。文件 backend 的 `Path.resolve()` 检查须配 Windows junction 与 POSIX symlink 替换用例；若发现 TOCTOU，可用 no-follow 打开或禁止不安全入口，不能把单次路径规范化说成完整 OS 沙箱。OS 上同账户的其他进程访问不属于 030A 所声称的隔离。

## 删除、解绑与恢复

- 现有 `DELETE /api/projects/{id}/tasks/{thread_id}` 对 `chat`/`files` 都是**解绑**，不删除本人对话、文件或 runtime；项目成员移除、项目删除也只让指令快照/项目关联失效，私人文件任务仍可经本人会话列表打开。`runtime_kind` 与 `threads.agent_id` 独立保存，不能以 context/link 消失判断“孤儿”。解绑后不再注入项目指令，不能因此继续访问项目资产。
- 本人对唯一 runtime 线程执行“删除整任务”时先停止运行、删 checkpoint，再安全删除工作区和 Agent/线程元数据；任何步骤失败返回错误且留下可重试记录，不得在文件删除失败后误报成功并仅删除数据库行。旧普通线程删除保持旧行为。针对 runtime 的通用 `DELETE /agents/{id}/threads/{thread_id}` 必须转入该完整清理流程或明确拒绝并给对应专用路由；通用 Agent 删除（HTTP `DELETE /api/agents/{id}` 与 CLI `delete_agent_offline`）必须拒绝内部 runtime，**专用整任务删除是唯一删除入口**。前端删除按钮走相同路径。重复删除在首次完成后统一 404；启动扫描只回收无 thread 引用的未完成创建，不清理仍可恢复的个人任务。

## 并行所有权与验收

- `claude-bailian/qwen3.8-max` 优先承担后端：先独占 M0 探针及必要的工具调用门禁；M0 验证通过后再执行 028 双迁移、项目任务 repo/service/router、内部 Agent 列/创建/启动、每轮工具门禁、HTTP/WS/CLI 管理阻断、文件路径和后端测试。工作包必须再按不重叠文件白名单拆为可审候选；若该路由继续报 `unrecognized_model` 且零差异，记录失败，由用户已授权 OpenCode 在同一后端白名单备援。Codex 只整合通过测试和范围检查的候选。
- `opencode-bailian/bailian-token-plan-personal/deepseek-v4.1-flash` 前端拥有 `dashboard/src/api/modules/projectTasks.ts`、`pages/Projects/ProjectTasks*`、`context/AgentContext.tsx`、聊天入口必要文件及 `locales/{zh,en}.json` 和对应测试；不改 Python。项目文件任务使用 `chat_agent_id` 导航，所有普通选择器过滤 `internal`，本人既有线程仍可进入。
- `qwen-code-review/glm-5.3` 固定 SHA 只读审查 ACL/工具/路径/生命周期；Codex 独立运行 Windows 目录逃逸、四身份 API 与真实浏览器入口、相关旧 028/029 回归、SQLite 迁移/竞态及前端类型/构建，然后只快进推送 `xiongbao/main`。根 `make all` 及真实 PostgreSQL 可用时另验；未运行的门禁不得写“通过”。

030A 完成条件：Windows 与 Linux 上新文件任务可真实读写仅自己的托管目录，重启后保留；chat 旧链路不变；任务级权限与文件/连接器/终端旁路有拒绝证据；无 P0/P1 审查；工作代码与记录推送。即便全部达到，也**不**核销 WorkBuddy 完整本地/云端执行、用户既有文件夹选择或视觉 1:1。
