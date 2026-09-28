# PS-04B 补验独立只读审查摘录

协调者据两名独立审查子代理的实际返回整理此记录。审查者只读，没有导入或重跑脚本/测试，没有修改文件或启停服务。它们不是 GLM。绑定源码 HEAD 为 `e72f72758a4f91c00738127e1a46850c9fd54174`，唯一测试改动 SHA256 为 `a186d40c017cfd872cbbbbedfd025fb971e306dd6fefba842626577707a9bf44`。

## HTTP 与 SQLite

`ps08_browser_gate_map` 最终结论：未发现可操作 P0/P1/P2，可用于指定两份测试的真实 PostgreSQL ASGI/直接 service 和 SQLite 兼容验收，不能替代 TCP 浏览器通过。

- RED 仅目标 call 因 PostgresPool 缺少 .path 失败，setup/teardown 通过；内存逆转两行适配精确恢复 RED 源码指纹。
- GREEN 38 个唯一用例、114 个阶段均通过；34 ASGI 加 4 直接 service；3 个故障标签是子集。
- GREEN 727 项绑定、SQLite v2 723 项绑定与当前字节逐项一致；实际读取全部 38 份 SQLite 文件头。
- 38 server 正常关闭、39 PG pool 各关闭一次，剩余连接为 0，logger/patch/环境恢复通过。
- 第一轮 SQLite 和第一轮 Job smoke 保持 INCONCLUSIVE；v2 成功另列。
- 六个 Windows PID 当时均不存在；旧集群 pg_ctl status 实际为 3、相关 WSL PID 不存在、43317 无监听。

## TCP 浏览器与生命周期

`ps08_model_tool_boundary_map` 先核对执行脚本，又核对完成结果；最终结论未发现可操作 P0/P1/P2，13 阶段均完成，可用于本片合并门禁。

- 实际正式路由、私有 PG 身份/目录、schema 33、canonical 导入、pool close=1/底层关闭/剩余连接 0 与脚本一致。
- 没有 route/fulfill 网络替身；登录、409、三图顺序/响应头/字节、双视口、SPA 切换、撤权/删除旧路径断言均核对。
- 六个 Context/browser 关闭、Node rc=0、cleanup_errors 空；三个 Job active=0/handle 关闭。Vite rc=2 是 runner 对其私有 Job 的终止，API/browser rc=0。
- 五个 Windows PID 与记录的 WSL cluster/backend PID 当前均不存在；API/Vite/47687 无监听；cluster STOPPED、stop code 0/status after 3。
- 1973 项绑定与执行前字节一致，四份脚本快照一致，12 张截图存在。
- 三条图片 GET ERR_ABORTED 保留在原始结果，不能报告成所有网络请求零失败。

| 证据 | SHA256 |
| --- | --- |
| browser_flow.cjs | d6e12dab6a80a6a0d35622c763ab233bb15ab1714e52aece5961c3fce0fc5d2b |
| tcp/run_browser.py | 59861ba237a66149b72f43a373c4bcc6629dab211895fdae1c26b8db68c7abf9 |
| attempt-3/command-result.json | 1dbba1d514661a196ab2535163f1237baa7042eec6cdc93fbc6a94b5eeb42f77 |
| attempt-3/journey/result.json | cf1fa0b9daee1f5a8497f232ca1b54e68f1e4aa7eefbf2bdff6f2c1ed916fc1b |
| attempt-3/server-result.json | bfb7096fa8405298fd9fbd2931083ab9bb6aa9d5530c88eee7d5ea8dab28e9f9 |
| cluster-result.json | 1bfe993560bda7f5d92a9615f3f1961dd54f6c02d57deaa156a7ea07a1dd41ee |

## 未验范围

FakeHarnessManager、停用主动调度；未证明真实模型/工具执行、GLM、系统剪贴板、上传失败重试、直接 prop 切换晚到响应、并发撤权/删除竞态、既存库迁移、Blob 生命周期、完整键盘/布局矩阵或 WorkBuddy 全面对齐。完整边界见[补验记录](../../PROJECT_TODO_DETAIL_PS04B_PG_HTTP_BROWSER_ACCEPTANCE_20260928.md)。

本摘录记录上述原始 QA 的只读审查；归档后的文件与说明另由协调者核对并交付审查，不把后续文档变更冒称已在这些执行中运行。
