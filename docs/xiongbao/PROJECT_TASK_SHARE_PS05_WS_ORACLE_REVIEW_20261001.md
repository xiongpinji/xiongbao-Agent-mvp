# PS05 会话归属测试观察补强

2026-10-01，来源 `338aef305d9fb7b4f7d376d5ef5e8f235e5fe4e2` 已包含发布的会话归属修复 `3d30a36120b673b61371be5bf28781c0a886c5cb`。本轮只补强 `tests/integration/test_chat_ws.py`，9 行增加、1 行删除；业务源码未改。

测试 helper 会克隆模板 `FakeHarnessAgent`，fixture 原先返回模板，部分拒绝访问后的 `last_request` 断言没有观察实际注册的执行实例。fixture 现在返回 registry 中的实际 Agent；合法 owner 请求须产生非空记录，且对象身份与 registry 一致；跨 Agent 异常绑定拒绝后，两个实际实例均须未记录请求。已有错误帧、会话绑定和线程归属断言保留。

先加入合法请求的非空断言，实际 RED 为 1 个失败、exit1；修复 fixture 后，四文件聚焦 GREEN 为 63/63、exit0。GREEN 运行于格式前原始字节，后续仅规范化该文件换行。独立 SOURCE_REVIEW_V2 确认规范化 Git diff 相同，并绑定最终 raw SHA256 `225b8fd2beea4f1c92e4e6b759375bf8a4a338c48fe24ab7453cd2e9972dbeae`。这次 RED 检验的是测试观察缺口，没有回退到未修复的业务版本。

最终冻结候选重新执行整套后端非 live 测试：**5329 通过、319 跳过、0 failures、0 errors**，exit0，另有 **24 warnings**，详细判断保留在独立实际质量报告。Ruff 检查与格式检查、mypy、前端 TypeScript、ESLint、Windows 换行兼容的 Prettier 检查及 Vite 构建均 exit0。前端格式使用 `--end-of-line auto`；字面 `make all` 在本机不可用，默认前端 CRLF 格式检查和前端单元测试未重跑。

质量运行 `quality-v3-e869bc921dfe4a819dd1ebd030c55816` 的来源与方法 before/after 字节一致，八个步骤的 owned Job 均已关闭、active0、child 已退出。TypeScript 构建可能写入 ignored `node_modules/.tmp` 缓存；不能称所有派生文件都只写 QA。子进程凭据采用 OS allowlist、uv offline/no-sync，不能代替通用网络防火墙。

独立源审查 V2、方法审查 V3 和实际质量审查 V1 均 GO，无未解决 P0/P1/P2。本地 cleanup probe 的正常退出、Job 分配前拒绝、超时三分支均确认本次 child 退出、Jobclosed/active0。被注入的失败分支保持 FAIL，probe PASS 仅表示清理合同通过，不能替代产品执行验收。

原始失败记录全部保留且不计为通过：源审查 V1 的观察对象 P2，方法 V1 的 Job 分配失败清理 P1，方法 V2 的缺失日志接受 P2，初次格式失败、后续被中止的 full attempt，以及首轮 probe 对 Windows venv launcher 进程数的过严断言。本轮只调整测试观察与私有 QA 方法。本次全套使用较短临时目录并正常完成；先前 full 中止前未取得完整错误追踪，不能据此断言其失败原因已被证明为路径长度。

仓库提供[结果摘要](evidence/ps05-current-review-20261001/summary.json)与[证据 SHA256 清单](evidence/ps05-current-review-20261001/evidence_manifest.json)。原始 XML、日志、方法及独立报告保留在本地 `work/qa-ps05-current-review-20261001`，原始身份载荷不发布。

本轮没有新增 TCP、浏览器、PostgreSQL、真实模型、付费 provider 或 GLM 证据。[9 月 30 日真实 TCP/WebSocket 15/15 补验](PROJECT_TASK_SHARE_PS05_WS_TCP_ACCEPTANCE_20260930.md)仍属于其原固定版本与合成 SQLite、本地 fake 边界；其旧文档和摘要未改。原三路由与既有失败 job 状态保留，本轮依据用户对 PS05 的 Codex 承接授权执行，不登记为原 CLI 路由成功。完整任务协作、全入口权限和 WorkBuddy 功能/视觉 1:1 继续未核销。
