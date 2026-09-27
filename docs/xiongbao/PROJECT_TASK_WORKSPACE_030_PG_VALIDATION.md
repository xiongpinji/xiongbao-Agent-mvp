# 030A 文件任务：PostgreSQL 实库补验

验证日期：2026-09-27。固定源码：`1813933de6ab4fffdf762dae76b14f594c3ce867`。本批只补 B1 数据库门禁证据，不改变 030A 的激活结论。

在 WSL 中新建的隔离 PostgreSQL 18.6 集群及一次性 `octop_test` 库上运行 `tests/unit/db/test_project_task_file_runtime_postgres.py`，**3 passed，退出码 0**。另有 3 条既有 pytest 临时目录清理警告。该文件会执行 `DROP SCHEMA public CASCADE`，复测必须只指向一次性测试库，不能使用共享库或本机已有的 5432 数据库；本次业务测试仅连接临时随机端口，测试后已停止集群。

三个实库用例分别验证：

1. PostgreSQL fresh schema 的 `runtime_kind`、任务模式约束和唯一索引，并创建至 owner 配额上限、拒绝超额创建；普通 Agent 配置不能伪造文件任务运行体种类。
2. 两个独立连接池争抢 owner 最后一个配额席位，恰好一方创建成功、一方按配额拒绝，最终数量不超上限。
3. 文件模式任务上下文要求成对的来源专家与运行体 ID，并对运行体唯一绑定及约束做实库验证。

该结果覆盖**新库迁移形状与上述配额竞争**。真实历史数据库升级、项目级配额的所有并发时序、实际模型/运行体工具可见性与调用、已认证浏览器创建—读写—重启—撤权旅程、Windows 其余重解析点和 TOCTOU 风险仍需独立验收。`PROJECT_TASK_FILES_MODE_ENABLED = False` 保持关闭；本记录不授权激活文件模式，也不代表 WorkBuddy 项目任务空间整体对齐。
