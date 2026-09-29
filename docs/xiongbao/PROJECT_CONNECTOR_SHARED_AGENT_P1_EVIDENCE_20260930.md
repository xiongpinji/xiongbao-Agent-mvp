# 共享 Agent 个人连接器同名绑定风险：本地复现

状态：2026-09-30 只读核查，业务源码固定于 `7960a29bd1a2f0e92096d20983334057cb879719`，依赖 `orcakit-harness-agent==1.0.14`。本页记录的是**本地合成复现的 P1 权限风险**，不是生产账号泄漏或 PS-07 连接器功能验收；没有调用真实 MCP、模型或凭据。

两个用户各有名为 `vault` 的个人自定义 MCP，分别使用纯合成的 `owner-1` / `owner-2` 结果。在同一个共享 Agent 上，用户 1 先加载工具后，用户 2 调用 `AgentManager.prepare_chat_mcp(..., connector_user_id=2)` 返回无缺失，却没有加载用户 2 的工具；按用户 2 本轮 `mcp_servers=["vault"]` 过滤后的工具调用返回 `owner-1`。Codex 独立复跑仓库外的无网络探针，退出码 0，关键输出为：

```json
{"loader_users_after_second_prepare":[1],"second_missing":[],"second_turn_visible_tool_names":["vault_read"],"second_turn_synthetic_result":"owner-1","user2_cache_tool_result":"owner-2","graph_rebuilds":1}
```

探针位于本机 `work/qa-project-activity-20260929/readonly-ps07b-mcp-seam-v1/synthetic_probe.py`（SHA-256 `C67E270C09CAE7904CAB254AF0CA3D1E9B73FF68B974058EB6110D0E9C4909BE`），不随此文档推送。它 mock 了 MCP 加载器并使用假 Agent，实际调用项目的 `prepare_chat_mcp`、依赖的 `HarnessAgent.append_mcp_tools` 和工具过滤器；这证明当前本地逻辑路径，不能替代真实共享 Agent、真实连接器与真实模型的端到端验收。

错误接线可定位在 `src/octop/infra/agents/manager.py:2212-2256`：已加载工具仅按名称前缀判定，早于按当前用户读取个人配置；同文件 `:2119-2178` 的缓存键虽包含用户 ID，却被这条早退绕过。`src/octop/infra/connectors/mcp_tool_cache.py:40-90` 的工具闭包绑定首次加载的底层工具。锁定依赖的 `harness_agent/agent.py:277-287` 按工具名去重，即使强制加载第二人的缓存也不会替换注册工具；`harness_agent/mcp.py:304-335` 的模型轮过滤同样只看名称前缀。连接器变更时的 Agent 重载不是每轮身份门禁，见 `manager.py:2023-2067`。

**验收阻断：** 在个人连接器可进入共享 Agent 项目任务之前，必须证明两名用户交错调用同名不同凭据的工具始终各用本人身份，撤权或配置变化后的下一次调用拒绝旧工具；无法证明时须明确拒绝该能力。仅调整前端标签、缓存键、早退判断或手工重载不足以证明隔离。下一步安全修复节奏待用户选择，PS-07B 不据此核销。
