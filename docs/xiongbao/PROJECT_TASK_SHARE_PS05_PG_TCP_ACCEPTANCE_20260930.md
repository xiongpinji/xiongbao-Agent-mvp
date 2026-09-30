# PS-05B 024/025：PostgreSQL/TCP 与浏览器局部补验

日期：2026-09-30。固定业务源码 `259506837b95adabbccf630a7e15cccbdf7b4732`。本批没有修改 024/025 的业务实现；在新建的独立 PostgreSQL 18 数据库（schema 35）上启动真实 TCP API、Vite 和无头 Google Chrome，以四个合成账号运行 API 权限链，以 recipient 身份运行浏览器链，复核[卡片摘要分享](PROJECT_TASK_SHARE_CONTRACT.md)与[单独正文授权](PROJECT_TASK_CONTENT_READ_CONTRACT.md)的局部行为。

| 路径 | 实际结果 |
| --- | --- |
| 全新库迁移与多身份 API | 新库完成 schema 35 迁移，API 强制使用 PostgreSQL、拒绝 SQLite。owner 经 HTTP 创建项目、邀请 recipient 与 bystander、创建本人任务并关联项目。直接向**本次测试库**写入三条合成历史投影行，未调用模型。授权前 recipient、bystander、outsider 均不能读取任务；卡片授予后 recipient 只能读安全摘要，正文仍为 404，原 Agent 历史入口仍为 403。再次单独授正文后，recipient 只收到 human/assistant 纯文本，tool 行和原始结构字段不进入响应；bystander、outsider 仍为 404。数据库直接回读为一条有效卡片、一条正文授权。 |
| 真实浏览器与撤权 | recipient 在“分享给我的”任务卡打开只读详情，看到合成 human/assistant 正文，无 `/chat` 深链或 tool 文本。owner 通过 TCP API 撤正文后，新请求 404；浏览器重新加载并重开卡片，只显示“对话内容尚未共享”，数据库为卡片 1、正文 0。再撤卡片后，详情和正文均为 404，浏览器重新加载后列表无该任务，数据库有效授权均为 0。三张状态截图与浏览器 API 响应摘要随证据提交。owner 的撤权动作是 HTTP 调用，本批未复测 owner 的授权弹窗操作。 |
| PostgreSQL 有界交错 | 读取正文与撤卡片并发 20 轮：读取 200 共 11 次、404 共 9 次，撤回均 204；授正文与撤卡片并发 20 轮：授予 201 共 15 次、404 共 5 次，撤回均 204。每轮结束后直接回读无有效卡片/正文授权，撤回后新起的正文请求为 404；卡片重授时正文仍为 0，必须再次明确授予。已观察交错中无 500 或死锁，**不代表所有锁序交错都安全**。 |
| 归档顺序门禁 | 在本次测试库把项目置为已归档后，已授权 recipient 仍可读取；新增/重复卡片与正文授予均为 403，正文和卡片撤销均为 204，最后授权行归零。这里是顺序验证，归档提交与授权同时发生的竞态未测。 |

证据目录：[冻结清单](evidence/ps05-pg-tcp-20260930/evidence_manifest.json)含 16 个原始 QA 输入/收据/截图的大小与 SHA-256；同目录保存[正向 API 结果](evidence/ps05-pg-tcp-20260930/api_flow_result.json)、[浏览器撤权结果](evidence/ps05-pg-tcp-20260930/browser_revoke_result.json)、[并发结果](evidence/ps05-pg-tcp-20260930/pg_races_result.json)、[临时集群目录清理收据](evidence/ps05-pg-tcp-20260930/owned_temp_cleanup.json)及三张 PNG。仓库内八个非敏感文件的七项发布字节哈希另列于清单；发布的 JSON 将原始收据换行规范为 LF，内容未变。其余九项需要本机同级 `work/qa-project-task-share-20260930/` 的完整脚本与启停收据；没有把合成登录口令、JWT、浏览器配置或测试 app home 加入仓库。两次先行浏览器尝试分别在脚本定位“关闭”按钮和 Escape 关闭弹窗处超时；第三次改为页面重新加载后完整通过。前两次不计为通过证据，业务源码未因此改动。

资源收据显示 API `STOPPED/exit 0`、启动/停止各一次、测试库已删除、剩余数据库连接 0、业务源码前后哈希一致；PG 集群 `STOPPED/exit 0`、`pg_ctl status 3`。随后脚本再次验证收据绑定的 `/tmp` 路径、停机状态和端口，只删除该自建临时集群目录，并验证路径消失。PG、API、Vite 三个回环端口均已关闭。冻结时 HEAD 保持上述固定源码，业务文件逐字节与 API 收据一致；工作区内仅有本次验收文档与证据改动。

本批**只补全新库的 024/025 权限与有界并发局部证据**：023→024→025 旧库升级、归档/授权同发、读取与解绑、成员移除与授予、owner/outsider 浏览器路径、同页收到 404 后清缓存、安装版/真实账号、多端协同、附件/工作区/执行流、外部逐消息分享以及 WorkBuddy 同夹具逐状态视觉均未验收。并发脚本仅同时发起 HTTP 请求，没有强制特定数据库锁冲突。GLM-5.3 未对这次补验执行新只读审查；此前[025 验收记录](PROJECT_TASK_TEXT_025_ACCEPTANCE.md)中的锁序死锁与归档竞态 P2 风险不能凭这 40 轮消除。PS-05B、完整 PS-05 和 25 项总体目标仍未核销。

后续另有[合成 v23 库逐版升级补验](PROJECT_TASK_SHARE_PS05_PG_UPGRADE_ACCEPTANCE_20260930.md)通过。上段的旧库升级缺口是本批冻结时的状态；新补验没有使用真实用户旧库，也不改变上述其他未验范围。
