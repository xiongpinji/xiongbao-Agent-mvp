# 030A M0 · 文件工具与路径边界探针验收

状态：**M0 探针通过，030A 功能尚未实现**。固定代码为 `96dc290a5a4142aeae7092d14c4a17b485fad4bd`，已快进推送用户仓库 `xiongbao/main` 并核对远端 SHA。此记录只准许后续分工实现；它不开放 `mode="files"`，也不核销 WorkBuddy 本地/云端项目空间。

## 已验证的事实

- 实际安装的 `harness-agent 1.0.14` 配置探针显示，模型可见工具被收敛到 `ls/read_file/write_file/edit_file/glob/grep` 六项；伪造 `task`、`execute`、web、env-file 及未知插件工具调用在处理器执行前被拒绝，测试中的 handler spy 未执行。工具白名单由 `project_task_file_boundary.py` 的中间件执行，不仅靠提示词或 `tools_disabled`。
- 固定虚拟 `FilesystemBackend` 的路径探针覆盖越界；`project_task_file_paths.py` 提供严格的相对路径规范化与托管根解析。测试拒绝宿主绝对路径、Windows drive/UNC、`file://`、`~`、`..`、NUL、`from_workspace=False`、越界 POSIX symlink 与 Windows junction。
- Codex 独立复跑：WSL 定向 pytest **59 通过、2 项 Windows 专项跳过**；Windows 仓库 venv 定向 pytest **57 通过、4 项 POSIX 专项跳过**。四项 Windows 跳过之外的 junction 单测已执行并通过；另以仓库外独立临时 canary 验证安装版 `BackendWorkspace` 的 junction `read_text`、`exists`、`materialize_local` 均拒绝越界。四个改动文件的 Ruff 检查/格式检查及两个源码模块 strict mypy 通过。
- 源码审查发现安装版 `BackendWorkspace` 在 POSIX 上对宿主绝对路径有回退行为，因此任何内部 runtime 的 HTTP 文件入口都必须调用新严格辅助函数；不能把底层 backend 自身当作完整隔离。M0 新模块尚未接入 manager、API 或现有 Agent 路由，旧运行行为未改变。

## 后续强制关卡

1. 后端创建时挂载并验证该工具中间件、文件 backend 和专属托管根；每一轮禁用插件、MCP、技能、子 Agent、媒体与个人连接器，不能让普通 Agent 的配置绕过六工具边界。
2. 工作区、附件、artifacts、媒体、预览和下载的每个 HTTP/WS/CLI 入口都要用真实托管根做路径和本人既有线程检查；不能证明安全的入口直接拒绝。对符号链接/junction 变化与打开文件之间的竞态继续做专门回归或禁用高风险入口。
3. 完成双数据库 028 迁移、原子 8/64 配额、本人私有 runtime、创建失败补偿、重启恢复、解绑保留、整任务删除，以及前端能力和错误态接线。最后由 GLM 在固定整合 SHA 上只读审查，Codex 独立验证四身份 API、Windows/Linux 路径、构建与真实浏览器旅程。

M0 仅证明可构建受控文件任务的基础边界；它没有做真实模型请求、PostgreSQL 实库运行、完整 Windows 安装路径或 WorkBuddy 逐状态视觉验收。
