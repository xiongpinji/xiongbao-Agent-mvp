# PS-05 Dashboard 会话归属：真实 TCP/WebSocket 局部补验

日期：2026-09-30。已发布修复 `3d30a36120b673b61371be5bf28781c0a886c5cb` 固定 Dashboard 会话的登录用户归属，校验已绑定线程的用户与 Agent，并让合法新线程操作原子修复历史归属异常。本批在包含该修复的干净源码 `0a79200e20cfce73dfa97a44efec92b816915848` 上完成真实回环 HTTP/WebSocket 补验：**初次运行 8/8 阶段、同库重启后 7/7 阶段，共 15/15 PASS**。产品业务源码未改。

成功 run 为 `run-5be7a351024e4708810bb4cc910cacd9`，父进程退出码 0。公开 HTTP 和 WebSocket 保持当前产品实现；模型由本地 `FakeHarnessAgent` 替代。全新 owned home 使用合成 SQLite；admin、owner 和 member 通过本地 TCP API 登录，JWT 仅在内存中使用。初始账户预置使用测试 helper，第二次启动不再 bootstrap。该批没有浏览器或截图。

| 路径                                   | 实际结果与读回                                                                                                                                                             |
| -------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 另一用户尚未建立会话时的外来归属请求   | 拒绝；没有新建目标会话，聊天记录与实际 fake 调用保持不变                                                                                                                   |
| 另一用户已有绑定会话时的外来归属请求   | 拒绝；原绑定、线程、消息记录与实际 fake 调用保持不变                                                                                                                       |
| 使用本人线程尝试改变另一用户的会话绑定 | 拒绝；另一用户绑定保持不变，未进入实际 fake 执行                                                                                                                           |
| 本人会话历史错绑到另一用户线程         | 同库重启后拒绝；异常绑定与另一用户线程保持不变，未进入实际 fake 执行                                                                                                       |
| 正常 owner/member 对话、订阅与切换     | 两身份输出属于各自线程；本人订阅成功，外来订阅拒绝；member 切换不改变 owner 绑定                                                                                           |
| 合法修复、删除后恢复与再拒绝           | 本人新线程操作修复归属；历史 owner、metadata、channel subject 和未读数读回正确；member 删除返回 204 与空正文，默认对话恢复到新的 active thread；修复后外来归属请求继续拒绝 |

两阶段记录 **15 次 WebSocket 交换，全部 socket 已关闭；实际 Agent 共执行 6 次 fake stream**。审计记录来自已装载 Agent 的实际实例，instance id 和 marker 均与 live registry、正常输入匹配。拒绝前后直接比较所选合成 Agent 的 `sessions`、`threads`、`thread_messages` 和实际执行调用数。现有测试 helper 会克隆模板 fake，模板记录本身不能代表已装载实例；本批实际实例观察补足了这项证据。报告还登记 26 次 HTTP 请求；该计数只包括 journey 包装器与登录记录，不包括初始账户和 provider helper 的预置请求。

两条异常会话记录是在第一次服务**正常停止且端口关闭之后**，仅向本 run 的合成库定点写入的 QA 夹具；线程和消息行未改。它用于检验历史异常的拒绝和合法修复，不是观测到的真实用户数据或生产迁移。重启后重新登录，先核对同库状态，再执行修复和恢复路径。公开 HTTP 修复的证据不替代内部通知分支测试；本地 fake 的历史存储也不证明真实模型消息正文跨重启持久化。

来源绑定 **297 个 tracked 文件**、方法绑定 **6 个脚本**在运行前后与当前字节全部一致，源码 HEAD/clean 未漂移。两次 API 均记录 `STOPPED`、`stop_complete=true`、正常退出；API、journey 和夹具共 5 个 child 退出码均为 0，全部 Windows Job `active=0` 且已关闭，回环端口关闭。外部 socket 和 PostgreSQL fallback 被禁止，两个 server 的 `denied_external_connections=[]`。owned 合成库保留为本地证据。

方法 V4 和成功原始证据分别经独立只读复核 **GO，无 P0/P1/P2**。原始报告及六脚本保留在本地 `work/qa-ps05-ws-tcp-20260930`；仓库发布[结果摘要](evidence/ps05-ws-tcp-20260930/summary.json)和[证据 SHA-256 清单](evidence/ps05-ws-tcp-20260930/evidence_manifest.json)，绑定发布摘要、原始报告、脚本与独立审查字节。成功 `run-result.json` SHA-256 为 `5a4d21453798e9fed5d1c41f28053d2d34ee3567159b6fced533f3faa20f8e0f`；实际证据复核报告 SHA-256 为 `717de8f279150d2fa965c540030fdfbd6e4377d15d559151d5ac0c72fe5495ba`。

此前三个失败 run 完整保留并不计为通过：V1 完成第一阶段但后台观察任务未处理停机，精确异常未记录；V2 增加失败传播后确认 Windows 读取句柄导致观察报告替换 `WinError 5`；V3 已正常收尾，但删除检查误用默认 200，当前接口合同是 204。修正仅发生在 QA 方法：观察任务显式传播异常，报告替换针对已定位的 Windows 错误最多重试一秒，持续占用仍失败；删除断言严格匹配 204 和空正文。业务归属、数据库无副作用、实际执行、哈希和正常停机断言保持。

原三条 Agent Orchestrator 路由保持配置：Claude/Qwen 月额度 429，OpenCode/DeepSeek 原实施任务超时零交付，GLM 固定提交复核因月额度 429 未运行。依据用户已批准的 Codex 承接，本批使用原生执行与独立只读审查；失败原 jobs 继续保留失败状态，PS05 子计划仍有两个旧 `in_progress` 台账项，不能登记为原路由成功。

这次仅发布局部补验文档，未重跑全量业务测试或构建。[此前字段保存补验](PROJECT_TODO_PS04C_SAVE_CONFLICT_20260930.md)固定 `87cec425` 已完成后端非 live 全套 **5329 通过/319 跳过/21 warnings**、前端相邻 **649 通过**及类型、Ruff、构建；`87cec425 → 0a79200e` 只有文档变更，包含同一 PS05 修复。该结果不改写早期中止的全套记录。默认全前端格式因 Windows CRLF 存量仍失败，Windows `--end-of-line auto` 检查通过；`make` 在本机不可用。

本批未验浏览器/登录表单、PostgreSQL、内部通知分支、真实用户或生产数据、付费 provider、WorkBuddy 同夹具功能/视觉 1:1、任务级全入口 ACL 或完整协作。既有 [PS05 PostgreSQL/TCP 补验](PROJECT_TASK_SHARE_PS05_PG_TCP_ACCEPTANCE_20260930.md)和[合成旧库升级补验](PROJECT_TASK_SHARE_PS05_PG_UPGRADE_ACCEPTANCE_20260930.md)各保持其固定版本与边界。PS-05B/006 和总体 25 项继续未核销。
