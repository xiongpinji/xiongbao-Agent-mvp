# 030A 文件任务 B4 受控候选验收

状态：**允许在创建开关关闭时集成；不允许据此激活文件模式。** 本文记录的是 030A 的受控候选，不代表 WorkBuddy 项目空间整体对齐。

## 固定候选与复核

- B3 受控创建与绑定：`54c25b885c5fd63378b4ebe0630107dde2b5e0f5`。
- B4 默认拒绝、所有者隔离和托管根路径边界：`e493e9e95235772f9252f0efb8934b0f69d1baab`。
- Codex 定点修复：`9ca5adeaabbad8b9ad10d0f1e129b2b166d1c195` 与 `09117ef5929c643609f6a8228b95fe02ac08c1e4`。修复在有损路径转换前拒绝 UNC、双前导分隔符及混合分隔符，并禁止内部运行时预览 URL 通过 `source` 切换到其他 Agent。
- GLM 5.3 只读复核固定于 `09117ef5929c643609f6a8228b95fe02ac08c1e4`：两项缺陷均判闭合，无新 P0/P1/P2；结论是**开关关闭时有条件 GO**。审查任务为 `qwen-code-review-20260926-095517-859c4b`。GLM 没有运行 Git 或测试命令，不能把其静态审查写成实测。

## Codex 独立验证

在 B4 隔离工作树上，新增回归先复现三类 404 错误，再证明修复后应得的 403；补充的 `/\\host` 混合分隔符同样先红后绿。最终 B4 七个聚焦测试文件 **155/155 通过**，Ruff 检查与格式检查、两处改动模块的严格 mypy、差异检查通过。

从已推送基线 `eb25a8626f39ac175b06fe77a2170cc21dd2572b` 建立的隔离集成工作树中，B3、B4、F1、F2 候选顺序摘取且无冲突。集成字节上再次通过 B4 聚焦测试 **155/155**、普通 Agent/API/网关/CLI/工作区/聊天/终端回归 **946/946**，前端八个相关文件 **91/91**；Dashboard TypeScript/Vite/PWA 构建、全量 Ruff 检查与格式检查、全量 ESLint 均通过（ESLint 有 67 条警告）。

未通过的全量检查应单列：严格 mypy 在未改动的 `src/octop/infra/projects/asset_storage.py` 两处 `unused-ignore` 报错；以当前 WSL 工作树 CRLF 检出方式检查全量 Prettier 时，三个未改动文件仍不符合格式。未将这些基线项算作本批绿灯，也未运行会自动重写全仓文件的 `make all`。

## 激活前置条件

`src/octop/infra/projects/file_tasks.py` 中 `PROJECT_TASK_FILES_MODE_ENABLED = False` 保持不变。当前不能创建文件模式任务。启用前仍需：修复 F2 的“已发现文件”逐行下载 P1、Windows junction/reparse-point 实机隔离、真实 B2 runtime 和模型可见工具边界、PostgreSQL 迁移与配额并发、已认证浏览器/HTTP 完整旅程，以及按 030 合同决定 TOCTOU 风险姿态。GLM 的三个 P3（大写 `FILE://` 的拒绝码、额外路径矩阵、下载文件名 CR/LF）也保留为后续修整项。
